import httpx
import pytest

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from apps.scheduler.src.gpu_control_scheduler.main import reconcile_prompt_submission
from packages.comfy_client import ComfyClient, ComfyError
from tests.fake_comfyui.app import Behavior, State, create_app


def test_runtime_provenance_requires_package_and_build_version_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler_main, "package_version", lambda _: "1.5.5")
    monkeypatch.setenv("GPU_CONTROL_BUILD_REVISION", "a" * 40)
    monkeypatch.setenv("GPU_CONTROL_BUILD_VERSION", "1.5.4")

    mismatched = scheduler_main.runtime_version_metadata()
    assert mismatched["version_aligned"] is False
    assert mismatched["provenance_complete"] is False

    monkeypatch.setenv("GPU_CONTROL_BUILD_VERSION", "1.5.5")
    aligned = scheduler_main.runtime_version_metadata()
    assert aligned["version_aligned"] is True
    assert aligned["provenance_complete"] is True


async def test_prompt_response_before_db_commit_is_adopted_without_second_submit() -> None:
    state = State(behavior=Behavior(duration_seconds=60))
    client = ComfyClient(
        "http://fake",
        transport=httpx.ASGITransport(app=create_app(state)),
    )
    try:
        client_id = "gpu-control-job-1-attempt-1"
        accepted_prompt_id = await client.submit({"1": {}}, client_id)

        # Simulate a crash after Comfy accepted the prompt but before the
        # scheduler persisted prompt_id. Recovery may only reconcile; it must
        # not issue a second POST /prompt.
        recovered_prompt_id = await reconcile_prompt_submission(client, client_id)

        assert recovered_prompt_id == accepted_prompt_id
        assert list(state.prompts) == [accepted_prompt_id]
    finally:
        await client.close()


async def test_missing_or_duplicate_submit_intent_fails_closed() -> None:
    state = State(behavior=Behavior(duration_seconds=60))
    client = ComfyClient(
        "http://fake",
        transport=httpx.ASGITransport(app=create_app(state)),
    )
    try:
        with pytest.raises(ComfyError) as missing:
            await reconcile_prompt_submission(client, "gpu-control-missing-attempt-1")
        assert missing.value.code == "COMFY_SUBMISSION_UNKNOWN"
        assert state.prompts == {}

        client_id = "gpu-control-job-2-attempt-1"
        await client.submit({"1": {}}, client_id)
        await client.submit({"1": {}}, client_id)
        with pytest.raises(ComfyError) as duplicate:
            await reconcile_prompt_submission(client, client_id)
        assert duplicate.value.code == "COMFY_SUBMISSION_DUPLICATE"
        assert len(state.prompts) == 2
    finally:
        await client.close()
