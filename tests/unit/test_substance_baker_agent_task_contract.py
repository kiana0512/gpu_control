from pathlib import Path

INSTALLER = (
    Path(__file__).parents[2]
    / "apps"
    / "substance_baker_agent"
    / "Install-GPUControlSubstanceAgent.ps1"
)


def test_substance_agent_task_recovers_from_console_control_exit() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert "STATUS_CONTROL_C_EXIT (0xC000013A)" in source
    assert "-WindowStyle Hidden" in source
    assert "-RepetitionInterval (New-TimeSpan -Minutes 1)" in source
    assert "-RepetitionDuration (New-TimeSpan -Days 3650)" in source
    assert "$triggers = @($logonTrigger, $recoveryTrigger)" in source
    assert "-MultipleInstances IgnoreNew" in source
    assert "-Trigger $triggers" in source


def test_substance_agent_task_ignores_desktop_power_and_idle_transitions() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert "-StartWhenAvailable" in source
    assert "-AllowStartIfOnBatteries" in source
    assert "-DontStopIfGoingOnBatteries" in source
    assert "-DontStopOnIdleEnd" in source
    assert "-WindowStyle Hidden" in source


def test_installer_fails_closed_when_an_agent_does_not_stay_running() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert "if ($installedTask.State -ne 'Running')" in source
    assert "Get-ScheduledTaskInfo -TaskName $taskName" in source
    assert "Start-Sleep -Seconds 2" in source
    assert "did not stay Running after install" in source
    assert "INSTALLED RUNNING RECOVERY=1m" in source
