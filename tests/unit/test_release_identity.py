import hashlib
import json
from pathlib import Path

from scripts.verify_release_identity import _bound_sbom, _named_values, source_versions, verify

REPOSITORY = Path(__file__).resolve().parents[2]


def test_source_release_versions_are_aligned() -> None:
    assert source_versions(REPOSITORY) == {
        "python": "1.5.6",
        "web": "1.5.6",
        "web_lock": "1.5.6",
    }


def test_release_revision_must_be_a_full_git_sha() -> None:
    try:
        verify(REPOSITORY, "1.5.5", "unknown", {}, {})
    except ValueError as exc:
        assert "40-character Git SHA" in str(exc)
    else:
        raise AssertionError("release verification must reject an unbound revision")


def test_release_evidence_names_must_be_unique() -> None:
    try:
        _named_values(["api=one", "api=two"], "--image")
    except ValueError as exc:
        assert "unique COMPONENT=VALUE" in str(exc)
    else:
        raise AssertionError("duplicate component evidence must be rejected")


def test_sbom_must_be_bound_to_the_registry_manifest(tmp_path: Path) -> None:
    digest = "a" * 64
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "registry/gpu-control-api", "digest": {"sha256": digest}}],
        "predicateType": "https://spdx.dev/Document",
        "predicate": {"spdxVersion": "SPDX-2.3"},
    }
    path = tmp_path / "api.intoto.json"
    raw = json.dumps(statement).encode()
    path.write_bytes(raw)
    _, actual = _bound_sbom(path, {f"sha256:{digest}"})
    assert actual == hashlib.sha256(raw).hexdigest()
