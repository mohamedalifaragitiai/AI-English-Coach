"""Base evaluator framework for cold path processing.

All evaluators inherit from BaseEvaluator and implement the score() method.
Evaluators are registered in the registry and run by the orchestrator.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np

from backend.core.logging import get_logger
from backend.core.resource_guard import ResourceGuard

logger = get_logger(__name__)


class SkillType(str, Enum):
    """Types of skills that can be evaluated."""

    PRONUNCIATION = "pronunciation"
    GRAMMAR = "grammar"
    VOCABULARY = "vocabulary"
    FLUENCY = "fluency"
    COHERENCE = "coherence"
    RELEVANCE = "relevance"


@dataclass
class EvaluatorInput:
    """Input data for evaluators."""

    utterance_id: str
    session_id: str
    user_id: str
    transcript: str
    audio_path: str | None = None
    audio_array: np.ndarray | None = None
    sample_rate: int = 16000
    stt_confidence: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluatorResult:
    """Result from an evaluator.

    Scores are 0.0-1.0 normalized.
    """

    skill: SkillType
    score: float  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "skill": self.skill.value,
            "score": self.score,
            "confidence": self.confidence,
            "details": self.details,
            "errors": self.errors,
            "timestamp": self.timestamp.isoformat(),
        }


class BaseEvaluator(ABC):
    """Abstract base class for all evaluators.

    Evaluators analyze utterances and produce scores for specific skills.
    They must be async and work within the ResourceGuard constraints.
    """

    def __init__(self, guard: ResourceGuard) -> None:
        """Initialize evaluator.

        Args:
            guard: Resource guard for admission control
        """
        self.guard = guard
        self._name = self.__class__.__name__
        logger.info("evaluator_initialized", evaluator=self._name)

    @property
    @abstractmethod
    def skill_type(self) -> SkillType:
        """The skill type this evaluator scores."""
        ...

    @property
    def name(self) -> str:
        """Evaluator name."""
        return self._name

    @abstractmethod
    async def score(self, input_data: EvaluatorInput) -> EvaluatorResult:
        """Score an utterance for this skill.

        Args:
            input_data: Input data including transcript and optional audio

        Returns:
            EvaluatorResult with score and details
        """
        ...

    async def _check_resources(self) -> bool:
        """Check if resources are available for evaluation.

        Cold path operations are deferrable, so check admission.
        """
        admission = await self.guard.acquire(
            path="cold",
            category="evaluation",
            description=f"{self._name} evaluation",
        )
        return admission.allowed

    def _create_error_result(self, error: str) -> EvaluatorResult:
        """Create an error result when evaluation fails."""
        return EvaluatorResult(
            skill=self.skill_type,
            score=0.0,
            confidence=0.0,
            details={"error": error},
            errors=[{"type": "evaluation_error", "message": error}],
        )


class EvaluatorRegistry:
    """Registry for managing evaluators.

    Evaluators register themselves and the orchestrator uses
    the registry to run all registered evaluators.
    """

    def __init__(self) -> None:
        """Initialize registry."""
        self._evaluators: dict[SkillType, BaseEvaluator] = {}

    def register(self, evaluator: BaseEvaluator) -> None:
        """Register an evaluator.

        Args:
            evaluator: The evaluator to register
        """
        skill = evaluator.skill_type
        if skill in self._evaluators:
            logger.warning(
                "evaluator_replaced",
                skill=skill.value,
                old=self._evaluators[skill].name,
                new=evaluator.name,
            )
        self._evaluators[skill] = evaluator
        logger.info("evaluator_registered", skill=skill.value, evaluator=evaluator.name)

    def unregister(self, skill: SkillType) -> None:
        """Unregister an evaluator.

        Args:
            skill: The skill type to unregister
        """
        if skill in self._evaluators:
            del self._evaluators[skill]
            logger.info("evaluator_unregistered", skill=skill.value)

    def get(self, skill: SkillType) -> BaseEvaluator | None:
        """Get an evaluator by skill type.

        Args:
            skill: The skill type to get

        Returns:
            The evaluator or None if not registered
        """
        return self._evaluators.get(skill)

    def get_all(self) -> list[BaseEvaluator]:
        """Get all registered evaluators.

        Returns:
            List of all registered evaluators
        """
        return list(self._evaluators.values())

    @property
    def skills(self) -> list[SkillType]:
        """List of registered skill types."""
        return list(self._evaluators.keys())

    def __len__(self) -> int:
        """Number of registered evaluators."""
        return len(self._evaluators)


# Global registry instance
_registry = EvaluatorRegistry()


def get_registry() -> EvaluatorRegistry:
    """Get the global evaluator registry."""
    return _registry
