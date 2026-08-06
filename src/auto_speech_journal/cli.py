from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import os
import sys
import tempfile
import time
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

    installer_probe = commands.add_parser(
        "installer-probe",
        help="check the frozen runtime without reading or creating user state",
    )
    installer_probe.add_argument("--isolated", action="store_true")

    provision = commands.add_parser(
        "provision",
        help="install versioned model assets from a signed release manifest",
    )
    provision.add_argument("--manifest", type=Path, required=True)
    provision.add_argument("--progress-json", type=Path, required=True)
    provision.add_argument("--verbose", action="store_true")

    shutdown = commands.add_parser(
        "request-shutdown",
        help="ask a running journal process to flush state and exit",
    )
    shutdown.add_argument("--timeout", type=float, default=30.0)

    startup = commands.add_parser("startup", help="manage the owned per-user startup task")
    startup.add_argument("startup_action", choices=("enable", "disable", "status"))

    repair = commands.add_parser("repair", help="repair installed models, GPU, or runtime")
    repair.add_argument("repair_target", choices=("models", "gpu", "runtime"))
    repair.add_argument("--manifest", type=Path)
    repair.add_argument("--progress-json", type=Path)
    repair.add_argument("--force-gpu", action="store_true")
    repair.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # The isolated readiness probe must not resolve or create any per-user paths.
    if args.command == "installer-probe":
        return run_installer_probe(isolated=args.isolated)

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

    if args.command == "request-shutdown":
        return run_shutdown_request(paths, timeout=args.timeout)
    if args.command == "startup":
        return run_startup_command(args.startup_action)
    if args.command == "provision":
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        configure_logging(paths.logs_dir, verbose=args.verbose)
        return run_provision_command(
            paths,
            manifest_path=args.manifest,
            progress_path=args.progress_json,
        )
    if args.command == "repair":
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        configure_logging(paths.logs_dir, verbose=args.verbose)
        return run_repair_command(
            paths,
            target=args.repair_target,
            manifest_path=args.manifest,
            progress_path=args.progress_json,
            force_gpu=args.force_gpu,
        )

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

    if status.available and status.owned:
        try:
            manager.disable()
        except Exception as error:
            LOGGER.warning("Unable to remove disabled owned startup task: %s", error)
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
        from .gpu_runtime import activate_gpu_runtime
        from .shutdown_ipc import ShutdownServer, queue_qt_quit
        from .storage import JournalStorage
        from .ui import run_ui
        from .vocabulary import VocabularyStore
        from .workers import JournalWorkers

        activate_gpu_runtime(paths.runtime_root / "gpu-runtime")
        storage = JournalStorage(active_paths.database_file, config.timezone)
        controller: JournalController | None = None
        shutdown_server: ShutdownServer | None = None
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

            def queue_shutdown() -> bool:
                # The IPC server starts immediately before run_ui creates QApplication.
                # A short bounded retry closes that startup race without blocking shutdown.
                for _attempt in range(20):
                    if queue_qt_quit():
                        return True
                    time.sleep(0.05)
                return False

            shutdown_server = ShutdownServer(paths.runtime_root, queue_shutdown).start()
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
            if shutdown_server is not None:
                shutdown_server.stop()


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


class _CliProgressReporter:
    def __init__(self, writer: Callable[[object], None] | None = None) -> None:
        self.writer = writer
        self._last: tuple[str, str | None, int | None] | None = None

    def __call__(self, event: object) -> None:
        status = str(getattr(event, "status", "progress"))
        asset = getattr(event, "asset", None)
        completed = int(getattr(event, "completed", 0))
        total = int(getattr(event, "total", 0))
        percent = int(completed * 100 / total) if total > 0 else None
        current = (status, asset, percent)
        if status == "downloading" and self._last == current:
            return
        self._last = current
        if self.writer is not None:
            self.writer(event)
        progress_text = f" {percent}%" if percent is not None else ""
        asset_text = f" {asset}" if asset else ""
        eta = getattr(event, "eta_seconds", None)
        eta_text = f" ETA {eta}s" if eta is not None else ""
        message = getattr(event, "message", None)
        message_text = f" - {message}" if message else ""
        print(
            f"[{status}]{asset_text}{progress_text}{eta_text}{message_text}",
            flush=True,
        )


