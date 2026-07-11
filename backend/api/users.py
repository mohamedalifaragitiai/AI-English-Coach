"""User API endpoints.

REST endpoints for user CRUD and progress queries.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.domain import (
    DEFAULT_WEIGHTS,
    LEVEL_THRESHOLDS,
    SKILL_DIMENSIONS,
    Assessment,
    User,
)
from backend.persistence import (
    AssessmentRepository,
    Database,
    GapSnapshotRepository,
    SessionRepository,
    UserRepository,
    generate_id,
)


async def get_db(request: Request) -> Database:
    """Get database from app state."""
    return request.app.state.database


# Dependency type alias
DbDep = Annotated[Database, Depends(get_db)]

router = APIRouter(prefix="/users", tags=["users"])


# Pydantic models for API
class UserCreate(BaseModel):
    """Request body for creating a user."""

    user_id: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    display_name: str = Field(..., min_length=1, max_length=100)


class UserResponse(BaseModel):
    """User response model."""

    user_id: str
    display_name: str
    created_at: datetime
    current_level: int
    streak_days: int
    settings: dict[str, Any] = {}


class UserUpdate(BaseModel):
    """Request body for updating a user."""

    display_name: str | None = None
    settings: dict[str, Any] | None = None


class ProgressResponse(BaseModel):
    """User progress summary."""

    user_id: str
    current_level: int
    level_name: str
    streak_days: int
    total_sessions: int
    total_assessments: int
    latest_overall: float | None
    skills: dict[str, float | None]
    gaps: dict[str, float] | None
    time_to_next_level: str | None


class SkillTrendPoint(BaseModel):
    """Single point in a skill trend."""

    timestamp: datetime
    score: float


class SkillTrendResponse(BaseModel):
    """Skill trend over time."""

    user_id: str
    skill: str
    points: list[SkillTrendPoint]


LEVEL_NAMES = {
    0: "Beginner",
    1: "Intermediate",
    2: "Advanced",
    3: "Professional",
    4: "Fluent",
    5: "Native-like",
}


def get_repos(db: Database) -> tuple[UserRepository, SessionRepository, AssessmentRepository, GapSnapshotRepository]:
    """Get repository instances."""
    return (
        UserRepository(db),
        SessionRepository(db),
        AssessmentRepository(db),
        GapSnapshotRepository(db),
    )


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(body: UserCreate, db: DbDep) -> UserResponse:
    """Create a new user/learner profile."""
    user_repo = UserRepository(db)

    # Check if user already exists
    existing = await user_repo.get(body.user_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"User {body.user_id} already exists")

    user = User(
        user_id=body.user_id,
        display_name=body.display_name,
        created_at=datetime.now(timezone.utc),
        current_level=0,
        streak_days=0,
        settings={},
    )
    await user_repo.create(user)

    return UserResponse(
        user_id=user.user_id,
        display_name=user.display_name,
        created_at=user.created_at,
        current_level=user.current_level,
        streak_days=user.streak_days,
        settings=user.settings,
    )


@router.get("", response_model=list[UserResponse])
async def list_users(db: DbDep) -> list[UserResponse]:
    """List all users."""
    user_repo = UserRepository(db)
    users = await user_repo.list_all()
    return [
        UserResponse(
            user_id=u.user_id,
            display_name=u.display_name,
            created_at=u.created_at,
            current_level=u.current_level,
            streak_days=u.streak_days,
            settings=u.settings,
        )
        for u in users
    ]


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: str, db: DbDep) -> UserResponse:
    """Get a user by ID."""
    user_repo = UserRepository(db)
    user = await user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    return UserResponse(
        user_id=user.user_id,
        display_name=user.display_name,
        created_at=user.created_at,
        current_level=user.current_level,
        streak_days=user.streak_days,
        settings=user.settings,
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str, body: UserUpdate, db: DbDep
) -> UserResponse:
    """Update a user."""
    user_repo = UserRepository(db)
    user = await user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    if body.display_name is not None:
        user.display_name = body.display_name
    if body.settings is not None:
        user.settings.update(body.settings)

    await user_repo.update(user)

    return UserResponse(
        user_id=user.user_id,
        display_name=user.display_name,
        created_at=user.created_at,
        current_level=user.current_level,
        streak_days=user.streak_days,
        settings=user.settings,
    )


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: str, db: DbDep) -> None:
    """Delete a user and all related data."""
    user_repo = UserRepository(db)
    deleted = await user_repo.delete(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")


@router.get("/{user_id}/progress", response_model=ProgressResponse)
async def get_progress(user_id: str, db: DbDep) -> ProgressResponse:
    """Get user progress summary."""
    user_repo, session_repo, assessment_repo, gap_repo = get_repos(db)

    user = await user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    # Get session count
    sessions = await session_repo.get_user_sessions(user_id, limit=1000)
    total_sessions = len(sessions)

    # Get assessments
    assessments = await assessment_repo.get_user_assessments(user_id, limit=1000)
    total_assessments = len(assessments)

    # Get latest assessment for current skills
    latest = await assessment_repo.get_latest_assessment(user_id)
    skills: dict[str, float | None] = {}
    latest_overall: float | None = None

    if latest:
        latest_overall = latest.overall
        for skill in SKILL_DIMENSIONS:
            skills[skill] = getattr(latest, skill, None)
    else:
        for skill in SKILL_DIMENSIONS:
            skills[skill] = None

    # Get latest gap snapshot
    gap_snapshot = await gap_repo.get_latest(user_id)
    gaps = gap_snapshot.gaps if gap_snapshot else None

    # Estimate time to next level
    time_to_next = await _estimate_time_to_next_level(user, assessments)

    return ProgressResponse(
        user_id=user.user_id,
        current_level=user.current_level,
        level_name=LEVEL_NAMES.get(user.current_level, "Unknown"),
        streak_days=user.streak_days,
        total_sessions=total_sessions,
        total_assessments=total_assessments,
        latest_overall=latest_overall,
        skills=skills,
        gaps=gaps,
        time_to_next_level=time_to_next,
    )


@router.get("/{user_id}/skills/{skill}/trend", response_model=SkillTrendResponse)
async def get_skill_trend(
    user_id: str,
    skill: str,
    db: DbDep,
    days: int = Query(default=30, ge=1, le=365),
) -> SkillTrendResponse:
    """Get trend data for a specific skill over time."""
    if skill not in SKILL_DIMENSIONS + ["overall"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid skill. Must be one of: {SKILL_DIMENSIONS + ['overall']}",
        )

    user_repo = UserRepository(db)
    user = await user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    assessment_repo = AssessmentRepository(db)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    trend_data = await assessment_repo.get_skill_trend(user_id, skill, since=since)

    return SkillTrendResponse(
        user_id=user_id,
        skill=skill,
        points=[SkillTrendPoint(timestamp=ts, score=score) for ts, score in trend_data],
    )


@router.post("/{user_id}/streak/update")
async def update_streak(user_id: str, db: DbDep) -> dict[str, int]:
    """Update and return the user's current streak."""
    user_repo = UserRepository(db)
    user = await user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    new_streak = await user_repo.update_streak(user_id)
    return {"streak_days": new_streak}


