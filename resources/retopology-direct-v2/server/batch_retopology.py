#!/usr/bin/env python3
"""Batch multiple independent FBX uploads through the one-click skill adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def safe_stem(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or fallback


def valid_batch_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", value):
        raise argparse.ArgumentTypeError(
            "batch id must contain only ASCII letters, digits, dot, underscore, or hyphen"
        )
    return value


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch independent FBX retopology jobs")
    parser.add_argument("--input", action="append", type=Path, required=True, help="source FBX; repeatable")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--job-root", type=Path, default=Path("jobs"))
    parser.add_argument("--batch-id", type=valid_batch_id, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=7200, help="timeout for each FBX")
    parser.add_argument("--package-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    sources = [path.resolve() for path in args.input]
    if len(set(sources)) != len(sources):
        raise SystemExit("the same FBX input was supplied more than once")
    for source in sources:
        if source.suffix.lower() != ".fbx" or not source.is_file() or source.stat().st_size == 0:
            raise SystemExit(f"each input must be a non-empty FBX file: {source}")

    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"output directory must be empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    package_root = args.package_root.resolve()
    one_click = package_root / "server" / "one_click_retopology.py"
    if not one_click.is_file():
        raise SystemExit(f"one-click entrypoint is missing: {one_click}")

    batch_id = args.batch_id or f"retopo-batch-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    job_root = args.job_root.resolve()
    result_dir = output_dir / "results"
    log_dir = output_dir / "logs"
    result_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    used_names: set[str] = set()
    items: list[dict] = []
    for index, source in enumerate(sources, start=1):
        stem = safe_stem(source.stem, f"asset-{index:03d}")
        base_name = stem
        suffix = 2
        while stem.casefold() in used_names:
            stem = f"{base_name}-{suffix}"
            suffix += 1
        used_names.add(stem.casefold())

        job_id = f"{batch_id}-{index:03d}"
        output = result_dir / f"{index:03d}_{stem}_retopology.blend"
        stdout_log = log_dir / f"{index:03d}_{stem}.stdout.log"
        stderr_log = log_dir / f"{index:03d}_{stem}.stderr.log"
        command = [
            sys.executable,
            str(one_click),
            "--input",
            str(source),
            "--output",
            str(output),
            "--job-root",
            str(job_root),
            "--job-id",
            job_id,
            "--timeout-seconds",
            str(args.timeout_seconds),
            "--package-root",
            str(package_root),
        ]
        with stdout_log.open("w", encoding="utf-8", newline="\n") as stdout, stderr_log.open(
            "w", encoding="utf-8", newline="\n"
        ) as stderr:
            completed = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)

        child_result_path = job_root / job_id / "result.json"
        child_result = read_json(child_result_path)
        succeeded = (
            completed.returncode == 0
            and child_result.get("status") == "generated_for_user_inspection"
            and output.is_file()
        )
        item = {
            "index": index,
            "source_filename": source.name,
            "input_sha256": sha256(source),
            "job_id": job_id,
            "status": "generated_for_user_inspection" if succeeded else "failed",
            "automatic_retry": False,
        }
        if succeeded:
            item.update(
                {
                    "output": output.relative_to(output_dir).as_posix(),
                    "output_sha256": sha256(output),
                    "assets": child_result.get("assets", []),
                }
            )
        else:
            item.update(
                {
                    "error": child_result.get("error") or f"one_click_exit_{completed.returncode}",
                    "stdout_log": stdout_log.relative_to(output_dir).as_posix(),
                    "stderr_log": stderr_log.relative_to(output_dir).as_posix(),
                }
            )
        items.append(item)

    success_count = sum(item["status"] == "generated_for_user_inspection" for item in items)
    if success_count == len(items):
        status = "generated_for_user_inspection"
    elif success_count:
        status = "partial_failure"
    else:
        status = "failed"

    archive_path = output_dir / "batch-results.zip"
    report_path = output_dir / "batch_report.json"
    report = {
        "batch_id": batch_id,
        "status": status,
        "item_count": len(items),
        "success_count": success_count,
        "failure_count": len(items) - success_count,
        "archive": archive_path.name,
        "automatic_post_generation_review": False,
        "automatic_retry": False,
        "items": items,
    }
    atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(report_path, report_path.name)
        for item in items:
            if item["status"] == "generated_for_user_inspection":
                output = output_dir / item["output"]
                archive.write(output, item["output"])
            else:
                for field in ("stdout_log", "stderr_log"):
                    log_path = output_dir / item[field]
                    archive.write(log_path, item[field])

    print(
        json.dumps(
            {**report, "archive_sha256": sha256(archive_path)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status == "generated_for_user_inspection" else 2


if __name__ == "__main__":
    sys.exit(main())
