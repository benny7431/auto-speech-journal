from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "AutoSpeechJournal"


def _known_folder(env_name: str, fallback: Path) -> Path:
    raw = os.environ.get(env_name)
    return Path(raw).expanduser() if raw else fallback


@dataclass(frozen=True, slots=True)
class AppPaths:
    runtime_root: Path
    records_root: Path

    @classmethod
    def defaults(cls) -> AppPaths:
        home = Path.home()
        local_app_data = _known_folder("LOCALAPPDATA", home / "AppData" / "Local")
        documents = home / "Documents"
        return cls(
            runtime_root=local_app_data / APP_DIR_NAME,
            records_root=documents / "語音紀錄",
        )

    @property
    def config_file(self) -> Path:
        return self.runtime_root / "config.json"

    @property
    def settings_history_file(self) -> Path:
        return self.runtime_root / "settings-history.jsonl"

    @property
    def database_file(self) -> Path:
        return self.runtime_root / "state.db"

    @property
    def models_dir(self) -> Path:
        return self.runtime_root / "models"

    @property
    def spool_dir(self) -> Path:
        return self.runtime_root / "spool"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_root / "logs"

    @property
    def fonts_dir(self) -> Path:
        return self.runtime_root / "fonts"

    def ensure_runtime_dirs(self) -> None:
        for directory in (
            self.runtime_root,
            self.records_root,
            self.models_dir,
            self.spool_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
