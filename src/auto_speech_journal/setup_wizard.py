from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .audio import InputDevice, list_wasapi_input_devices
from .config import (
    AppConfig,
    MicrophoneMode,
    MicrophoneSelection,
    ModelConfig,
    load_config,
    save_config,
)
from .model_download import ModelDownloadError, ensure_models, verify_models
from .paths import AppPaths

Output = Callable[[str], None]
DeviceProvider = Callable[[], tuple[Sequence["InputDevice"], int | None]]


@dataclass(frozen=True, slots=True)
class ModelSetupStatus:
    """Thread-safe value passed from model setup workers to the Qt view model."""

    state: str
    ready: bool
    message: str
    completed: int = 0
    total: int = 0
    asset: str = ""


ModelSetupProgress = Callable[[ModelSetupStatus], None]


class SetupError(RuntimeError):
    pass


def check_runtime_models_for_setup(
    config: ModelConfig,
    *,
    paths: AppPaths | None = None,
    manifest_path: Path | None = None,
) -> ModelSetupStatus:
    """Verify the runtime manifest and installed models without using the network."""

    selected_paths = paths or AppPaths.defaults()
    try:
        verify_models(
            config,
            selected_paths.models_dir,
            deep=True,
            manifest_path=manifest_path,
        )
    except (ModelDownloadError, OSError, ValueError) as error:
        return ModelSetupStatus(
            state="not_ready",
            ready=False,
            message=f"語音模型尚未就緒：{error}",
        )
    return ModelSetupStatus(
        state="ready",
        ready=True,
        message="語音模型已完成驗證，可以開始錄音。",
    )


def repair_runtime_models_for_setup(
    config: ModelConfig,
    *,
    paths: AppPaths | None = None,
    manifest_path: Path | None = None,
    progress: ModelSetupProgress | None = None,
) -> ModelSetupStatus:
    """Download pinned runtime files with the official Hugging Face client."""

    selected_paths = paths or AppPaths.defaults()

    def report(asset: str, completed: int, total: int) -> None:
        if progress is not None:
            progress(
                ModelSetupStatus(
                    state="downloading",
                    ready=False,
                    message=f"正在從 Hugging Face 下載並驗證：{asset}",
                    completed=max(0, int(completed)),
                    total=max(0, int(total)),
                    asset=asset,
                )
            )

    try:
        ensure_models(
            config,
            selected_paths.models_dir,
            progress=report,
            manifest_path=manifest_path,
        )
    except (ModelDownloadError, OSError, ValueError) as error:
        return ModelSetupStatus(
            state="error",
            ready=False,
            message=f"模型下載未完成，請重試：{error}",
        )
    return ModelSetupStatus(
        state="ready",
        ready=True,
        message="語音模型已完成下載與驗證。",
    )


def discover_input_devices() -> tuple[list[InputDevice], int | None]:
    """Return the shared WASAPI catalog and that host API's default input index."""
    try:
        devices = list_wasapi_input_devices()
    except Exception as error:
        raise SetupError(f"無法列出麥克風：{error}") from error
    default_index = next((device.index for device in devices if device.is_default), None)
    return devices, default_index


