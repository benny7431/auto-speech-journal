from __future__ import annotations

import json

import pytest

from auto_speech_journal import __version__, cli
from auto_speech_journal.config import AppConfig
from auto_speech_journal.gpu_runtime import GpuDetection, GpuInstallResult
from auto_speech_journal.paths import AppPaths
from auto_speech_journal.provisioning import ProvisionEvent


def test_parser_exposes_model_download_and_microphone_skip() -> None:
    parser = cli.build_parser()

    download = parser.parse_args(["download-models"])
    self_test = parser.parse_args(["self-test", "--no-microphone-check"])
    setup = parser.parse_args(["setup", "--system-default"])
    probe = parser.parse_args(["installer-probe", "--isolated"])
    provision = parser.parse_args(
        [
            "provision",
            "--manifest",
            "models-v1.json",
            "--progress-json",
            "progress.json",
        ]
    )
    shutdown = parser.parse_args(["request-shutdown", "--timeout", "12"])
    startup = parser.parse_args(["startup", "status"])
    repair = parser.parse_args(["repair", "gpu", "--force-gpu"])

    assert download.command == "download-models"
    assert self_test.no_microphone_check is True
    assert setup.system_default is True
    assert probe.isolated is True
    assert provision.manifest.name == "models-v1.json"
    assert provision.progress_json.name == "progress.json"
    assert shutdown.timeout == 12
    assert startup.startup_action == "status"
    assert repair.repair_target == "gpu"
    assert repair.force_gpu is True


def test_version_option_uses_package_version(capsys) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli.build_parser().parse_args(["--version"])

    assert capsys.readouterr().out.strip() == f"auto-speech-journal {__version__}"


def test_download_models_does_not_create_config(tmp_path, monkeypatch) -> None:
    paths = AppPaths(tmp_path / "runtime", tmp_path / "records")
    calls = []

    def ensure_models(model, models_dir, progress):
        calls.append((model, models_dir, progress))

    monkeypatch.setattr(cli, "ensure_models", ensure_models)

    assert cli.run_model_download(paths) == 0
    assert len(calls) == 1
    assert calls[0][1] == paths.models_dir
    assert not paths.config_file.exists()


def test_download_models_does_not_modify_existing_config(tmp_path, monkeypatch) -> None:
    paths = AppPaths(tmp_path / "runtime", tmp_path / "records")
    paths.runtime_root.mkdir(parents=True)
    original = b'{"preserve": "exactly"}\n'
    paths.config_file.write_bytes(original)
    monkeypatch.setattr(cli, "ensure_models", lambda *_args, **_kwargs: None)

    assert cli.run_model_download(paths) == 0
    assert paths.config_file.read_bytes() == original


def test_isolated_installer_probe_never_resolves_app_paths(monkeypatch, capsys) -> None:
    def fail_defaults(_cls):
        raise AssertionError("AppPaths.defaults must not run for isolated probe")

    monkeypatch.setattr(cli.AppPaths, "defaults", classmethod(fail_defaults))

    result = cli.main(["installer-probe", "--isolated"])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["isolated"] is True
    assert payload["ready"] is True


def test_invalid_version_disables_update_service_without_raising(tmp_path, caplog) -> None:
    def invalid_factory(_path, _version):
        raise ValueError("nonsemantic version")

    service = cli.build_update_check_service(
        tmp_path / "update.json",
        "0+unknown",
        factory=invalid_factory,
    )

    assert service is None
    assert "Update checks disabled" in caplog.text


def test_repair_models_reports_console_progress_without_progress_file(tmp_path, capsys) -> None:
    manifest = tmp_path / "models-v1.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "release": "models-v1", "assets": []}),
        encoding="utf-8",
    )
    paths = AppPaths(tmp_path / "runtime", tmp_path / "records")

    result = cli.run_repair_command(
        paths,
        target="models",
        manifest_path=manifest,
        progress_path=None,
        force_gpu=False,
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "[preflight]" in output
    assert "[complete]" in output


def test_console_progress_is_percent_and_eta_throttled(capsys) -> None:
    reporter = cli._CliProgressReporter()
    event = ProvisionEvent("downloading", "models-v1", "preview", 10, 100, 9)

    reporter(event)
    reporter(event)
    output = capsys.readouterr().out

    assert output.count("[downloading]") == 1
    assert "10%" in output
    assert "ETA 9s" in output


def test_gpu_probe_failure_is_nonfatal_cpu_fallback(tmp_path, monkeypatch, capsys) -> None:
    from auto_speech_journal import gpu_runtime

    detection = GpuDetection(True, True, "999.0", ("GPU",), "compatible")

    def fallback(*_args, **_kwargs):
        return GpuInstallResult("cpu", True, detection, None, "CPU fallback: probe failed")

    monkeypatch.setattr(gpu_runtime, "install_gpu_runtime", fallback)
    paths = AppPaths(tmp_path / "runtime", tmp_path / "records")

    result = cli.run_repair_command(
        paths,
        target="gpu",
        manifest_path=None,
        progress_path=None,
        force_gpu=False,
    )
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])

    assert result == 0
    assert payload["active_device"] == "cpu"


def test_worker_paths_follow_current_config_records_root(tmp_path) -> None:
    initial_records = tmp_path / "initial"
    selected_records = tmp_path / "selected-during-onboarding"
    paths = AppPaths(tmp_path / "runtime", initial_records)
    config = AppConfig(records_root=str(selected_records.resolve()))

    current = cli.worker_paths_for_config(paths.runtime_root, config)

    assert current.runtime_root == paths.runtime_root
    assert current.records_root == selected_records.resolve()
