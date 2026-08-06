from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .paths import AppPaths

DEFAULT_UI_FONT_FAMILY = "SentyCreek"
DEFAULT_UI_FONT_SIZE = 18
MIN_UI_FONT_SIZE = 14
MAX_UI_FONT_SIZE = 26


@dataclass(slots=True)
class DeviceFingerprint:
    name: str
    host_api: str = "Windows WASAPI"
    endpoint_id: str = ""
    default_sample_rate: float | None = None
    max_input_channels: int | None = None


class MicrophoneMode(StrEnum):
    PENDING = "pending"
    SKIPPED = "skipped"
    SYSTEM_DEFAULT = "system_default"
    FIXED = "fixed"


@dataclass(slots=True)
class MicrophoneSelection:
    mode: MicrophoneMode = MicrophoneMode.PENDING
    preferred_device: DeviceFingerprint | None = None

    def validate(self) -> None:
        if not isinstance(self.mode, MicrophoneMode):
            raise ValueError("microphone mode is invalid")
        preferred = self.preferred_device
        if self.mode is MicrophoneMode.FIXED:
            if preferred is None:
                raise ValueError("fixed microphone mode requires preferred_device")
            if not preferred.name.strip():
                raise ValueError("device name is required")
            if preferred.host_api != "Windows WASAPI":
                raise ValueError("v3 supports only Windows WASAPI input")
            if preferred.default_sample_rate is not None and preferred.default_sample_rate <= 0:
                raise ValueError("device default sample rate must be positive")
            if preferred.max_input_channels is not None and preferred.max_input_channels <= 0:
                raise ValueError("device input channel count must be positive")
        elif preferred is not None:
            raise ValueError("preferred_device is valid only for fixed microphone mode")


@dataclass(slots=True)
class ModelConfig:
    preview_model: str = "sherpa-onnx-streaming-paraformer-bilingual-zh-en-int8"
    preview_revision: str = "github-release:asr-models:asset-155855418"
    final_model: str = "openai/whisper-large-v3-turbo"
    final_revision: str = "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"
    final_compute_type: str = "int8_float16"
    cpu_compute_type: str = "int8"