def run_installer_probe(*, isolated: bool = False) -> int:
    """Check frozen dependencies without creating AppPaths or loading user configuration."""
    package_root = Path(__file__).resolve().parent
    checks: list[CheckResult] = [
        CheckResult("python", sys.version_info[:2] == (3, 11), sys.version.split()[0]),
        CheckResult("version", _semantic_version(__version__), __version__),
        CheckResult(
            "qml",
            (package_root / "qml" / "JournalWindow.qml").is_file(),
            str(package_root / "qml" / "JournalWindow.qml"),
        ),
        CheckResult(
            "scene-assets",
            (package_root / "assets" / "scenes" / "manifest.json").is_file(),
            str(package_root / "assets" / "scenes" / "manifest.json"),
        ),
    ]
    for module in (
        "PySide6",
        "numpy",
        "onnxruntime",
        "sherpa_onnx",
        "ctranslate2",
        "faster_whisper",
        "opencc",
        "soundfile",
        "sounddevice",
        "soxr",
    ):
        checks.append(_probe_import(module))

    if bool(getattr(sys, "frozen", False)):
        checks.append(_probe_qml_component(package_root / "qml" / "JournalWindow.qml"))
    else:
        checks.append(
            CheckResult(
                "qml-component",
                True,
                "deferred until frozen runtime",
            )
        )

    if isolated:
        try:
            with tempfile.TemporaryDirectory(prefix="asj-installer-probe-") as temporary:
                probe = Path(temporary) / "atomic.tmp"
                installed = Path(temporary) / "atomic.ok"
                probe.write_bytes(b"probe")
                os.replace(probe, installed)
                passed = installed.read_bytes() == b"probe"
        except OSError as error:
            checks.append(CheckResult("isolated-write", False, str(error)))
        else:
            checks.append(CheckResult("isolated-write", passed, "temporary directory"))

    if bool(getattr(sys, "frozen", False)):
        forbidden = ("nvidia", "torch", "transformers")
        bundled = [name for name in forbidden if importlib.util.find_spec(name) is not None]
        checks.append(
            CheckResult(
                "excluded-build-dependencies",
                not bundled,
                "none" if not bundled else ",".join(bundled),
            )
        )
        frozen_root = Path(sys.executable).resolve().parent
        model_payloads = [
            path
            for path in frozen_root.rglob("*")
            if path.is_file()
            and (path.suffix.casefold() in {".onnx", ".safetensors"} or path.name == "model.bin")
        ]
        checks.append(
            CheckResult(
                "excluded-model-payloads",
                not model_payloads,
                "none" if not model_payloads else ",".join(path.name for path in model_payloads),
            )
        )
        checks.append(CheckResult("windows", os.name == "nt", os.name))

    _print_json(
        {
            "schema_version": 1,
            "version": __version__,
            "frozen": bool(getattr(sys, "frozen", False)),
            "isolated": isolated,
            "ready": all(check.passed for check in checks),
            "checks": [asdict(check) for check in checks],
        }
    )
    return 0 if all(check.passed for check in checks) else 1


def _probe_import(module: str) -> CheckResult:
    try:
        imported = importlib.import_module(module)
    except Exception as error:
        return CheckResult(f"import:{module}", False, f"{type(error).__name__}: {error}")
    version = getattr(imported, "__version__", None)
    return CheckResult(f"import:{module}", True, str(version or "loaded"))


def _probe_qml_component(qml_path: Path) -> CheckResult:
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
        os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        from PySide6.QtQuickControls2 import QQuickStyle

        QQuickStyle.setStyle("Basic")
        application = QGuiApplication.instance()
        if application is None:
            application = QGuiApplication(["AutoSpeechJournal.CLI", "-platform", "offscreen"])
        engine = QQmlEngine()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_path.resolve())))
        if component.status() == QQmlComponent.Status.Loading:
            application.processEvents()
        if component.status() != QQmlComponent.Status.Ready:
            detail = "; ".join(error.toString() for error in component.errors())
            return CheckResult("qml-component", False, detail or component.errorString())
    except Exception as error:
        return CheckResult("qml-component", False, f"{type(error).__name__}: {error}")
    return CheckResult("qml-component", True, str(qml_path))


