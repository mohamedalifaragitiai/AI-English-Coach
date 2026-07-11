"""Text-to-Speech using Piper.

CPU-based TTS synthesis with streaming audio output.
Runs on CPU to leave VRAM for STT and LLM.
"""

import io
import json
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from backend.core.logging import get_logger
from backend.core.metrics import hotpath_stage_duration_seconds
from backend.core.resource_guard import ResourceEstimate, ResourceGuard

logger = get_logger(__name__)


@dataclass
class TTSResult:
    """Result from TTS synthesis."""

    audio_bytes: bytes
    sample_rate: int
    duration_seconds: float
    processing_time_seconds: float


class TTSService:
    """Text-to-Speech service using Piper.

    Piper is a fast, local neural TTS that runs on CPU.
    This leaves GPU VRAM available for STT and LLM.

    Usage:
        tts = TTSService(tts_config, guard)

        # Synthesize to bytes
        result = await tts.synthesize("Hello, how are you?")

        # Save to file
        await tts.synthesize_to_file("Hello!", "output.wav")
    """

    def __init__(
        self,
        tts_config: dict | None,
        guard: ResourceGuard,
    ) -> None:
        """Initialize TTS service.

        Args:
            tts_config: Dict with 'model_path' and 'config_path' keys, or None if TTS unavailable
            guard: ResourceGuard for admission control
        """
        self.guard = guard
        self.available = False
        self.model_path: Path | None = None
        self.config_path: Path | None = None
        self.sample_rate = 22050  # Default Piper sample rate
        self._voice = None

        if tts_config:
            self.model_path = Path(tts_config["model_path"])
            self.config_path = Path(tts_config["config_path"])

            if self.model_path.exists() and self.config_path.exists():
                # Read sample rate from config
                try:
                    with open(self.config_path) as f:
                        config = json.load(f)
                        self.sample_rate = config.get("audio", {}).get("sample_rate", 22050)
                except Exception:
                    pass

                # Load Piper voice
                try:
                    from piper import PiperVoice

                    self._voice = PiperVoice.load(str(self.model_path), str(self.config_path))
                    self.available = True
                    logger.info(
                        "tts_initialized",
                        model=str(self.model_path),
                        sample_rate=self.sample_rate,
                    )
                except ImportError:
                    logger.error("piper_not_installed", message="Run: uv add piper-tts")
                except Exception as e:
                    logger.error("tts_load_error", error=str(e))
            else:
                logger.warning("tts_model_not_found", path=str(self.model_path))
        else:
            logger.warning("tts_not_configured")

    async def synthesize(self, text: str) -> TTSResult | None:
        """Synthesize text to audio.

        Args:
            text: Text to synthesize

        Returns:
            TTSResult with audio bytes, or None if TTS unavailable
        """
        if not self.available or self._voice is None:
            logger.warning("tts_unavailable")
            return None

        if not text or not text.strip():
            logger.warning("tts_empty_text")
            return None

        start_time = time.perf_counter()

        # Request admission (TTS is CPU-based, minimal resources)
        admission = await self.guard.acquire(
            ResourceEstimate(ram_bytes=0.1e9, description="TTS synthesis"),
            path="hot",
        )

        # Under severe pressure, skip TTS
        if admission.degraded and admission.params.get("skip_tts"):
            logger.info("tts_skipped", reason="resource_pressure")
            return None

        try:
            # Synthesize to WAV bytes in memory
            wav_buffer = io.BytesIO()

            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(self.sample_rate)

                # Synthesize and write audio
                for audio_bytes in self._voice.synthesize_stream_raw(text):
                    wav_file.writeframes(audio_bytes)

            audio_bytes = wav_buffer.getvalue()
            wav_buffer.seek(0)

            # Calculate duration
            duration = 0.0
            try:
                with wave.open(wav_buffer, "rb") as wf:
                    frames = wf.getnframes()
                    rate = wf.getframerate()
                    duration = frames / rate
            except Exception:
                pass

            processing_time = time.perf_counter() - start_time
            hotpath_stage_duration_seconds.labels(stage="tts").observe(processing_time)

            logger.info(
                "tts_complete",
                text_len=len(text),
                audio_size=len(audio_bytes),
                duration=round(duration, 2),
                processing_time=round(processing_time, 3),
            )

            return TTSResult(
                audio_bytes=audio_bytes,
                sample_rate=self.sample_rate,
                duration_seconds=duration,
                processing_time_seconds=processing_time,
            )

        except Exception as e:
            logger.error("tts_error", error=str(e))
            return None

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str | Path,
    ) -> bool:
        """Synthesize text directly to a file.

        Args:
            text: Text to synthesize
            output_path: Path for output WAV file

        Returns:
            True if successful
        """
        result = await self.synthesize(text)
        if result is None:
            return False

        try:
            Path(output_path).write_bytes(result.audio_bytes)
            return True
        except Exception as e:
            logger.error("tts_save_error", error=str(e))
            return False

    @property
    def is_available(self) -> bool:
        """Check if TTS is available."""
        return self.available
