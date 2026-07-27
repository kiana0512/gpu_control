#!/usr/bin/env python3
"""Submit and fully validate one real ImageClip sequence batch."""

import argparse
import hashlib
import io
import json
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://10.3.34.11")
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=3)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    return parser.parse_args()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_input(fixture: bytes, frames: int, external_id: str) -> tuple[bytes, dict[str, Any]]:
    rows = []
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for ordinal in range(frames):
            relative_path = f"episode_01/shot_010/frame_{ordinal:06d}.png"
            archive.writestr(relative_path, fixture, compress_type=zipfile.ZIP_STORED)
            rows.append(
                {
                    "ordinal": ordinal,
                    "relative_path": relative_path,
                    "size_bytes": len(fixture),
                    "sha256": digest(fixture),
                }
            )
    return archive_buffer.getvalue(), {
        "schema_version": "1.0",
        "external_batch_id": external_id,
        "failure_policy": "all_or_nothing",
        "output_naming": "preserve_stem_png",
        "parameters": {},
        "frames": rows,
    }


def validate_result(
    payload: bytes,
    artifact: dict[str, Any],
    response: httpx.Response,
    manifest: dict[str, Any],
    batch_id: str,
) -> dict[str, Any]:
    archive_sha = digest(payload)
    if archive_sha != artifact["sha256"]:
        raise ValueError("download SHA does not match artifact metadata")
    if response.headers.get("X-Artifact-SHA256") != archive_sha:
        raise ValueError("download SHA does not match response header")
    expected_outputs = {
        str(Path(frame["relative_path"]).with_suffix(".png")).replace("\\", "/")
        for frame in manifest["frames"]
    }
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        names = set(archive.namelist())
        expected_names = {"manifest.json"} | {
            f"results/{relative_path}" for relative_path in expected_outputs
        }
        if names != expected_names:
            raise ValueError("result archive entry set mismatch")
        result_manifest = json.loads(archive.read("manifest.json"))
        if result_manifest.get("batch_id") != batch_id:
            raise ValueError("result manifest batch_id mismatch")
        items = list(result_manifest.get("items", []))
        if result_manifest.get("total") != len(manifest["frames"]) or len(items) != len(
            manifest["frames"]
        ):
            raise ValueError("result manifest frame count mismatch")
        node_distribution: dict[str, int] = {}
        for ordinal, (source, item) in enumerate(zip(manifest["frames"], items, strict=True)):
            if item.get("ordinal") != ordinal:
                raise ValueError("result ordinals are not contiguous")
            if item.get("input_relative_path") != source["relative_path"]:
                raise ValueError("result input path mismatch")
            if item.get("input_sha256") != source["sha256"]:
                raise ValueError("result input SHA mismatch")
            output_path = str(Path(source["relative_path"]).with_suffix(".png")).replace(
                "\\", "/"
            )
            if item.get("output_relative_path") != output_path:
                raise ValueError("result output path mismatch")
            image_bytes = archive.read(f"results/{output_path}")
            if digest(image_bytes) != item.get("output_sha256"):
                raise ValueError("result frame SHA mismatch")
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.load()
                if (image.format or "").upper() != "PNG" or "A" not in image.getbands():
                    raise ValueError("result is not an RGBA PNG")
            node_id = str(item.get("node_id"))
            node_distribution[node_id] = node_distribution.get(node_id, 0) + 1
    return {
        "archive_sha256": archive_sha,
        "archive_size_bytes": len(payload),
        "validated_frames": len(manifest["frames"]),
        "node_distribution": node_distribution,
    }


def main() -> None:
    args = arguments()
    if not 1 <= args.frames <= 5000:
        raise ValueError("frames must be between 1 and 5000")
    credentials = json.loads(args.credentials.read_text(encoding="utf-8"))
    identity = credentials["clients"][0]
    fixture = args.fixture.read_bytes()
    external_id = f"loadtest:{args.run_id}:batch:g1"
    idempotency_key = external_id
    archive, manifest = build_input(fixture, args.frames, external_id)
    manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    started = datetime.now(UTC)
    headers = {"X-API-Key": str(identity["api_key"]), "Idempotency-Key": idempotency_key}
    timeout = httpx.Timeout(120, connect=15, write=3600)
    with httpx.Client(
        base_url=args.base_url,
        verify=str(args.ca),
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        request_files = {
            "archive": ("frames.zip", archive, "application/zip"),
            "manifest": (None, manifest_json),
        }
        created = client.post(
            "/api/v1/batches/imageclip-rgba", headers=headers, files=request_files
        )
        created.raise_for_status()
        if created.status_code != 202:
            raise ValueError(f"first create returned {created.status_code}, expected 202")
        created_payload = created.json()
        batch_id = str(created_payload["batch_id"])

        replay = client.post(
            "/api/v1/batches/imageclip-rgba", headers=headers, files=request_files
        )
        replay.raise_for_status()
        if replay.status_code != 200 or replay.json().get("batch_id") != batch_id:
            raise ValueError("idempotent replay did not return the original batch")

        deadline = time.monotonic() + args.timeout_seconds
        snapshots = []
        final: dict[str, Any] | None = None
        last_progress = 0.0
        while time.monotonic() < deadline:
            response = client.get(f"/api/v1/batches/{batch_id}", headers=headers)
            response.raise_for_status()
            state = response.json()
            progress = float(state["progress"])
            if progress + 0.000001 < last_progress:
                raise ValueError(
                    f"batch progress regressed from {last_progress} to {progress}"
                )
            last_progress = progress
            snapshots.append(
                {
                    "captured_at": datetime.now(UTC).isoformat(),
                    "status": state["status"],
                    "progress": progress,
                    "counts": state["counts"],
                }
            )
            print(
                f"batch={batch_id} status={state['status']} progress={state['progress']} "
                f"counts={state['counts']}"
            )
            if state["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                final = state
                break
            time.sleep(args.poll_seconds)
        if final is None:
            raise TimeoutError("batch did not reach a terminal state")
        if final["status"] != "SUCCEEDED":
            raise RuntimeError(f"batch ended as {final['status']}: {final.get('error')}")
        artifacts = [row for row in final["artifacts"] if row["kind"] == "result_archive"]
        if len(artifacts) != 1:
            raise ValueError("batch did not expose exactly one result archive")
        artifact = artifacts[0]
        download = client.get(str(artifact["download_url"]), headers=headers)
        download.raise_for_status()
        validation = validate_result(download.content, artifact, download, manifest, batch_id)

    report = {
        "run_id": args.run_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "batch_id": batch_id,
        "external_batch_id": external_id,
        "frames": args.frames,
        "create_status": created.status_code,
        "idempotent_replay_status": replay.status_code,
        "final": final,
        "validation": validation,
        "snapshots": snapshots,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"batch_id": batch_id, **validation}, ensure_ascii=False))


if __name__ == "__main__":
    main()