async def _estimate_time_to_next_level(
    user: User,
    assessments: list[Assessment],
) -> str | None:
    """Estimate time to reach the next level based on trend."""
    if user.current_level >= 5:
        return None  # Already at max level

    if len(assessments) < 5:
        return None  # Not enough data

    # Get overall scores with timestamps
    recent = [(a.created_at, a.overall) for a in assessments[:30] if a.overall is not None]
    if len(recent) < 5:
        return None

    # Calculate improvement rate (points per day)
    recent.sort(key=lambda x: x[0])
    first_score = recent[0][1]
    last_score = recent[-1][1]
    days_elapsed = (recent[-1][0] - recent[0][0]).total_seconds() / 86400

    if days_elapsed < 1:
        return None

    rate = (last_score - first_score) / days_elapsed

    if rate <= 0:
        return "Keep practicing to see progress"

    # Target score for next level
    next_level = user.current_level + 1
    target = LEVEL_THRESHOLDS.get(next_level, 100)
    points_needed = target - last_score

    if points_needed <= 0:
        return "Ready for level up!"

    days_needed = points_needed / rate

    if days_needed < 7:
        return f"~{int(days_needed)} days"
    elif days_needed < 30:
        return f"~{int(days_needed / 7)} weeks"
    elif days_needed < 365:
        return f"~{int(days_needed / 30)} months"
    else:
        return "Keep practicing consistently"
