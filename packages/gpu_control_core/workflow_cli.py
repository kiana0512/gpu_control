import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import Database
from .models import Node, Workflow, WorkflowNodeCompatibility, WorkflowVersion
from .settings import get_settings
from .workflow import (
    WorkflowManifest,
    node_compatibility_reasons,
    template_digest,
    validate_api_workflow,
)


def load_bundle(manifest_path: Path) -> tuple[WorkflowManifest, dict[str, object]]:
    manifest = WorkflowManifest.load(manifest_path)
    template_path = (manifest_path.parent / manifest.template_file).resolve()
    if manifest_path.parent.resolve() not in template_path.parents:
        raise ValueError("template_file escapes manifest directory")
    template = json.loads(template_path.read_text(encoding="utf-8"))
    validate_api_workflow(template, manifest.allowed_class_types)
    return manifest, template


async def refresh_compatibility(
    session: AsyncSession, version: WorkflowVersion
) -> list[dict[str, object]]:
    """Refresh the same label/VRAM gate used by the admin API."""
    nodes = list((await session.scalars(select(Node).order_by(Node.id))).all())
    results: list[dict[str, object]] = []
    for node in nodes:
        reasons = node_compatibility_reasons(
            min_vram_mb=version.min_vram_mb,
            required_labels=version.node_labels,
            allowed_class_types=version.allowed_class_types,
            total_vram_mb=node.total_vram_mb,
            reported_labels=node.labels,
        )
        compatibility = await session.scalar(
            select(WorkflowNodeCompatibility).where(
                WorkflowNodeCompatibility.workflow_version_id == version.id,
                WorkflowNodeCompatibility.node_id == node.id,
            )
        )
        if compatibility is None:
            compatibility = WorkflowNodeCompatibility(
                workflow_version_id=version.id,
                node_id=node.id,
                compatible=not reasons,
                reasons=reasons,
            )
            session.add(compatibility)
        else:
            compatibility.compatible = not reasons
            compatibility.reasons = reasons
            compatibility.checked_at = datetime.now(UTC)
        results.append(
            {"node_id": node.id, "compatible": compatibility.compatible, "reasons": reasons}
        )
    return results


async def command_import(path: Path) -> None:
    manifest, template = load_bundle(path)
    db = Database(get_settings())
    async with db.session() as session:
        workflow = await session.get(Workflow, manifest.workflow_key)
        if workflow is None:
            session.add(
                Workflow(
                    key=manifest.workflow_key, display_name=manifest.workflow_key, description=""
                )
            )
        existing = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_key == manifest.workflow_key,
                WorkflowVersion.version == manifest.version,
            )
        )
        if existing:
            raise ValueError("workflow version already exists; use a new immutable version")
        version = WorkflowVersion(
            workflow_key=manifest.workflow_key,
            version=manifest.version,
            template=template,
            parameter_schema=manifest.parameter_schema,
            bindings=manifest.bindings,
            allowed_class_types=sorted(manifest.allowed_class_types),
            required_models=list(manifest.required_models),
            required_custom_nodes=list(manifest.required_custom_nodes),
            min_vram_mb=manifest.min_vram_mb,
            timeout_seconds=manifest.timeout_seconds,
            node_labels=manifest.node_labels,
            output_nodes=list(manifest.output_nodes),
            enabled=False,
            template_sha256=template_digest(template),
        )
        session.add(version)
        await session.flush()
        compatibility = await refresh_compatibility(session, version)
        await session.commit()
    await db.close()
    print(
        f"Imported {manifest.workflow_key}:{manifest.version} as disabled; "
        f"compatible_nodes={sum(bool(item['compatible']) for item in compatibility)}"
    )


async def set_enabled(key: str, version: str, enabled: bool) -> None:
    db = Database(get_settings())
    async with db.session() as session:
        item = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_key == key, WorkflowVersion.version == version
            )
        )
        if item is None:
            raise ValueError("workflow version not found")
        compatibility = await refresh_compatibility(session, item)
        if enabled and not any(bool(row["compatible"]) for row in compatibility):
            raise ValueError("workflow has no compatible node")
        item.enabled = enabled
        await session.commit()
    await db.close()
    print(f"{key}:{version} enabled={enabled}")


def main() -> None:
    parser = argparse.ArgumentParser(description="GPU Control 工作流注册工具")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "import", "test"):
        command = sub.add_parser(name)
        command.add_argument("manifest", type=Path)
    diff = sub.add_parser("diff")
    diff.add_argument("left", type=Path)
    diff.add_argument("right", type=Path)
    for name in ("enable", "disable"):
        command = sub.add_parser(name)
        command.add_argument("key")
        command.add_argument("version")
    args = parser.parse_args()
    try:
        if args.command in {"validate", "test"}:
            manifest, template = load_bundle(args.manifest)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "workflow_key": manifest.workflow_key,
                        "version": manifest.version,
                        "sha256": template_digest(template),
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "import":
            asyncio.run(command_import(args.manifest))
        elif args.command == "diff":
            left_manifest, left = load_bundle(args.left)
            right_manifest, right = load_bundle(args.right)
            print(
                json.dumps(
                    {
                        "from": f"{left_manifest.workflow_key}:{left_manifest.version}",
                        "to": f"{right_manifest.workflow_key}:{right_manifest.version}",
                        "template_changed": template_digest(left) != template_digest(right),
                        "bindings_changed": left_manifest.bindings != right_manifest.bindings,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            asyncio.run(set_enabled(args.key, args.version, args.command == "enable"))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
