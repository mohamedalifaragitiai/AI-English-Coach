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
    """Text-to-Speech service using Piper."""

    def __init__(
        self,
        tts_config: dict | None,
        guard: ResourceGuard,
    ) -> None:
        self.guard = guard
        self.available = False
        self.model_path: Path | None = None
        self.config_path: Path | None = None
        self.sample_rate = 22050
        self._voice = None

        if tts_config:
            self.model_path = Path(tts_config["model_path"])
            self.config_path = Path(tts_config["config_path"])

            if self.model_path.exists() and self.config_path.exists():
                try:
                    with open(self.config_path) as f:
                        config = json.load(f)
                        self.sample_rate = config.get("audio", {}).get("sample_rate", 22050)
                except Exception:
                    pass

                try:
                    from piper import PiperVoice
                    self._voice = PiperVoice.load(str(self.model_path), str(self.config_path))
                    self.available = True
                    logger.info("tts_initialized", model=str(self.model_path), sample_rate=self.sample_rate)
                except Exception as e:
                    logger.error("tts_load_error", error=str(e))
            else:
                logger.warning("tts_model_not_found", path=str(self.model_path))
        else:
            logger.warning("tts_not_configured")

    async def synthesize(self, text: str) -> TTSResult | None:
        """Synthesize text to audio."""
        if not self.available or self._voice is None:
            logger.warning("tts_unavailable")
            return None

        if not text or not text.strip():
            return None

        start_time = time.perf_counter()

        admission = await self.guard.acquire(
            ResourceEstimate(ram_bytes=0.1e9, description="TTS synthesis"),
            path="hot",
        )

        if admission.degraded and admission.params.get("skip_tts"):
            logger.info("tts_skipped", reason="resource_pressure")
            return None

        try:
            # Collect audio from generator
            audio_chunks = []
            for chunk in self._voice.synthesize(text):
                audio_chunks.append(chunk.audio_int16_bytes)

            raw_audio = b''.join(audio_chunks)

            # Create WAV in memory
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(raw_audio)

            audio_bytes = wav_buffer.getvalue()
            duration = len(raw_audio) / (self.sample_rate * 2)  # 16-bit = 2 bytes per sample

            processing_time = time.perf_counter() - start_time
            hotpath_stage_duration_seconds.labels(stage="tts").observe(processing_time)

            logger.info(
                "tts_complete",
                text_len=len(text),
                audio_size=len(audio_bytes),
                duration=round(duration, 2),
                time=round(processing_time, 3),
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

    @property
    def is_available(self) -> bool:
        return self.available
