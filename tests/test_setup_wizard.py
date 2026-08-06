from __future__ import annotations

import pytest

from auto_speech_journal.audio import InputDevice
from auto_speech_journal.config import MicrophoneMode, load_config
from auto_speech_journal.paths import AppPaths
from auto_speech_journal.setup_wizard import SetupError, run_setup


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


def test_setup_can_persist_follow_windows_default(tmp_path) -> None:
    paths = AppPaths(tmp_path / "runtime", tmp_path / "records")
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


def test_setup_can_persist_fixed_wasapi_device(tmp_path) -> None:
    paths = AppPaths(tmp_path / "runtime", tmp_path / "records")
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


def test_setup_refuses_ambiguous_fixed_binding(tmp_path) -> None:
    paths = AppPaths(tmp_path / "runtime", tmp_path / "records")
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


def test_interactive_setup_empty_choice_follows_windows_default(tmp_path) -> None:
    paths = AppPaths(tmp_path / "runtime", tmp_path / "records")
    device = _device()

    configured = run_setup(
        paths=paths,
        device_provider=lambda: ([device], device.index),
        input_fn=lambda _prompt: "",
        output_fn=lambda _message: None,
    )

    assert configured.microphone.mode is MicrophoneMode.SYSTEM_DEFAULT
