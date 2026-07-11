"""Text-to-Speech using Piper.

CPU-based TTS synthesis with streaming audio output.
Runs on CPU to leave VRAM for STT and LLM.
"""

import io
import json
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

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

        if tts_config:
            self.model_path = Path(tts_config["model_path"])
            self.config_path = Path(tts_config["config_path"])

            if self.model_path.exists() and self.config_path.exists():
                self.available = True
                # Read sample rate from config
                try:
                    with open(self.config_path) as f:
                        config = json.load(f)
                        self.sample_rate = config.get("audio", {}).get("sample_rate", 22050)
                except Exception:
                    pass

                logger.info(
                    "tts_initialized",
                    model=str(self.model_path),
                    sample_rate=self.sample_rate,
                )
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
        if not self.available:
            logger.warning("tts_unavailable")
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
            # Use piper CLI for synthesis
            # piper --model model.onnx --output_file - < text
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(text)
                text_file = f.name

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                output_file = f.name

            # Run piper
            result = subprocess.run(
                [
                    "piper",
                    "--model", str(self.model_path),
                    "--config", str(self.config_path),
                    "--output_file", output_file,
                ],
                input=text,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                # Fallback: try with echo pipe
                result = subprocess.run(
                    f'echo "{text}" | piper --model {self.model_path} --config {self.config_path} --output_file {output_file}',
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

            if result.returncode != 0:
                logger.error("tts_piper_error", stderr=result.stderr)
                return None

            # Read the output file
            output_path = Path(output_file)
            if not output_path.exists():
                logger.error("tts_output_missing")
                return None

            audio_bytes = output_path.read_bytes()

            # Calculate duration from WAV header
            duration = 0.0
            try:
                with wave.open(output_file, "rb") as wf:
                    frames = wf.getnframes()
                    rate = wf.getframerate()
                    duration = frames / rate
            except Exception:
                pass

            # Cleanup temp files
            Path(text_file).unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

            processing_time = time.perf_counter() - start_time
            hotpath_stage_duration_seconds.labels(stage="tts").observe(processing_time)

            logger.info(
                "tts_complete",
                text_len=len(text),
                duration=duration,
                processing_time=processing_time,
            )

            return TTSResult(
                audio_bytes=audio_bytes,
                sample_rate=self.sample_rate,
                duration_seconds=duration,
                processing_time_seconds=processing_time,
            )

        except subprocess.TimeoutExpired:
            logger.error("tts_timeout")
            return None
        except FileNotFoundError:
            logger.error("tts_piper_not_found", message="Install piper: pip install piper-tts")
            self.available = False
            return None
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
