"""Repository pattern for data access.

Repositories provide async CRUD operations and domain-specific queries.
All datetime serialization uses ISO format strings for SQLite storage.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.core.logging import get_logger
from backend.domain import (
    Assessment,
    GapSnapshot,
    Session,
    SessionMode,
    User,
    Utterance,
    UtteranceRole,
)
from backend.persistence.database import Database

logger = get_logger(__name__)


def generate_id() -> str:
    """Generate a unique ID."""
    return str(uuid.uuid4())


def now_iso() -> str:
    """Get current time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(s: str | None) -> datetime | None:
    """Parse ISO datetime string."""
    if s is None:
        return None
    return datetime.fromisoformat(s)


class UserRepository:
    """Repository for User aggregate."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, user: User) -> User:
        """Create a new user."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id, display_name, created_at, current_level, streak_days, settings_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user.user_id,
                    user.display_name,
                    user.created_at.isoformat(),
                    user.current_level,
                    user.streak_days,
                    json.dumps(user.settings) if user.settings else None,
                ),
            )
        logger.info("user_created", user_id=user.user_id)
        return user

    async def get(self, user_id: str) -> User | None:
        """Get a user by ID."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_user(row)

    async def update(self, user: User) -> User:
        """Update an existing user."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                UPDATE users
                SET display_name = ?, current_level = ?, streak_days = ?, settings_json = ?
                WHERE user_id = ?
                """,
                (
                    user.display_name,
                    user.current_level,
                    user.streak_days,
                    json.dumps(user.settings) if user.settings else None,
                    user.user_id,
                ),
            )
        logger.info("user_updated", user_id=user.user_id)
        return user

    async def list_all(self) -> list[User]:
        """List all users."""
        async with self.db.connection() as conn:
            cursor = await conn.execute("SELECT * FROM users ORDER BY created_at")
            rows = await cursor.fetchall()
            return [self._row_to_user(row) for row in rows]

    async def delete(self, user_id: str) -> bool:
        """Delete a user and all related data."""
        async with self.db.transaction() as conn:
            # Delete in correct order for foreign keys
            await conn.execute("DELETE FROM achievements WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM reports WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM plans WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM gap_snapshots WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM assessments WHERE user_id = ?", (user_id,))
            await conn.execute(
                """
                DELETE FROM evaluator_outputs WHERE utterance_id IN
                (SELECT utterance_id FROM utterances WHERE user_id = ?)
                """,
                (user_id,),
            )
            await conn.execute("DELETE FROM utterances WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            result = await conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            deleted = result.rowcount > 0
        if deleted:
            logger.info("user_deleted", user_id=user_id)
        return deleted

    async def update_streak(self, user_id: str) -> int:
        """Update streak based on practice days. Returns new streak."""
        async with self.db.connection() as conn:
            # Get distinct practice days in last 30 days
            cursor = await conn.execute(
                """
                SELECT DISTINCT date(started_at) as practice_date
                FROM sessions
                WHERE user_id = ? AND started_at >= date('now', '-30 days')
                ORDER BY practice_date DESC
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
            dates = [row[0] for row in rows]

            if not dates:
                streak = 0
            else:
                # Count consecutive days from today/yesterday
                today = datetime.now(timezone.utc).date().isoformat()
                yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

                # Streak starts if practiced today or yesterday
                if dates[0] not in (today, yesterday):
                    streak = 0
                else:
                    streak = 1
                    for i in range(1, len(dates)):
                        prev = datetime.fromisoformat(dates[i - 1]).date()
                        curr = datetime.fromisoformat(dates[i]).date()
                        if (prev - curr).days == 1:
                            streak += 1
                        else:
                            break

        # Update user
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE users SET streak_days = ? WHERE user_id = ?",
                (streak, user_id),
            )

        return streak

    def _row_to_user(self, row: Any) -> User:
        """Convert database row to User object."""
        return User(
            user_id=row["user_id"],
            display_name=row["display_name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            current_level=row["current_level"],
            streak_days=row["streak_days"],
            settings=json.loads(row["settings_json"]) if row["settings_json"] else {},
        )


class SessionRepository:
    """Repository for Session aggregate."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, session: Session) -> Session:
        """Create a new session."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (session_id, user_id, mode, started_at, ended_at, difficulty)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.mode.value,
                    session.started_at.isoformat(),
                    session.ended_at.isoformat() if session.ended_at else None,
                    session.difficulty,
                ),
            )
        logger.info("session_created", session_id=session.session_id, user_id=session.user_id)
        return session

    async def get(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_session(row)

    async def end_session(self, session_id: str) -> Session | None:
        """End a session by setting ended_at."""
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                (now_iso(), session_id),
            )
        return await self.get(session_id)

    async def get_user_sessions(
        self,
        user_id: str,
        limit: int = 50,
        since: datetime | None = None,
    ) -> list[Session]:
        """Get sessions for a user."""
        async with self.db.connection() as conn:
            if since:
                cursor = await conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE user_id = ? AND started_at >= ?
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (user_id, since.isoformat(), limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE user_id = ?
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                )
            rows = await cursor.fetchall()
            return [self._row_to_session(row) for row in rows]

    async def get_active_session(self, user_id: str) -> Session | None:
        """Get the active (not ended) session for a user."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM sessions
                WHERE user_id = ? AND ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_session(row)

    def _row_to_session(self, row: Any) -> Session:
        """Convert database row to Session object."""
        return Session(
            session_id=row["session_id"],
            user_id=row["user_id"],
            mode=SessionMode(row["mode"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=parse_datetime(row["ended_at"]),
            difficulty=row["difficulty"] or 0.5,
        )


class UtteranceRepository:
    """Repository for Utterance aggregate."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, utterance: Utterance) -> Utterance:
        """Create a new utterance."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO utterances
                (utterance_id, session_id, user_id, role, audio_path, transcript,
                 stt_confidence, start_ms, end_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utterance.utterance_id,
                    utterance.session_id,
                    utterance.user_id,
                    utterance.role.value,
                    utterance.audio_path,
                    utterance.transcript,
                    utterance.stt_confidence,
                    utterance.start_ms,
                    utterance.end_ms,
                    utterance.created_at.isoformat(),
                ),
            )
        return utterance

    async def get(self, utterance_id: str) -> Utterance | None:
        """Get an utterance by ID."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM utterances WHERE utterance_id = ?",
                (utterance_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_utterance(row)

    async def get_session_utterances(self, session_id: str) -> list[Utterance]:
        """Get all utterances for a session."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM utterances
                WHERE session_id = ?
                ORDER BY created_at
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_utterance(row) for row in rows]

    def _row_to_utterance(self, row: Any) -> Utterance:
        """Convert database row to Utterance object."""
        return Utterance(
            utterance_id=row["utterance_id"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            role=UtteranceRole(row["role"]),
            audio_path=row["audio_path"],
            transcript=row["transcript"],
            stt_confidence=row["stt_confidence"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class AssessmentRepository:
    """Repository for Assessment aggregate."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, assessment: Assessment) -> Assessment:
        """Create a new assessment. Never overwrites - append only."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO assessments
                (assessment_id, user_id, session_id, utterance_id, scoring_model_version,
                 pronunciation, grammar, vocabulary, listening, fluency, confidence,
                 coherence, relevance, overall, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment.assessment_id,
                    assessment.user_id,
                    assessment.session_id,
                    assessment.utterance_id,
                    assessment.scoring_model_version,
                    assessment.pronunciation,
                    assessment.grammar,
                    assessment.vocabulary,
                    assessment.listening,
                    assessment.fluency,
                    assessment.confidence,
                    assessment.coherence,
                    assessment.relevance,
                    assessment.overall,
                    assessment.created_at.isoformat(),
                ),
            )
        logger.info(
            "assessment_created",
            assessment_id=assessment.assessment_id,
            user_id=assessment.user_id,
        )
        return assessment

    async def get(self, assessment_id: str) -> Assessment | None:
        """Get an assessment by ID."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM assessments WHERE assessment_id = ?",
                (assessment_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_assessment(row)

    async def get_user_assessments(
        self,
        user_id: str,
        since: datetime | None = None,
        scoring_version: str | None = None,
        limit: int = 100,
    ) -> list[Assessment]:
        """Get assessments for a user with optional filters."""
        conditions = ["user_id = ?"]
        params: list[Any] = [user_id]

        if since:
            conditions.append("created_at >= ?")
            params.append(since.isoformat())

        if scoring_version:
            conditions.append("scoring_model_version = ?")
            params.append(scoring_version)

        params.append(limit)

        async with self.db.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT * FROM assessments
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            )
            rows = await cursor.fetchall()
            return [self._row_to_assessment(row) for row in rows]

    async def get_skill_trend(
        self,
        user_id: str,
        skill: str,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[tuple[datetime, float]]:
        """Get trend data for a specific skill.

        Returns list of (datetime, score) tuples for charting.
        """
        if skill not in (
            "pronunciation",
            "grammar",
            "vocabulary",
            "listening",
            "fluency",
            "confidence",
            "coherence",
            "relevance",
            "overall",
        ):
            raise ValueError(f"Invalid skill: {skill}")

        conditions = [f"user_id = ?", f"{skill} IS NOT NULL"]
        params: list[Any] = [user_id]

        if since:
            conditions.append("created_at >= ?")
            params.append(since.isoformat())

        params.append(limit)

        async with self.db.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT created_at, {skill}
                FROM assessments
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at ASC
                LIMIT ?
                """,
                params,
            )
            rows = await cursor.fetchall()
            return [(datetime.fromisoformat(row[0]), row[1]) for row in rows]

    async def get_latest_assessment(self, user_id: str) -> Assessment | None:
        """Get the most recent assessment for a user."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM assessments
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_assessment(row)

    async def get_session_assessment(self, session_id: str) -> Assessment | None:
        """Get the session-level assessment."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM assessments
                WHERE session_id = ? AND utterance_id IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_assessment(row)

    def _row_to_assessment(self, row: Any) -> Assessment:
        """Convert database row to Assessment object."""
        return Assessment(
            assessment_id=row["assessment_id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            utterance_id=row["utterance_id"],
            scoring_model_version=row["scoring_model_version"],
            pronunciation=row["pronunciation"],
            grammar=row["grammar"],
            vocabulary=row["vocabulary"],
            listening=row["listening"],
            fluency=row["fluency"],
            confidence=row["confidence"],
            coherence=row["coherence"],
            relevance=row["relevance"],
            overall=row["overall"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class GapSnapshotRepository:
    """Repository for GapSnapshot aggregate."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, snapshot: GapSnapshot) -> GapSnapshot:
        """Create a new gap snapshot."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO gap_snapshots (id, user_id, taken_at, gaps_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.id,
                    snapshot.user_id,
                    snapshot.taken_at.isoformat(),
                    json.dumps(snapshot.gaps),
                ),
            )
        return snapshot

    async def get_latest(self, user_id: str) -> GapSnapshot | None:
        """Get the latest gap snapshot for a user."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM gap_snapshots
                WHERE user_id = ?
                ORDER BY taken_at DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return GapSnapshot(
                id=row["id"],
                user_id=row["user_id"],
                taken_at=datetime.fromisoformat(row["taken_at"]),
                gaps=json.loads(row["gaps_json"]),
            )

    async def get_at_date(self, user_id: str, date: datetime) -> GapSnapshot | None:
        """Get gap snapshot closest to a specific date."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM gap_snapshots
                WHERE user_id = ? AND taken_at <= ?
                ORDER BY taken_at DESC
                LIMIT 1
                """,
                (user_id, date.isoformat()),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return GapSnapshot(
                id=row["id"],
                user_id=row["user_id"],
                taken_at=datetime.fromisoformat(row["taken_at"]),
                gaps=json.loads(row["gaps_json"]),
            )
