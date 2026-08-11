import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.create_admin import UserAlreadyExists, create_admin, main

from app.config import get_settings
from app.services.auth import authenticate_user


async def test_create_admin_creates_an_account_verified_by_the_auth_service(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await create_admin(conn, "admin", "bootstrap-secret", is_admin=True)

        user = await authenticate_user(conn, "admin", "bootstrap-secret")

    assert user["username"] == "admin"
    assert user["is_admin"] is True


def test_cli_reads_password_from_environment_and_returns_nonzero_for_a_duplicate(
    monkeypatch, migrated_db: str
):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("ADMIN_PASSWORD", "environment-secret")
    monkeypatch.setattr(sys, "argv", ["create_admin.py", "admin", "--admin"])
    get_settings.cache_clear()

    assert main() == 0
    assert main() != 0


async def test_create_admin_refuses_to_overwrite_an_existing_username(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await create_admin(conn, "admin", "first-secret", is_admin=True)

        with pytest.raises(UserAlreadyExists, match="이미 존재"):
            await create_admin(conn, "admin", "replacement-secret", is_admin=True)

        user = await authenticate_user(conn, "admin", "first-secret")

    assert user["username"] == "admin"
