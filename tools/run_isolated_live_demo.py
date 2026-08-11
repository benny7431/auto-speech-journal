"""Launch the production recorder against an isolated demo data directory.

The recording, ASR, storage, exporter, and QML paths are the normal application
paths. Only Task Scheduler reconciliation and registry-backed QSettings are
redirected so a capture session cannot change the user's installed app state.
"""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path


def _require_demo_root() -> Path:
    raw = os.environ.get("AUTO_SPEECH_JOURNAL_DEMO_ROOT", "").strip()
    if not raw:
        raise RuntimeError("AUTO_SPEECH_JOURNAL_DEMO_ROOT is required")
    root = Path(raw).expanduser().resolve()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", "")).expanduser().resolve()
    if local_app_data != root / "LocalAppData":
        raise RuntimeError("LOCALAPPDATA must point to <demo-root>/LocalAppData")
    return root


def main() -> int:
    root = _require_demo_root()

    from PySide6.QtCore import QSettings

    settings_root = root / "qt-settings"
    settings_root.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_root))
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, str(settings_root))

    from auto_speech_journal import cli

    # The demo has no reason to inspect, create, or remove the user's login task.
    cli.reconcile_startup_config = lambda config, _manager, *, persist: config

    if os.environ.get("AUTO_SPEECH_JOURNAL_DEMO_START_PAUSED") == "1":
        from auto_speech_journal.controller import JournalController

        original_start = JournalController.start

        def start_paused(controller: JournalController) -> None:
            original_start(controller)
            if not controller.snapshot.paused:
                controller.toggle_pause()

        JournalController.start = start_paused

    if os.environ.get("AUTO_SPEECH_JOURNAL_DEMO_EXPANDED") == "1":
        from PySide6.QtCore import QTimer

        from auto_speech_journal.ui_models import JournalViewModel

        original_activate = JournalViewModel.activate

        def activate_expanded(view_model: JournalViewModel) -> None:
            original_activate(view_model)
            QTimer.singleShot(0, view_model.toggleExpanded)

        JournalViewModel.activate = activate_expanded

    return cli.main(("run",))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
