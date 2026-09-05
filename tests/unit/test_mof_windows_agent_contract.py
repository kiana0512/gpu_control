import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
AGENT = ROOT / "apps" / "mof_worker" / "Invoke-GPUControlMofAgent.ps1"
INSTALLER = ROOT / "apps" / "mof_worker" / "Install-GPUControlMofAgent.ps1"
SSH_BOOTSTRAP = ROOT / "apps" / "mof_worker" / "Enable-GPUControlWindowsSsh.ps1"
RUNTIME_FILES = {
    "mof_unwrap.py": ROOT / "runtime" / "asset-skills" / "blender-pbr-uv" / "scripts" / "mof_unwrap.py",
    "preflight_mof.py": ROOT / "runtime" / "asset-skills" / "blender-pbr-uv" / "scripts" / "preflight_mof.py",
    "qa_uv.py": ROOT / "runtime" / "asset-skills" / "blender-pbr-uv" / "scripts" / "qa_uv.py",
    "blender_uv_fbx_units.py": ROOT / "packages" / "asset_processing" / "blender_uv_fbx_units.py",
    "blender_uv_qa_adapter.py": ROOT / "apps" / "mof_worker" / "blender_uv_qa_adapter.py",
}


def test_mof_agent_is_native_windows_only_and_fail_closed() -> None:
    source = AGENT.read_text("utf-8")

    assert "wsl.exe" not in source.lower()
    assert "wslpath" not in source.lower()
    assert "/mnt/c" not in source.lower()
    assert "$BlenderExe = 'C:\\Program Files\\Blender Foundation" in source
    assert "$ControlBaseUrl = 'https://lilithgames2'" in source
    assert "$ControlResolve = 'lilithgames2:443:10.3.34.11'" in source
    assert source.count("'--ipv4'") >= 2
    assert source.count("--ipv4 --resolve $ControlResolve") == 2
    assert source.count("'--resolve', $ControlResolve") == 2
    assert "$SkillVersion = 'mof-windows-native-1.0.9-2026.08.19-v3'" in source
    assert "Invoke-StartupPreflight" in source
    assert "LI3D_MOF_PREFLIGHT_END" in source
    assert "$jsonText | ConvertFrom-Json" in source
    assert "$null -ne $preflightExitCode" in source
    assert "exit_code=$preflightExitCode" in source
    assert "UV_MOF_RUNTIME_UNAVAILABLE" in source
    assert "Global\\GPUControl.MofAgent.$WorkerId" in source
    assert "Write-AgentLog 'FATAL' $_.Exception.ToString()" in source


def test_mof_agent_runtime_hashes_match_payloads() -> None:
    source = AGENT.read_text("utf-8")

    for name, path in RUNTIME_FILES.items():
        match = re.search(rf"'{re.escape(name)}'\s*=\s*'([0-9a-f]{{64}})'", source)
        assert match is not None, name
        assert match.group(1) == hashlib.sha256(path.read_bytes()).hexdigest(), name

    adapter = RUNTIME_FILES["blender_uv_qa_adapter.py"].read_text("utf-8")
    qa_match = re.search(r'SKILL_QA_SHA256 = "([0-9a-f]{64})"', adapter)
    assert qa_match is not None
    assert qa_match.group(1) == hashlib.sha256(RUNTIME_FILES["qa_uv.py"].read_bytes()).hexdigest()


def test_mof_agent_requires_approved_complex_profile_at_worker_boundary() -> None:
    source = AGENT.read_text("utf-8")

    assert "options.algorithm -ne 'mof_low_seam'" in source
    assert "@('complex_non_hardsurface', 'complex_multi_mesh')" in source
    assert "UV_MOF_ASSET_PROFILE_REQUIRED" in source
    assert "uv_algorithms = @('mof_low_seam')" in source
    assert "legacy_pbr" not in source


def test_mof_wrapper_preserves_multi_mesh_boundaries_and_packs_globally() -> None:
    source = RUNTIME_FILES["mof_unwrap.py"].read_text("utf-8")

    assert "targets, source_meshes = choose_targets(args.object)" in source
    assert "face_parts = [part for part in all_parts if part.data.polygons]" in source
    assert "select_objects(face_parts)" in source
    assert "state[\"joined\"] = joined" in source
    assert "pack_uv_objects(" in source
    assert '"cross_object_uv_audit": cross_object_audit' in source
    assert "if len(final_meshes) != source_mesh_count" in source
    assert "export_fbx(final_meshes, output_fbx)" in source


def test_mof_agent_preserves_dual_qa_and_five_artifact_contract() -> None:
    source = AGENT.read_text("utf-8")

    assert "'UV_QA_BLEND'" in source
    assert "'UV_QA_FBX_READBACK'" in source
    assert "BLENDER_PBR_UV_QA_END" in source
    assert "UV_QA_FAILED" in source
    assert "blender_uv_fbx_units.py" in source
    for field in ("blend", "fbx", "report", "qa", "fbx_qa"):
        assert f'"{field}=@$' in source
    assert "/internal/v1/assets/jobs/$jobId/uv-v2-complete" in source


def test_mof_agent_renews_lease_and_terminates_native_process_tree() -> None:
    source = AGENT.read_text("utf-8")

    assert "function Invoke-LeaseRenewal" in source
    assert "$process.WaitForExit($LeaseRenewalSeconds * 1000)" in source
    assert "try { Send-Heartbeat }" in source
    assert "function Stop-NativeProcessTree" in source
    assert "$TaskKillExe /PID $Process.Id /T /F" in source
    assert "MOF_PROCESS_TERMINATION_UNCONFIRMED" in source
    assert "cancel_requested" in source
    assert "$process.ExitCode -ne 0" not in source
    assert "Assert-FileHash $Destination $ExpectedSha256" in source
    assert "result upload was not accepted" in source


def test_mof_installer_uses_native_windows_scheduled_task() -> None:
    source = INSTALLER.read_text("utf-8")

    assert "wsl" not in source.lower()
    assert "New-ScheduledTaskAction -Execute 'powershell.exe'" in source
    assert "-LogonType Interactive" in source
    assert "[string]$RunAsUser = ''" in source
    assert "$RunAsUser.Trim()" in source
    assert "Security.Principal.NTAccount($windowsIdentity)" in source
    assert '"*${runAsSid}:R"' in source
    assert "-MultipleInstances IgnoreNew" in source
    assert "-RepetitionInterval (New-TimeSpan -Minutes 1)" in source
    assert "MODE=NATIVE_WINDOWS" in source
    assert "ConfirmNoActiveMofJobs" in source


def test_windows_control_bootstrap_is_key_only_and_lan_scoped() -> None:
    source = SSH_BOOTSTRAP.read_text("utf-8")

    assert "OpenSSH.Server~~~~0.0.1.0" in source
    assert "administrators_authorized_keys" in source
    assert "10.3.34.11" in source
    assert "10.3.34.238" in source
    assert "LocalPort 22" in source
    assert "auth=public-key" in source
    assert "wsl" not in source.lower()
