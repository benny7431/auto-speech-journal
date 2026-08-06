from __future__ import annotations

from auto_speech_journal import cli
from auto_speech_journal.paths import AppPaths


def test_parser_exposes_model_download_and_microphone_skip() -> None:
    parser = cli.build_parser()

    download = parser.parse_args(["download-models"])
    self_test = parser.parse_args(["self-test", "--no-microphone-check"])
    setup = parser.parse_args(["setup", "--system-default"])

    assert download.command == "download-models"
    assert self_test.no_microphone_check is True
    assert setup.system_default is True


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