def test_microphone(device: InputDevice, *, seconds: float = 0.8) -> float:
    """Capture a short block and return its RMS level."""
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as error:  # pragma: no cover - packaging failure
        raise SetupError("麥克風測試套件未安裝完整") from error

    sample_rate = int(device.default_sample_rate or 16_000)
    try:
        samples = sd.rec(
            int(sample_rate * seconds),
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            device=device.index,
            blocking=True,
        )
    except Exception as error:
        raise SetupError(f"麥克風測試失敗：{error}") from error
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def run_setup(
    *,
    paths: AppPaths | None = None,
    non_interactive: bool = False,
    records_root: Path | None = None,
    device_index: int | None = None,
    system_default: bool = False,
    test_audio: bool = False,
    download_models: bool = False,
    input_fn: Callable[[str], str] = input,
    output_fn: Output = print,
    device_provider: DeviceProvider = discover_input_devices,
) -> AppConfig:
    """Select an input device, save config, and optionally verify audio/models."""
    paths = paths or AppPaths.defaults()
    paths.ensure_runtime_dirs()
    config = load_config(paths.config_file)
    devices, default_index = device_provider()
    selection, selected = _select_microphone(
        devices,
        default_index=default_index,
        requested_index=device_index,
        system_default=system_default,
        preferred=config.microphone,
        non_interactive=non_interactive,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    chosen_root = _select_records_root(
        config,
        requested=records_root,
        non_interactive=non_interactive,
        input_fn=input_fn,
    )
    configured = replace(
        config,
        microphone=selection,
        records_root=str(chosen_root),
    )
    configured.validate()
    chosen_root.mkdir(parents=True, exist_ok=True)
    save_config(paths.config_file, configured)

    if selection.mode is MicrophoneMode.SYSTEM_DEFAULT:
        output_fn(f"麥克風：跟隨 Windows 預設（目前 {selected.name}）")
    else:
        output_fn(f"麥克風：{selected.name} ({selected.host_api or '未知介面'})")
    output_fn(f"紀錄資料夾：{chosen_root}")
    output_fn(f"設定檔：{paths.config_file}")

    if test_audio:
        level = test_microphone(selected)
        output_fn(f"麥克風測試 RMS：{level:.6f}")
        if level < 0.0001:
            output_fn("警告：幾乎沒有收到聲音，請確認麥克風權限與音量。")

    if download_models:
        output_fn("開始下載並驗證本機辨識模型；此步驟可能需要數 GB。")
        ensure_models(
            configured.model,
            paths.models_dir,
            progress=_progress_reporter(output_fn),
        )
        output_fn("模型下載與驗證完成。")
    return configured


def _select_microphone(
    devices: Sequence[InputDevice],
    *,
    default_index: int | None,
    requested_index: int | None,
    system_default: bool,
    preferred: MicrophoneSelection,
    non_interactive: bool,
    input_fn: Callable[[str], str],
    output_fn: Output,
) -> tuple[MicrophoneSelection, InputDevice]:
    if requested_index is not None and system_default:
        raise SetupError("--device-index 與 --system-default 不可同時使用")
    default_device = _default_device(devices, default_index)
    if system_default:
        if default_device is None:
            raise SetupError("Windows WASAPI 沒有預設輸入裝置")
        return MicrophoneSelection(MicrophoneMode.SYSTEM_DEFAULT), default_device

    if requested_index is not None:
        for device in devices:
            if device.index == requested_index:
                return _fixed_choice(device, devices)
        raise SetupError(f"找不到裝置索引 {requested_index}")

    preferred_device = preferred.preferred_device
    preferred_positions = [
        position
        for position, item in enumerate(devices)
        if preferred_device is not None
        and item.name == preferred_device.name
        and item.host_api == preferred_device.host_api
    ]
    if len(preferred_positions) > 1:
        raise SetupError(
            "找到多個同名 WASAPI 麥克風；為避免切換到錯誤裝置，請先停用重複端點"
        )
    preferred_position = preferred_positions[0] if preferred_positions else None
    if non_interactive:
        if preferred.mode is MicrophoneMode.SYSTEM_DEFAULT:
            if default_device is None:
                raise SetupError("Windows WASAPI 沒有預設輸入裝置")
            return MicrophoneSelection(MicrophoneMode.SYSTEM_DEFAULT), default_device
        if preferred.mode is not MicrophoneMode.FIXED or preferred_position is None:
            raise SetupError(
                "尚未選擇麥克風；請啟動 App 或執行互動式 setup"
            )
        return _fixed_choice(devices[preferred_position], devices)

    output_fn("可用的輸入裝置：")
    default_name = f"（目前 {default_device.name}）" if default_device is not None else "（不可用）"
    output_fn(f"  1. 跟隨 Windows 預設{default_name}")
    for position, device in enumerate(devices, start=2):
        marker = " (目前預設)" if device.is_default else ""
        unavailable = (
            f" (不可固定：{device.binding_error})"
            if not device.fixed_binding_available
            else ""
        )
        output_fn(f"  {position}. {device.name} [{device.host_api}]{marker}{unavailable}")
    answer = input_fn("選擇麥克風 [預設 1]：").strip()
    if not answer:
        position = 0
    else:
        try:
            position = int(answer) - 1
        except ValueError as error:
            raise SetupError("麥克風選項必須是數字") from error
    if position == 0:
        if default_device is None:
            raise SetupError("Windows WASAPI 沒有預設輸入裝置")
        return MicrophoneSelection(MicrophoneMode.SYSTEM_DEFAULT), default_device
    device_position = position - 1
    if not 0 <= device_position < len(devices):
        raise SetupError("麥克風選項超出範圍")
    return _fixed_choice(devices[device_position], devices)


def _default_device(
    devices: Sequence[InputDevice],
    default_index: int | None,
) -> InputDevice | None:
    defaults = [device for device in devices if device.is_default]
    if len(defaults) == 1:
        return defaults[0]
    if len(defaults) > 1:
        raise SetupError("WASAPI 回報多個預設輸入裝置")
    return next((device for device in devices if device.index == default_index), None)


def _fixed_choice(
    device: InputDevice,
    devices: Sequence[InputDevice],
) -> tuple[MicrophoneSelection, InputDevice]:
    same_name = [
        item
        for item in devices
        if item.name.casefold() == device.name.casefold()
        and item.host_api.casefold() == device.host_api.casefold()
    ]
    if not device.fixed_binding_available or len(same_name) > 1:
        raise SetupError(
            device.binding_error
            or "找到多個同名 WASAPI 麥克風；無法安全固定，請改用 Windows 預設"
        )
    return (
        MicrophoneSelection(MicrophoneMode.FIXED, device.fingerprint()),
        device,
    )


def _select_records_root(
    config: AppConfig,
    *,
    requested: Path | None,
    non_interactive: bool,
    input_fn: Callable[[str], str],
) -> Path:
    if requested is not None:
        return requested.expanduser().resolve()
    current = Path(config.records_root).expanduser().resolve()
    if non_interactive:
        return current
    answer = input_fn(f"紀錄資料夾 [預設 {current}]：").strip()
    return Path(answer).expanduser().resolve() if answer else current


def _print_progress(output_fn: Output, name: str, completed: int, total: int) -> None:
    if total > 0:
        output_fn(f"  {name}: {completed / total:.0%}")
    else:
        output_fn(f"  {name}: {completed} bytes")


def _progress_reporter(output_fn: Output) -> Callable[[str, int, int], None]:
    last_bucket: dict[str, int] = {}

    def report(name: str, completed: int, total: int) -> None:
        bucket = int(completed * 20 / total) if total > 0 else completed // (100 * 1024**2)
        if last_bucket.get(name) == bucket and (total <= 0 or completed < total):
            return
        last_bucket[name] = bucket
        _print_progress(output_fn, name, completed, total)

    return report


__all__ = [
    "InputDevice",
    "ModelSetupProgress",
    "ModelSetupStatus",
    "SetupError",
    "check_runtime_models_for_setup",
    "discover_input_devices",
    "repair_runtime_models_for_setup",
    "run_setup",
    "test_microphone",
]
