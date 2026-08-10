from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_speech_journal.config import (
    DEFAULT_UI_FONT_FAMILY,
    DEFAULT_UI_FONT_SIZE,
    AppConfig,
    DeviceFingerprint,
    MicrophoneMode,
    MicrophoneSelection,
    ModelConfig,
    load_config,
    save_config,
)
from auto_speech_journal.paths import AppPaths


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(records_root=str(tmp_path / "語音紀錄"))
    save_config(path, config)

    loaded = load_config(path)

    assert loaded == config
    assert json.loads(path.read_text(encoding="utf-8"))["timezone"] == "Asia/Taipei"


def test_load_config_creates_defaults(tmp_path):
    path = tmp_path / "nested" / "config.json"

    config = load_config(path)

    assert path.exists()
    assert config.microphone == MicrophoneSelection(MicrophoneMode.PENDING)
    assert config.schema_version == 4
    assert config.onboarding_completed is False
    assert config.startup_enabled is False
    assert config.update_check_enabled is False
    assert "device" not in json.loads(path.read_text(encoding="utf-8"))
    assert config.preview_interval_ms == 350
    assert config.ui_font_family == DEFAULT_UI_FONT_FAMILY == "SentyCreek"
    assert config.ui_font_size == DEFAULT_UI_FONT_SIZE == 18


