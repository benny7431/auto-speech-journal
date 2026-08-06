from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

from auto_speech_journal.config import AppConfig, MicrophoneMode, MicrophoneSelection
from auto_speech_journal.controller import JournalController
from auto_speech_journal.settings_history import SettingsHistoryStore
from auto_speech_journal.ui_models import JournalViewModel


class _Storage:
    def count_pending(self) -> int:
        return 0


def test_view_model_refreshes_readable_recent_history_and_can_open_file(qapp, tmp_path):
    base = AppConfig(
        microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT),
        records_root=str((tmp_path / "records").resolve()),
    )
    history = SettingsHistoryStore(
        tmp_path / "runtime" / "settings-history.jsonl",
        clock=lambda: datetime(2026, 7, 12, 5, 1, tzinfo=UTC),
    )
    opened = []
    controller = JournalController(
        storage=_Storage(),
        exporter=object(),
        workers=None,
        config=base,
        save_config_callback=lambda _config: None,
        settings_history_store=history,
        folder_opener=opened.append,
    )
    view_model = JournalViewModel(
        controller,
        qapp,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        font_directories=(tmp_path / "fonts",),
    )

    assert view_model.settingsHistoryEntries == []
    assert view_model.applySettings(
        view_model.recordsRoot,
        500,
        view_model.endpointSilence,
        view_model.maxSegment,
    )

    assert len(view_model.settingsHistoryEntries) == 1
    item = view_model.settingsHistoryEntries[0]
    assert item["timestamp"] == "2026/07/12 13:01:00"
    assert "即時預覽間隔" in item["summary"]
    assert "350 毫秒 → 500 毫秒" in item["details"]

    view_model.openSettingsHistoryFile()
    assert opened == [history.path]
