#!/usr/bin/env python3
import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import ApiClient
from packages.gpu_control_core.security import hash_password
from packages.gpu_control_core.settings import get_settings


async def create(username: str, password: str, ensure: bool = False) -> bool:
    db = Database(get_settings())
    async with db.session() as session:
        existing = await session.scalar(select(ApiClient).where(ApiClient.name == username))
        if existing:
            if ensure:
                return False
            raise ValueError("administrator already exists")
        session.add(
            ApiClient(
                id=username,
                name=username,
                role="admin",
                password_hash=hash_password(password),
                max_queued=100,
                max_running=3,
            )
        )
        await session.commit()
    await db.close()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="创建首个 GPU Control 管理员")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--ensure", action="store_true")
    args = parser.parse_args()
    password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else getpass.getpass(
        "管理员密码（至少 12 位）: "
    )
    if len(password) < 12:
        raise SystemExit("密码至少 12 位")
    created = asyncio.run(create(args.username, password, args.ensure))
    print("管理员创建成功" if created else "管理员已存在，保持原密码")


if __name__ == "__main__":
    main()
