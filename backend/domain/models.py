"""Domain models for the English Coach.

These are the core aggregates following DDD principles:
- LearnerProfile: The per-user root aggregate
- Session: One practice sitting
- Utterance: Atomic unit of speech
- Assessment: Versioned scores for an utterance/session
- EvaluatorOutput: Raw evaluator results (separate from aggregated scores)
- GapSnapshot: Point-in-time skill gaps
- Plan: Adaptive learning plan
- Report: Generated progress report
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SessionMode(str, Enum):
    """Practice session modes."""

    FREE = "free"
    INTERVIEW = "interview"
    IELTS = "ielts"
    BUSINESS = "business"
    PRONUNCIATION = "pronunciation"
    VOCABULARY = "vocabulary"


class UtteranceRole(str, Enum):
    """Who spoke the utterance."""

    LEARNER = "learner"
    COACH = "coach"


class ReportFormat(str, Enum):
    """Report output formats."""

    JSON = "json"
    PDF = "pdf"
    CSV = "csv"
    EXCEL = "excel"


@dataclass
class User:
    """Learner profile - the durable per-user root.

    Each learner (e.g., "abu_ali") owns a persistent profile that tracks
    skill scores, gaps, and progress across time.
    """

    user_id: str  # Stable slug like 'abu_ali'
    display_name: str
    created_at: datetime
    current_level: int = 0  # 0-5 custom scale
    streak_days: int = 0
    settings: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.current_level <= 5:
            raise ValueError(f"Level must be 0-5, got {self.current_level}")


@dataclass
class Session:
    """One practice sitting for a user."""

    session_id: str
    user_id: str
    mode: SessionMode
    started_at: datetime
    ended_at: datetime | None = None
    difficulty: float = 0.5  # Adaptive difficulty 0-1

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    @property
    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()


@dataclass
class Utterance:
    """Atomic unit: audio ref, transcript, confidence, timestamps.

    Everything (assessments, pronunciation) attaches here.
    """

    utterance_id: str
    session_id: str
    user_id: str
    role: UtteranceRole
    created_at: datetime
    audio_path: str | None = None  # Path to stored wav (learner turns)
    transcript: str | None = None
    stt_confidence: float | None = None
    start_ms: int | None = None
    end_ms: int | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.start_ms is None or self.end_ms is None:
            return None
        return self.end_ms - self.start_ms


@dataclass
class Assessment:
    """Per-dimension scores for an utterance/session.

    Each assessment records the scoring_model_version that produced it,
    so trends survive retuning. Never overwrite; append only.
    """

    assessment_id: str
    user_id: str
    scoring_model_version: str
    created_at: datetime
    session_id: str | None = None
    utterance_id: str | None = None

    # Dimension scores (0-100 each)
    pronunciation: float | None = None
    grammar: float | None = None
    vocabulary: float | None = None
    listening: float | None = None
    fluency: float | None = None
    confidence: float | None = None
    coherence: float | None = None
    relevance: float | None = None
    overall: float | None = None

    def compute_overall(self, weights: dict[str, float] | None = None) -> float:
        """Compute weighted overall score.

        Default weights from scoring.md:
        pronunciation=0.20, grammar=0.15, vocabulary=0.15, listening=0.15,
        fluency=0.15, confidence=0.10, coherence=0.05, relevance=0.05
        """
        if weights is None:
            weights = {
                "pronunciation": 0.20,
                "grammar": 0.15,
                "vocabulary": 0.15,
                "listening": 0.15,
                "fluency": 0.15,
                "confidence": 0.10,
                "coherence": 0.05,
                "relevance": 0.05,
            }

        total = 0.0
        weight_sum = 0.0

        for dim, weight in weights.items():
            value = getattr(self, dim, None)
            if value is not None:
                total += value * weight
                weight_sum += weight

        if weight_sum == 0:
            return 0.0

        return total / weight_sum * sum(weights.values())

    @staticmethod
    def overall_to_level(overall: float) -> int:
        """Map overall score (0-100) to level (0-5).

        Thresholds from scoring.md:
        0-39→0, 40-54→1, 55-69→2, 70-82→3, 83-93→4, 94-100→5
        """
        if overall < 40:
            return 0
        elif overall < 55:
            return 1
        elif overall < 70:
            return 2
        elif overall < 83:
            return 3
        elif overall < 94:
            return 4
        else:
            return 5


@dataclass
class EvaluatorOutput:
    """Raw evaluator output, kept separate for recompute & audit.

    This allows re-running scoring without re-running inference.
    """

    id: str
    utterance_id: str
    evaluator: str  # grammar, vocab, fluency, pronunciation, etc.
    version: str
    payload: dict[str, Any]  # Full typed output from evaluator
    created_at: datetime


@dataclass
class GapSnapshot:
    """Point-in-time gap vector.

    Enables "which gap improved most" queries by comparing snapshots.
    """

    id: str
    user_id: str
    taken_at: datetime
    gaps: dict[str, float]  # {skill: severity} ranked by severity


@dataclass
class Plan:
    """Adaptive learning plan for a user."""

    plan_id: str
    user_id: str
    created_at: datetime
    horizon: str | None = None  # e.g., "1 week", "1 month"
    plan: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    """Generated progress report."""

    report_id: str
    user_id: str
    period: str  # e.g., "2024-01", "2024-W01"
    created_at: datetime
    format: ReportFormat = ReportFormat.JSON
    path: str | None = None  # Path to generated file


@dataclass
class Achievement:
    """Unlocked achievement/badge."""

    id: str
    user_id: str
    code: str  # e.g., "streak_7", "level_up_3"
    earned_at: datetime


# Skill dimensions as a constant for iteration
SKILL_DIMENSIONS = [
    "pronunciation",
    "grammar",
    "vocabulary",
    "listening",
    "fluency",
    "confidence",
    "coherence",
    "relevance",
]

# Default scoring weights
DEFAULT_WEIGHTS = {
    "pronunciation": 0.20,
    "grammar": 0.15,
    "vocabulary": 0.15,
    "listening": 0.15,
    "fluency": 0.15,
    "confidence": 0.10,
    "coherence": 0.05,
    "relevance": 0.05,
}

# Level thresholds (minimum overall score for each level)
LEVEL_THRESHOLDS = {
    0: 0,
    1: 40,
    2: 55,
    3: 70,
    4: 83,
    5: 94,
}
