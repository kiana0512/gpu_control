import asyncio
import hashlib
import json
import logging
import os
import shutil
import socket
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.gpu_control_core.security import sign_agent_request

LOG = logging.getLogger("gpu_control_blender_worker")

UV_UNWRAP_SCRIPT_SHA256 = "ebfa3546d61c548a11c0e7561c75f93b6ef93308d8da9f27788bf35643303758"
UV_QA_SCRIPT_SHA256 = "bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d"
RETOPOLOGY_AUDIT_SCRIPT_SHA256 = (
    "a6575902cfacd7b8106f9c887069d717a880d870fc48a6295431cdcf717a9dc4"
)
RETOPOLOGY_PROCESS_SCRIPT_SHA256 = (
    "4c843d7c45c8665ff5e3ded8db6db1b1344deb16c6b7ffd71b7a6c3ec1b184a0"
)
RETOPOLOGY_RENDER_SCRIPT_SHA256 = (
    "b1b6344ec78a7c1d333cc875c0eeee20087df27878d67c28fa413f9ab3dcdf09"
)


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    asset_api_url: str = "http://asset-api:8010"
    asset_worker_id: str = Field(
        default="asset-worker-local",
        pattern=r"^asset-(?:control|worker)-[a-z0-9-]+$",
        max_length=64,
    )
    asset_node_id: str = Field(
        default="worker-local",
        pattern=r"^(?:control|worker)-[a-z0-9-]+$",
        max_length=64,
    )
    asset_worker_display_name: str = "Local Blender Worker"
    asset_worker_hmac_secret: str = Field(min_length=32)
    asset_worker_max_concurrency: int = 2
    blender_binary: str = "/opt/blender/blender"
    blender_version: str = "5.1.2"
    blender_skill_version: str = "asset-skills-2026.07.28"
    uv_skill_root: Path = Path("/opt/codex/skills/blender-pbr-uv")
    retopology_skill_root: Path = Path(
        "/opt/codex/skills/blender-retopology-compare-iterate"
    )
    retopology_process_script: Path = Path(
        "/app/packages/asset_processing/blender_retopology_process.py"
    )
    retopology_render_script: Path = Path(
        "/app/packages/asset_processing/blender_retopology_render.py"
    )
    codex_binary: str = "/usr/local/bin/codex"
    codex_auth_source: Path = Path("/run/secrets/codex-auth.json")
    asset_poll_seconds: float = 1.0


def available_memory_mb() -> int:
    for line in Path("/proc/meminfo").read_text("utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def signed_headers(settings: WorkerSettings, method: str, path: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = sign_agent_request(
        method, path, body, timestamp, nonce, settings.asset_worker_hmac_secret
    )
    return {
        "Content-Type": "application/json",
        "X-Asset-Timestamp": timestamp,
        "X-Asset-Nonce": nonce,
        "X-Asset-Signature": signature,
    }


