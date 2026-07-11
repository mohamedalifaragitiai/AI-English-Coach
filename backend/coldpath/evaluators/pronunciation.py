"""Pronunciation evaluator using STT confidence and audio analysis.

Uses multiple signals to assess pronunciation quality:
- STT confidence as a proxy for clarity
- Audio SNR (signal-to-noise ratio)
- Word-level analysis when available

Note: For full GOP (Goodness of Pronunciation) scoring, a dedicated
phoneme alignment model would be needed. This evaluator provides a
simpler but still useful approximation.
"""

import re
from dataclasses import dataclass, field

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


# Common mispronunciation patterns for ESL learners
# Maps common errors to correct forms
COMMON_ERRORS = {
    # TH sound difficulties
    "think": ["sink", "tink"],
    "this": ["dis", "zis"],
    "the": ["da", "za"],
    "three": ["tree", "sree"],
    # R/L confusion (common for Asian language speakers)
    "really": ["leally", "rearry"],
    "right": ["light", "rite"],
    "long": ["rong"],
    # V/W confusion
    "very": ["wery", "bery"],
    "want": ["vant"],
    # Final consonant clusters
    "asked": ["ask"],
    "texts": ["text"],
    "months": ["month"],
}


@dataclass
class PronunciationMetrics:
    """Pronunciation analysis metrics."""

    stt_confidence: float
    audio_snr_db: float
    clarity_score: float
    word_count: int
    problematic_words: list[str] = field(default_factory=list)


