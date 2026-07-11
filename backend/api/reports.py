"""Reports API endpoints.

REST endpoints for gap analysis, learning plans, and progress reports.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from backend.analysis.gap_analyzer import GapAnalyzer, GapSnapshot
from backend.analysis.learning_plan import LearningPlan, LearningPlanGenerator
from backend.analysis.progress_report import ProgressReport, ProgressReportGenerator
from backend.persistence import Database
from backend.persistence.repositories import (
    AssessmentRepository,
    GapSnapshotRepository,
    SessionRepository,
    UserRepository,
)


async def get_db(request: Request) -> Database:
    """Get database from app state."""
    return request.app.state.database


DbDep = Annotated[Database, Depends(get_db)]

router = APIRouter(prefix="/users/{user_id}/reports", tags=["reports"])


# Response models
class GapResponse(BaseModel):
    """Gap analysis response."""

    user_id: str
    timestamp: str
    overall_score: float
    overall_level: int
    overall_level_name: str
    gaps: list[dict[str, Any]]
    priority_skills: list[str]


class PlanResponse(BaseModel):
    """Learning plan response."""

    user_id: str
    created_at: str
    valid_until: str
    current_level: int
    current_level_name: str
    target_level: int
    target_level_name: str
    focus_skills: list[str]
    daily_goal_minutes: int
    weekly_goal_sessions: int
    items: list[dict[str, Any]]
    milestones: list[dict[str, Any]]


class ReportResponse(BaseModel):
    """Progress report response."""

    user_id: str
    user_name: str
    generated_at: str
    period: dict[str, str]
    overall: dict[str, Any]
    skill_trends: list[dict[str, Any]]
    practice_stats: dict[str, Any]
    achievements: list[dict[str, Any]]
    recommendations: list[str]
    highlights: list[str]


def get_repos(db: Database) -> tuple[
    UserRepository, SessionRepository, AssessmentRepository, GapSnapshotRepository
]:
    """Get repository instances."""
    return (
        UserRepository(db),
        SessionRepository(db),
        AssessmentRepository(db),
        GapSnapshotRepository(db),
    )


@router.get("/gaps", response_model=GapResponse)
async def get_gap_analysis(
    user_id: str,
    db: DbDep,
    target_level: int = Query(default=-1, ge=-1, le=6),
) -> GapResponse:
    """Get gap analysis for a user.

    Analyzes recent assessments to identify skill gaps and prioritize
    areas for improvement.

    Args:
        user_id: User ID
        target_level: Target CEFR level (0-6), or -1 for next level
    """
    user_repo, session_repo, assessment_repo, gap_repo = get_repos(db)

    # Check user exists
    user = await user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    # Run gap analysis
    analyzer = GapAnalyzer(
        assessment_repo=assessment_repo,
        gap_snapshot_repo=gap_repo,
    )

    snapshot = await analyzer.analyze(user_id, target_level)
    data = snapshot.to_dict()

    return GapResponse(**data)


@router.get("/plan", response_model=PlanResponse)
async def get_learning_plan(
    user_id: str,
    db: DbDep,
    duration_days: int = Query(default=14, ge=7, le=90),
    daily_minutes: int = Query(default=30, ge=10, le=120),
) -> PlanResponse:
    """Generate a personalized learning plan.

    Creates a structured study plan based on gap analysis with
    exercises, goals, and milestones.

    Args:
        user_id: User ID
        duration_days: Plan duration in days
        daily_minutes: Daily practice goal in minutes
    """
    user_repo, session_repo, assessment_repo, gap_repo = get_repos(db)

    # Check user exists
    user = await user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    # First run gap analysis
    analyzer = GapAnalyzer(
        assessment_repo=assessment_repo,
        gap_snapshot_repo=gap_repo,
    )
    gap_snapshot = await analyzer.analyze(user_id)

    # Generate learning plan
    generator = LearningPlanGenerator(
        plan_duration_days=duration_days,
        daily_goal_minutes=daily_minutes,
    )
    plan = generator.generate(gap_snapshot)
    data = plan.to_dict()

    return PlanResponse(**data)


@router.get("/progress", response_model=ReportResponse)
async def get_progress_report(
    user_id: str,
    db: DbDep,
    days: int = Query(default=30, ge=7, le=365),
) -> ReportResponse:
    """Generate a comprehensive progress report.

    Summarizes learner progress including skill trends, practice
    statistics, achievements, and recommendations.

    Args:
        user_id: User ID
        days: Number of days to cover in the report
    """
    user_repo, session_repo, assessment_repo, gap_repo = get_repos(db)

    # Check user exists
    user = await user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    # Generate report
    generator = ProgressReportGenerator(
        user_repo=user_repo,
        assessment_repo=assessment_repo,
        session_repo=session_repo,
        report_period_days=days,
    )
    report = await generator.generate(user_id)
    data = report.to_dict()

    return ReportResponse(**data)
