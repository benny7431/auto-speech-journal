from __future__ import annotations

import argparse

import pytest

from auto_speech_journal import __version__, cli
from auto_speech_journal.config import AppConfig
from auto_speech_journal.paths import AppPaths


def _command_names(parser: argparse.ArgumentParser) -> set[str]:
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return set(subparsers.choices)


def test_parser_exposes_only_supported_commands() -> None:
    parser = cli.build_parser()

    assert _command_names(parser) == {
        "setup",
        "download-models",
        "run",
        "self-test",
        "startup",
    }
    assert parser.parse_args(["download-models"]).command == "download-models"
    assert parser.parse_args(["setup", "--system-default"]).system_default is True
    assert parser.parse_args(["self-test", "--no-microphone-check"]).no_microphone_check
    assert parser.parse_args(["startup", "status"]).startup_action == "status"


@pytest.mark.parametrize(
    "command",
    ["installer-probe", "provision", "request-shutdown", "repair"],
)
def test_parser_rejects_removed_installer_commands(command: str) -> None:
    with pytest.raises(SystemExit, match="2"):
        cli.build_parser().parse_args([command])


def test_version_option_uses_package_version(capsys: pytest.CaptureFixture[str]) -> None:
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


def test_worker_paths_follow_current_config_records_root(tmp_path) -> None:
    initial_records = tmp_path / "initial"
    selected_records = tmp_path / "selected-during-onboarding"
    paths = AppPaths(tmp_path / "runtime", initial_records)
    config = AppConfig(records_root=str(selected_records.resolve()))

    current = cli.worker_paths_for_config(paths.runtime_root, config)

    assert current.runtime_root == paths.runtime_root
    assert current.records_root == selected_records.resolve()