def test_load_config_adds_missing_appearance_defaults_to_existing_v4(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    raw = AppConfig(records_root=str(tmp_path / "records")).to_dict()
    raw.pop("ui_font_family")
    raw.pop("ui_font_size")
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_config(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert loaded.ui_font_family == persisted["ui_font_family"] == "SentyCreek"
    assert loaded.ui_font_size == persisted["ui_font_size"] == 18


def test_load_config_adds_missing_vocabulary_learning_default_to_existing_v4(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    raw = AppConfig(records_root=str(tmp_path / "records")).to_dict()
    raw.pop("vocabulary_learning_enabled")
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_config(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert loaded.vocabulary_learning_enabled is True
    assert persisted["vocabulary_learning_enabled"] is True


def test_load_config_migrates_legacy_v4_model_pins_without_losing_user_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    records_root = tmp_path / "existing-journal"
    original = AppConfig(
        records_root=str(records_root),
        microphone=MicrophoneSelection(
            MicrophoneMode.FIXED,
            DeviceFingerprint(
                name="Existing USB microphone",
                host_api="Windows WASAPI",
                endpoint_id="wasapi:existing",
            ),
        ),
        onboarding_completed=True,
        startup_enabled=True,
        update_check_enabled=True,
        vocabulary_learning_enabled=False,
        ui_font_family="Existing User Font",
        ui_font_size=22,
    )
    raw = original.to_dict()
    raw["model"] = {
        "preview_model": "sherpa-onnx-streaming-paraformer-bilingual-zh-en-int8",
        "preview_revision": "github-release:asr-models:asset-155855418",
        "final_model": "openai/whisper-large-v3-turbo",
        "final_revision": "41f01f3fe87f28c78e2fbf8b568835947dd65ed9",
        "final_compute_type": "int8_float16",
        "cpu_compute_type": "int8",
    }
    path.write_text(json.dumps(raw), encoding="utf-8")

    migrated = load_config(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert migrated.model == ModelConfig()
    assert persisted["model"] == migrated.to_dict()["model"]
    assert migrated.records_root == str(records_root.resolve())
    assert migrated.microphone == original.microphone
    assert migrated.onboarding_completed is True
    assert migrated.startup_enabled is True
    assert migrated.update_check_enabled is True
    assert migrated.vocabulary_learning_enabled is False
    assert migrated.ui_font_family == "Existing User Font"
    assert migrated.ui_font_size == 22
    assert list(path.parent.glob("*.tmp")) == []
    assert list(path.parent.glob(".*.tmp")) == []


def test_load_config_preserves_explicit_appearance_settings(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    raw = AppConfig(records_root=str(tmp_path / "records")).to_dict()
    raw.update(ui_font_family="Existing User Font", ui_font_size=24)
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_config(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert loaded.ui_font_family == persisted["ui_font_family"] == "Existing User Font"
    assert loaded.ui_font_size == persisted["ui_font_size"] == 24


def test_config_preserves_explicit_disabled_vocabulary_learning(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig(
        records_root=str(tmp_path / "records"),
        vocabulary_learning_enabled=False,
    )

    save_config(path, config)
    loaded = load_config(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert loaded == config
    assert loaded.vocabulary_learning_enabled is False
    assert persisted["vocabulary_learning_enabled"] is False


@pytest.mark.parametrize(
    ("old_interval", "expected_interval"),
    [(2_000, 350), (900, 900)],
)
def test_load_config_atomically_migrates_v1_preview_interval(
    tmp_path: Path,
    old_interval: int,
    expected_interval: int,
) -> None:
    path = tmp_path / "config.json"
    raw = AppConfig(records_root=str(tmp_path / "records")).to_dict()
    raw.pop("microphone")
    raw.update(
        schema_version=1,
        device={
            "name": "Legacy microphone",
            "host_api": "Windows WASAPI",
            "endpoint_id": "legacy-endpoint",
            "default_sample_rate": 48_000.0,
            "max_input_channels": 2,
        },
        preview_interval_ms=old_interval,
        endpoint_silence_ms=2_500,
    )
    path.write_text(json.dumps(raw), encoding="utf-8")

    migrated = load_config(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert migrated.schema_version == persisted["schema_version"] == 4
    assert migrated.onboarding_completed is True
    assert migrated.microphone.mode is MicrophoneMode.FIXED
    assert migrated.microphone.preferred_device == DeviceFingerprint(
        name="Legacy microphone",
        host_api="Windows WASAPI",
        endpoint_id="legacy-endpoint",
        default_sample_rate=48_000.0,
        max_input_channels=2,
    )
    assert "device" not in persisted
    assert migrated.preview_interval_ms == persisted["preview_interval_ms"] == expected_interval
    assert migrated.endpoint_silence_ms == 2_500
    assert list(path.parent.glob("*.tmp")) == []
    assert list(path.parent.glob(".*.tmp")) == []


def test_load_config_migrates_v2_device_without_losing_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    raw = AppConfig(records_root=str(tmp_path / "records")).to_dict()
    raw.pop("microphone")
    raw.update(
        schema_version=2,
        device={
            "name": "Virtual Cable Output",
            "host_api": "Windows WASAPI",
            "endpoint_id": "wasapi:7:virtual cable output",
            "default_sample_rate": 96_000.0,
            "max_input_channels": 8,
        },
    )
    path.write_text(json.dumps(raw), encoding="utf-8")

    migrated = load_config(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert migrated.microphone == MicrophoneSelection(
        MicrophoneMode.FIXED,
        DeviceFingerprint(
            name="Virtual Cable Output",
            host_api="Windows WASAPI",
            endpoint_id="wasapi:7:virtual cable output",
            default_sample_rate=96_000.0,
            max_input_channels=8,
        ),
    )
    assert persisted["microphone"]["mode"] == "fixed"
    assert persisted["microphone"]["preferred_device"] == raw["device"]
    assert "device" not in persisted


def test_load_config_migrates_v3_as_completed_without_losing_user_choices(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    records_root = tmp_path / "existing-journal"
    raw = AppConfig(
        records_root=str(records_root),
        microphone=MicrophoneSelection(
            MicrophoneMode.FIXED,
            DeviceFingerprint(name="Existing USB microphone", endpoint_id="wasapi:existing"),
        ),
    ).to_dict()
    raw.update(schema_version=3, startup_enabled=True)
    raw.pop("onboarding_completed")
    raw.pop("update_check_enabled")
    path.write_text(json.dumps(raw), encoding="utf-8")

    migrated = load_config(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert migrated.schema_version == 4
    assert migrated.onboarding_completed is True
    assert migrated.startup_enabled is True
    assert migrated.update_check_enabled is False
    assert migrated.records_root == str(records_root.resolve())
    assert migrated.microphone.preferred_device is not None
    assert migrated.microphone.preferred_device.name == "Existing USB microphone"
    assert persisted == migrated.to_dict()


@pytest.mark.parametrize("mode", [MicrophoneMode.PENDING, MicrophoneMode.SKIPPED])
def test_load_config_keeps_incomplete_v3_users_out_of_recording(mode, tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    raw = AppConfig(
        microphone=MicrophoneSelection(mode=mode),
        onboarding_completed=False,
    ).to_dict()
    raw.update(schema_version=3, startup_enabled=True)
    raw.pop("onboarding_completed")
    raw.pop("update_check_enabled")
    path.write_text(json.dumps(raw), encoding="utf-8")

    migrated = load_config(path)

    assert migrated.onboarding_completed is False
    assert migrated.startup_enabled is False
    assert migrated.microphone.mode is mode


@pytest.mark.parametrize(
    "mode",
    [
        MicrophoneMode.PENDING,
        MicrophoneMode.SKIPPED,
        MicrophoneMode.SYSTEM_DEFAULT,
    ],
)
def test_non_fixed_microphone_modes_forbid_preferred_device(mode: MicrophoneMode) -> None:
    selection = MicrophoneSelection(
        mode,
        DeviceFingerprint(name="Should not be persisted"),
    )

    with pytest.raises(ValueError, match="only for fixed"):
        AppConfig(microphone=selection).validate()


def test_fixed_microphone_mode_requires_device() -> None:
    with pytest.raises(ValueError, match="requires preferred_device"):
        AppConfig(microphone=MicrophoneSelection(MicrophoneMode.FIXED)).validate()


@pytest.mark.parametrize(
    "mode",
    [
        MicrophoneMode.PENDING,
        MicrophoneMode.SKIPPED,
        MicrophoneMode.SYSTEM_DEFAULT,
    ],
)
def test_non_fixed_microphone_modes_are_valid(mode: MicrophoneMode) -> None:
    AppConfig(microphone=MicrophoneSelection(mode)).validate()


def test_fixed_microphone_mode_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig(
        records_root=str(tmp_path / "records"),
        microphone=MicrophoneSelection(
            MicrophoneMode.FIXED,
            DeviceFingerprint(name="FXR-HUM-15", endpoint_id="wasapi:1:fxr-hum-15"),
        ),
    )

    save_config(path, config)

    assert load_config(path) == config
    assert json.loads(path.read_text(encoding="utf-8"))["microphone"]["mode"] == "fixed"


def test_config_rejects_wrong_timezone():
    with pytest.raises(ValueError, match="Asia/Taipei"):
        AppConfig(timezone="UTC").validate()


@pytest.mark.parametrize("records_root", ["", "   ", "records", ".\\records"])
def test_config_rejects_empty_or_relative_records_root(records_root: str) -> None:
    config = AppConfig(records_root=records_root)
    with pytest.raises(ValueError, match="records_root"):
        config.validate()


def test_config_normalizes_absolute_records_root(tmp_path: Path) -> None:
    requested = tmp_path / "intermediate" / ".." / "records"
    config = AppConfig(records_root=str(requested))

    config.validate()

    assert config.records_root == str((tmp_path / "records").resolve())


@pytest.mark.parametrize("suffix", [(), ("app",), ("spool",), ("spool", "nested")])
def test_config_rejects_runtime_and_its_subtrees(suffix: tuple[str, ...]) -> None:
    runtime_root = AppPaths.defaults().runtime_root
    config = AppConfig(records_root=str(runtime_root.joinpath(*suffix)))

    with pytest.raises(ValueError, match="runtime"):
        config.validate()


def test_default_documents_records_root_is_valid() -> None:
    config = AppConfig()
    config.validate()
    assert Path(config.records_root).is_absolute()


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_config_rejects_non_boolean_vocabulary_learning_setting(value: object) -> None:
    config = AppConfig(vocabulary_learning_enabled=value)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="vocabulary_learning_enabled must be boolean"):
        config.validate()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("onboarding_completed", 1),
        ("startup_enabled", "false"),
        ("update_check_enabled", None),
    ],
)
def test_config_rejects_non_boolean_v4_flags(field: str, value: object) -> None:
    config = AppConfig(**{field: value})  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=f"{field} must be boolean"):
        config.validate()


def test_config_rejects_startup_before_onboarding_completion() -> None:
    with pytest.raises(ValueError, match="before onboarding"):
        AppConfig(startup_enabled=True).validate()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"pre_roll_ms": -1}, "pre_roll"),
        ({"final_deadline_ms": 0}, "final_deadline"),
        ({"audio_sample_rate": 44_100}, "16000"),
        ({"startup_enabled": 1}, "boolean"),
        ({"ui_font_family": "  "}, "ui_font_family"),
        ({"ui_font_size": 13}, "ui_font_size"),
        ({"ui_font_size": 27}, "ui_font_size"),
        ({"model": ModelConfig(final_revision="moving-main")}, "model"),
    ],
)
def test_config_rejects_invalid_runtime_contract(changes: dict, message: str) -> None:
    config = AppConfig(**changes)
    with pytest.raises(ValueError, match=message):
        config.validate()
