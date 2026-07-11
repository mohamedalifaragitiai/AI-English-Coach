"""Tests for persistence layer.

Tests for:
- Database initialization and migrations
- User CRUD operations
- Session management
- Assessment versioning
- Progress queries
- Streak calculation
"""

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.domain import (
    Assessment,
    GapSnapshot,
    Session,
    SessionMode,
    User,
    Utterance,
    UtteranceRole,
)
from backend.persistence import (
    AssessmentRepository,
    Database,
    GapSnapshotRepository,
    SessionRepository,
    UserRepository,
    UtteranceRepository,
    generate_id,
)


@pytest.fixture
async def db() -> Database:
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        database = Database(db_path)
        await database.initialize()
        yield database
        await database.close()


@pytest.fixture
def user_repo(db: Database) -> UserRepository:
    """Create user repository."""
    return UserRepository(db)


@pytest.fixture
def session_repo(db: Database) -> SessionRepository:
    """Create session repository."""
    return SessionRepository(db)


@pytest.fixture
def utterance_repo(db: Database) -> UtteranceRepository:
    """Create utterance repository."""
    return UtteranceRepository(db)


@pytest.fixture
def assessment_repo(db: Database) -> AssessmentRepository:
    """Create assessment repository."""
    return AssessmentRepository(db)


@pytest.fixture
def gap_repo(db: Database) -> GapSnapshotRepository:
    """Create gap snapshot repository."""
    return GapSnapshotRepository(db)


def now() -> datetime:
    """Get current time."""
    return datetime.now(timezone.utc)


class TestDatabase:
    """Test database initialization and migrations."""

    @pytest.mark.asyncio
    async def test_database_initializes(self, db: Database):
        """Database initializes successfully."""
        assert db._connection is not None

    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self, db: Database):
        """WAL mode is enabled."""
        async with db.connection() as conn:
            cursor = await conn.execute("PRAGMA journal_mode")
            row = await cursor.fetchone()
            assert row[0].lower() == "wal"

    @pytest.mark.asyncio
    async def test_foreign_keys_enabled(self, db: Database):
        """Foreign keys are enabled."""
        async with db.connection() as conn:
            cursor = await conn.execute("PRAGMA foreign_keys")
            row = await cursor.fetchone()
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_migrations_applied(self, db: Database):
        """Migrations table exists and has entries."""
        async with db.connection() as conn:
            cursor = await conn.execute("SELECT name FROM _migrations")
            rows = await cursor.fetchall()
            assert len(rows) >= 1
            assert rows[0][0] == "001_initial_schema"