@dataclass(slots=True)
class AppConfig:
    schema_version: int = 3
    microphone: MicrophoneSelection = field(default_factory=MicrophoneSelection)
    records_root: str = field(default_factory=lambda: str(AppPaths.defaults().records_root))
    timezone: str = "Asia/Taipei"
    language: str = "zh"
    preview_interval_ms: int = 350
    endpoint_silence_ms: int = 2_000
    pre_roll_ms: int = 300
    max_segment_ms: int = 28_000
    segment_overlap_ms: int = 1_000
    final_deadline_ms: int = 10_000
    spool_limit_bytes: int = 10 * 1024**3
    spool_warning_ratio: float = 0.8
    audio_sample_rate: int = 16_000
    startup_enabled: bool = True
    vocabulary_learning_enabled: bool = True
    ui_font_family: str = DEFAULT_UI_FONT_FAMILY
    ui_font_size: int = DEFAULT_UI_FONT_SIZE
    model: ModelConfig = field(default_factory=ModelConfig)

    def validate(self) -> None:
        if self.schema_version != 3:
            raise ValueError(f"unsupported config schema: {self.schema_version}")
        self.microphone.validate()
        if self.timezone != "Asia/Taipei":
            raise ValueError("v3 supports only Asia/Taipei")
        if self.language != "zh":
            raise ValueError("v3 supports only Chinese transcription")
        if not (250 <= self.preview_interval_ms <= 10_000):
            raise ValueError("preview_interval_ms is out of range")
        if not (250 <= self.endpoint_silence_ms <= 10_000):
            raise ValueError("endpoint_silence_ms is out of range")
        if self.max_segment_ms <= self.endpoint_silence_ms:
            raise ValueError("max segment must exceed endpoint silence")
        if not (0 <= self.pre_roll_ms < self.max_segment_ms):
            raise ValueError("pre_roll_ms is out of range")
        if not (0 <= self.segment_overlap_ms < self.max_segment_ms):
            raise ValueError("invalid segment overlap")
        if self.final_deadline_ms <= 0:
            raise ValueError("final_deadline_ms must be positive")
        if not (0.1 <= self.spool_warning_ratio < 1.0):
            raise ValueError("spool_warning_ratio must be in [0.1, 1.0)")
        if self.spool_limit_bytes <= 0:
            raise ValueError("spool limit must be positive")
        if self.audio_sample_rate != 16_000:
            raise ValueError("v3 requires a 16000 Hz audio sample rate")
        if not isinstance(self.startup_enabled, bool):
            raise ValueError("startup_enabled must be boolean")
        if not isinstance(self.vocabulary_learning_enabled, bool):
            raise ValueError("vocabulary_learning_enabled must be boolean")
        self.ui_font_family = self.ui_font_family.strip()
        if not self.ui_font_family:
            raise ValueError("ui_font_family is required")
        if not (MIN_UI_FONT_SIZE <= self.ui_font_size <= MAX_UI_FONT_SIZE):
            raise ValueError(
                f"ui_font_size must be between {MIN_UI_FONT_SIZE} and {MAX_UI_FONT_SIZE}"
            )
        expected_model = ModelConfig()
        if self.model != expected_model:
            raise ValueError("v3 model names, revisions, and compute profiles are fixed")
        records_value = self.records_root.strip()
        if not records_value:
            raise ValueError("records_root is required")
        records_root = Path(records_value).expanduser()
        if not records_root.is_absolute():
            raise ValueError("records_root must be an absolute path")
        records_root = records_root.resolve(strict=False)
        runtime_root = AppPaths.defaults().runtime_root.resolve(strict=False)
        if records_root == runtime_root or records_root.is_relative_to(runtime_root):
            raise ValueError("records_root must not be inside the application runtime directory")
        self.records_root = str(records_root)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AppConfig:
        values = dict(raw)
        microphone_raw = values.get("microphone", {})
        if not isinstance(microphone_raw, dict):
            raise ValueError("microphone must be an object")
        microphone_values = dict(microphone_raw)
        try:
            microphone_values["mode"] = MicrophoneMode(
                microphone_values.get("mode", MicrophoneMode.PENDING)
            )
        except (TypeError, ValueError) as error:
            raise ValueError("microphone mode is invalid") from error
        preferred_raw = microphone_values.get("preferred_device")
        if preferred_raw is not None:
            if not isinstance(preferred_raw, dict):
                raise ValueError("preferred_device must be an object or null")
            microphone_values["preferred_device"] = DeviceFingerprint(**preferred_raw)
        values["microphone"] = MicrophoneSelection(**microphone_values)
        values["model"] = ModelConfig(**values.get("model", {}))
        config = cls(**values)
        config.validate()
        return config

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        config = AppConfig()
        save_config(path, config)
        return config
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("config root must be an object")
    schema_version = raw.get("schema_version", 1)
    if schema_version in {1, 2}:
        migrated = dict(raw)
        migrated["schema_version"] = 3
        legacy_device = migrated.pop(
            "device",
            {
                "name": "Microphone (Realtek(R) Audio)",
                "host_api": "Windows WASAPI",
                "endpoint_id": "",
                "default_sample_rate": None,
                "max_input_channels": None,
            },
        )
        migrated["microphone"] = {
            "mode": MicrophoneMode.FIXED,
            "preferred_device": legacy_device,
        }
        if schema_version == 1 and migrated.get("preview_interval_ms", 2_000) == 2_000:
            migrated["preview_interval_ms"] = 350
        config = AppConfig.from_dict(migrated)
        save_config(path, config)
        return config
    config = AppConfig.from_dict(raw)
    if any(
        field not in raw
        for field in (
            "ui_font_family",
            "ui_font_size",
            "vocabulary_learning_enabled",
        )
    ):
        save_config(path, config)
    return config


def save_config(path: Path, config: AppConfig) -> None:
    config.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(config.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
