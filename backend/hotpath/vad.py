"""Voice Activity Detection using Silero VAD.

Detects speech start/end for turn-taking in conversation.
Handles barge-in detection and silence timeouts.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import numpy as np
import torch

from backend.core.logging import get_logger

logger = get_logger(__name__)


class VADState(Enum):
    """Current state of voice activity detection."""

    IDLE = "idle"  # No speech detected
    SPEAKING = "speaking"  # User is speaking
    SILENCE = "silence"  # Silence after speech (may be pause or end)


@dataclass
class VADConfig:
    """Configuration for VAD."""

    sample_rate: int = 16000
    threshold: float = 0.5  # Speech probability threshold
    min_speech_duration_ms: int = 250  # Minimum speech to trigger start
    min_silence_duration_ms: int = 500  # Silence to trigger end
    speech_pad_ms: int = 30  # Padding around speech
    window_size_samples: int = 512  # Silero VAD window size


@dataclass
class VADResult:
    """Result from VAD processing."""

    state: VADState
    speech_probability: float
    is_speech: bool
    speech_start_time: float | None = None
    speech_end_time: float | None = None
    audio_buffer: np.ndarray | None = None


class SileroVAD:
    """Silero VAD wrapper for speech detection.

    Usage:
        vad = SileroVAD()

        # Process audio frames
        for frame in audio_frames:
            result = vad.process(frame)
            if result.state == VADState.SPEAKING:
                # User is speaking
                pass
            elif result.speech_end_time:
                # User finished speaking
                audio = result.audio_buffer
    """

    def __init__(self, config: VADConfig | None = None) -> None:
        """Initialize VAD.

        Args:
            config: VAD configuration
        """
        self.config = config or VADConfig()
        self._model = None
        self._state = VADState.IDLE
        self._speech_buffer: list[np.ndarray] = []
        self._speech_start_time: float | None = None
        self._last_speech_time: float | None = None
        self._silence_start_time: float | None = None

        # Load Silero VAD model
        self._load_model()

    def _load_model(self) -> None:
        """Load Silero VAD model from torch hub."""
        try:
            self._model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True,
            )
            self._model.eval()
            logger.info("vad_model_loaded")
        except Exception as e:
            logger.error("vad_model_load_failed", error=str(e))
            raise

    def reset(self) -> None:
        """Reset VAD state for new conversation."""
        self._state = VADState.IDLE
        self._speech_buffer.clear()
        self._speech_start_time = None
        self._last_speech_time = None
        self._silence_start_time = None
        # Reset model state
        if self._model:
            self._model.reset_states()

    def process(self, audio_frame: np.ndarray) -> VADResult:
        """Process an audio frame and detect speech.

        Args:
            audio_frame: Audio samples (float32, mono, 16kHz)

        Returns:
            VADResult with current state and any completed speech
        """
        current_time = time.time()

        # Ensure correct format
        if audio_frame.dtype != np.float32:
            audio_frame = audio_frame.astype(np.float32)

        if len(audio_frame.shape) > 1:
            audio_frame = audio_frame.mean(axis=1)

        # Normalize to [-1, 1]
        max_val = np.abs(audio_frame).max()
        if max_val > 1.0:
            audio_frame = audio_frame / max_val

        # Get speech probability from Silero
        tensor = torch.from_numpy(audio_frame)
        speech_prob = self._model(tensor, self.config.sample_rate).item()
        is_speech = speech_prob >= self.config.threshold

        result = VADResult(
            state=self._state,
            speech_probability=speech_prob,
            is_speech=is_speech,
        )

        # State machine
        if self._state == VADState.IDLE:
            if is_speech:
                # Start of speech
                self._state = VADState.SPEAKING
                self._speech_start_time = current_time
                self._speech_buffer = [audio_frame.copy()]
                self._last_speech_time = current_time
                result.state = VADState.SPEAKING
                result.speech_start_time = current_time
                logger.debug("vad_speech_start", prob=speech_prob)

        elif self._state == VADState.SPEAKING:
            self._speech_buffer.append(audio_frame.copy())

            if is_speech:
                self._last_speech_time = current_time
                self._silence_start_time = None
            else:
                # Silence during speech
                if self._silence_start_time is None:
                    self._silence_start_time = current_time

                silence_duration_ms = (current_time - self._silence_start_time) * 1000

                if silence_duration_ms >= self.config.min_silence_duration_ms:
                    # Speech ended
                    speech_duration_ms = (self._last_speech_time - self._speech_start_time) * 1000

                    if speech_duration_ms >= self.config.min_speech_duration_ms:
                        # Valid speech segment
                        result.state = VADState.IDLE
                        result.speech_end_time = self._last_speech_time
                        result.audio_buffer = np.concatenate(self._speech_buffer)
                        logger.debug(
                            "vad_speech_end",
                            duration_ms=speech_duration_ms,
                            samples=len(result.audio_buffer),
                        )
                    else:
                        # Too short, ignore
                        logger.debug("vad_speech_too_short", duration_ms=speech_duration_ms)

                    # Reset state
                    self._state = VADState.IDLE
                    self._speech_buffer.clear()
                    self._speech_start_time = None
                    self._last_speech_time = None
                    self._silence_start_time = None

            result.state = self._state

        return result

    def process_chunk(self, audio_chunk: np.ndarray) -> list[VADResult]:
        """Process a larger audio chunk by splitting into frames.

        Args:
            audio_chunk: Audio samples

        Returns:
            List of VADResults for each frame
        """
        results = []
        frame_size = self.config.window_size_samples

        for i in range(0, len(audio_chunk), frame_size):
            frame = audio_chunk[i : i + frame_size]
            if len(frame) == frame_size:
                result = self.process(frame)
                results.append(result)

        return results

    @property
    def state(self) -> VADState:
        """Current VAD state."""
        return self._state

    @property
    def is_speaking(self) -> bool:
        """Whether speech is currently detected."""
        return self._state == VADState.SPEAKING

    @property
    def speech_duration(self) -> float:
        """Duration of current speech in seconds."""
        if self._speech_start_time is None:
            return 0.0
        return time.time() - self._speech_start_time


class VADProcessor:
    """Higher-level VAD processor with callbacks.

    Usage:
        def on_speech_start():
            print("User started speaking")

        def on_speech_end(audio):
            # Process the audio
            transcript = transcribe(audio)

        processor = VADProcessor(
            on_speech_start=on_speech_start,
            on_speech_end=on_speech_end,
        )

        # Feed audio frames
        for frame in audio_stream:
            processor.feed(frame)
    """

    def __init__(
        self,
        config: VADConfig | None = None,
        on_speech_start: Callable[[], None] | None = None,
        on_speech_end: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        """Initialize processor.

        Args:
            config: VAD configuration
            on_speech_start: Callback when speech starts
            on_speech_end: Callback when speech ends (receives audio buffer)
        """
        self.vad = SileroVAD(config)
        self.on_speech_start = on_speech_start
        self.on_speech_end = on_speech_end
        self._was_speaking = False

    def feed(self, audio_frame: np.ndarray) -> VADResult:
        """Feed an audio frame to the processor.

        Args:
            audio_frame: Audio samples

        Returns:
            VADResult
        """
        result = self.vad.process(audio_frame)

        # Handle state transitions
        if result.is_speech and not self._was_speaking:
            self._was_speaking = True
            if self.on_speech_start:
                self.on_speech_start()

        if result.speech_end_time and result.audio_buffer is not None:
            self._was_speaking = False
            if self.on_speech_end:
                self.on_speech_end(result.audio_buffer)

        return result

    def reset(self) -> None:
        """Reset processor state."""
        self.vad.reset()
        self._was_speaking = False
