from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig, MicrophoneMode, load_config, save_config
from .logging_setup import configure_logging, install_exception_hook
from .model_download import ModelDownloadError, ensure_models, verify_models
from .paths import AppPaths
from .settings_history import SettingsHistoryStore
from .setup_wizard import SetupError, discover_input_devices, run_setup
from .single_instance import NamedMutex, SingleInstanceError

LOGGER = logging.getLogger("auto_speech_journal.cli")


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-speech-journal",
        description="Windows 本機常駐語音紀錄工具",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="選擇麥克風與紀錄資料夾")
    setup.add_argument("--non-interactive", action="store_true")
    setup.add_argument("--records-root", type=Path)
    setup.add_argument("--device-index", type=int)
    setup.add_argument("--system-default", action="store_true")
    setup.add_argument("--test-microphone", action="store_true")
    setup.add_argument("--download-models", action="store_true")

    download_models = commands.add_parser(
        "download-models",
        help="下載並驗證固定版本的本機辨識模型",
    )
    download_models.add_argument("--verbose", action="store_true")

    run = commands.add_parser("run", help="啟動常駐視窗與錄音 worker")
    run.add_argument("--verbose", action="store_true")

    self_test = commands.add_parser("self-test", help="檢查安裝、麥克風與模型")
    self_test.add_argument("--verbose", action="store_true")
    self_test.add_argument("--deep-model-check", action="store_true")
    self_test.add_argument("--test-microphone", action="store_true")
    self_test.add_argument("--no-microphone-check", action="store_true")
    self_test.add_argument("--no-model-check", action="store_true")
    self_test.add_argument("--allow-cpu-finalizer", action="store_true")

    startup = commands.add_parser("startup", help="manage the owned per-user startup task")
    startup.add_argument("startup_action", choices=("enable", "disable", "status"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    paths = AppPaths.defaults()

    if args.command == "setup":
        try:
            run_setup(
                paths=paths,
                non_interactive=args.non_interactive,
                records_root=args.records_root,
                device_index=args.device_index,
                system_default=args.system_default,
                test_audio=args.test_microphone,
                download_models=args.download_models,
            )
        except (SetupError, ModelDownloadError, OSError, ValueError) as error:
            print(f"設定失敗：{error}", file=sys.stderr)
            return 2
        return 0

    if args.command == "startup":
        return run_startup_command(args.startup_action)

    paths.ensure_runtime_dirs()
    configure_logging(paths.logs_dir, verbose=args.verbose)
    install_exception_hook()
    if args.command == "download-models":
        return run_model_download(paths)
    if args.command == "self-test":
        return run_self_test(
            paths,
            deep_model_check=args.deep_model_check,
            microphone_test=args.test_microphone,
            check_microphone=not args.no_microphone_check,
            check_models=not args.no_model_check,
            allow_cpu_finalizer=args.allow_cpu_finalizer,
        )
    if args.command == "run":
        try:
            return run_application(paths)
        except SingleInstanceError as error:
            LOGGER.info("%s", error)
            return 0
        except Exception:
            LOGGER.exception("Application startup failed")
            return 1
    raise AssertionError(f"unhandled command: {args.command}")


def reconcile_startup_config(
    config: AppConfig,
    manager: Any,
    *,
    persist: Callable[[AppConfig], None],
) -> AppConfig:
    """Make config and the owned task agree without making application startup fatal."""

    def downgrade(detail: str) -> None:
        config.startup_enabled = False
        LOGGER.warning("Startup task disabled; using manual launch: %s", detail)
        try:
            persist(config)
        except Exception:
            LOGGER.exception("Unable to persist manual-start fallback")

    try:
        status = manager.status()
    except Exception as error:
        if config.startup_enabled:
            downgrade(str(error))
        else:
            LOGGER.warning("Unable to inspect startup task: %s", error)
        return config

    if config.startup_enabled:
        if not status.available:
            downgrade(status.detail)
            return config
        if not status.enabled:
            try:
                status = manager.enable()
            except Exception as error:
                downgrade(str(error))
                return config
            if not status.available or not status.enabled:
                downgrade(status.detail)
        return config

    if status.available:
        try:
            manager.disable()
        except Exception as error:
            LOGGER.warning("Unable to remove disabled startup task: %s", error)
    return config


def build_update_check_service(
    state_file: Path,
    current_version: str,
    *,
    factory: Callable[[Path, str], Any] | None = None,
) -> Any | None:
    if factory is None:
        from .update_check import ReleaseCheckService

        factory = ReleaseCheckService
    try:
        return factory(state_file, current_version)
    except Exception as error:
        LOGGER.warning("Update checks disabled for this session: %s", error)
        return None


def worker_paths_for_config(runtime_root: Path, config: AppConfig) -> AppPaths:
    return AppPaths(
        runtime_root=runtime_root,
        records_root=Path(config.records_root).expanduser(),
    )


def run_application(paths: AppPaths) -> int:
    """Compose runtime dependencies only after logging and the singleton guard exist."""
    with NamedMutex():
        config = load_config(paths.config_file)
        from .startup import StartupTaskManager

        startup_manager = StartupTaskManager()
        config = reconcile_startup_config(
            config,
            startup_manager,
            persist=lambda value: save_config(paths.config_file, value),
        )
        active_paths = AppPaths(
            runtime_root=paths.runtime_root,
            records_root=Path(config.records_root).expanduser(),
        )
        active_paths.ensure_runtime_dirs()

        # Heavy/native modules are deliberately delayed until the actual run command.
        from .controller import JournalController
        from .exporter import MarkdownExporter
        from .storage import JournalStorage
        from .ui import run_ui
        from .vocabulary import VocabularyStore
        from .workers import JournalWorkers

        storage = JournalStorage(active_paths.database_file, config.timezone)
        controller: JournalController | None = None
        try:
            report = storage.recover(active_paths.spool_dir)
            LOGGER.info("Storage recovery: %s", report)
            repaired = storage.repair_pathological_transcripts()
            if repaired:
                LOGGER.warning(
                    "Replaced %d pathological final transcripts with preview text",
                    len(repaired),
                )
            exporter = MarkdownExporter(storage, active_paths.records_root)
            try:
                rebuilt = exporter.rebuild_dirty()
                if rebuilt:
                    LOGGER.info("Rebuilt %d dirty hour files", len(rebuilt))
            except Exception:
                LOGGER.exception("Unable to rebuild all dirty hour files at startup")
            vocabulary = VocabularyStore(storage)
            controller = JournalController(
                storage=storage,
                exporter=exporter,
                workers=None,
                workers_factory=lambda current: JournalWorkers(
                    config=current,
                    paths=worker_paths_for_config(paths.runtime_root, current),
                ),
                config=config,
                vocabulary=vocabulary,
                save_config_callback=lambda value: save_config(paths.config_file, value),
                settings_history_store=SettingsHistoryStore(paths.settings_history_file),
            )
            update_service = build_update_check_service(
                paths.runtime_root / "update-check.json",
                __version__,
            )

            def apply_startup_setting(enabled: bool):
                status = startup_manager.enable() if enabled else startup_manager.disable()
                if not status.available:
                    LOGGER.warning(
                        "Startup task unavailable; using manual launch: %s",
                        status.detail,
                    )
                return status

            return run_ui(
                controller,
                startup_setting_callback=apply_startup_setting,
                update_check_callback=(
                    update_service.check_async if update_service is not None else None
                ),
            )
        finally:
            if controller is not None:
                controller.stop(suppress_errors=True)
            storage.close()


def run_model_download(paths: AppPaths) -> int:
    """Download pinned models without creating or changing application settings."""
    try:
        config = AppConfig()
        print("開始下載並驗證本機辨識模型；此步驟可能需要數 GB。")
        ensure_models(config.model, paths.models_dir, progress=_model_download_progress)
    except (ModelDownloadError, OSError, ValueError) as error:
        print(f"模型下載失敗：{error}", file=sys.stderr)
        return 2
    print("模型下載與驗證完成。")
    return 0


def _model_download_progress(name: str, completed: int, total: int) -> None:
    if total > 0:
        print(f"  {name}: {completed / total:.0%}")
    else:
        print(f"  {name}: {completed} bytes")


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")))


def run_startup_command(action: str) -> int:
    from .startup import StartupTaskError, StartupTaskManager

    manager = StartupTaskManager()
    try:
        if action == "enable":
            status = manager.enable()
        elif action == "disable":
            status = manager.disable()
        elif action == "status":
            status = manager.status()
        else:  # pragma: no cover - argparse owns this boundary
            raise AssertionError(f"unsupported startup action: {action}")
    except (OSError, StartupTaskError) as error:
        print(f"Startup task failed: {error}", file=sys.stderr)
        return 2
    payload = asdict(status)
    payload["launcher"] = str(status.launcher)
    _print_json(payload)
    # An unavailable Task Scheduler is a supported manual-start degradation.
    return 0


def run_self_test(
    paths: AppPaths,
    *,
    deep_model_check: bool = False,
    microphone_test: bool = False,
    check_microphone: bool = True,
    check_models: bool = True,
    allow_cpu_finalizer: bool = False,
) -> int:
    checks: list[CheckResult] = []
    checks.append(
        CheckResult(
            "Python",
            sys.version_info[:2] == (3, 11),
            sys.version.split()[0],
        )
    )
    checks.append(CheckResult("Windows", os.name == "nt", os.name))

    try:
        paths.ensure_runtime_dirs()
        with tempfile.NamedTemporaryFile(dir=paths.runtime_root, delete=True):
            pass
    except Exception as error:
        checks.append(CheckResult("執行資料夾", False, str(error)))
    else:
        checks.append(CheckResult("執行資料夾", True, str(paths.runtime_root)))

    config = None
    try:
        config = load_config(paths.config_file)
        checks.append(CheckResult("設定檔", True, str(paths.config_file)))
    except Exception as error:
        checks.append(CheckResult("設定檔", False, str(error)))

    devices = []
    configured_device = None
    if check_microphone:
        try:
            devices, _default_index = discover_input_devices()
            detail = f"{len(devices)} 個 WASAPI 輸入裝置"
            checks.append(CheckResult("WASAPI 麥克風列舉", bool(devices), detail))
        except Exception as error:
            checks.append(CheckResult("WASAPI 麥克風列舉", False, str(error)))

        if config is not None:
            selection = config.microphone
            if selection.mode is MicrophoneMode.SYSTEM_DEFAULT:
                defaults = [device for device in devices if device.is_default]
                configured_device = defaults[0] if len(defaults) == 1 else None
                detail = "跟隨 Windows 預設"
            elif (
                selection.mode is MicrophoneMode.FIXED
                and selection.preferred_device is not None
            ):
                preferred = selection.preferred_device
                configured = [
                    device
                    for device in devices
                    if device.name == preferred.name and device.host_api == preferred.host_api
                ]
                configured_device = configured[0] if len(configured) == 1 else None
                detail = f"{preferred.name} [{preferred.host_api}]"
            else:
                detail = f"尚未設定（{selection.mode.value}）"
            checks.append(CheckResult("設定麥克風", configured_device is not None, detail))

        if microphone_test and configured_device is not None:
            try:
                from .audio import measure_input_level

                level = measure_input_level(
                    configured_device.fingerprint(),
                    duration_ms=800,
                    follow_system_default=(
                        selection.mode is MicrophoneMode.SYSTEM_DEFAULT
                    ),
                )
                detail = (
                    f"RMS {level.rms:.6f}, peak {level.peak:.6f}, "
                    f"duration {level.duration_ms} ms"
                )
                if level.rms < 0.0001:
                    detail += "；警告：音量很低"
                checks.append(CheckResult("麥克風收音與 soxr", True, detail))
            except Exception as error:
                checks.append(CheckResult("麥克風收音與 soxr", False, str(error)))

    if config is not None:
        records_root = Path(config.records_root)
        probe_source = records_root / f".asj-self-test-{uuid.uuid4().hex}.tmp"
        probe_destination = records_root / f".asj-self-test-{uuid.uuid4().hex}.ok"
        try:
            records_root.mkdir(parents=True, exist_ok=True)
            with probe_source.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write("Auto Speech Journal output probe\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(probe_source, probe_destination)
            if probe_destination.read_text(encoding="utf-8") != (
                "Auto Speech Journal output probe\n"
            ):
                raise OSError("atomic output probe content mismatch")
            checks.append(CheckResult("紀錄資料夾原子寫入", True, str(records_root)))
        except Exception as error:
            checks.append(CheckResult("紀錄資料夾原子寫入", False, str(error)))
        finally:
            probe_source.unlink(missing_ok=True)
            probe_destination.unlink(missing_ok=True)

    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtWidgets import QApplication

        qt_application = QApplication.instance() or QApplication([])
        qt_application.processEvents()
        checks.append(CheckResult("PySide6/Qt 平台", True, f"PySide6 {pyside_version}"))
    except Exception as error:
        checks.append(CheckResult("PySide6/Qt 平台", False, str(error)))

    if check_models and config is not None:
        model_paths = None
        try:
            model_paths = verify_models(
                config.model,
                paths.models_dir,
                deep=deep_model_check,
            )
            checks.append(CheckResult("辨識模型", True, str(paths.models_dir)))
        except Exception as error:
            checks.append(CheckResult("辨識模型", False, str(error)))
        if deep_model_check and model_paths is not None:
            try:
                from .workers import probe_realtime_models

                realtime_probe = probe_realtime_models(config, paths.models_dir)
                checks.append(
                    CheckResult(
                        "即時辨識、VAD 與正體轉換",
                        realtime_probe.preview_loaded and realtime_probe.vad_loaded,
                        f"OpenCC={realtime_probe.normalized_example}",
                    )
                )
            except Exception as error:
                checks.append(CheckResult("即時辨識、VAD 與正體轉換", False, str(error)))
            try:
                import numpy as np
                import soundfile as sf

                from .finalizer_engine import FasterWhisperFinalizer

                with tempfile.TemporaryDirectory(dir=paths.runtime_root) as temporary:
                    probe_audio = Path(temporary) / "finalizer-probe.flac"
                    sf.write(
                        probe_audio,
                        np.zeros(config.audio_sample_rate, dtype=np.float32),
                        config.audio_sample_rate,
                        format="FLAC",
                        subtype="PCM_16",
                    )
                    finalizer = FasterWhisperFinalizer(
                        model_paths.final_dir,
                        language=config.language,
                        prefer_cuda=not allow_cpu_finalizer,
                        cuda_compute_type=config.model.final_compute_type,
                        cpu_compute_type=config.model.cpu_compute_type,
                        deadline_ms=config.final_deadline_ms,
                    )
                    try:
                        probe = finalizer.probe(probe_audio)
                    finally:
                        finalizer.close()
                expected_device = "cpu" if allow_cpu_finalizer else "cuda"
                passed = probe.active_device == expected_device
                detail = (
                    f"device={probe.active_device}, compute={probe.compute_type}, "
                    f"latency={probe.latency_ms} ms"
                )
                checks.append(CheckResult("最終辨識推論", passed, detail))
            except Exception as error:
                checks.append(CheckResult("最終辨識推論", False, str(error)))

    try:
        from .storage import JournalStorage

        storage = JournalStorage(paths.database_file)
        try:
            detail = ", ".join(f"{key}={value}" for key, value in storage.pragmas().items())
        finally:
            storage.close()
        checks.append(CheckResult("資料庫", True, detail))
    except Exception as error:
        checks.append(CheckResult("資料庫", False, str(error)))

    for check in checks:
        symbol = "PASS" if check.passed else "FAIL"
        print(f"[{symbol}] {check.name}: {check.detail}")
    return 0 if all(check.passed for check in checks) else 1


__all__ = [
    "build_update_check_service",
    "build_parser",
    "main",
    "reconcile_startup_config",
    "run_application",
    "run_model_download",
    "run_self_test",
    "run_startup_command",
    "worker_paths_for_config",
]