async def signed_post(
    client: httpx.AsyncClient, settings: WorkerSettings, path: str, payload: dict[str, Any]
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return await client.post(
        path, content=body, headers=signed_headers(settings, "POST", path, body)
    )


async def heartbeat(
    client: httpx.AsyncClient, settings: WorkerSettings, running: int
) -> None:
    payload = {
        "worker_id": settings.asset_worker_id,
        "node_id": settings.asset_node_id,
        "display_name": settings.asset_worker_display_name,
        "hostname": socket.gethostname(),
        "blender_version": settings.blender_version,
        "skill_version": settings.blender_skill_version,
        "cpu_count": os.cpu_count() or 1,
        "max_concurrency": settings.asset_worker_max_concurrency,
        "current_jobs": running,
        "load_1m": os.getloadavg()[0],
        "available_memory_mb": available_memory_mb(),
    }
    response = await signed_post(
        client, settings, "/internal/v1/assets/workers/heartbeat", payload
    )
    response.raise_for_status()


def verified_script(path: Path, expected_sha256: str) -> Path:
    if not path.is_file():
        raise RuntimeError(f"required Skill script is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise RuntimeError(f"Skill script SHA-256 mismatch: {path}")
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def extract_retopology_bundle(bundle: Path, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=False)
    destination_root = destination.resolve()
    with zipfile.ZipFile(bundle) as archive:
        for item in archive.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination_root):
                raise RuntimeError(f"unsafe retopology bundle member: {item.filename}")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    manifest_path = destination / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if manifest.get("schema_version") != "retopology_input.v1":
        raise RuntimeError("retopology input manifest schema is invalid")
    project = manifest.get("project")
    if not isinstance(project, dict):
        raise RuntimeError("retopology input manifest is missing project")
    project_path = destination / str(project.get("filename", ""))
    if not project_path.is_file() or file_sha256(project_path) != project.get("sha256"):
        raise RuntimeError("retopology project SHA-256 mismatch")
    references = manifest.get("reference_views", [])
    if not isinstance(references, list):
        raise RuntimeError("retopology reference view manifest is invalid")
    for reference in references:
        if not isinstance(reference, dict):
            raise RuntimeError("retopology reference view entry is invalid")
        path = destination / "references" / str(reference.get("filename", ""))
        if not path.is_file() or file_sha256(path) != reference.get("sha256"):
            raise RuntimeError(f"reference image SHA-256 mismatch: {path.name}")
    return manifest


def contact_sheet(
    sources: list[tuple[str, Path]],
    output: Path,
    *,
    columns: int,
    cell_size: int,
) -> None:
    if not sources:
        raise RuntimeError("cannot create an empty contact sheet")
    rows = (len(sources) + columns - 1) // columns
    label_height = 28
    sheet = Image.new(
        "RGB", (columns * cell_size, rows * (cell_size + label_height)), (8, 10, 18)
    )
    draw = ImageDraw.Draw(sheet)
    for index, (label, source) in enumerate(sources):
        row, column = divmod(index, columns)
        with Image.open(source) as opened:
            image = opened.convert("RGBA")
            image.thumbnail((cell_size, cell_size), Image.Resampling.LANCZOS)
            background = Image.new("RGBA", (cell_size, cell_size), (12, 15, 25, 255))
            position = ((cell_size - image.width) // 2, (cell_size - image.height) // 2)
            background.alpha_composite(image, position)
            sheet.paste(
                background.convert("RGB"),
                (column * cell_size, row * (cell_size + label_height) + label_height),
            )
        draw.text(
            (column * cell_size + 8, row * (cell_size + label_height) + 7),
            label,
            fill=(225, 230, 240),
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="PNG", optimize=True)


async def run_retopology_agent_plan(
    client: httpx.AsyncClient,
    settings: WorkerSettings,
    job_id: str,
    lease_headers: dict[str, str],
    workspace: Path,
    output_dir: Path,
    input_manifest: dict[str, Any],
    baseline_path: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    codex = Path(settings.codex_binary)
    if not codex.is_file() or not os.access(codex, os.X_OK):
        raise RuntimeError("Codex CLI is required for retopology planning but is unavailable")
    if not settings.codex_auth_source.is_file():
        raise RuntimeError("Codex CLI auth secret is not mounted")
    codex_home = workspace / ".codex-runtime"
    codex_home.mkdir(mode=0o700)
    shutil.copyfile(settings.codex_auth_source, codex_home / "auth.json")
    os.chmod(codex_home / "auth.json", 0o600)
    skill_source = settings.retopology_skill_root.parent
    os.symlink(skill_source, codex_home / "skills", target_is_directory=True)

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "recommended_algorithm",
            "target_faces",
            "asset_class",
            "silhouette_critical_regions",
            "bake_instead_of_model",
            "component_decisions",
            "topology_strategy",
            "required_views",
            "risks",
        ],
        "properties": {
            "recommended_algorithm": {
                "type": "string",
                "enum": ["quadriflow", "cleanup_existing"],
            },
            "target_faces": {"type": "integer", "minimum": 100, "maximum": 5000000},
            "asset_class": {"type": "string"},
            "silhouette_critical_regions": {"type": "array", "items": {"type": "string"}},
            "bake_instead_of_model": {"type": "array", "items": {"type": "string"}},
            "component_decisions": {"type": "array", "items": {"type": "string"}},
            "topology_strategy": {"type": "string"},
            "required_views": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
        },
    }
    schema_path = output_dir / "retopology_agent_schema.json"
    plan_path = output_dir / "retopology_agent_plan.json"
    prompt_path = output_dir / "retopology_agent_prompt.txt"
    events_path = output_dir / "retopology_agent_events.jsonl"
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), "utf-8")
    baseline = baseline_path.read_text("utf-8")
    prompt = f"""Use $blender-retopology-compare-iterate in planning-only mode.
Do not modify any file and do not claim that automatic output is final.
Return only JSON matching the supplied schema.

The high-poly is the shape authority. The reference low is the topology-style and density
authority. The current low is only a starting candidate. Every generated result must still pass
strict audit and matched front/side/top/perspective human review.

User request:
{input_manifest.get('user_request') or 'No additional natural-language request was supplied.'}

Object selectors:
- high: {options['high_object']}
- reference low: {options['reference_object']}
- current low: {options['low_object']}

Requested target_faces: {options.get('target_faces') or 'derive from reference low'}
Requested algorithm: {options.get('algorithm')}
External reference views: {json.dumps(input_manifest.get('reference_views', []), ensure_ascii=False)}

Real baseline Blender audit:
{baseline}
"""
    prompt_path.write_text(prompt, "utf-8")
    command = [
        str(codex),
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(plan_path),
        "--json",
        "-C",
        str(workspace),
    ]
    for reference in input_manifest.get("reference_views", []):
        command.extend(
            ("--image", str(workspace / "references" / str(reference["filename"])))
        )
    command.append("-")
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(codex_home)
    process = await asyncio.create_subprocess_exec(
        *command,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if process.stdin is None:
        raise RuntimeError("Codex CLI stdin is unavailable")
    process.stdin.write(prompt.encode("utf-8"))
    await process.stdin.drain()
    process.stdin.close()
    events = await wait_for_blender(
        client,
        job_id,
        lease_headers,
        process,
        18,
        38,
        "RETOPOLOGY_AGENT_PLANNING",
        "Codex 正在结合高模、参考低模、当前低模及多视角参考图制定候选方案",
        180,
    )
    events_path.write_bytes(events)
    try:
        plan = json.loads(plan_path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Codex CLI did not produce a valid retopology plan") from exc
    algorithm = plan.get("recommended_algorithm")
    target_faces = plan.get("target_faces")
    if algorithm not in {"quadriflow", "cleanup_existing"}:
        raise RuntimeError("Codex retopology plan selected an unsupported algorithm")
    if not isinstance(target_faces, int) or not 100 <= target_faces <= 5_000_000:
        raise RuntimeError("Codex retopology plan target_faces is invalid")
    return plan


async def start_blender(
    settings: WorkerSettings, *arguments: str
) -> asyncio.subprocess.Process:
    environment = dict(os.environ)
    environment["CUDA_VISIBLE_DEVICES"] = ""
    return await asyncio.create_subprocess_exec(
        settings.blender_binary,
        *arguments,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


async def wait_for_blender(
    client: httpx.AsyncClient,
    job_id: str,
    lease_headers: dict[str, str],
    process: asyncio.subprocess.Process,
    progress_start: float,
    progress_end: float,
    stage: str,
    message: str,
    estimated_stage_seconds: int,
) -> bytes:
    progress = progress_start
    started = time.monotonic()
    status = await client.post(
        f"/internal/v1/assets/jobs/{job_id}/progress",
        headers=lease_headers,
        json={
            "progress": progress,
            "stage": stage,
            "message": message,
            "estimated_remaining_seconds": estimated_stage_seconds,
        },
    )
    status.raise_for_status()
    if status.json().get("cancel_requested"):
        process.terminate()
        await process.wait()
        raise RuntimeError("asset job cancelled")
    while process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=15)
        except TimeoutError as exc:
            progress = min(progress_end, progress + max(1.0, (progress_end - progress_start) / 8))
            status = await client.post(
                f"/internal/v1/assets/jobs/{job_id}/progress",
                headers=lease_headers,
                json={
                    "progress": progress,
                    "stage": stage,
                    "message": message,
                    "estimated_remaining_seconds": max(
                        0, estimated_stage_seconds - int(time.monotonic() - started)
                    ),
                },
            )
            status.raise_for_status()
            if status.json().get("cancel_requested"):
                process.terminate()
                await process.wait()
                raise RuntimeError("asset job cancelled") from exc
    output = await process.stdout.read() if process.stdout else b""
    if process.returncode != 0:
        raise RuntimeError(output.decode("utf-8", "replace")[-4000:])
    return output


async def run_uv_skill(
    client: httpx.AsyncClient,
    settings: WorkerSettings,
    job_id: str,
    lease_headers: dict[str, str],
    input_path: Path,
    output_dir: Path,
    options: dict[str, Any],
    job_type: str,
) -> dict[str, str]:
    unwrap_script = verified_script(
        settings.uv_skill_root / "scripts" / "unwrap_fbx.py", UV_UNWRAP_SCRIPT_SHA256
    )
    qa_script = verified_script(
        settings.uv_skill_root / "scripts" / "qa_uv.py", UV_QA_SCRIPT_SHA256
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    if job_type == "UV_PROCESS_V2":
        stem = input_path.stem
        output_blend = output_dir / f"{stem}_PBR_UV.blend"
        output_fbx = output_dir / f"{stem}_PBR_UV.fbx"
        output_report = output_dir / f"{stem}_PBR_UV_report.json"
        blend_qa_path = output_dir / f"{stem}_PBR_UV_QA.json"
        fbx_qa_path = output_dir / f"{stem}_PBR_UV_FBX_QA.json"
    else:
        output_blend = output_dir / "model_PBR_UV.blend"
        output_fbx = output_dir / "model_PBR_UV.fbx"
        output_report = output_dir / "model_report.json"
        blend_qa_path = output_dir / ".blend-qa.json"
        fbx_qa_path = output_dir / ".fbx_readback-qa.json"
    hidden_axis = str(options.get("hidden_axis", "auto"))
    if hidden_axis == "auto":
        hidden_axis = "y+"
    process = await start_blender(
        settings,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(unwrap_script),
        "--",
        "--input",
        str(input_path),
        "--output-blend",
        str(output_blend),
        "--output-fbx",
        str(output_fbx),
        "--output-report",
        str(output_report),
        "--hard-angle",
        str(options["hard_edge_angle_degrees"]),
        "--hidden-axis",
        hidden_axis,
        "--padding-px",
        str(options["padding_px"]),
        "--resolution",
        str(options["resolution"]),
    )
    await wait_for_blender(
        client,
        job_id,
        lease_headers,
        process,
        5,
        60,
        "UV_UNWRAPPING",
        "Blender 正在执行切缝、展开、打直与排版",
        180,
    )

    qa_payloads: dict[str, Any] = {}
    for label, source, qa_path, start, end in (
        ("blend", output_blend, blend_qa_path, 60.0, 76.0),
        ("fbx_readback", output_fbx, fbx_qa_path, 76.0, 90.0),
    ):
        process = await start_blender(
            settings,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(qa_script),
            "--",
            "--input",
            str(source),
            "--output",
            str(qa_path),
            "--strict",
        )
        await wait_for_blender(
            client,
            job_id,
            lease_headers,
            process,
            start,
            end,
            "UV_QA_BLEND" if label == "blend" else "UV_QA_FBX_READBACK",
            "正在检查 Blender 工程 UV" if label == "blend" else "正在回读 FBX 并验证 UV 可交付性",
            45,
        )
        qa_payloads[label] = json.loads(qa_path.read_text("utf-8"))
    if job_type == "UV_PROCESS_V2":
        return {
            "blend": output_blend.name,
            "fbx": output_fbx.name,
            "report": output_report.name,
            "qa": blend_qa_path.name,
            "fbx_qa": fbx_qa_path.name,
        }
    hard_failures = [
        f"{label}: {failure}"
        for label, payload in qa_payloads.items()
        for failure in payload.get("hard_failures", [])
    ]
    blend_qa_path.unlink()
    fbx_qa_path.unlink()
    combined_qa = {
        "schema_version": "pbr-uv-qa.v2",
        "skill": "blender-pbr-uv",
        "script_sha256": {
            "unwrap_fbx.py": UV_UNWRAP_SCRIPT_SHA256,
            "qa_uv.py": UV_QA_SCRIPT_SHA256,
        },
        "passed": not hard_failures,
        "hard_failures": hard_failures,
        "blend": qa_payloads["blend"],
        "fbx_readback": qa_payloads["fbx_readback"],
    }
    (output_dir / "model_QA.json").write_text(
        json.dumps(combined_qa, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "blend": output_blend.name,
        "fbx": output_fbx.name,
        "report": output_report.name,
        "qa": "model_QA.json",
    }


async def run_retopology_audit(
    client: httpx.AsyncClient,
    settings: WorkerSettings,
    job_id: str,
    lease_headers: dict[str, str],
    input_path: Path,
    output_dir: Path,
    options: dict[str, Any],
    input_sha256: str,
) -> None:
    audit_script = verified_script(
        settings.retopology_skill_root / "scripts" / "audit_pair.py",
        RETOPOLOGY_AUDIT_SCRIPT_SHA256,
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    audit_path = output_dir / "retopology_audit.json"
    arguments = [
        "--background",
        "--disable-autoexec",
        str(input_path),
        "--python-exit-code",
        "1",
        "--python",
        str(audit_script),
        "--",
        "--high",
        str(options["high_object"]),
        "--reference",
        str(options["reference_object"]),
        "--low",
        str(options["low_object"]),
        "--output",
        str(audit_path),
    ]
    if bool(options.get("require_closed")):
        arguments.append("--require-closed")
    # Deliberately omit --strict: an audit failure is a review result, not a
    # process crash. The control plane exposes it as WAITING_REVIEW.
    process = await start_blender(settings, *arguments)
    await wait_for_blender(
        client,
        job_id,
        lease_headers,
        process,
        5,
        90,
        "RETOPOLOGY_AUDIT",
        "正在对高模、参考低模和当前低模执行严格拓扑审计",
        120,
    )
    audit_payload = json.loads(audit_path.read_text("utf-8"))
    manifest = {
        "schema_version": "retopology_manifest.v1",
        "job_id": job_id,
        "job_type": "RETOPOLOGY_AUDIT",
        "input_sha256": input_sha256,
        "skill": "blender-retopology-compare-iterate",
        "audit_script_sha256": RETOPOLOGY_AUDIT_SCRIPT_SHA256,
        "objects": {
            "high": options["high_object"],
            "reference": options["reference_object"],
            "low": options["low_object"],
        },
        "audit_passed": bool(audit_payload.get("audit_passed")),
        "visual_review_required": True,
    }
    (output_dir / "retopology_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def run_retopology_process(
    client: httpx.AsyncClient,
    settings: WorkerSettings,
    job_id: str,
    lease_headers: dict[str, str],
    bundle_path: Path,
    output_dir: Path,
    options: dict[str, Any],
    input_sha256: str,
) -> dict[str, str]:
    audit_script = verified_script(
        settings.retopology_skill_root / "scripts" / "audit_pair.py",
        RETOPOLOGY_AUDIT_SCRIPT_SHA256,
    )
    process_script = verified_script(
        settings.retopology_process_script, RETOPOLOGY_PROCESS_SCRIPT_SHA256
    )
    render_script = verified_script(
        settings.retopology_render_script, RETOPOLOGY_RENDER_SCRIPT_SHA256
    )
    extracted = bundle_path.parent / "retopology-input"
    input_manifest = extract_retopology_bundle(bundle_path, extracted)
    project = input_manifest["project"]
    project_path = extracted / str(project["filename"])
    if project["filename"] != options.get("project_filename"):
        raise RuntimeError("retopology project filename differs from leased options")

    output_dir.mkdir(parents=True, exist_ok=False)
    baseline_path = output_dir / "retopology_baseline_audit.json"
    candidate_blend = output_dir / "retopology_candidate.blend"
    candidate_fbx = output_dir / "retopology_candidate.fbx"
    process_report = output_dir / "retopology_process_report.json"
    final_audit = output_dir / "retopology_final_audit.json"
    high = str(options["high_object"])
    reference = str(options["reference_object"])
    current = str(options["low_object"])
    generated = str(options["generated_low_object"])

    baseline_arguments = [
        "--background",
        "--disable-autoexec",
        str(project_path),
        "--python-exit-code",
        "1",
        "--python",
        str(audit_script),
        "--",
        "--high",
        high,
        "--reference",
        reference,
        "--low",
        current,
        "--output",
        str(baseline_path),
    ]
    if bool(options.get("require_closed")):
        baseline_arguments.append("--require-closed")
    process = await start_blender(settings, *baseline_arguments)
    await wait_for_blender(
        client,
        job_id,
        lease_headers,
        process,
        3,
        18,
        "RETOPOLOGY_BASELINE",
        "正在计算源文件指纹与当前低模基线审计",
        120,
    )

    agent_plan = await run_retopology_agent_plan(
        client,
        settings,
        job_id,
        lease_headers,
        extracted,
        output_dir,
        input_manifest,
        baseline_path,
        options,
    )
    resolved_algorithm = (
        str(agent_plan["recommended_algorithm"])
        if options.get("algorithm") == "agent"
        else str(options["algorithm"])
    )
    resolved_target_faces = options.get("target_faces") or int(agent_plan["target_faces"])

    process_arguments = [
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(process_script),
        "--",
        "--input",
        str(project_path),
        "--output-blend",
        str(candidate_blend),
        "--output-fbx",
        str(candidate_fbx),
        "--output-report",
        str(process_report),
        "--high",
        high,
        "--reference",
        reference,
        "--current",
        current,
        "--generated",
        generated,
        "--algorithm",
        resolved_algorithm,
        "--max-repair-rounds",
        str(options["max_repair_rounds"]),
    ]
    process_arguments.extend(("--target-faces", str(resolved_target_faces)))
    if bool(options.get("preserve_sharp")):
        process_arguments.append("--preserve-sharp")
    if bool(options.get("preserve_boundary")):
        process_arguments.append("--preserve-boundary")
    process = await start_blender(settings, *process_arguments)
    await wait_for_blender(
        client,
        job_id,
        lease_headers,
        process,
        38,
        70,
        "RETOPOLOGY_GENERATING",
        "正在生成独立版本的重拓扑候选，不覆盖任何源对象",
        480,
    )

    final_arguments = [
        "--background",
        "--disable-autoexec",
        str(candidate_blend),
        "--python-exit-code",
        "1",
        "--python",
        str(audit_script),
        "--",
        "--high",
        high,
        "--reference",
        reference,
        "--low",
        generated,
        "--output",
        str(final_audit),
        "--baseline",
        str(baseline_path),
    ]
    if bool(options.get("require_closed")):
        final_arguments.append("--require-closed")
    process = await start_blender(settings, *final_arguments)
    await wait_for_blender(
        client,
        job_id,
        lease_headers,
        process,
        70,
        82,
        "RETOPOLOGY_FINAL_AUDIT",
        "正在验证轮廓、面数、N-gon、破面、法线和源文件保护",
        120,
    )

    process = await start_blender(
        settings,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(render_script),
        "--",
        "--input",
        str(candidate_blend),
        "--output-dir",
        str(output_dir),
        "--high",
        high,
        "--reference",
        reference,
        "--generated",
        generated,
        "--resolution",
        str(options["render_resolution"]),
    )
    await wait_for_blender(
        client,
        job_id,
        lease_headers,
        process,
        82,
        94,
        "RETOPOLOGY_RENDERING",
        "正在生成高模、参考低模、候选低模的三组四视图",
        120,
    )

    view_sources = [
        (f"{role} / {view}", output_dir / f"{role}_{view}.png")
        for role in ("high", "reference", "generated")
        for view in ("front", "side", "top", "perspective")
    ]
    contact_sheet(
        view_sources,
        output_dir / "retopology_comparison.png",
        columns=4,
        cell_size=int(options["render_resolution"]),
    )
    reference_sources = [
        (
            f"{item['view']} / {item.get('label') or item['filename']}",
            extracted / "references" / str(item["filename"]),
        )
        for item in input_manifest.get("reference_views", [])
    ]
    if reference_sources:
        contact_sheet(
            reference_sources,
            output_dir / "reference_images.png",
            columns=min(4, len(reference_sources)),
            cell_size=int(options["render_resolution"]),
        )

    audit_payload = json.loads(final_audit.read_text("utf-8"))
    report_payload = json.loads(process_report.read_text("utf-8"))
    preservation = audit_payload.get("preservation", {})
    source_preserved = (
        isinstance(preservation, dict)
        and preservation.get("high") is True
        and preservation.get("reference") is True
        and report_payload.get("source_preserved") is True
    )
    manifest = {
        "schema_version": "retopology_process_manifest.v1",
        "job_id": job_id,
        "job_type": "RETOPOLOGY_PROCESS_V1",
        "input_sha256": input_sha256,
        "project_sha256": project["sha256"],
        "skill": "blender-retopology-compare-iterate",
        "skill_audit_script_sha256": RETOPOLOGY_AUDIT_SCRIPT_SHA256,
        "control_plane_scripts": {
            "process_sha256": RETOPOLOGY_PROCESS_SCRIPT_SHA256,
            "render_sha256": RETOPOLOGY_RENDER_SCRIPT_SHA256,
        },
        "agent_plan": {
            "required": True,
            "recommended_algorithm": agent_plan["recommended_algorithm"],
            "resolved_algorithm": resolved_algorithm,
            "recommended_target_faces": agent_plan["target_faces"],
            "resolved_target_faces": resolved_target_faces,
            "plan_filename": "retopology_agent_plan.json",
            "prompt_filename": "retopology_agent_prompt.txt",
            "events_filename": "retopology_agent_events.jsonl",
        },
        "objects": {
            "high": high,
            "reference": reference,
            "current": current,
            "generated": generated,
        },
        "reference_views": input_manifest.get("reference_views", []),
        "source_preserved": source_preserved,
        "audit_passed": bool(audit_payload.get("audit_passed")),
        "visual_review": {
            "required": True,
            "views": ["front", "side", "top", "perspective"],
            "roles": ["high", "reference", "generated"],
            "comparison_filename": "retopology_comparison.png",
        },
        "automatic_final_promotion_allowed": False,
        "uv_status": report_payload.get("uv_status"),
        "cage_status": report_payload.get("cage_status"),
        "bake_status": report_payload.get("bake_status"),
    }
    (output_dir / "retopology_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    contract = {
        "candidate_blend": "retopology_candidate.blend",
        "candidate_fbx": "retopology_candidate.fbx",
        "process_report": "retopology_process_report.json",
        "baseline_audit": "retopology_baseline_audit.json",
        "audit": "retopology_final_audit.json",
        "manifest": "retopology_manifest.json",
        "comparison": "retopology_comparison.png",
        "agent_plan": "retopology_agent_plan.json",
        "agent_prompt": "retopology_agent_prompt.txt",
        "agent_events": "retopology_agent_events.jsonl",
        **{
            f"view_{role}_{view}": f"{role}_{view}.png"
            for role in ("high", "reference", "generated")
            for view in ("front", "side", "top", "perspective")
        },
    }
    if reference_sources:
        contract["reference_images"] = "reference_images.png"
    return contract


async def process_job(
    client: httpx.AsyncClient, settings: WorkerSettings, job: dict[str, Any]
) -> None:
    job_id = str(job["job_id"])
    lease = str(job["lease_token"])
    lease_headers = {"X-Asset-Lease": lease}
    with tempfile.TemporaryDirectory(prefix=f"asset-{job_id}-") as temporary:
        root = Path(temporary)
        input_path = root / str(job["source_filename"])
        progress = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/progress",
            headers=lease_headers,
            json={
                "progress": 2,
                "stage": "DOWNLOADING_INPUT",
                "message": "Worker 正在下载并校验不可变输入包",
                "estimated_remaining_seconds": 900
                if job["job_type"] == "RETOPOLOGY_PROCESS_V1"
                else 240,
            },
        )
        progress.raise_for_status()
        if progress.json().get("cancel_requested"):
            raise RuntimeError("asset job cancelled")
        async with client.stream("GET", job["input_url"], headers=lease_headers) as response:
            response.raise_for_status()
            digest = hashlib.sha256()
            with input_path.open("xb") as destination:
                async for chunk in response.aiter_bytes():
                    digest.update(chunk)
                    destination.write(chunk)
        if digest.hexdigest() != job["input_sha256"]:
            raise RuntimeError("downloaded asset SHA-256 mismatch")
        output_dir = root / "output"
        contract: dict[str, str]
        if job["job_type"] in {"UV_UNWRAP", "UV_PROCESS_V2"}:
            contract = await run_uv_skill(
                client,
                settings,
                job_id,
                lease_headers,
                input_path,
                output_dir,
                job["options"],
                str(job["job_type"]),
            )
        elif job["job_type"] == "RETOPOLOGY_AUDIT":
            await run_retopology_audit(
                client,
                settings,
                job_id,
                lease_headers,
                input_path,
                output_dir,
                job["options"],
                str(job["input_sha256"]),
            )
            contract = {
                "audit": "retopology_audit.json",
                "manifest": "retopology_manifest.json",
            }
        elif job["job_type"] == "RETOPOLOGY_PROCESS_V1":
            contract = await run_retopology_process(
                client,
                settings,
                job_id,
                lease_headers,
                input_path,
                output_dir,
                job["options"],
                str(job["input_sha256"]),
            )
        else:
            raise RuntimeError(f"unsupported asset job type: {job['job_type']}")
        progress = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/progress",
            headers=lease_headers,
            json={
                "progress": 94.5,
                "stage": "UPLOADING_ARTIFACTS",
                "message": "正在上传最终制品、哈希、审计报告与预览图",
                "estimated_remaining_seconds": 60,
            },
        )
        progress.raise_for_status()
        if progress.json().get("cancel_requested"):
            raise RuntimeError("asset job cancelled")
        handles = []
        try:
            files: dict[str, tuple[str, Any, str]] = {}
            if job["job_type"] == "UV_UNWRAP":
                complete_path = f"/internal/v1/assets/jobs/{job_id}/complete"
            elif job["job_type"] == "UV_PROCESS_V2":
                complete_path = f"/internal/v1/assets/jobs/{job_id}/uv-v2-complete"
            elif job["job_type"] == "RETOPOLOGY_PROCESS_V1":
                complete_path = (
                    f"/internal/v1/assets/jobs/{job_id}/retopology-process-complete"
                )
            else:
                complete_path = (
                    f"/internal/v1/assets/jobs/{job_id}/retopology-complete"
                )
            for kind, filename in contract.items():
                handle = (output_dir / filename).open("rb")
                handles.append(handle)
                if filename.endswith(".json"):
                    content_type = "application/json"
                elif filename.endswith(".png"):
                    content_type = "image/png"
                else:
                    content_type = "application/octet-stream"
                files[kind] = (filename, handle, content_type)
            completed = await client.post(
                complete_path,
                headers=lease_headers,
                files=files,
                timeout=3600,
            )
            completed.raise_for_status()
        finally:
            for handle in handles:
                handle.close()


async def execute_job(
    client: httpx.AsyncClient, settings: WorkerSettings, job: dict[str, Any]
) -> None:
    try:
        await process_job(client, settings, job)
    except Exception as exc:
        try:
            response = await client.post(
                f"/internal/v1/assets/jobs/{job['job_id']}/fail",
                headers={"X-Asset-Lease": str(job["lease_token"])},
                json={
                    "code": "BLENDER_EXECUTION_FAILED",
                    "message": str(exc)[-4000:] or type(exc).__name__,
                    "retryable": True,
                },
            )
            response.raise_for_status()
        except Exception:
            # The lease recovery path in Asset API is the final safety net.
            LOG.exception("failed to report Blender job failure", extra={"job_id": job["job_id"]})


async def worker_loop(settings: WorkerSettings) -> None:
    timeout = httpx.Timeout(30, read=3600)
    async with httpx.AsyncClient(base_url=settings.asset_api_url, timeout=timeout) as client:
        running: set[asyncio.Task[None]] = set()
        control_plane_backoff = 1.0
        while True:
            running = {task for task in running if not task.done()}
            try:
                await heartbeat(client, settings, len(running))
                while len(running) < settings.asset_worker_max_concurrency:
                    response = await signed_post(
                        client,
                        settings,
                        "/internal/v1/assets/jobs/claim",
                        {
                            "worker_id": settings.asset_worker_id,
                            "load_1m": os.getloadavg()[0],
                            "available_memory_mb": available_memory_mb(),
                        },
                    )
                    response.raise_for_status()
                    job = response.json().get("job")
                    if job is None:
                        break
                    task = asyncio.create_task(execute_job(client, settings, job))
                    running.add(task)
                control_plane_backoff = 1.0
            except httpx.HTTPError as exc:
                # A control-plane restart, DNS refresh, or brief network outage
                # must not permanently remove a Blender worker from the pool.
                # Keep already-running Blender subprocesses alive and retry the
                # heartbeat/claim loop with a bounded backoff.
                LOG.warning(
                    "asset control plane unavailable (%s); retrying in %.1fs",
                    type(exc).__name__,
                    control_plane_backoff,
                )
                await asyncio.sleep(control_plane_backoff)
                control_plane_backoff = min(control_plane_backoff * 2, 30.0)
                continue
            await asyncio.sleep(settings.asset_poll_seconds)


def run() -> None:
    asyncio.run(worker_loop(WorkerSettings()))


if __name__ == "__main__":
    run()
