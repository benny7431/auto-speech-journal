from __future__ import annotations

import pytest

from auto_speech_journal.audio import InputDevice
from auto_speech_journal.config import AppConfig, MicrophoneMode, load_config
from auto_speech_journal.model_download import ModelDownloadError
from auto_speech_journal.setup_wizard import (
    SetupError,
    check_runtime_models_for_setup,
    repair_runtime_models_for_setup,
    run_setup,
)


def _device(
    *,
    index: int = 4,
    name: str = "FXR-HUM-15",
    is_default: bool = True,
    fixed_binding_available: bool = True,
    binding_error: str = "",
) -> InputDevice:
    return InputDevice(
        index=index,
        name=name,
        host_api="Windows WASAPI",
        endpoint_id=f"wasapi:windows wasapi:{name.casefold()}",
        default_sample_rate=48_000,
        max_input_channels=1,
        is_default=is_default,
        fixed_binding_available=fixed_binding_available,
        binding_error=binding_error,
    )


def test_setup_can_persist_follow_windows_default(tmp_path, paths) -> None:
    device = _device()

    configured = run_setup(
        paths=paths,
        non_interactive=True,
        system_default=True,
        device_provider=lambda: ([device], device.index),
        output_fn=lambda _message: None,
    )

    assert configured.microphone.mode is MicrophoneMode.SYSTEM_DEFAULT
    assert configured.microphone.preferred_device is None
    assert load_config(paths.config_file) == configured


def test_setup_can_persist_fixed_wasapi_device(tmp_path, paths) -> None:
    device = _device(is_default=False)

    configured = run_setup(
        paths=paths,
        non_interactive=True,
        device_index=device.index,
        device_provider=lambda: ([device], None),
        output_fn=lambda _message: None,
    )

    assert configured.microphone.mode is MicrophoneMode.FIXED
    assert configured.microphone.preferred_device == device.fingerprint()


def test_setup_refuses_ambiguous_fixed_binding(tmp_path, paths) -> None:
    device = _device(
        fixed_binding_available=False,
        binding_error="同名端點無法安全區分",
    )

    with pytest.raises(SetupError, match="同名"):
        run_setup(
            paths=paths,
            non_interactive=True,
            device_index=device.index,
            device_provider=lambda: ([device], device.index),
            output_fn=lambda _message: None,
        )


def test_interactive_setup_empty_choice_follows_windows_default(tmp_path, paths) -> None:
    device = _device()

    configured = run_setup(
        paths=paths,
        device_provider=lambda: ([device], device.index),
        input_fn=lambda _prompt: "",
        output_fn=lambda _message: None,
    )

    assert configured.microphone.mode is MicrophoneMode.SYSTEM_DEFAULT


def test_model_setup_check_reports_missing_without_network(tmp_path, monkeypatch, paths) -> None:

    def missing(*_args, **_kwargs):
        raise ModelDownloadError("required model files are missing")

    monkeypatch.setattr("auto_speech_journal.setup_wizard.verify_models", missing)

    status = check_runtime_models_for_setup(AppConfig().model, paths=paths)

    assert status.ready is False
    assert status.state == "not_ready"
    assert "尚未就緒" in status.message


def test_model_setup_repair_forwards_progress_and_returns_ready(
    tmp_path, monkeypatch, paths
) -> None:
    updates = []

    def provision(_config, models_dir, progress, **_kwargs):
        assert models_dir == paths.models_dir
        progress("whisper/model.bin", 25, 100)
        progress("whisper/model.bin", 100, 100)

    monkeypatch.setattr("auto_speech_journal.setup_wizard.ensure_models", provision)

    status = repair_runtime_models_for_setup(
        AppConfig().model,
        paths=paths,
        progress=updates.append,
    )

    assert status.ready is True
    assert status.state == "ready"
    assert [(item.completed, item.total) for item in updates] == [(25, 100), (100, 100)]
