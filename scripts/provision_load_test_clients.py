#!/usr/bin/env python3
"""Provision or disable isolated synthetic API clients for a real GPU load run."""

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from sqlalchemy import select

from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import ApiClient, ApiKey, RateLimitPolicy
from packages.gpu_control_core.security import hash_api_secret, issue_api_key
from packages.gpu_control_core.settings import Settings


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--clients", type=int, default=12)
    parser.add_argument("--max-queued", type=int, default=100)
    parser.add_argument("--max-running", type=int, default=1)
    parser.add_argument("--daily-quota", type=int, default=100_000)
    parser.add_argument("--requests-per-second", type=float, default=20)
    parser.add_argument("--burst", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disable", action="store_true")
    return parser.parse_args()


async def provision(args: argparse.Namespace) -> None:
    if not 1 <= args.clients <= 100:
        raise ValueError("clients must be between 1 and 100")
    if not args.run_id.replace("-", "").isalnum():
        raise ValueError("run-id must contain only letters, numbers and hyphens")
    settings = Settings()
    database = Database(settings)
    credentials: list[dict[str, str]] = []
    try:
        async with database.session() as session:
            for index in range(1, args.clients + 1):
                client_id = f"loadtest-{args.run_id}-{index:02d}"
                client = await session.get(ApiClient, client_id, with_for_update=True)
                if client is not None and client.client_kind != "test":
                    raise RuntimeError(f"refusing to overwrite non-test client {client_id}")
                if client is None:
                    client = ApiClient(
                        id=client_id,
                        name=f"极限压测 {args.run_id} / {index:02d}",
                        role="client",
                        client_kind="test",
                    )
                    session.add(client)
                client.enabled = True
                client.max_queued = args.max_queued
                client.max_running = args.max_running
                client.daily_quota = args.daily_quota
                client.weight = 1
                client.allowed_ips = []
                client.callback_hosts = []
                policy = await session.scalar(
                    select(RateLimitPolicy).where(
                        RateLimitPolicy.client_id == client_id
                    )
                )
                if policy is None:
                    policy = RateLimitPolicy(client_id=client_id)
                    session.add(policy)
                policy.requests_per_second = args.requests_per_second
                policy.burst = args.burst
                plaintext, prefix, secret = issue_api_key()
                session.add(
                    ApiKey(
                        id=str(uuid.uuid4()),
                        client_id=client_id,
                        prefix=prefix,
                        secret_hash=hash_api_secret(secret, settings.api_key_pepper),
                    )
                )
                credentials.append({"client_id": client_id, "api_key": plaintext})
            await session.commit()
    finally:
        await database.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(
            {"run_id": args.run_id, "clients": credentials},
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")
    os.chmod(args.output, 0o600)
    print(f"provisioned {len(credentials)} isolated test clients for {args.run_id}")


async def disable(args: argparse.Namespace) -> None:
    payload = json.loads(args.output.read_text(encoding="utf-8"))
    client_ids = [str(item["client_id"]) for item in payload["clients"]]
    settings = Settings()
    database = Database(settings)
    try:
        async with database.session() as session:
            clients = list(
                (
                    await session.scalars(
                        select(ApiClient).where(ApiClient.id.in_(client_ids))
                    )
                ).all()
            )
            if any(client.client_kind != "test" for client in clients):
                raise RuntimeError("refusing to disable a non-test client")
            for client in clients:
                client.enabled = False
            keys = list(
                (
                    await session.scalars(
                        select(ApiKey).where(ApiKey.client_id.in_(client_ids))
                    )
                ).all()
            )
            for key in keys:
                key.enabled = False
            await session.commit()
    finally:
        await database.close()
    print(f"disabled {len(client_ids)} test clients and their API keys")


async def main() -> None:
    args = arguments()
    if args.disable:
        await disable(args)
    else:
        await provision(args)


if __name__ == "__main__":
    asyncio.run(main())
