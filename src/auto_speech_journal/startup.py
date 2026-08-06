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
    return root / "Programs" / "AutoSpeechJournal" / "AutoSpeechJournal.exe"


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


class StartupTaskManager:
    def __init__(
        self,
        launcher: Path | None = None,
        *,
        task_name: str = TASK_NAME,
        runner: CommandRunner = _default_runner,
        os_name: str = os.name,
    ) -> None:
        self.launcher = (launcher or default_launcher_path()).resolve(strict=False)
        self.task_name = task_name
        self._runner = runner
        self._os_name = os_name

    def status(self) -> StartupStatus:
        if self._os_name != "nt":
            return StartupStatus(
                enabled=False,
                owned=False,
                available=False,
                task_name=self.task_name,
                launcher=self.launcher,
                detail="Task Scheduler startup is supported on Windows only",
            )
        try:
            result = self._runner(("schtasks.exe", "/Query", "/TN", self.task_name, "/XML"))
        except OSError as error:
            return StartupStatus(
                enabled=False,
                owned=False,
                available=False,
                task_name=self.task_name,
                launcher=self.launcher,
                detail=f"Task Scheduler is unavailable: {error}",
            )
        if result.returncode != 0:
            return StartupStatus(
                enabled=False,
                owned=False,
                available=True,
                task_name=self.task_name,
                launcher=self.launcher,
                detail="startup task is not registered",
            )
        enabled, owned, detail = _parse_task(result.stdout, self.launcher)
        return StartupStatus(
            enabled=enabled,
            owned=owned,
            available=True,
            task_name=self.task_name,
            launcher=self.launcher,
            detail=detail,
        )

    def enable(self) -> StartupStatus:
        before = self.status()
        if not before.available:
            return before
        if before.enabled:
            return before
        if before.owned is False and "not registered" not in before.detail:
            raise StartupTaskError(before.detail)
        if not self.launcher.is_file():
            raise StartupTaskError(f"stable launcher is missing: {self.launcher}")
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
        if not before.owned:
            if before.detail == "startup task is not registered":
                return before
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
    "OWNERSHIP_MARKER",
    "TASK_NAME",
    "StartupStatus",
    "StartupTaskError",
    "StartupTaskManager",
    "default_launcher_path",
]