class TestUserRepository:
    """Test user CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_user(self, user_repo: UserRepository):
        """Create a new user."""
        user = User(
            user_id="test_user",
            display_name="Test User",
            created_at=now(),
            current_level=1,
            streak_days=5,
            settings={"theme": "dark"},
        )
        created = await user_repo.create(user)

        assert created.user_id == "test_user"
        assert created.display_name == "Test User"
        assert created.current_level == 1
        assert created.streak_days == 5
        assert created.settings == {"theme": "dark"}

    @pytest.mark.asyncio
    async def test_get_user(self, user_repo: UserRepository):
        """Get a user by ID."""
        user = User(
            user_id="get_test",
            display_name="Get Test",
            created_at=now(),
        )
        await user_repo.create(user)

        retrieved = await user_repo.get("get_test")
        assert retrieved is not None
        assert retrieved.user_id == "get_test"
        assert retrieved.display_name == "Get Test"

    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, user_repo: UserRepository):
        """Get returns None for nonexistent user."""
        result = await user_repo.get("does_not_exist")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_user(self, user_repo: UserRepository):
        """Update an existing user."""
        user = User(
            user_id="update_test",
            display_name="Original Name",
            created_at=now(),
            current_level=0,
        )
        await user_repo.create(user)

        user.display_name = "Updated Name"
        user.current_level = 2
        await user_repo.update(user)

        retrieved = await user_repo.get("update_test")
        assert retrieved.display_name == "Updated Name"
        assert retrieved.current_level == 2

    @pytest.mark.asyncio
    async def test_list_all_users(self, user_repo: UserRepository):
        """List all users."""
        for i in range(3):
            user = User(
                user_id=f"list_test_{i}",
                display_name=f"User {i}",
                created_at=now(),
            )
            await user_repo.create(user)

        users = await user_repo.list_all()
        assert len(users) == 3

    @pytest.mark.asyncio
    async def test_delete_user(self, user_repo: UserRepository):
        """Delete a user."""
        user = User(
            user_id="delete_test",
            display_name="Delete Me",
            created_at=now(),
        )
        await user_repo.create(user)

        deleted = await user_repo.delete("delete_test")
        assert deleted is True

        retrieved = await user_repo.get("delete_test")
        assert retrieved is None


class TestSessionRepository:
    """Test session management."""

    @pytest.mark.asyncio
    async def test_create_session(
        self, user_repo: UserRepository, session_repo: SessionRepository
    ):
        """Create a new session."""
        # Create user first
        user = User(user_id="session_user", display_name="Session User", created_at=now())
        await user_repo.create(user)

        session = Session(
            session_id=generate_id(),
            user_id="session_user",
            mode=SessionMode.FREE,
            started_at=now(),
            difficulty=0.5,
        )
        created = await session_repo.create(session)

        assert created.session_id == session.session_id
        assert created.is_active is True

    @pytest.mark.asyncio
    async def test_end_session(
        self, user_repo: UserRepository, session_repo: SessionRepository
    ):
        """End an active session."""
        user = User(user_id="end_session_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        session = Session(
            session_id=generate_id(),
            user_id="end_session_user",
            mode=SessionMode.INTERVIEW,
            started_at=now(),
        )
        await session_repo.create(session)

        ended = await session_repo.end_session(session.session_id)
        assert ended.is_active is False
        assert ended.ended_at is not None

    @pytest.mark.asyncio
    async def test_get_active_session(
        self, user_repo: UserRepository, session_repo: SessionRepository
    ):
        """Get active session for a user."""
        user = User(user_id="active_session_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        # Create ended session
        old_session = Session(
            session_id=generate_id(),
            user_id="active_session_user",
            mode=SessionMode.FREE,
            started_at=now() - timedelta(hours=2),
            ended_at=now() - timedelta(hours=1),
        )
        await session_repo.create(old_session)

        # Create active session
        active_session = Session(
            session_id=generate_id(),
            user_id="active_session_user",
            mode=SessionMode.BUSINESS,
            started_at=now(),
        )
        await session_repo.create(active_session)

        result = await session_repo.get_active_session("active_session_user")
        assert result is not None
        assert result.session_id == active_session.session_id
        assert result.is_active is True


class TestAssessmentRepository:
    """Test assessment versioning and queries."""

    @pytest.mark.asyncio
    async def test_create_assessment(
        self, user_repo: UserRepository, assessment_repo: AssessmentRepository
    ):
        """Create a new assessment."""
        user = User(user_id="assess_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        assessment = Assessment(
            assessment_id=generate_id(),
            user_id="assess_user",
            scoring_model_version="v1.0.0",
            created_at=now(),
            pronunciation=75.0,
            grammar=80.0,
            vocabulary=70.0,
            fluency=65.0,
            overall=72.5,
        )
        created = await assessment_repo.create(assessment)

        assert created.assessment_id == assessment.assessment_id
        assert created.pronunciation == 75.0

    @pytest.mark.asyncio
    async def test_assessments_preserve_version(
        self, user_repo: UserRepository, assessment_repo: AssessmentRepository
    ):
        """Assessments preserve scoring model version."""
        user = User(user_id="version_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        # Create assessments with different versions
        for version in ["v1.0.0", "v1.1.0", "v2.0.0"]:
            assessment = Assessment(
                assessment_id=generate_id(),
                user_id="version_user",
                scoring_model_version=version,
                created_at=now(),
                overall=70.0,
            )
            await assessment_repo.create(assessment)

        # Query by version
        v1_assessments = await assessment_repo.get_user_assessments(
            "version_user", scoring_version="v1.0.0"
        )
        assert len(v1_assessments) == 1
        assert v1_assessments[0].scoring_model_version == "v1.0.0"

    @pytest.mark.asyncio
    async def test_get_skill_trend(
        self, user_repo: UserRepository, assessment_repo: AssessmentRepository
    ):
        """Get skill trend over time."""
        user = User(user_id="trend_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        # Create assessments over time
        base_time = now()
        for i in range(5):
            assessment = Assessment(
                assessment_id=generate_id(),
                user_id="trend_user",
                scoring_model_version="v1.0.0",
                created_at=base_time + timedelta(days=i),
                fluency=60.0 + i * 5,  # Improving fluency
            )
            await assessment_repo.create(assessment)

        trend = await assessment_repo.get_skill_trend("trend_user", "fluency")
        assert len(trend) == 5
        # Scores should be increasing
        scores = [point[1] for point in trend]
        assert scores == [60.0, 65.0, 70.0, 75.0, 80.0]

    @pytest.mark.asyncio
    async def test_get_latest_assessment(
        self, user_repo: UserRepository, assessment_repo: AssessmentRepository
    ):
        """Get the most recent assessment."""
        user = User(user_id="latest_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        # Create multiple assessments
        for i in range(3):
            assessment = Assessment(
                assessment_id=generate_id(),
                user_id="latest_user",
                scoring_model_version="v1.0.0",
                created_at=now() + timedelta(seconds=i),
                overall=50.0 + i * 10,
            )
            await assessment_repo.create(assessment)

        latest = await assessment_repo.get_latest_assessment("latest_user")
        assert latest is not None
        assert latest.overall == 70.0  # Last one created


class TestGapSnapshotRepository:
    """Test gap snapshot storage."""

    @pytest.mark.asyncio
    async def test_create_gap_snapshot(
        self, user_repo: UserRepository, gap_repo: GapSnapshotRepository
    ):
        """Create a gap snapshot."""
        user = User(user_id="gap_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        snapshot = GapSnapshot(
            id=generate_id(),
            user_id="gap_user",
            taken_at=now(),
            gaps={"pronunciation": 0.8, "grammar": 0.5, "vocabulary": 0.3},
        )
        created = await gap_repo.create(snapshot)

        assert created.gaps["pronunciation"] == 0.8

    @pytest.mark.asyncio
    async def test_get_latest_gap_snapshot(
        self, user_repo: UserRepository, gap_repo: GapSnapshotRepository
    ):
        """Get latest gap snapshot."""
        user = User(user_id="latest_gap_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        # Create snapshots at different times
        for i in range(3):
            snapshot = GapSnapshot(
                id=generate_id(),
                user_id="latest_gap_user",
                taken_at=now() + timedelta(days=i),
                gaps={"pronunciation": 0.8 - i * 0.1},  # Improving
            )
            await gap_repo.create(snapshot)

        latest = await gap_repo.get_latest("latest_gap_user")
        assert latest is not None
        assert abs(latest.gaps["pronunciation"] - 0.6) < 0.01  # Last one


class TestStreakCalculation:
    """Test streak calculation logic."""

    @pytest.mark.asyncio
    async def test_streak_consecutive_days(
        self,
        user_repo: UserRepository,
        session_repo: SessionRepository,
    ):
        """Streak counts consecutive practice days."""
        user = User(user_id="streak_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        # Create sessions for consecutive days
        today = now()
        for i in range(5):
            session = Session(
                session_id=generate_id(),
                user_id="streak_user",
                mode=SessionMode.FREE,
                started_at=today - timedelta(days=i),
                ended_at=today - timedelta(days=i) + timedelta(minutes=30),
            )
            await session_repo.create(session)

        streak = await user_repo.update_streak("streak_user")
        assert streak == 5

    @pytest.mark.asyncio
    async def test_streak_broken(
        self,
        user_repo: UserRepository,
        session_repo: SessionRepository,
    ):
        """Streak resets when a day is missed."""
        user = User(user_id="broken_streak_user", display_name="Test", created_at=now())
        await user_repo.create(user)

        today = now()

        # Practice yesterday
        session1 = Session(
            session_id=generate_id(),
            user_id="broken_streak_user",
            mode=SessionMode.FREE,
            started_at=today - timedelta(days=1),
        )
        await session_repo.create(session1)

        # Skip a day, practice 3 days ago
        session2 = Session(
            session_id=generate_id(),
            user_id="broken_streak_user",
            mode=SessionMode.FREE,
            started_at=today - timedelta(days=3),
        )
        await session_repo.create(session2)

        streak = await user_repo.update_streak("broken_streak_user")
        assert streak == 1  # Only yesterday counts


class TestAssessmentScoring:
    """Test assessment scoring calculations."""

    def test_compute_overall_default_weights(self):
        """Overall score computed with default weights."""
        assessment = Assessment(
            assessment_id="test",
            user_id="test",
            scoring_model_version="v1.0.0",
            created_at=now(),
            pronunciation=80.0,
            grammar=70.0,
            vocabulary=75.0,
            listening=65.0,
            fluency=70.0,
            confidence=60.0,
            coherence=75.0,
            relevance=80.0,
        )
        overall = assessment.compute_overall()
        # Should be weighted average
        expected = (
            80.0 * 0.20  # pronunciation
            + 70.0 * 0.15  # grammar
            + 75.0 * 0.15  # vocabulary
            + 65.0 * 0.15  # listening
            + 70.0 * 0.15  # fluency
            + 60.0 * 0.10  # confidence
            + 75.0 * 0.05  # coherence
            + 80.0 * 0.05  # relevance
        )
        assert abs(overall - expected) < 0.01

    def test_overall_to_level(self):
        """Overall score maps to correct level."""
        assert Assessment.overall_to_level(30) == 0  # Beginner
        assert Assessment.overall_to_level(45) == 1  # Intermediate
        assert Assessment.overall_to_level(60) == 2  # Advanced
        assert Assessment.overall_to_level(75) == 3  # Professional
        assert Assessment.overall_to_level(88) == 4  # Fluent
        assert Assessment.overall_to_level(95) == 5  # Native-like
