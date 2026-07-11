"""Fluency evaluator analyzing speech rate, pauses, and hesitations.

Analyzes audio features to assess fluency without pronunciation accuracy.
Focuses on temporal aspects: speech rate, pause patterns, rhythm.
"""

import re
from dataclasses import dataclass

import numpy as np

from backend.coldpath.evaluators.base import (
    BaseEvaluator,
    EvaluatorInput,
    EvaluatorResult,
    SkillType,
)
from backend.core.logging import get_logger
from backend.core.resource_guard import ResourceGuard

logger = get_logger(__name__)


# CEFR-appropriate speech rates (words per minute)
# Lower levels speak slower, higher levels speak faster
SPEECH_RATE_TARGETS = {
    0: (60, 100),   # A0/beginner: 60-100 WPM
    1: (80, 120),   # A1: 80-120 WPM
    2: (100, 140),  # A2: 100-140 WPM
    3: (120, 160),  # B1: 120-160 WPM
    4: (130, 180),  # B2: 130-180 WPM
    5: (140, 200),  # C1: 140-200 WPM
    6: (150, 220),  # C2: 150-220 WPM (native-like)
}

# Common filler words and hesitation markers
FILLER_WORDS = {
    "um", "uh", "er", "ah", "like", "you know",
    "i mean", "sort of", "kind of", "basically",
    "actually", "well", "so", "right", "okay",
}


@dataclass
class FluencyMetrics:
    """Fluency analysis metrics."""

    speech_rate_wpm: float  # Words per minute
    pause_ratio: float  # Ratio of silence to speech
    filler_ratio: float  # Ratio of filler words
    mean_word_length: float  # Average word length (characters)
    sentence_count: int  # Number of sentences
    word_count: int  # Total words
    audio_duration_seconds: float  # Total audio duration
    speech_duration_seconds: float  # Duration of actual speech


