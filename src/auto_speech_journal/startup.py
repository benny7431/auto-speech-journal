from __future__ import annotations

import csv
import io
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

TASK_NAME = r"\AutoSpeechJournal\Auto Speech Journal"
OWNERSHIP_MARKER = "AutoSpeechJournal-owned:v1"
LEGACY_TASK_NAME = r"\Auto Speech Journal"
LEGACY_TASK_ARGUMENTS = "-X utf8 -m auto_speech_journal run"
TASK_XML_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


class StartupTaskError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StartupStatus:
    enabled: bool
    owned: bool
    available: bool
    task_name: str
    launcher: Path
    detail: str


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def default_launcher_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "Programs" / "AutoSpeechJournal" / "app" / "AutoSpeechJournal.exe"


def default_legacy_launcher_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "AutoSpeechJournal" / "app" / ".venv" / "Scripts" / "pythonw.exe"


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _element(parent: ET.Element, name: str, text: str | None = None, **attrs: str) -> ET.Element:
    child = ET.SubElement(parent, f"{{{TASK_XML_NAMESPACE}}}{name}", attrs)
    if text is not None:
        child.text = text
    return child


def _task_xml(*, launcher: Path, user_sid: str, task_name: str) -> ET.ElementTree:
    ET.register_namespace("", TASK_XML_NAMESPACE)
    task = ET.Element(
        f"{{{TASK_XML_NAMESPACE}}}Task",
        {"version": "1.4"},
    )
    registration = _element(task, "RegistrationInfo")
    _element(registration, "Description", OWNERSHIP_MARKER)
    _element(registration, "URI", task_name)

    triggers = _element(task, "Triggers")
    trigger = _element(triggers, "LogonTrigger")
    _element(trigger, "Enabled", "true")
    _element(trigger, "Delay", "PT20S")
    _element(trigger, "UserId", user_sid)

    principals = _element(task, "Principals")
    principal = _element(principals, "Principal", id="Author")
    _element(principal, "UserId", user_sid)
    _element(principal, "LogonType", "InteractiveToken")
    _element(principal, "RunLevel", "LeastPrivilege")

    settings = _element(task, "Settings")
    _element(settings, "MultipleInstancesPolicy", "IgnoreNew")
    _element(settings, "DisallowStartIfOnBatteries", "false")
    _element(settings, "StopIfGoingOnBatteries", "false")
    _element(settings, "AllowHardTerminate", "false")
    _element(settings, "StartWhenAvailable", "true")
    _element(settings, "RunOnlyIfNetworkAvailable", "false")
    _element(settings, "Enabled", "true")
    _element(settings, "Hidden", "false")
    _element(settings, "ExecutionTimeLimit", "PT0S")
    restart = _element(settings, "RestartOnFailure")
    _element(restart, "Interval", "PT1M")
    _element(restart, "Count", "3")

    actions = _element(task, "Actions", Context="Author")
    execute = _element(actions, "Exec")
    _element(execute, "Command", str(launcher))
    _element(execute, "WorkingDirectory", str(launcher.parent))
    return ET.ElementTree(task)


def _read_user_sid(runner: CommandRunner) -> str:
    result = runner(("whoami.exe", "/user", "/fo", "csv", "/nh"))
    if result.returncode != 0:
        raise StartupTaskError((result.stderr or result.stdout or "whoami failed").strip())
    try:
        row = next(csv.reader(io.StringIO(result.stdout)))
    except (StopIteration, csv.Error) as error:
        raise StartupTaskError("unable to parse the current Windows user SID") from error
    if len(row) < 2 or not row[1].strip().startswith("S-"):
        raise StartupTaskError("whoami did not return a Windows user SID")
    return row[1].strip()


def _parse_task(xml_text: str, launcher: Path) -> tuple[bool, bool, str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        return False, False, f"Task Scheduler returned invalid XML: {error}"
    namespace = {"t": TASK_XML_NAMESPACE}
    description = root.findtext("t:RegistrationInfo/t:Description", namespaces=namespace)
    command = root.findtext("t:Actions/t:Exec/t:Command", namespaces=namespace)
    enabled_text = root.findtext("t:Settings/t:Enabled", default="true", namespaces=namespace)
    owned = description == OWNERSHIP_MARKER
    command_matches = (
        command is not None
        and Path(command).resolve(strict=False) == launcher.resolve(strict=False)
    )
    enabled = owned and command_matches and enabled_text.casefold() != "false"
    if not owned:
        detail = "a task with this name exists but is not owned by Auto Speech Journal"
    elif not command_matches:
        detail = "the owned task points to a different launcher"
    elif enabled:
        detail = "startup task is enabled"
    else:
        detail = "startup task exists but is disabled"
    return enabled, owned, detail


def _is_owned_legacy_task(xml_text: str, launcher: Path) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    namespace = {"t": TASK_XML_NAMESPACE}
    command = root.findtext("t:Actions/t:Exec/t:Command", namespaces=namespace)
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=namespace)
    if command is None or arguments is None:
        return False
    command_matches = Path(command.strip().strip('"')).resolve(
        strict=False
    ) == launcher.resolve(strict=False)
    return command_matches and " ".join(arguments.split()) == LEGACY_TASK_ARGUMENTS


