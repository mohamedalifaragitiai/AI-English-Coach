"""Gap analyzer for identifying skill weaknesses.

Analyzes assessment history to identify areas where the learner
needs improvement, prioritized by severity and frequency.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.coldpath.evaluators.base import SkillType
from backend.core.logging import get_logger
from backend.persistence.repositories import AssessmentRepository, GapSnapshotRepository

logger = get_logger(__name__)


# CEFR level thresholds
LEVEL_THRESHOLDS = [0.0, 0.20, 0.40, 0.55, 0.70, 0.85, 0.95]  # A0, A1, A2, B1, B2, C1, C2

# Skill weights for gap priority calculation
SKILL_WEIGHTS = {
    SkillType.PRONUNCIATION: 0.25,
    SkillType.GRAMMAR: 0.25,
    SkillType.FLUENCY: 0.20,
    SkillType.VOCABULARY: 0.15,
    SkillType.COHERENCE: 0.10,
    SkillType.RELEVANCE: 0.05,
}


@dataclass
class SkillGap:
    """A skill gap identified for a learner."""

    skill: SkillType
    current_score: float  # Current average score (0-1)
    target_score: float  # Target score for next level
    gap_size: float  # target - current
    trend: float  # Recent trend (-1 to 1, negative = declining)
    priority: float  # Priority score for fixing (higher = more urgent)
    assessment_count: int  # Number of assessments used
    recent_scores: list[float] = field(default_factory=list)

    @property
    def level(self) -> int:
        """Current CEFR level (0-6)."""
        for i, threshold in enumerate(LEVEL_THRESHOLDS):
            if self.current_score < threshold:
                return max(0, i - 1)
        return 6

    @property
    def level_name(self) -> str:
        """CEFR level name."""
        names = ["A0", "A1", "A2", "B1", "B2", "C1", "C2"]
        return names[self.level]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "skill": self.skill.value,
            "current_score": round(self.current_score, 3),
            "target_score": round(self.target_score, 3),
            "gap_size": round(self.gap_size, 3),
            "trend": round(self.trend, 3),
            "priority": round(self.priority, 3),
            "level": self.level,
            "level_name": self.level_name,
            "assessment_count": self.assessment_count,
        }


@dataclass
class GapSnapshot:
    """Complete gap analysis snapshot."""

    user_id: str
    timestamp: datetime
    overall_score: float
    overall_level: int
    gaps: list[SkillGap]
    priority_skills: list[SkillType]  # Skills to focus on, in order

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        level_names = ["A0", "A1", "A2", "B1", "B2", "C1", "C2"]
        return {
            "user_id": self.user_id,
            "timestamp": self.timestamp.isoformat(),
            "overall_score": round(self.overall_score, 3),
            "overall_level": self.overall_level,
            "overall_level_name": level_names[self.overall_level],
            "gaps": [g.to_dict() for g in self.gaps],
            "priority_skills": [s.value for s in self.priority_skills],
        }


class GapAnalyzer:
    """Analyzes learner assessments to identify skill gaps.

    Computes gap scores for each skill, identifies trends, and
    prioritizes areas for improvement.
    """

    def __init__(
        self,
        assessment_repo: AssessmentRepository,
        gap_snapshot_repo: GapSnapshotRepository,
        window_days: int = 30,
        min_assessments: int = 3,
    ) -> None:
        """Initialize analyzer.

        Args:
            assessment_repo: Repository for assessments
            gap_snapshot_repo: Repository for gap snapshots
            window_days: Days of history to analyze
            min_assessments: Minimum assessments for trend calculation
        """
        self.assessment_repo = assessment_repo
        self.gap_snapshot_repo = gap_snapshot_repo
        self.window_days = window_days
        self.min_assessments = min_assessments

    async def analyze(self, user_id: str, target_level: int = -1) -> GapSnapshot:
        """Analyze gaps for a user.

        Args:
            user_id: User ID to analyze
            target_level: Target CEFR level (0-6), or -1 for next level

        Returns:
            GapSnapshot with analysis results
        """
        # Get recent assessments
        skill_scores: dict[SkillType, list[tuple[datetime, float]]] = {}

        for skill in SkillType:
            trend_data = await self.assessment_repo.get_skill_trend(
                user_id=user_id,
                skill=skill.value,
                days=self.window_days,
            )
            if trend_data:
                skill_scores[skill] = trend_data

        # Compute gaps for each skill
        gaps: list[SkillGap] = []
        total_weighted_score = 0.0
        total_weight = 0.0

        for skill in SkillType:
            scores = skill_scores.get(skill, [])

            if not scores:
                # No data for this skill
                gap = SkillGap(
                    skill=skill,
                    current_score=0.0,
                    target_score=LEVEL_THRESHOLDS[1],  # Target A1
                    gap_size=LEVEL_THRESHOLDS[1],
                    trend=0.0,
                    priority=1.0,  # High priority if no data
                    assessment_count=0,
                )
            else:
                # Compute current score (weighted recent average)
                current_score = self._compute_weighted_average(scores)

                # Determine target
                current_level = self._score_to_level(current_score)
                if target_level >= 0:
                    target = target_level
                else:
                    target = min(6, current_level + 1)

                target_score = LEVEL_THRESHOLDS[target] if target < len(LEVEL_THRESHOLDS) else 1.0

                # Compute trend
                trend = self._compute_trend(scores)

                # Compute priority
                gap_size = max(0, target_score - current_score)
                weight = SKILL_WEIGHTS.get(skill, 0.1)
                priority = self._compute_priority(gap_size, trend, weight, len(scores))

                gap = SkillGap(
                    skill=skill,
                    current_score=current_score,
                    target_score=target_score,
                    gap_size=gap_size,
                    trend=trend,
                    priority=priority,
                    assessment_count=len(scores),
                    recent_scores=[s for _, s in scores[-5:]],  # Last 5
                )

            gaps.append(gap)

            # Accumulate for overall
            if gap.assessment_count > 0:
                weight = SKILL_WEIGHTS.get(skill, 0.1)
                total_weighted_score += gap.current_score * weight
                total_weight += weight

        # Compute overall
        overall_score = total_weighted_score / total_weight if total_weight > 0 else 0.0
        overall_level = self._score_to_level(overall_score)

        # Sort gaps by priority (descending)
        gaps.sort(key=lambda g: g.priority, reverse=True)
        priority_skills = [g.skill for g in gaps[:3]]  # Top 3

        snapshot = GapSnapshot(
            user_id=user_id,
            timestamp=datetime.now(timezone.utc),
            overall_score=overall_score,
            overall_level=overall_level,
            gaps=gaps,
            priority_skills=priority_skills,
        )

        # Store snapshot
        await self._store_snapshot(snapshot)

        logger.info(
            "gap_analysis_complete",
            user_id=user_id,
            overall_score=overall_score,
            overall_level=overall_level,
            priority_skills=[s.value for s in priority_skills],
        )

        return snapshot

    def _compute_weighted_average(
        self, scores: list[tuple[datetime, float]]
    ) -> float:
        """Compute weighted average with recent scores weighted more.

        Args:
            scores: List of (timestamp, score) tuples

        Returns:
            Weighted average score
        """
        if not scores:
            return 0.0

        # Sort by time
        sorted_scores = sorted(scores, key=lambda x: x[0])

        # Exponential weighting (more recent = higher weight)
        total = 0.0
        total_weight = 0.0

        for i, (_, score) in enumerate(sorted_scores):
            weight = 1.5 ** i  # Exponential growth
            total += score * weight
            total_weight += weight

        return total / total_weight if total_weight > 0 else 0.0

    def _compute_trend(self, scores: list[tuple[datetime, float]]) -> float:
        """Compute trend from score history.

        Args:
            scores: List of (timestamp, score) tuples

        Returns:
            Trend value (-1 to 1, positive = improving)
        """
        if len(scores) < self.min_assessments:
            return 0.0

        # Sort by time
        sorted_scores = sorted(scores, key=lambda x: x[0])

        # Simple linear regression slope
        n = len(sorted_scores)
        x_vals = list(range(n))
        y_vals = [s for _, s in sorted_scores]

        x_mean = sum(x_vals) / n
        y_mean = sum(y_vals) / n

        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
        denominator = sum((x - x_mean) ** 2 for x in x_vals)

        if denominator == 0:
            return 0.0

        slope = numerator / denominator

        # Normalize to [-1, 1] (assuming max expected change is 0.1 per assessment)
        normalized = max(-1.0, min(1.0, slope / 0.1))

        return normalized

    def _compute_priority(
        self,
        gap_size: float,
        trend: float,
        weight: float,
        count: int,
    ) -> float:
        """Compute priority score for a skill gap.

        Args:
            gap_size: Size of the gap (0-1)
            trend: Current trend (-1 to 1)
            weight: Skill importance weight
            count: Number of assessments

        Returns:
            Priority score (higher = more urgent)
        """
        # Base priority from gap size
        base = gap_size * 2  # Scale gap to 0-2 range

        # Boost priority for declining skills
        trend_factor = 1.0 - (trend * 0.3)  # Declining adds up to 30%

        # Boost for important skills
        weight_factor = 0.5 + weight  # Range 0.5-1.5

        # Reduce priority for skills with few assessments (uncertain)
        confidence = min(1.0, count / self.min_assessments)

        priority = base * trend_factor * weight_factor * confidence

        return min(10.0, priority)  # Cap at 10

    def _score_to_level(self, score: float) -> int:
        """Convert score to CEFR level.

        Args:
            score: Score value (0-1)

        Returns:
            CEFR level (0-6)
        """
        for i, threshold in enumerate(LEVEL_THRESHOLDS):
            if score < threshold:
                return max(0, i - 1)
        return 6

    async def _store_snapshot(self, snapshot: GapSnapshot) -> None:
        """Store gap snapshot in database.

        Args:
            snapshot: Gap snapshot to store
        """
        try:
            gaps_dict = {
                g.skill.value: {
                    "score": g.current_score,
                    "gap": g.gap_size,
                    "priority": g.priority,
                    "trend": g.trend,
                }
                for g in snapshot.gaps
            }

            await self.gap_snapshot_repo.create(
                user_id=snapshot.user_id,
                gaps=gaps_dict,
            )
        except Exception as e:
            logger.warning("gap_snapshot_store_failed", error=str(e))
