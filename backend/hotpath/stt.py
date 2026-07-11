"""Speech-to-Text using Faster-Whisper.

Provides streaming transcription with ResourceGuard integration.
Supports GPU acceleration with CPU fallback.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Iterator

import numpy as np

from backend.core.logging import get_logger
from backend.core.metrics import hotpath_stage_duration_seconds
from backend.core.resource_guard import Admission, ResourceEstimate, ResourceGuard

logger = get_logger(__name__)


@dataclass
class TranscriptionSegment:
    """A segment of transcribed speech."""

    text: str
    start: float  # seconds
    end: float  # seconds
    confidence: float  # 0-1
    words: list[dict] | None = None  # word-level timestamps if available


@dataclass
class TranscriptionResult:
    """Complete transcription result."""

    text: str
    segments: list[TranscriptionSegment]
    language: str
    confidence: float
    duration_seconds: float
    processing_time_seconds: float


class STTService:
    """Speech-to-Text service using Faster-Whisper.

    Usage:
        stt = STTService(model, guard)

        # Transcribe audio file
        result = await stt.transcribe(audio_path)

        # Transcribe audio array
        result = await stt.transcribe_array(audio_array, sample_rate=16000)

        # Stream transcription
        async for segment in stt.transcribe_stream(audio_path):
            print(segment.text)
    """

    def __init__(
        self,
        model,  # WhisperModel instance
        guard: ResourceGuard,
        language: str = "en",
    ) -> None:
        """Initialize STT service.

        Args:
            model: Faster-Whisper model instance
            guard: ResourceGuard for admission control
            language: Target language code
        """
        self.model = model
        self.guard = guard
        self.language = language

    async def transcribe(
        self,
        audio_path: str | Path,
        word_timestamps: bool = False,
    ) -> TranscriptionResult:
        """Transcribe an audio file.

        Args:
            audio_path: Path to audio file (wav, mp3, etc.)
            word_timestamps: Include word-level timestamps

        Returns:
            TranscriptionResult with full transcription
        """
        start_time = time.perf_counter()

        # Request admission from guard
        admission = await self.guard.acquire(
            ResourceEstimate(vram_bytes=0.5e9, description="STT transcription"),
            path="hot",
        )

        # Apply any degraded parameters
        beam_size = 5
        if admission.degraded:
            beam_size = admission.params.get("beam_size", 1)
            logger.info("stt_degraded", beam_size=beam_size)

        try:
            segments_iter, info = self.model.transcribe(
                str(audio_path),
                language=self.language,
                beam_size=beam_size,
                word_timestamps=word_timestamps,
                vad_filter=True,
            )

            # Collect all segments
            segments = []
            full_text_parts = []

            for segment in segments_iter:
                seg = TranscriptionSegment(
                    text=segment.text.strip(),
                    start=segment.start,
                    end=segment.end,
                    confidence=getattr(segment, "avg_logprob", 0.0),
                    words=[
                        {"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                        for w in (segment.words or [])
                    ] if word_timestamps else None,
                )
                segments.append(seg)
                full_text_parts.append(seg.text)

            processing_time = time.perf_counter() - start_time
            hotpath_stage_duration_seconds.labels(stage="stt").observe(processing_time)

            # Calculate average confidence
            avg_confidence = 0.0
            if segments:
                # Convert log probability to confidence (rough approximation)
                avg_logprob = sum(s.confidence for s in segments) / len(segments)
                avg_confidence = min(1.0, max(0.0, 1.0 + avg_logprob / 5.0))

            result = TranscriptionResult(
                text=" ".join(full_text_parts),
                segments=segments,
                language=info.language,
                confidence=avg_confidence,
                duration_seconds=info.duration,
                processing_time_seconds=processing_time,
            )

            logger.info(
                "stt_complete",
                duration=info.duration,
                processing_time=processing_time,
                segments=len(segments),
                rtf=processing_time / info.duration if info.duration > 0 else 0,
            )

            return result

        except Exception as e:
            logger.error("stt_error", error=str(e))
            raise

    async def transcribe_array(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        word_timestamps: bool = False,
    ) -> TranscriptionResult:
        """Transcribe audio from numpy array.

        Args:
            audio: Audio samples as numpy array (float32, mono)
            sample_rate: Sample rate (should be 16000 for Whisper)
            word_timestamps: Include word-level timestamps

        Returns:
            TranscriptionResult with full transcription
        """
        start_time = time.perf_counter()

        # Ensure correct format
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        if len(audio.shape) > 1:
            audio = audio.mean(axis=1)  # Convert to mono

        # Resample if needed
        if sample_rate != 16000:
            # Simple resampling (for production, use proper resampling)
            ratio = 16000 / sample_rate
            new_length = int(len(audio) * ratio)
            indices = np.linspace(0, len(audio) - 1, new_length).astype(int)
            audio = audio[indices]

        # Request admission
        admission = await self.guard.acquire(
            ResourceEstimate(vram_bytes=0.5e9, description="STT array"),
            path="hot",
        )

        beam_size = 5
        if admission.degraded:
            beam_size = admission.params.get("beam_size", 1)

        try:
            segments_iter, info = self.model.transcribe(
                audio,
                language=self.language,
                beam_size=beam_size,
                word_timestamps=word_timestamps,
                vad_filter=True,
            )

            segments = []
            full_text_parts = []

            for segment in segments_iter:
                seg = TranscriptionSegment(
                    text=segment.text.strip(),
                    start=segment.start,
                    end=segment.end,
                    confidence=getattr(segment, "avg_logprob", 0.0),
                )
                segments.append(seg)
                full_text_parts.append(seg.text)

            processing_time = time.perf_counter() - start_time
            hotpath_stage_duration_seconds.labels(stage="stt").observe(processing_time)

            avg_confidence = 0.0
            if segments:
                avg_logprob = sum(s.confidence for s in segments) / len(segments)
                avg_confidence = min(1.0, max(0.0, 1.0 + avg_logprob / 5.0))

            return TranscriptionResult(
                text=" ".join(full_text_parts),
                segments=segments,
                language=info.language,
                confidence=avg_confidence,
                duration_seconds=info.duration,
                processing_time_seconds=processing_time,
            )

        except Exception as e:
            logger.error("stt_array_error", error=str(e))
            raise

    async def transcribe_stream(
        self,
        audio_path: str | Path,
    ) -> AsyncIterator[TranscriptionSegment]:
        """Stream transcription segments as they become available.

        Args:
            audio_path: Path to audio file

        Yields:
            TranscriptionSegment as each segment is transcribed
        """
        admission = await self.guard.acquire(
            ResourceEstimate(vram_bytes=0.5e9, description="STT stream"),
            path="hot",
        )

        beam_size = 5
        if admission.degraded:
            beam_size = 1

        try:
            segments_iter, info = self.model.transcribe(
                str(audio_path),
                language=self.language,
                beam_size=beam_size,
                vad_filter=True,
            )

            for segment in segments_iter:
                yield TranscriptionSegment(
                    text=segment.text.strip(),
                    start=segment.start,
                    end=segment.end,
                    confidence=getattr(segment, "avg_logprob", 0.0),
                )

        except Exception as e:
            logger.error("stt_stream_error", error=str(e))
            raise
