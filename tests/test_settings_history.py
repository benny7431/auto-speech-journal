from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

from auto_speech_journal.config import AppConfig
from auto_speech_journal.controller import JournalController
from auto_speech_journal.settings_history import SettingsHistoryStore


class _Storage:
    def count_pending(self) -> int:
        return 0


def test_store_appends_only_changes_with_fsync_and_reads_newest_first(
    tmp_path, monkeypatch
):
    history_file = tmp_path / "runtime" / "settings-history.jsonl"
    moments = iter(
        (
            datetime(2026, 7, 12, 5, 1, tzinfo=UTC),
            datetime(2026, 7, 12, 5, 2, tzinfo=UTC),
        )
    )
    store = SettingsHistoryStore(history_file, clock=lambda: next(moments))
    synced: list[int] = []
    monkeypatch.setattr(
        "auto_speech_journal.settings_history.os.fsync",
        lambda descriptor: synced.append(descriptor),
    )
    original = AppConfig(records_root=str(tmp_path / "records")).to_dict()
    first = dict(original, preview_interval_ms=500)
    next_font_size = 19 if original["ui_font_size"] != 19 else 18
    second = dict(first, ui_font_size=next_font_size)

    assert store.append_change(original, original) is None
    store.append_change(original, first)
    store.append_change(first, second)

    assert len(synced) == 2
    entries = store.read_recent(5)
    assert [entry.changed_fields for entry in entries] == [
        ("ui_font_size",),
        ("preview_interval_ms",),
    ]
    assert entries[0].before == {"ui_font_size": original["ui_font_size"]}
    assert entries[0].after == {"ui_font_size": next_font_size}
    persisted = [json.loads(line) for line in history_file.read_text("utf-8").splitlines()]
    assert persisted[0]["timestamp_utc"] == "2026-07-12T05:01:00Z"


def test_read_recent_skips_malformed_and_structurally_invalid_lines(tmp_path):
    history_file = tmp_path / "settings-history.jsonl"
    store = SettingsHistoryStore(
        history_file,
        clock=lambda: datetime(2026, 7, 12, 5, 1, tzinfo=UTC),
    )
    store.append_change({"ui_font_size": 20}, {"ui_font_size": 18})
    with history_file.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")
        handle.write('{"timestamp_utc":"not-a-date","changed_fields":[]}\n')

    entries = store.read_recent(5)

    assert len(entries) == 1
    assert entries[0].after == {"ui_font_size": 18}
    assert store.read_recent(0) == ()


def test_controller_records_actual_changes_but_not_noops_and_opens_history(tmp_path):
    records_root = (tmp_path / "records").resolve()
    base = AppConfig(records_root=str(records_root))
    history = SettingsHistoryStore(
        tmp_path / "runtime" / "settings-history.jsonl",
        clock=lambda: datetime(2026, 7, 12, 5, 1, tzinfo=UTC),
    )
    saved: list[AppConfig] = []
    opened = []
    controller = JournalController(
        storage=_Storage(),
        exporter=object(),
        workers=None,
        config=base,
        save_config_callback=saved.append,
        settings_history_store=history,
        folder_opener=opened.append,
    )
    changed = replace(base, preview_interval_ms=500)

    controller.update_settings(changed)
    controller.update_settings(changed)
    controller.open_settings_history_file()

    assert saved == [changed]
    assert controller.config == changed
    assert len(controller.recent_settings_history()) == 1
    assert controller.recent_settings_history()[0].changed_fields == (
        "preview_interval_ms",
    )
    assert opened == [history.path]


def test_history_write_failure_does_not_undo_saved_settings(tmp_path):
    class BrokenHistory:
        def append_change(self, _before, _after):
            raise OSError("disk full")

    base = AppConfig(records_root=str((tmp_path / "records").resolve()))
    changed = replace(base, ui_font_size=19)
    saved: list[AppConfig] = []
    controller = JournalController(
        storage=_Storage(),
        exporter=object(),
        workers=None,
        config=base,
        save_config_callback=saved.append,
        settings_history_store=BrokenHistory(),
    )

    controller.update_settings(changed)

    assert saved == [changed]
    assert controller.config == changed
    assert "設定歷程寫入失敗" in controller.snapshot.message