class StartupTaskManager:
    def __init__(
        self,
        launcher: Path | None = None,
        *,
        task_name: str = TASK_NAME,
        legacy_task_name: str = LEGACY_TASK_NAME,
        legacy_launcher: Path | None = None,
        runner: CommandRunner = _default_runner,
        os_name: str = os.name,
    ) -> None:
        self.launcher = (launcher or default_launcher_path()).resolve(strict=False)
        self.task_name = task_name
        self.legacy_task_name = legacy_task_name
        self.legacy_launcher = (legacy_launcher or default_legacy_launcher_path()).resolve(
            strict=False
        )
        self._runner = runner
        self._os_name = os_name

    def _remove_owned_legacy_task(self) -> None:
        if self._os_name != "nt":
            return
        result = self._runner(
            ("schtasks.exe", "/Query", "/TN", self.legacy_task_name, "/XML")
        )
        if result.returncode != 0 or not _is_owned_legacy_task(
            result.stdout,
            self.legacy_launcher,
        ):
            return
        removed = self._runner(
            ("schtasks.exe", "/Delete", "/TN", self.legacy_task_name, "/F")
        )
        if removed.returncode != 0:
            detail = (removed.stderr or removed.stdout or "legacy task delete failed").strip()
            raise StartupTaskError(detail)

    def status(self) -> StartupStatus:
        def build(
            *, available: bool, detail: str, enabled: bool = False, owned: bool = False
        ) -> StartupStatus:
            return StartupStatus(
                enabled=enabled,
                owned=owned,
                available=available,
                task_name=self.task_name,
                launcher=self.launcher,
                detail=detail,
            )

        if self._os_name != "nt":
            return build(
                available=False,
                detail="Task Scheduler startup is supported on Windows only",
            )
        try:
            result = self._runner(("schtasks.exe", "/Query", "/TN", self.task_name, "/XML"))
        except OSError as error:
            return build(available=False, detail=f"Task Scheduler is unavailable: {error}")
        if result.returncode != 0:
            return build(available=True, detail="startup task is not registered")
        enabled, owned, detail = _parse_task(result.stdout, self.launcher)
        return build(available=True, detail=detail, enabled=enabled, owned=owned)

    def enable(self) -> StartupStatus:
        before = self.status()
        if not before.available:
            return before
        self._remove_owned_legacy_task()
        if before.enabled:
            return before
        if before.owned is False and "not registered" not in before.detail:
            raise StartupTaskError(before.detail)
        if not self.launcher.is_file():
            raise StartupTaskError(f"application executable is missing: {self.launcher}")
        sid = _read_user_sid(self._runner)
        with tempfile.TemporaryDirectory(prefix="asj-startup-") as temporary:
            xml_path = Path(temporary) / "task.xml"
            _task_xml(
                launcher=self.launcher,
                user_sid=sid,
                task_name=self.task_name,
            ).write(
                xml_path,
                encoding="utf-16",
                xml_declaration=True,
            )
            result = self._runner(
                (
                    "schtasks.exe",
                    "/Create",
                    "/TN",
                    self.task_name,
                    "/XML",
                    str(xml_path),
                    "/F",
                )
            )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Task Scheduler create failed").strip()
            raise StartupTaskError(detail)
        after = self.status()
        if not after.enabled or not after.owned:
            raise StartupTaskError(
                f"startup task did not pass ownership verification: {after.detail}"
            )
        return after

    def disable(self) -> StartupStatus:
        before = self.status()
        if not before.available:
            return before
        self._remove_owned_legacy_task()
        if not before.owned:
            if before.detail == "startup task is not registered":
                return self.status()
            raise StartupTaskError(before.detail)
        result = self._runner(("schtasks.exe", "/Delete", "/TN", self.task_name, "/F"))
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Task Scheduler delete failed").strip()
            raise StartupTaskError(detail)
        after = self.status()
        if after.enabled or after.owned:
            raise StartupTaskError("owned startup task still exists after deletion")
        return after


__all__ = [
    "LEGACY_TASK_ARGUMENTS",
    "LEGACY_TASK_NAME",
    "OWNERSHIP_MARKER",
    "TASK_NAME",
    "StartupStatus",
    "StartupTaskError",
    "StartupTaskManager",
    "default_legacy_launcher_path",
    "default_launcher_path",
]
