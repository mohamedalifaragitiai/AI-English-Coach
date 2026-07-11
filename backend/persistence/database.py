"""SQLite database connection with WAL mode.

Single-file, zero-server database - perfect for one host.
WAL mode enables concurrent reads while writing.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from backend.core.logging import get_logger

logger = get_logger(__name__)


class Database:
    """Async SQLite database manager.

    Usage:
        db = Database(path)
        await db.initialize()

        async with db.connection() as conn:
            await conn.execute("SELECT ...")

        await db.close()
    """

    def __init__(self, path: Path | str) -> None:
        """Initialize database manager.

        Args:
            path: Path to SQLite database file
        """
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the database and run migrations."""
        # Ensure directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Open connection
        self._connection = await aiosqlite.connect(self.path)

        # Enable WAL mode for concurrent reads
        await self._connection.execute("PRAGMA journal_mode=WAL")

        # Enable foreign keys
        await self._connection.execute("PRAGMA foreign_keys=ON")

        # Optimize for performance
        await self._connection.execute("PRAGMA synchronous=NORMAL")
        await self._connection.execute("PRAGMA cache_size=-64000")  # 64MB cache

        # Row factory for dict-like access
        self._connection.row_factory = aiosqlite.Row

        # Run migrations
        await self._run_migrations()

        logger.info("database_initialized", path=str(self.path))

    async def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("database_closed")

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Get a database connection.

        For single-connection mode (recommended for SQLite),
        returns the shared connection.
        """
        if self._connection is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        async with self._lock:
            yield self._connection

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Execute within a transaction."""
        async with self.connection() as conn:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def _run_migrations(self) -> None:
        """Run database migrations."""
        async with self.connection() as conn:
            # Create migrations table if not exists
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Get applied migrations
            cursor = await conn.execute("SELECT name FROM _migrations")
            applied = {row[0] for row in await cursor.fetchall()}

            # Apply pending migrations
            for name, sql in MIGRATIONS:
                if name not in applied:
                    logger.info("applying_migration", name=name)
                    await conn.executescript(sql)
                    await conn.execute(
                        "INSERT INTO _migrations (name) VALUES (?)",
                        (name,),
                    )
                    await conn.commit()
                    logger.info("migration_applied", name=name)


# Migrations - append only, never modify existing
MIGRATIONS = [
    (
        "001_initial_schema",
        """
        -- Users own everything. user_id is a stable slug like 'abu_ali'.
        CREATE TABLE IF NOT EXISTS users (
            user_id        TEXT PRIMARY KEY,
            display_name   TEXT NOT NULL,
            created_at     TEXT NOT NULL,
            current_level  INTEGER NOT NULL DEFAULT 0,
            streak_days    INTEGER NOT NULL DEFAULT 0,
            settings_json  TEXT
        );

        -- Sessions for practice sittings
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL REFERENCES users(user_id),
            mode         TEXT NOT NULL,
            started_at   TEXT NOT NULL,
            ended_at     TEXT,
            difficulty   REAL DEFAULT 0.5
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user_time ON sessions(user_id, started_at);

        -- Utterances - atomic speech units
        CREATE TABLE IF NOT EXISTS utterances (
            utterance_id   TEXT PRIMARY KEY,
            session_id     TEXT NOT NULL REFERENCES sessions(session_id),
            user_id        TEXT NOT NULL REFERENCES users(user_id),
            role           TEXT NOT NULL,
            audio_path     TEXT,
            transcript     TEXT,
            stt_confidence REAL,
            start_ms       INTEGER,
            end_ms         INTEGER,
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_utt_session ON utterances(session_id);
        CREATE INDEX IF NOT EXISTS idx_utt_user ON utterances(user_id);

        -- Aggregated, versioned scores. Never overwrite; append only.
        CREATE TABLE IF NOT EXISTS assessments (
            assessment_id         TEXT PRIMARY KEY,
            user_id               TEXT NOT NULL REFERENCES users(user_id),
            session_id            TEXT REFERENCES sessions(session_id),
            utterance_id          TEXT REFERENCES utterances(utterance_id),
            scoring_model_version TEXT NOT NULL,
            pronunciation         REAL,
            grammar               REAL,
            vocabulary            REAL,
            listening             REAL,
            fluency               REAL,
            confidence            REAL,
            coherence             REAL,
            relevance             REAL,
            overall               REAL,
            created_at            TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_assess_user_time ON assessments(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_assess_session ON assessments(session_id);

        -- Raw evaluator payloads, kept separate for recompute & audit.
        CREATE TABLE IF NOT EXISTS evaluator_outputs (
            id            TEXT PRIMARY KEY,
            utterance_id  TEXT REFERENCES utterances(utterance_id),
            evaluator     TEXT NOT NULL,
            version       TEXT NOT NULL,
            payload_json  TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_eval_utterance ON evaluator_outputs(utterance_id);

        -- Point-in-time gap vector for tracking improvement
        CREATE TABLE IF NOT EXISTS gap_snapshots (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(user_id),
            taken_at    TEXT NOT NULL,
            gaps_json   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gaps_user ON gap_snapshots(user_id, taken_at);

        -- Learning plans
        CREATE TABLE IF NOT EXISTS plans (
            plan_id    TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(user_id),
            created_at TEXT NOT NULL,
            horizon    TEXT,
            plan_json  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_plans_user ON plans(user_id);

        -- Generated reports
        CREATE TABLE IF NOT EXISTS reports (
            report_id  TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(user_id),
            period     TEXT NOT NULL,
            created_at TEXT NOT NULL,
            format     TEXT,
            path       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id);

        -- Achievements/badges
        CREATE TABLE IF NOT EXISTS achievements (
            id        TEXT PRIMARY KEY,
            user_id   TEXT NOT NULL REFERENCES users(user_id),
            code      TEXT NOT NULL,
            earned_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_achievements_user ON achievements(user_id);
        """,
    ),
]


# Global database instance
_db: Database | None = None


async def get_database() -> Database:
    """Get the global database instance."""
    global _db
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db


async def init_database(path: Path | str) -> Database:
    """Initialize the global database instance."""
    global _db
    _db = Database(path)
    await _db.initialize()
    return _db


async def close_database() -> None:
    """Close the global database instance."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
