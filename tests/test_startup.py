from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from auto_speech_journal import cli
from auto_speech_journal.config import AppConfig
from auto_speech_journal.startup import (
    LEGACY_TASK_ARGUMENTS,
    LEGACY_TASK_NAME,
    OWNERSHIP_MARKER,
    StartupStatus,
    StartupTaskError,
    StartupTaskManager,
)


def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class _TaskScheduler:
    def __init__(self) -> None:
        self.xml: str | None = None
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command):
        call = tuple(command)
        self.calls.append(call)
        if call[0] == "whoami.exe":
            return _result(stdout='"desktop\\user","S-1-5-21-123"\n')
        if "/Query" in call:
            return _result(1, stderr="missing") if self.xml is None else _result(stdout=self.xml)
        if "/Create" in call:
            xml_path = Path(call[call.index("/XML") + 1])
            self.xml = xml_path.read_text(encoding="utf-16")
            return _result()
        if "/Delete" in call:
            self.xml = None
            return _result()
        raise AssertionError(call)


def test_startup_task_is_limited_owned_and_points_to_stable_launcher(tmp_path) -> None:
    launcher = tmp_path / "AutoSpeechJournal.exe"
    launcher.write_bytes(b"launcher")
    scheduler = _TaskScheduler()
    manager = StartupTaskManager(launcher, runner=scheduler, os_name="nt")

    enabled = manager.enable()

    assert enabled.enabled is True
    assert enabled.owned is True
    assert scheduler.xml is not None
    assert OWNERSHIP_MARKER in scheduler.xml
    assert "LeastPrivilege" in scheduler.xml
    assert "PT20S" in scheduler.xml
    assert str(launcher.resolve()) in scheduler.xml

    disabled = manager.disable()
    assert disabled.enabled is False
    assert scheduler.xml is None


def test_startup_xml_uses_manager_task_name(tmp_path) -> None:
    launcher = tmp_path / "AutoSpeechJournal.exe"
    launcher.write_bytes(b"launcher")
    scheduler = _TaskScheduler()
    task_name = r"\CustomFolder\Journal Test"
    manager = StartupTaskManager(
        launcher,
        task_name=task_name,
        runner=scheduler,
        os_name="nt",
    )

    manager.enable()

    assert scheduler.xml is not None
    assert f"<URI>{task_name}</URI>" in scheduler.xml


def test_startup_manager_refuses_to_replace_foreign_task(tmp_path) -> None:
    launcher = tmp_path / "AutoSpeechJournal.exe"
    launcher.write_bytes(b"launcher")
    scheduler = _TaskScheduler()
    scheduler.xml = """<?xml version="1.0"?>
    <Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
      <RegistrationInfo><Description>someone-else</Description></RegistrationInfo>
      <Settings><Enabled>true</Enabled></Settings>
      <Actions><Exec><Command>foreign.exe</Command></Exec></Actions>
    </Task>
    """
    manager = StartupTaskManager(launcher, runner=scheduler, os_name="nt")

    with pytest.raises(StartupTaskError, match="not owned"):
        manager.enable()
    with pytest.raises(StartupTaskError, match="not owned"):
        manager.disable()


def test_startup_manager_degrades_when_scheduler_is_unavailable(tmp_path) -> None:
    manager = StartupTaskManager(tmp_path / "missing.exe", os_name="posix")

    status = manager.enable()

    assert status.available is False
    assert status.enabled is False


def _task_xml(command: Path, arguments: str) -> str:
    return f"""<?xml version="1.0"?>
    <Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
      <RegistrationInfo><Description>legacy</Description></RegistrationInfo>
      <Settings><Enabled>true</Enabled></Settings>
      <Actions><Exec><Command>{command}</Command><Arguments>{arguments}</Arguments></Exec></Actions>
    </Task>
    """


class _NamedTaskScheduler:
    def __init__(self, tasks: dict[str, str]) -> None:
        self.tasks = tasks
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command):
        call = tuple(command)
        self.calls.append(call)
        task_name = call[call.index("/TN") + 1]
        if "/Query" in call:
            xml = self.tasks.get(task_name)
            return _result(1, stderr="missing") if xml is None else _result(stdout=xml)
        if "/Delete" in call:
            self.tasks.pop(task_name, None)
            return _result()
        raise AssertionError(call)


