from pathlib import Path

import pytest


def _evaluate_baker_result_contract(
    exit_code: int | None, has_success_marker: bool
) -> str:
    """Mirror the PowerShell result gate so its null/zero/non-zero table is explicit."""
    if exit_code is not None and exit_code != 0:
        return "nonzero_exit"
    if not has_success_marker:
        return "missing_marker"
    return "accepted"


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
        if "Invoke-BakerCommand $args $logPath" in line
    ]
    assert len(command_calls) == 5
    assert all("$jobId $lease" in line for line in command_calls)


@pytest.mark.parametrize(
    ("exit_code", "has_success_marker", "expected"),
    [
        (None, True, "accepted"),
        (0, True, "accepted"),
        (23, True, "nonzero_exit"),
        (None, False, "missing_marker"),
        (0, False, "missing_marker"),
        (23, False, "nonzero_exit"),
    ],
)
def test_baker_result_gate_truth_table(
    exit_code: int | None, has_success_marker: bool, expected: str
) -> None:
    assert _evaluate_baker_result_contract(exit_code, has_success_marker) == expected


def test_baker_result_gate_handles_powershell_null_exit_code_fail_closed() -> None:
    source = (
        Path(__file__).parents[2]
        / "apps"
        / "substance_baker_agent"
        / "Invoke-GPUControlSubstanceAgent.ps1"
    ).read_text(encoding="utf-8")

    assert "function Assert-BakerCommandResult" in source
    assert "$process.Refresh()" in source
    assert "if ($null -ne $ExitCode -and [int]$ExitCode -ne 0)" in source
    assert "if (-not $successMarkerPresent)" in source
    assert "if ($null -eq $ExitCode)" in source
    assert "Substance Baker exit code unavailable and success marker missing" in source
    assert "Assert-BakerCommandResult -ExitCode $exitCode -OutputLines $lines" in source
    assert "if ($exitCode -ne 0)" not in source
    assert "schema_version = 2" in source
    assert "exit_code_observed = $allExitCodesObserved" in source
    assert "exit_code = $summaryExitCode" in source
    assert "commands = $commandEvidence" in source
    assert "exit_code = 0; gpu_backends" not in source


def test_agent_http_calls_are_bounded_below_the_lease_window() -> None:
    source = (
        Path(__file__).parents[2]
        / "apps"
        / "substance_baker_agent"
        / "Invoke-GPUControlSubstanceAgent.ps1"
    ).read_text(encoding="utf-8")

    assert source.count("--connect-timeout 5 --max-time 20") == 2
    assert "Substance Baker lease renewal failed after 3 attempts" in source


def test_agent_reports_fail_closed_host_process_and_generation_evidence() -> None:
    source = (
        Path(__file__).parents[2]
        / "apps"
        / "substance_baker_agent"
        / "Invoke-GPUControlSubstanceAgent.ps1"
    ).read_text(encoding="utf-8")

    assert "$AgentInstanceId = [Guid]::NewGuid().ToString('N')" in source
    assert "$AgentStartedAt = [DateTimeOffset]::UtcNow.ToString('o')" in source
    assert "function Get-BakerHostProcessEvidence" in source
    assert "Get-CimInstance -ClassName Win32_Process" in source
    assert "Name = 'substance3d_baker.exe'" in source
    assert "-ErrorAction Stop" in source
    assert "status = 'HEALTHY'" in source
    assert "status = 'FAILED'" in source
    assert "active_processes = $null" in source
    assert "agent_instance_id = $AgentInstanceId" in source
    assert "agent_started_at = $AgentStartedAt" in source
    assert "substance_process_probe_status" in source
    assert "substance_process_probe_checked_at" in source
    assert "substance_active_processes" in source
    assert "worker_id = $WorkerId; agent_instance_id = $AgentInstanceId" in source
    assert "substance-baker-2026.08.03-v5" in source
    assert '$AgentMutexName = "Global\\GPUControl.SubstanceAgent.$WorkerId"' in source
    assert "$AgentMutex.WaitOne(0, $false)" in source
    assert "SUBSTANCE_AGENT_INSTANCE_ALREADY_RUNNING" in source
    assert "$AgentMutex.ReleaseMutex()" in source
