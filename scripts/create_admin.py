#!/usr/bin/env python3
"""자체 가입이 없는 설치에서 최초 계정을 만드는 부트스트랩 CLI."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import get_settings
from app.services.auth import hash_password


class UserAlreadyExists(Exception):
    """부트스트랩이 기존 계정을 덮어쓰려 할 때 발생한다."""


async def create_admin(
    conn: psycopg.AsyncConnection,
    username: str,
    password: str,
    *,
    is_admin: bool,
) -> None:
    try:
        await conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, %s)",
            (username, hash_password(password), is_admin),
        )
    except psycopg.errors.UniqueViolation as error:
        raise UserAlreadyExists(f"사용자명 '{username}'은 이미 존재합니다.") from error


async def run(username: str, password: str, *, is_admin: bool) -> None:
    async with await psycopg.AsyncConnection.connect(
        get_settings().database_url, autocommit=True
    ) as conn:
        await create_admin(conn, username, password, is_admin=is_admin)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenArchive 최초 계정을 생성합니다.")
    parser.add_argument("username")
    parser.add_argument("--admin", action="store_true", help="관리자 권한을 부여합니다.")
    args = parser.parse_args()
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("비밀번호: ")
    if not password:
        print("계정을 만들려면 비밀번호가 필요합니다.", file=sys.stderr)
        return 2
    try:
        asyncio.run(run(args.username, password, is_admin=args.admin))
    except UserAlreadyExists as error:
        print(error, file=sys.stderr)
        return 1
    except psycopg.Error as error:
        print(f"계정 생성 실패: {error}", file=sys.stderr)
        return 1
    print(f"사용자 '{args.username}'을 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