def _semantic_version(value: str) -> bool:
    parts = value.lstrip("v").split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def run_provision_command(
    paths: AppPaths,
    *,
    manifest_path: Path,
    progress_path: Path,
) -> int:
    from .provisioning import ProgressFile, ProvisioningError, load_manifest, provision

    writer = ProgressFile(progress_path)
    reporter = _CliProgressReporter(writer)
    release = manifest_path.stem
    try:
        manifest = load_manifest(manifest_path)
        release = manifest.release
        result = provision(manifest, paths.models_dir, progress=reporter)
    except (OSError, ValueError, ProvisioningError) as error:
        writer.failed(release, error)
        print(f"Provisioning failed: {error}", file=sys.stderr)
        return 2
    _print_json(asdict(result))
    return 0


def run_shutdown_request(paths: AppPaths, *, timeout: float) -> int:
    from .shutdown_ipc import request_shutdown

    try:
        result = request_shutdown(paths.runtime_root, timeout=timeout)
    except (OSError, ValueError) as error:
        print(f"Shutdown request failed: {error}", file=sys.stderr)
        return 2
    _print_json(asdict(result))
    return 0 if result.succeeded else 2


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


def _default_repair_manifest(paths: AppPaths, filename: str) -> Path | None:
    from .provisioning import find_manifest

    found = find_manifest(filename, runtime_root=paths.runtime_root)
    if found is not None:
        return found
    checkout = Path.cwd() / "packaging" / "manifests" / filename
    return checkout if checkout.is_file() else None


def run_repair_command(
    paths: AppPaths,
    *,
    target: str,
    manifest_path: Path | None,
    progress_path: Path | None,
    force_gpu: bool,
) -> int:
    from .provisioning import ProgressFile, ProvisioningError

    if target == "runtime":
        return run_installer_probe(isolated=True)

    filename = "models-v1.json" if target == "models" else "cuda-runtime-v1.json"
    selected_manifest = manifest_path or _default_repair_manifest(paths, filename)
    writer = ProgressFile(progress_path) if progress_path is not None else None
    reporter = _CliProgressReporter(writer)
    release = (
        selected_manifest.stem
        if selected_manifest is not None
        else filename.removesuffix(".json")
    )
    try:
        if target == "models":
            from .provisioning import load_manifest, provision

            if selected_manifest is None:
                raise ProvisioningError(f"unable to find {filename}; pass --manifest")
            manifest = load_manifest(selected_manifest)
            release = manifest.release
            result = provision(manifest, paths.models_dir, progress=reporter)
        elif target == "gpu":
            from .gpu_runtime import (
                PINNED_GPU_MANIFEST,
                install_gpu_runtime,
                load_gpu_manifest,
            )
            from .model_download import resolve_model_paths

            manifest = (
                load_gpu_manifest(selected_manifest)
                if selected_manifest is not None
                else PINNED_GPU_MANIFEST
            )
            release = manifest.release
            result = install_gpu_runtime(
                paths.runtime_root,
                manifest=manifest,
                force=force_gpu,
                progress=reporter,
                model_dir=resolve_model_paths(paths.models_dir).final_dir,
            )
        else:  # pragma: no cover - argparse owns this boundary
            raise AssertionError(f"unsupported repair target: {target}")
    except (OSError, ValueError, ProvisioningError) as error:
        if writer is not None:
            writer.failed(release, error)
        print(f"Repair failed: {error}", file=sys.stderr)
        return 2
    _print_json(asdict(result))
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
    "run_installer_probe",
    "run_model_download",
    "run_provision_command",
    "run_repair_command",
    "run_self_test",
    "run_shutdown_request",
    "run_startup_command",
    "worker_paths_for_config",
]