class PronunciationEvaluator(BaseEvaluator):
    """Pronunciation evaluator based on STT confidence and audio quality.

    This evaluator uses indirect signals to assess pronunciation:
    1. STT confidence - clear pronunciation leads to higher confidence
    2. Audio SNR - good recording quality enables better assessment
    3. Known difficulty patterns - common ESL pronunciation challenges

    For production use with full GOP scoring, consider integrating
    a dedicated phoneme alignment model like Kaldi or Montreal Forced Aligner.
    """

    def __init__(
        self,
        guard: ResourceGuard,
        sample_rate: int = 16000,
        stt_weight: float = 0.5,
        snr_weight: float = 0.2,
        clarity_weight: float = 0.3,
    ) -> None:
        """Initialize pronunciation evaluator.

        Args:
            guard: Resource guard for admission control
            sample_rate: Audio sample rate
            stt_weight: Weight for STT confidence component
            snr_weight: Weight for SNR component
            clarity_weight: Weight for clarity analysis component
        """
        super().__init__(guard)
        self.sample_rate = sample_rate
        self.stt_weight = stt_weight
        self.snr_weight = snr_weight
        self.clarity_weight = clarity_weight

    @property
    def skill_type(self) -> SkillType:
        """Pronunciation skill type."""
        return SkillType.PRONUNCIATION

    async def score(self, input_data: EvaluatorInput) -> EvaluatorResult:
        """Score pronunciation quality.

        Args:
            input_data: Input with transcript, audio, and STT confidence

        Returns:
            EvaluatorResult with pronunciation score
        """
        transcript = input_data.transcript.strip()

        if not transcript:
            return EvaluatorResult(
                skill=SkillType.PRONUNCIATION,
                score=0.0,
                confidence=0.0,
                details={"reason": "empty_transcript"},
            )

        # Check resources
        if not await self._check_resources():
            logger.warning(
                "pronunciation_evaluation_deferred",
                utterance_id=input_data.utterance_id,
            )
            return self._create_error_result("Resources unavailable, evaluation deferred")

        try:
            # Compute pronunciation metrics
            metrics = self._compute_metrics(
                transcript=transcript,
                audio=input_data.audio_array,
                sample_rate=input_data.sample_rate or self.sample_rate,
                stt_confidence=input_data.stt_confidence,
            )

            # Calculate component scores
            stt_score = self._score_stt_confidence(metrics.stt_confidence)
            snr_score = self._score_snr(metrics.audio_snr_db)
            clarity_score = metrics.clarity_score

            # Weighted combination
            score = (
                self.stt_weight * stt_score
                + self.snr_weight * snr_score
                + self.clarity_weight * clarity_score
            )

            # Confidence based on available data
            confidence = 0.7
            if input_data.audio_array is not None:
                confidence = 0.85
            if input_data.stt_confidence > 0:
                confidence = 0.9

            # Build errors list for problematic words
            errors = []
            for word in metrics.problematic_words:
                errors.append({
                    "type": "pronunciation_difficulty",
                    "word": word,
                    "suggestion": f"Practice the pronunciation of '{word}'",
                })

            return EvaluatorResult(
                skill=SkillType.PRONUNCIATION,
                score=score,
                confidence=confidence,
                details={
                    "stt_confidence": round(metrics.stt_confidence, 3),
                    "audio_snr_db": round(metrics.audio_snr_db, 1),
                    "clarity_score": round(clarity_score, 3),
                    "stt_score": round(stt_score, 3),
                    "snr_score": round(snr_score, 3),
                    "word_count": metrics.word_count,
                    "problematic_word_count": len(metrics.problematic_words),
                },
                errors=errors,
            )

        except Exception as e:
            logger.exception("pronunciation_evaluation_failed", error=str(e))
            return self._create_error_result(str(e))

    def _compute_metrics(
        self,
        transcript: str,
        audio: np.ndarray | None,
        sample_rate: int,
        stt_confidence: float,
    ) -> PronunciationMetrics:
        """Compute pronunciation metrics.

        Args:
            transcript: Utterance transcript
            audio: Optional audio array
            sample_rate: Audio sample rate
            stt_confidence: STT confidence score

        Returns:
            PronunciationMetrics
        """
        words = transcript.lower().split()
        word_count = len(words)

        # Calculate audio SNR if available
        snr_db = 0.0
        if audio is not None and len(audio) > 0:
            snr_db = self._estimate_snr(audio)

        # Identify potentially problematic words
        problematic = self._find_problematic_words(words)

        # Calculate clarity score based on word patterns
        clarity = self._calculate_clarity(transcript, problematic)

        return PronunciationMetrics(
            stt_confidence=stt_confidence,
            audio_snr_db=snr_db,
            clarity_score=clarity,
            word_count=word_count,
            problematic_words=problematic,
        )

    def _estimate_snr(self, audio: np.ndarray) -> float:
        """Estimate Signal-to-Noise Ratio.

        Uses a simple method: compares energy of high-energy frames
        (signal) to low-energy frames (noise).

        Args:
            audio: Audio samples

        Returns:
            Estimated SNR in dB
        """
        # Frame the audio
        frame_size = int(0.025 * self.sample_rate)  # 25ms
        hop_size = int(0.010 * self.sample_rate)  # 10ms

        energies = []
        for i in range(0, len(audio) - frame_size, hop_size):
            frame = audio[i : i + frame_size]
            energy = np.sum(frame**2)
            if energy > 0:
                energies.append(energy)

        if not energies:
            return 0.0

        energies = np.array(energies)

        # Top 10% as signal, bottom 10% as noise
        sorted_energies = np.sort(energies)
        n = len(sorted_energies)
        if n < 10:
            return 0.0

        signal_energy = np.mean(sorted_energies[int(n * 0.9) :])
        noise_energy = np.mean(sorted_energies[: int(n * 0.1)])

        if noise_energy <= 0:
            return 40.0  # Very clean

        snr = 10 * np.log10(signal_energy / noise_energy)
        return float(np.clip(snr, 0, 60))

    def _find_problematic_words(self, words: list[str]) -> list[str]:
        """Find words that commonly cause pronunciation difficulties.

        Args:
            words: List of words in transcript

        Returns:
            List of potentially problematic words
        """
        problematic = []

        for word in words:
            # Clean word
            clean = re.sub(r"[^\w]", "", word.lower())
            if not clean:
                continue

            # Check against known difficult words
            if clean in COMMON_ERRORS:
                problematic.append(clean)
                continue

            # Check for difficult phoneme patterns
            # TH sounds
            if re.search(r"th", clean):
                problematic.append(clean)
            # Final consonant clusters
            elif re.search(r"[bcdfghjklmnpqrstvwxz]{3}$", clean):
                problematic.append(clean)
            # Words ending in -tion, -sion
            elif re.search(r"(tion|sion)$", clean):
                problematic.append(clean)

        return list(set(problematic))  # Deduplicate

    def _calculate_clarity(self, transcript: str, problematic: list[str]) -> float:
        """Calculate clarity score based on transcript analysis.

        Args:
            transcript: Full transcript
            problematic: List of problematic words

        Returns:
            Clarity score 0.0 to 1.0
        """
        words = transcript.split()
        if not words:
            return 0.5

        # Base clarity from text complexity
        avg_word_length = sum(len(w) for w in words) / len(words)

        # Simpler words are usually clearer
        if avg_word_length <= 4:
            base_clarity = 0.9
        elif avg_word_length <= 6:
            base_clarity = 0.8
        else:
            base_clarity = 0.7

        # Penalize for problematic words
        problematic_ratio = len(problematic) / len(words)
        penalty = problematic_ratio * 0.3

        return max(0.3, base_clarity - penalty)

    def _score_stt_confidence(self, confidence: float) -> float:
        """Convert STT confidence to pronunciation score.

        High STT confidence suggests clear pronunciation.

        Args:
            confidence: STT confidence (0.0 to 1.0)

        Returns:
            Score from 0.0 to 1.0
        """
        if confidence <= 0:
            return 0.5  # No confidence available

        # Map confidence to score with slight boost
        # Good pronunciation typically yields >0.8 confidence
        if confidence >= 0.95:
            return 1.0
        elif confidence >= 0.85:
            return 0.95
        elif confidence >= 0.75:
            return 0.85
        elif confidence >= 0.60:
            return 0.70
        else:
            return max(0.3, confidence)

    def _score_snr(self, snr_db: float) -> float:
        """Convert SNR to quality score.

        Higher SNR indicates cleaner audio, enabling better pronunciation
        assessment.

        Args:
            snr_db: Signal-to-noise ratio in dB

        Returns:
            Score from 0.0 to 1.0
        """
        # SNR > 30 dB is excellent
        # SNR 20-30 dB is good
        # SNR 10-20 dB is acceptable
        # SNR < 10 dB is poor
        if snr_db >= 30:
            return 1.0
        elif snr_db >= 20:
            return 0.9
        elif snr_db >= 15:
            return 0.8
        elif snr_db >= 10:
            return 0.6
        elif snr_db >= 5:
            return 0.4
        else:
            return 0.2
