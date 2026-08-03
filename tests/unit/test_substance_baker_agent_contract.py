from pathlib import Path


def test_substance_baker_fence_preserves_process_without_model_eviction() -> None:
    source = (
        Path(__file__).parents[2]
        / "apps"
        / "substance_baker_agent"
        / "Invoke-GPUControlSubstanceAgent.ps1"
    ).read_text(encoding="utf-8")

    assert "function Assert-ComfyUiProcessStable" in source
    assert "Set-ComfyUiRunning" not in source
    assert "docker stop" not in source
    assert "docker start" not in source
    assert "/free" not in source
    assert "{{.State.StartedAt}}" in source
    assert "{{.RestartCount}}" in source
    assert "ComfyUI process changed during native Baker fence" in source
    assert "Read-BakerFenceJobs" not in source
    assert "Write-BakerFenceJobs" not in source
    assert "comfyui_cache_policy = 'no_explicit_eviction_process_preserved'" in source
    assert "comfyui_container_restarted = $false" in source
    assert "SUBSTANCE_COMFYUI_CONTINUITY_FAILED" in source


def test_long_substance_commands_renew_the_lease_and_worker_heartbeat() -> None:
    source = (
        Path(__file__).parents[2]
        / "apps"
        / "substance_baker_agent"
        / "Invoke-GPUControlSubstanceAgent.ps1"
    ).read_text(encoding="utf-8")

    assert "[ValidateRange(10, 240)][int]$LeaseRenewalSeconds = 60" in source
    assert "function Invoke-BakerLeaseRenewal" in source
    assert '"/internal/v1/assets/jobs/$JobId/progress"' in source
    assert "Start-Process -FilePath $BakerExe" in source
    assert "& $BakerExe @Arguments" not in source
    assert "$process.WaitForExit($LeaseRenewalSeconds * 1000)" in source
    assert "Invoke-BakerLeaseRenewal $JobId $Lease $Progress" in source
    assert "try { Send-Heartbeat }" in source
    assert "Stop-BakerProcess $process" in source

    # Every production Baker command must receive the current job/lease and a
    # stable progress payload; a forgotten call site would reintroduce expiry.
    command_calls = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("Invoke-BakerCommand $args $logPath")
    ]
    assert len(command_calls) == 5
    assert all("$jobId $lease" in line for line in command_calls)


def test_agent_http_calls_are_bounded_below_the_lease_window() -> None:
    source = (
        Path(__file__).parents[2]
        / "apps"
        / "substance_baker_agent"
        / "Invoke-GPUControlSubstanceAgent.ps1"
    ).read_text(encoding="utf-8")

    assert source.count("--connect-timeout 5 --max-time 20") == 2
    assert "Substance Baker lease renewal failed after 3 attempts" in source
