"""Progress report generator for learner summaries.

Generates comprehensive progress reports including:
- Skill trends over time
- Practice statistics
- Achievements and milestones
- Personalized recommendations
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.coldpath.evaluators.base import SkillType
from backend.core.logging import get_logger
from backend.persistence.repositories import (
    AssessmentRepository,
    SessionRepository,
    UserRepository,
)

logger = get_logger(__name__)


@dataclass
class SkillTrend:
    """Trend data for a single skill."""

    skill: SkillType
    current_score: float
    previous_score: float  # Score from previous period
    change: float  # current - previous
    trend_direction: str  # improving, declining, stable
    data_points: list[tuple[str, float]]  # (date_str, score) for charting

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "skill": self.skill.value,
            "current_score": round(self.current_score, 3),
            "previous_score": round(self.previous_score, 3),
            "change": round(self.change, 3),
            "trend_direction": self.trend_direction,
            "data_points": [
                {"date": d, "score": round(s, 3)} for d, s in self.data_points
            ],
        }


@dataclass
class PracticeStats:
    """Practice session statistics."""

    total_sessions: int
    total_utterances: int
    total_practice_minutes: int
    sessions_this_week: int
    sessions_last_week: int
    current_streak: int
    longest_streak: int
    average_session_minutes: float
    most_active_day: str  # Day of week

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_sessions": self.total_sessions,
            "total_utterances": self.total_utterances,
            "total_practice_minutes": self.total_practice_minutes,
            "sessions_this_week": self.sessions_this_week,
            "sessions_last_week": self.sessions_last_week,
            "current_streak": self.current_streak,
            "longest_streak": self.longest_streak,
            "average_session_minutes": round(self.average_session_minutes, 1),
            "most_active_day": self.most_active_day,
        }


@dataclass
class Achievement:
    """A learner achievement."""

    id: str
    title: str
    description: str
    earned_at: datetime | None
    progress: float  # 0-1, 1 = earned

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "earned_at": self.earned_at.isoformat() if self.earned_at else None,
            "progress": round(self.progress, 2),
            "earned": self.progress >= 1.0,
        }


@dataclass
class ProgressReport:
    """Complete progress report."""

    user_id: str
    user_name: str
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    overall_level: int
    overall_score: float
    level_progress: float  # Progress toward next level (0-1)
    skill_trends: list[SkillTrend]
    practice_stats: PracticeStats
    achievements: list[Achievement]
    recommendations: list[str]
    highlights: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        level_names = ["A0", "A1", "A2", "B1", "B2", "C1", "C2"]
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "generated_at": self.generated_at.isoformat(),
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "overall": {
                "level": self.overall_level,
                "level_name": level_names[self.overall_level],
                "score": round(self.overall_score, 3),
                "level_progress": round(self.level_progress, 2),
            },
            "skill_trends": [t.to_dict() for t in self.skill_trends],
            "practice_stats": self.practice_stats.to_dict(),
            "achievements": [a.to_dict() for a in self.achievements],
            "recommendations": self.recommendations,
            "highlights": self.highlights,
        }


class ProgressReportGenerator:
    """Generates progress reports for learners.

    Aggregates data from assessments and sessions to create
    comprehensive progress summaries.
    """

    def __init__(
        self,
        user_repo: UserRepository,
        assessment_repo: AssessmentRepository,
        session_repo: SessionRepository,
        report_period_days: int = 30,
    ) -> None:
        """Initialize generator.

        Args:
            user_repo: User repository
            assessment_repo: Assessment repository
            session_repo: Session repository
            report_period_days: Days to cover in report
        """
        self.user_repo = user_repo
        self.assessment_repo = assessment_repo
        self.session_repo = session_repo
        self.report_period_days = report_period_days

    async def generate(self, user_id: str) -> ProgressReport:
        """Generate a progress report for a user.

        Args:
            user_id: User ID

        Returns:
            ProgressReport
        """
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=self.report_period_days)

        # Get user info
        user = await self.user_repo.get(user_id)
        user_name = user.name if user else "Learner"

        # Get skill trends
        skill_trends = await self._compute_skill_trends(user_id, period_start, now)

        # Compute overall from trends
        overall_score, overall_level = self._compute_overall(skill_trends)
        level_progress = self._compute_level_progress(overall_score, overall_level)

        # Get practice stats
        practice_stats = await self._compute_practice_stats(user_id, now)

        # Get achievements
        achievements = self._compute_achievements(skill_trends, practice_stats)

        # Generate recommendations
        recommendations = self._generate_recommendations(skill_trends, practice_stats)

        # Generate highlights
        highlights = self._generate_highlights(skill_trends, practice_stats, achievements)

        report = ProgressReport(
            user_id=user_id,
            user_name=user_name,
            generated_at=now,
            period_start=period_start,
            period_end=now,
            overall_level=overall_level,
            overall_score=overall_score,
            level_progress=level_progress,
            skill_trends=skill_trends,
            practice_stats=practice_stats,
            achievements=achievements,
            recommendations=recommendations,
            highlights=highlights,
        )

        logger.info(
            "progress_report_generated",
            user_id=user_id,
            overall_level=overall_level,
            overall_score=overall_score,
        )

        return report

    async def _compute_skill_trends(
        self,
        user_id: str,
        period_start: datetime,
        period_end: datetime,
    ) -> list[SkillTrend]:
        """Compute skill trends over the period.

        Args:
            user_id: User ID
            period_start: Start of period
            period_end: End of period

        Returns:
            List of skill trends
        """
        trends = []
        days = (period_end - period_start).days

        for skill in SkillType:
            # Get trend data
            data = await self.assessment_repo.get_skill_trend(
                user_id=user_id,
                skill=skill.value,
                days=days,
            )

            if not data:
                continue

            # Sort by date
            sorted_data = sorted(data, key=lambda x: x[0])

            # Current score (average of last 5)
            recent = [s for _, s in sorted_data[-5:]]
            current_score = sum(recent) / len(recent) if recent else 0.0

            # Previous score (average before last 5)
            older = [s for _, s in sorted_data[:-5]]
            previous_score = sum(older) / len(older) if older else current_score

            # Change
            change = current_score - previous_score

            # Trend direction
            if change > 0.05:
                direction = "improving"
            elif change < -0.05:
                direction = "declining"
            else:
                direction = "stable"

            # Data points for charting
            data_points = [
                (dt.strftime("%Y-%m-%d"), score) for dt, score in sorted_data
            ]

            trends.append(SkillTrend(
                skill=skill,
                current_score=current_score,
                previous_score=previous_score,
                change=change,
                trend_direction=direction,
                data_points=data_points[-20:],  # Last 20 points
            ))

        return trends

    def _compute_overall(
        self, skill_trends: list[SkillTrend]
    ) -> tuple[float, int]:
        """Compute overall score and level.

        Args:
            skill_trends: List of skill trends

        Returns:
            Tuple of (overall_score, overall_level)
        """
        if not skill_trends:
            return 0.0, 0

        # Weighted average
        weights = {
            SkillType.PRONUNCIATION: 0.25,
            SkillType.GRAMMAR: 0.25,
            SkillType.FLUENCY: 0.20,
            SkillType.VOCABULARY: 0.15,
            SkillType.COHERENCE: 0.10,
            SkillType.RELEVANCE: 0.05,
        }

        total = 0.0
        total_weight = 0.0

        for trend in skill_trends:
            weight = weights.get(trend.skill, 0.1)
            total += trend.current_score * weight
            total_weight += weight

        score = total / total_weight if total_weight > 0 else 0.0

        # Convert to level
        thresholds = [0.0, 0.20, 0.40, 0.55, 0.70, 0.85, 0.95]
        level = 0
        for i, threshold in enumerate(thresholds):
            if score >= threshold:
                level = i

        return score, level

    def _compute_level_progress(self, score: float, level: int) -> float:
        """Compute progress toward next level.

        Args:
            score: Current overall score
            level: Current level

        Returns:
            Progress 0-1 toward next level
        """
        thresholds = [0.0, 0.20, 0.40, 0.55, 0.70, 0.85, 0.95]

        if level >= 6:
            return 1.0

        current_threshold = thresholds[level]
        next_threshold = thresholds[level + 1]

        range_size = next_threshold - current_threshold
        if range_size <= 0:
            return 0.0

        progress = (score - current_threshold) / range_size
        return max(0.0, min(1.0, progress))

    async def _compute_practice_stats(
        self,
        user_id: str,
        now: datetime,
    ) -> PracticeStats:
        """Compute practice statistics.

        Args:
            user_id: User ID
            now: Current time

        Returns:
            PracticeStats
        """
        # Get user for streak info
        user = await self.user_repo.get(user_id)

        # Default stats
        stats = PracticeStats(
            total_sessions=0,
            total_utterances=0,
            total_practice_minutes=0,
            sessions_this_week=0,
            sessions_last_week=0,
            current_streak=user.current_streak if user else 0,
            longest_streak=user.longest_streak if user else 0,
            average_session_minutes=0.0,
            most_active_day="Monday",
        )

        # In a full implementation, we would query session data
        # For now, return defaults based on user data

        return stats

    def _compute_achievements(
        self,
        skill_trends: list[SkillTrend],
        practice_stats: PracticeStats,
    ) -> list[Achievement]:
        """Compute earned and in-progress achievements.

        Args:
            skill_trends: Skill trend data
            practice_stats: Practice statistics

        Returns:
            List of achievements
        """
        achievements = []
        now = datetime.now(timezone.utc)

        # Streak achievements
        if practice_stats.current_streak >= 7:
            achievements.append(Achievement(
                id="streak_7",
                title="Week Warrior",
                description="Practice 7 days in a row",
                earned_at=now,
                progress=1.0,
            ))
        elif practice_stats.current_streak > 0:
            achievements.append(Achievement(
                id="streak_7",
                title="Week Warrior",
                description="Practice 7 days in a row",
                earned_at=None,
                progress=practice_stats.current_streak / 7,
            ))

        if practice_stats.current_streak >= 30:
            achievements.append(Achievement(
                id="streak_30",
                title="Monthly Master",
                description="Practice 30 days in a row",
                earned_at=now,
                progress=1.0,
            ))

        # Improvement achievements
        for trend in skill_trends:
            if trend.change >= 0.1:
                achievements.append(Achievement(
                    id=f"improve_{trend.skill.value}",
                    title=f"{trend.skill.value.title()} Champion",
                    description=f"Improve {trend.skill.value} by 10%",
                    earned_at=now,
                    progress=1.0,
                ))

        # Level achievements
        improving = [t for t in skill_trends if t.trend_direction == "improving"]
        if len(improving) >= 3:
            achievements.append(Achievement(
                id="multi_improve",
                title="All-Round Improver",
                description="Improve in 3+ skills simultaneously",
                earned_at=now,
                progress=1.0,
            ))

        return achievements

    def _generate_recommendations(
        self,
        skill_trends: list[SkillTrend],
        practice_stats: PracticeStats,
    ) -> list[str]:
        """Generate personalized recommendations.

        Args:
            skill_trends: Skill trend data
            practice_stats: Practice statistics

        Returns:
            List of recommendation strings
        """
        recommendations = []

        # Find declining skills
        declining = [t for t in skill_trends if t.trend_direction == "declining"]
        if declining:
            worst = min(declining, key=lambda t: t.change)
            recommendations.append(
                f"Focus on {worst.skill.value} - it has declined recently. "
                f"Try dedicated practice exercises."
            )

        # Find lowest skills
        if skill_trends:
            lowest = min(skill_trends, key=lambda t: t.current_score)
            if lowest.current_score < 0.5:
                recommendations.append(
                    f"Your {lowest.skill.value} score is below average. "
                    f"Consider spending more time on this area."
                )

        # Practice frequency
        if practice_stats.current_streak == 0:
            recommendations.append(
                "Start a new practice streak! Consistent daily practice "
                "is the key to improvement."
            )
        elif practice_stats.sessions_this_week < 3:
            recommendations.append(
                "Try to practice at least 3 times per week for best results."
            )

        # General tips
        if not recommendations:
            recommendations.append(
                "You're doing great! Keep up the consistent practice."
            )

        return recommendations[:3]  # Top 3 recommendations

    def _generate_highlights(
        self,
        skill_trends: list[SkillTrend],
        practice_stats: PracticeStats,
        achievements: list[Achievement],
    ) -> list[str]:
        """Generate progress highlights.

        Args:
            skill_trends: Skill trend data
            practice_stats: Practice statistics
            achievements: Earned achievements

        Returns:
            List of highlight strings
        """
        highlights = []

        # Best improving skill
        improving = [t for t in skill_trends if t.trend_direction == "improving"]
        if improving:
            best = max(improving, key=lambda t: t.change)
            highlights.append(
                f"Great progress in {best.skill.value}! "
                f"Up {abs(best.change)*100:.0f}% this period."
            )

        # Streak highlight
        if practice_stats.current_streak >= 7:
            highlights.append(
                f"Amazing {practice_stats.current_streak}-day practice streak!"
            )

        # Achievement highlight
        earned = [a for a in achievements if a.progress >= 1.0]
        if earned:
            highlights.append(
                f"Earned {len(earned)} achievement(s) this period!"
            )

        # Level progress
        # (would need overall level info passed in)

        if not highlights:
            highlights.append("Keep practicing to unlock achievements and track progress!")

        return highlights[:3]
