#!/usr/bin/env python3
"""Seed initial user data.

Creates the initial user (abu_ali) for testing.
Idempotent - safe to run multiple times.

Run with: uv run python scripts/seed_user.py
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.domain import User
from backend.persistence import UserRepository, init_database
from config.settings import get_settings


async def seed_users() -> None:
    """Seed initial users."""
    settings = get_settings()

    # Initialize database
    db = await init_database(settings.database.path)

    user_repo = UserRepository(db)

    # Define initial users
    users_to_create = [
        User(
            user_id="abu_ali",
            display_name="Abu Ali",
            created_at=datetime.now(timezone.utc),
            current_level=0,
            streak_days=0,
            settings={
                "preferred_mode": "free",
                "target_level": 3,
                "daily_goal_minutes": 15,
            },
        ),
    ]

    for user in users_to_create:
        existing = await user_repo.get(user.user_id)
        if existing:
            print(f"User '{user.user_id}' already exists (created {existing.created_at})")
        else:
            await user_repo.create(user)
            print(f"Created user '{user.user_id}' ({user.display_name})")

    # List all users
    all_users = await user_repo.list_all()
    print(f"\nTotal users: {len(all_users)}")
    for u in all_users:
        print(f"  - {u.user_id}: {u.display_name} (level {u.current_level})")

    await db.close()


def main() -> None:
    """Main entry point."""
    print("Seeding users...")
    print("=" * 40)
    asyncio.run(seed_users())
    print("=" * 40)
    print("Done!")


if __name__ == "__main__":
    main()