def test_disable_removes_only_exact_owned_legacy_source_task(tmp_path) -> None:
    launcher = tmp_path / "AutoSpeechJournal.exe"
    legacy_launcher = tmp_path / "pythonw.exe"
    scheduler = _NamedTaskScheduler(
        {LEGACY_TASK_NAME: _task_xml(legacy_launcher, LEGACY_TASK_ARGUMENTS)}
    )
    manager = StartupTaskManager(
        launcher,
        legacy_launcher=legacy_launcher,
        runner=scheduler,
        os_name="nt",
    )

    manager.disable()

    assert LEGACY_TASK_NAME not in scheduler.tasks
    assert ("schtasks.exe", "/Delete", "/TN", LEGACY_TASK_NAME, "/F") in scheduler.calls


def test_disable_preserves_same_named_foreign_legacy_task(tmp_path) -> None:
    launcher = tmp_path / "AutoSpeechJournal.exe"
    legacy_launcher = tmp_path / "pythonw.exe"
    foreign_xml = _task_xml(tmp_path / "foreign.exe", LEGACY_TASK_ARGUMENTS)
    scheduler = _NamedTaskScheduler({LEGACY_TASK_NAME: foreign_xml})
    manager = StartupTaskManager(
        launcher,
        legacy_launcher=legacy_launcher,
        runner=scheduler,
        os_name="nt",
    )

    manager.disable()

    assert scheduler.tasks[LEGACY_TASK_NAME] == foreign_xml
    assert not any("/Delete" in call for call in scheduler.calls)


class _ReconcileManager:
    def __init__(self, status: StartupStatus, enabled_status: StartupStatus | None = None) -> None:
        self.current = status
        self.enabled_status = enabled_status or status
        self.enable_calls = 0
        self.disable_calls = 0

    def status(self) -> StartupStatus:
        return self.current

    def enable(self) -> StartupStatus:
        self.enable_calls += 1
        self.current = self.enabled_status
        return self.current

    def disable(self) -> StartupStatus:
        self.disable_calls += 1
        self.current = StartupStatus(
            False,
            False,
            True,
            self.current.task_name,
            self.current.launcher,
            "startup task is not registered",
        )
        return self.current


def _status(tmp_path, *, enabled: bool, owned: bool, available: bool) -> StartupStatus:
    return StartupStatus(
        enabled,
        owned,
        available,
        "task",
        tmp_path / "AutoSpeechJournal.exe",
        "status detail",
    )


def test_reconcile_repairs_missing_enabled_task(tmp_path) -> None:
    config = AppConfig(onboarding_completed=True, startup_enabled=True)
    manager = _ReconcileManager(
        _status(tmp_path, enabled=False, owned=False, available=True),
        _status(tmp_path, enabled=True, owned=True, available=True),
    )

    cli.reconcile_startup_config(config, manager, persist=lambda _config: None)

    assert config.startup_enabled is True
    assert manager.enable_calls == 1


def test_reconcile_scheduler_unavailable_persists_manual_fallback(tmp_path) -> None:
    config = AppConfig(onboarding_completed=True, startup_enabled=True)
    manager = _ReconcileManager(
        _status(tmp_path, enabled=False, owned=False, available=False)
    )
    persisted = []

    cli.reconcile_startup_config(config, manager, persist=persisted.append)

    assert config.startup_enabled is False
    assert persisted == [config]


def test_reconcile_removes_owned_task_when_config_is_disabled(tmp_path) -> None:
    config = AppConfig(onboarding_completed=True, startup_enabled=False)
    manager = _ReconcileManager(
        _status(tmp_path, enabled=True, owned=True, available=True)
    )

    cli.reconcile_startup_config(config, manager, persist=lambda _config: None)

    assert manager.disable_calls == 1


def test_reconcile_checks_legacy_cleanup_when_no_new_task_exists(tmp_path) -> None:
    config = AppConfig(onboarding_completed=True, startup_enabled=False)
    manager = _ReconcileManager(
        _status(tmp_path, enabled=False, owned=False, available=True)
    )

    cli.reconcile_startup_config(config, manager, persist=lambda _config: None)

    assert manager.disable_calls == 1
