"""Learning plan generator for personalized study recommendations.

Generates structured learning plans based on gap analysis,
with exercises, goals, and milestones.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from backend.analysis.gap_analyzer import GapSnapshot, SkillGap
from backend.coldpath.evaluators.base import SkillType
from backend.core.logging import get_logger

logger = get_logger(__name__)


class ExerciseType(str, Enum):
    """Types of practice exercises."""

    CONVERSATION = "conversation"  # Free conversation practice
    READING_ALOUD = "reading_aloud"  # Read text aloud
    SHADOWING = "shadowing"  # Listen and repeat
    DICTATION = "dictation"  # Listen and write
    GRAMMAR_DRILL = "grammar_drill"  # Grammar exercises
    VOCABULARY_REVIEW = "vocabulary_review"  # Vocabulary flashcards
    PRONUNCIATION_FOCUS = "pronunciation_focus"  # Specific sound practice
    ROLE_PLAY = "role_play"  # Scenario-based practice


# Exercise recommendations by skill
SKILL_EXERCISES = {
    SkillType.PRONUNCIATION: [
        ExerciseType.SHADOWING,
        ExerciseType.READING_ALOUD,
        ExerciseType.PRONUNCIATION_FOCUS,
    ],
    SkillType.GRAMMAR: [
        ExerciseType.GRAMMAR_DRILL,
        ExerciseType.DICTATION,
        ExerciseType.CONVERSATION,
    ],
    SkillType.FLUENCY: [
        ExerciseType.CONVERSATION,
        ExerciseType.SHADOWING,
        ExerciseType.ROLE_PLAY,
    ],
    SkillType.VOCABULARY: [
        ExerciseType.VOCABULARY_REVIEW,
        ExerciseType.READING_ALOUD,
        ExerciseType.CONVERSATION,
    ],
    SkillType.COHERENCE: [
        ExerciseType.CONVERSATION,
        ExerciseType.ROLE_PLAY,
    ],
    SkillType.RELEVANCE: [
        ExerciseType.ROLE_PLAY,
        ExerciseType.CONVERSATION,
    ],
}

# Exercise descriptions
EXERCISE_DESCRIPTIONS = {
    ExerciseType.CONVERSATION: "Practice free-form conversation with the AI coach",
    ExerciseType.READING_ALOUD: "Read provided texts aloud, focusing on clarity",
    ExerciseType.SHADOWING: "Listen to native speech and repeat immediately after",
    ExerciseType.DICTATION: "Listen to sentences and type what you hear",
    ExerciseType.GRAMMAR_DRILL: "Complete grammar exercises and get feedback",
    ExerciseType.VOCABULARY_REVIEW: "Review and practice new vocabulary words",
    ExerciseType.PRONUNCIATION_FOCUS: "Practice specific sounds that need improvement",
    ExerciseType.ROLE_PLAY: "Practice specific scenarios like ordering food, job interviews",
}


@dataclass
class PlanItem:
    """A single item in a learning plan."""

    skill: SkillType
    exercise_type: ExerciseType
    description: str
    duration_minutes: int
    frequency: str  # daily, every_other_day, weekly
    goal: str
    tips: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "skill": self.skill.value,
            "exercise_type": self.exercise_type.value,
            "description": self.description,
            "duration_minutes": self.duration_minutes,
            "frequency": self.frequency,
            "goal": self.goal,
            "tips": self.tips,
        }


@dataclass
class LearningPlan:
    """Complete learning plan for a user."""

    user_id: str
    created_at: datetime
    valid_until: datetime
    current_level: int
    target_level: int
    focus_skills: list[SkillType]
    daily_goal_minutes: int
    weekly_goal_sessions: int
    items: list[PlanItem]
    milestones: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        level_names = ["A0", "A1", "A2", "B1", "B2", "C1", "C2"]
        return {
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "current_level": self.current_level,
            "current_level_name": level_names[self.current_level],
            "target_level": self.target_level,
            "target_level_name": level_names[self.target_level],
            "focus_skills": [s.value for s in self.focus_skills],
            "daily_goal_minutes": self.daily_goal_minutes,
            "weekly_goal_sessions": self.weekly_goal_sessions,
            "items": [i.to_dict() for i in self.items],
            "milestones": self.milestones,
        }


class LearningPlanGenerator:
    """Generates personalized learning plans.

    Uses gap analysis to create structured plans with:
    - Targeted exercises for weak skills
    - Realistic time goals
    - Progress milestones
    """

    def __init__(
        self,
        plan_duration_days: int = 14,
        daily_goal_minutes: int = 30,
        weekly_sessions: int = 5,
    ) -> None:
        """Initialize generator.

        Args:
            plan_duration_days: How long the plan should cover
            daily_goal_minutes: Default daily practice goal
            weekly_sessions: Default weekly session goal
        """
        self.plan_duration_days = plan_duration_days
        self.daily_goal_minutes = daily_goal_minutes
        self.weekly_sessions = weekly_sessions

    def generate(self, gap_snapshot: GapSnapshot) -> LearningPlan:
        """Generate a learning plan from gap analysis.

        Args:
            gap_snapshot: Gap analysis results

        Returns:
            Personalized learning plan
        """
        now = datetime.now(timezone.utc)

        # Determine target level
        current_level = gap_snapshot.overall_level
        target_level = min(6, current_level + 1)

        # Get focus skills (top 3 by priority)
        focus_skills = gap_snapshot.priority_skills[:3]

        # Generate plan items
        items = self._generate_items(gap_snapshot.gaps, focus_skills)

        # Generate milestones
        milestones = self._generate_milestones(
            current_level, target_level, focus_skills
        )

        plan = LearningPlan(
            user_id=gap_snapshot.user_id,
            created_at=now,
            valid_until=now + timedelta(days=self.plan_duration_days),
            current_level=current_level,
            target_level=target_level,
            focus_skills=focus_skills,
            daily_goal_minutes=self.daily_goal_minutes,
            weekly_goal_sessions=self.weekly_sessions,
            items=items,
            milestones=milestones,
        )

        logger.info(
            "learning_plan_generated",
            user_id=gap_snapshot.user_id,
            items=len(items),
            focus_skills=[s.value for s in focus_skills],
        )

        return plan

    def _generate_items(
        self,
        gaps: list[SkillGap],
        focus_skills: list[SkillType],
    ) -> list[PlanItem]:
        """Generate plan items based on gaps.

        Args:
            gaps: List of skill gaps
            focus_skills: Skills to focus on

        Returns:
            List of plan items
        """
        items: list[PlanItem] = []

        # Create items for focus skills
        for skill in focus_skills:
            gap = next((g for g in gaps if g.skill == skill), None)
            if not gap:
                continue

            # Get exercises for this skill
            exercises = SKILL_EXERCISES.get(skill, [ExerciseType.CONVERSATION])

            # Create primary exercise item
            primary_exercise = exercises[0]
            items.append(
                PlanItem(
                    skill=skill,
                    exercise_type=primary_exercise,
                    description=EXERCISE_DESCRIPTIONS.get(
                        primary_exercise, "Practice exercise"
                    ),
                    duration_minutes=self._compute_duration(gap),
                    frequency=self._compute_frequency(gap),
                    goal=self._generate_goal(skill, gap),
                    tips=self._generate_tips(skill, gap),
                )
            )

            # Add secondary exercise if gap is large
            if gap.gap_size > 0.2 and len(exercises) > 1:
                secondary_exercise = exercises[1]
                items.append(
                    PlanItem(
                        skill=skill,
                        exercise_type=secondary_exercise,
                        description=EXERCISE_DESCRIPTIONS.get(
                            secondary_exercise, "Practice exercise"
                        ),
                        duration_minutes=10,
                        frequency="every_other_day",
                        goal=f"Supplement {skill.value} practice",
                        tips=[],
                    )
                )

        # Add general conversation practice
        if ExerciseType.CONVERSATION not in [i.exercise_type for i in items]:
            items.append(
                PlanItem(
                    skill=SkillType.FLUENCY,
                    exercise_type=ExerciseType.CONVERSATION,
                    description=EXERCISE_DESCRIPTIONS[ExerciseType.CONVERSATION],
                    duration_minutes=15,
                    frequency="daily",
                    goal="Maintain and improve overall speaking fluency",
                    tips=["Speak naturally", "Don't worry about mistakes"],
                )
            )

        return items

    def _compute_duration(self, gap: SkillGap) -> int:
        """Compute recommended exercise duration.

        Args:
            gap: Skill gap

        Returns:
            Duration in minutes
        """
        # Larger gaps need more time
        if gap.gap_size > 0.3:
            return 20
        elif gap.gap_size > 0.15:
            return 15
        else:
            return 10

    def _compute_frequency(self, gap: SkillGap) -> str:
        """Compute recommended exercise frequency.

        Args:
            gap: Skill gap

        Returns:
            Frequency string
        """
        if gap.priority > 2.0:
            return "daily"
        elif gap.priority > 1.0:
            return "every_other_day"
        else:
            return "weekly"

    def _generate_goal(self, skill: SkillType, gap: SkillGap) -> str:
        """Generate a goal statement.

        Args:
            skill: The skill
            gap: Skill gap

        Returns:
            Goal statement
        """
        goals = {
            SkillType.PRONUNCIATION: f"Improve pronunciation clarity from {gap.level_name} to the next level",
            SkillType.GRAMMAR: f"Reduce grammar errors and reach {gap.level_name} proficiency",
            SkillType.FLUENCY: f"Increase speaking fluency and reduce hesitations",
            SkillType.VOCABULARY: f"Expand active vocabulary for {gap.level_name} level",
            SkillType.COHERENCE: f"Improve logical flow and organization of speech",
            SkillType.RELEVANCE: f"Stay on topic and give relevant responses",
        }
        return goals.get(skill, f"Improve {skill.value}")

    def _generate_tips(self, skill: SkillType, gap: SkillGap) -> list[str]:
        """Generate practice tips.

        Args:
            skill: The skill
            gap: Skill gap

        Returns:
            List of tips
        """
        tips = {
            SkillType.PRONUNCIATION: [
                "Listen carefully to native pronunciation before repeating",
                "Focus on sounds that don't exist in your native language",
                "Practice mouth and tongue positions for difficult sounds",
            ],
            SkillType.GRAMMAR: [
                "Pay attention to verb tenses in your speech",
                "Practice subject-verb agreement",
                "Listen for error corrections and learn from them",
            ],
            SkillType.FLUENCY: [
                "Don't stop to correct every mistake while speaking",
                "Practice thinking in English, not translating",
                "Use filler phrases like 'Let me think...' instead of 'um'",
            ],
            SkillType.VOCABULARY: [
                "Learn new words in context, not isolation",
                "Use new words in sentences right away",
                "Review vocabulary regularly with spaced repetition",
            ],
            SkillType.COHERENCE: [
                "Use transition words: first, then, however, therefore",
                "Plan your main points before speaking",
                "Summarize at the end of longer responses",
            ],
            SkillType.RELEVANCE: [
                "Listen carefully to the full question before answering",
                "Ask for clarification if unsure about the topic",
                "Stay focused on the main topic",
            ],
        }

        skill_tips = tips.get(skill, ["Practice regularly"])

        # Add trend-specific tip
        if gap.trend < -0.2:
            skill_tips.append("Your recent scores have declined - focus on fundamentals")
        elif gap.trend > 0.2:
            skill_tips.append("Great progress! Keep up the good work")

        return skill_tips[:3]  # Return up to 3 tips

    def _generate_milestones(
        self,
        current_level: int,
        target_level: int,
        focus_skills: list[SkillType],
    ) -> list[dict[str, Any]]:
        """Generate progress milestones.

        Args:
            current_level: Current CEFR level
            target_level: Target CEFR level
            focus_skills: Focus skill areas

        Returns:
            List of milestone dictionaries
        """
        level_names = ["A0", "A1", "A2", "B1", "B2", "C1", "C2"]
        milestones = []

        # Week 1 milestone
        milestones.append({
            "week": 1,
            "goal": "Complete initial practice sessions",
            "criteria": [
                f"Complete 5 {focus_skills[0].value if focus_skills else 'speaking'} exercises",
                "Establish daily practice habit",
                "Identify specific problem areas",
            ],
        })

        # Week 2 milestone
        milestones.append({
            "week": 2,
            "goal": "Show measurable improvement",
            "criteria": [
                f"Improve {focus_skills[0].value if focus_skills else 'overall'} score by 5%",
                "Complete 10 total practice sessions",
                "Reduce common errors",
            ],
        })

        # Final milestone (if plan is longer)
        if self.plan_duration_days > 14:
            milestones.append({
                "week": 4,
                "goal": f"Reach stable {level_names[target_level]} performance",
                "criteria": [
                    f"Achieve consistent {level_names[target_level]} scores",
                    "Complete all planned exercises",
                    "Ready for next level challenges",
                ],
            })

        return milestones