class FluencyEvaluator(BaseEvaluator):
    """Fluency evaluator based on speech temporal features.

    Analyzes:
    - Speech rate (words per minute)
    - Pause patterns (silence ratio)
    - Hesitation markers (filler words)
    - Overall rhythm and flow
    """

    def __init__(
        self,
        guard: ResourceGuard,
        sample_rate: int = 16000,
        silence_threshold: float = 0.02,
        min_silence_duration: float = 0.2,
    ) -> None:
        """Initialize fluency evaluator.

        Args:
            guard: Resource guard for admission control
            sample_rate: Audio sample rate
            silence_threshold: RMS threshold for silence detection
            min_silence_duration: Minimum silence duration (seconds)
        """
        super().__init__(guard)
        self.sample_rate = sample_rate
        self.silence_threshold = silence_threshold
        self.min_silence_duration = min_silence_duration

    @property
    def skill_type(self) -> SkillType:
        """Fluency skill type."""
        return SkillType.FLUENCY

    async def score(self, input_data: EvaluatorInput) -> EvaluatorResult:
        """Score fluency of the utterance.

        Args:
            input_data: Input data with transcript and audio

        Returns:
            EvaluatorResult with fluency score
        """
        transcript = input_data.transcript.strip()

        if not transcript:
            return EvaluatorResult(
                skill=SkillType.FLUENCY,
                score=0.0,
                confidence=0.0,
                details={"reason": "empty_transcript"},
            )

        # Check resources
        if not await self._check_resources():
            logger.warning("fluency_evaluation_deferred", utterance_id=input_data.utterance_id)
            return self._create_error_result("Resources unavailable, evaluation deferred")

        try:
            # Compute fluency metrics
            metrics = self._compute_metrics(
                transcript=transcript,
                audio=input_data.audio_array,
                sample_rate=input_data.sample_rate or self.sample_rate,
            )

            # Get learner level from context (default to intermediate)
            level = input_data.context.get("learner_level", 3)

            # Calculate component scores
            rate_score = self._score_speech_rate(metrics.speech_rate_wpm, level)
            pause_score = self._score_pause_ratio(metrics.pause_ratio)
            filler_score = self._score_filler_ratio(metrics.filler_ratio)

            # Weighted combination
            score = 0.4 * rate_score + 0.35 * pause_score + 0.25 * filler_score

            # Confidence based on data quality
            confidence = 0.9 if metrics.audio_duration_seconds > 0 else 0.6

            return EvaluatorResult(
                skill=SkillType.FLUENCY,
                score=score,
                confidence=confidence,
                details={
                    "speech_rate_wpm": round(metrics.speech_rate_wpm, 1),
                    "pause_ratio": round(metrics.pause_ratio, 3),
                    "filler_ratio": round(metrics.filler_ratio, 3),
                    "word_count": metrics.word_count,
                    "sentence_count": metrics.sentence_count,
                    "audio_duration_s": round(metrics.audio_duration_seconds, 2),
                    "rate_score": round(rate_score, 3),
                    "pause_score": round(pause_score, 3),
                    "filler_score": round(filler_score, 3),
                    "target_level": level,
                },
            )

        except Exception as e:
            logger.exception("fluency_evaluation_failed", error=str(e))
            return self._create_error_result(str(e))

    def _compute_metrics(
        self,
        transcript: str,
        audio: np.ndarray | None,
        sample_rate: int,
    ) -> FluencyMetrics:
        """Compute fluency metrics from transcript and audio.

        Args:
            transcript: Utterance transcript
            audio: Optional audio array
            sample_rate: Audio sample rate

        Returns:
            FluencyMetrics with computed values
        """
        # Text analysis
        words = transcript.split()
        word_count = len(words)
        sentence_count = max(1, len(re.split(r"[.!?]+", transcript.strip())))
        mean_word_length = sum(len(w) for w in words) / max(1, word_count)

        # Count filler words
        transcript_lower = transcript.lower()
        filler_count = sum(
            transcript_lower.count(filler) for filler in FILLER_WORDS
        )
        filler_ratio = filler_count / max(1, word_count)

        # Audio analysis
        audio_duration = 0.0
        speech_duration = 0.0
        pause_ratio = 0.0

        if audio is not None and len(audio) > 0:
            audio_duration = len(audio) / sample_rate

            # Detect speech vs silence
            frame_size = int(0.025 * sample_rate)  # 25ms frames
            hop_size = int(0.010 * sample_rate)  # 10ms hop

            speech_frames = 0
            total_frames = 0

            for i in range(0, len(audio) - frame_size, hop_size):
                frame = audio[i : i + frame_size]
                rms = np.sqrt(np.mean(frame**2))

                total_frames += 1
                if rms > self.silence_threshold:
                    speech_frames += 1

            if total_frames > 0:
                speech_ratio = speech_frames / total_frames
                pause_ratio = 1.0 - speech_ratio
                speech_duration = audio_duration * speech_ratio
        else:
            # Estimate from text (average speaking rate ~150 WPM)
            audio_duration = word_count / 150 * 60  # seconds
            speech_duration = audio_duration * 0.8  # assume 80% speech
            pause_ratio = 0.2

        # Calculate speech rate
        if audio_duration > 0:
            speech_rate_wpm = (word_count / audio_duration) * 60
        else:
            speech_rate_wpm = 150  # default

        return FluencyMetrics(
            speech_rate_wpm=speech_rate_wpm,
            pause_ratio=pause_ratio,
            filler_ratio=filler_ratio,
            mean_word_length=mean_word_length,
            sentence_count=sentence_count,
            word_count=word_count,
            audio_duration_seconds=audio_duration,
            speech_duration_seconds=speech_duration,
        )

    def _score_speech_rate(self, wpm: float, level: int) -> float:
        """Score speech rate based on learner level.

        Args:
            wpm: Words per minute
            level: Learner CEFR level (0-6)

        Returns:
            Score from 0.0 to 1.0
        """
        level = max(0, min(6, level))
        target_min, target_max = SPEECH_RATE_TARGETS.get(level, (120, 160))
        target_mid = (target_min + target_max) / 2

        if target_min <= wpm <= target_max:
            # Within target range - perfect or near perfect
            deviation = abs(wpm - target_mid) / (target_max - target_min)
            return 1.0 - (deviation * 0.2)  # Max 20% penalty
        elif wpm < target_min:
            # Too slow
            deficit = (target_min - wpm) / target_min
            return max(0.3, 1.0 - deficit * 1.5)
        else:
            # Too fast
            excess = (wpm - target_max) / target_max
            return max(0.4, 1.0 - excess * 1.2)

    def _score_pause_ratio(self, pause_ratio: float) -> float:
        """Score pause ratio.

        Natural speech has ~20-30% pauses. Too many or too few is unnatural.

        Args:
            pause_ratio: Ratio of pause time to total time

        Returns:
            Score from 0.0 to 1.0
        """
        # Optimal pause ratio around 0.2-0.3
        if 0.15 <= pause_ratio <= 0.35:
            # Good range
            return 1.0 - abs(pause_ratio - 0.25) * 2
        elif pause_ratio < 0.15:
            # Too little pause (rushed)
            return max(0.5, 1.0 - (0.15 - pause_ratio) * 4)
        else:
            # Too much pause (hesitant)
            return max(0.2, 1.0 - (pause_ratio - 0.35) * 2)

    def _score_filler_ratio(self, filler_ratio: float) -> float:
        """Score filler word ratio.

        Fewer fillers is better, but some are natural.

        Args:
            filler_ratio: Ratio of filler words to total words

        Returns:
            Score from 0.0 to 1.0
        """
        if filler_ratio <= 0.02:
            return 1.0  # Very few fillers
        elif filler_ratio <= 0.05:
            return 0.9  # Normal amount
        elif filler_ratio <= 0.10:
            return 0.7  # Somewhat high
        elif filler_ratio <= 0.15:
            return 0.5  # Too many
        else:
            return max(0.2, 0.3 - filler_ratio)  # Excessive
