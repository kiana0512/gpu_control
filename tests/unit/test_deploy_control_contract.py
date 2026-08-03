from pathlib import Path


def test_control_deploy_builds_every_versioned_control_plane_image() -> None:
    script = Path("scripts/deploy_control.sh").read_text(encoding="utf-8")

    assert '"${compose[@]}" build api scheduler asset-api web' in script
