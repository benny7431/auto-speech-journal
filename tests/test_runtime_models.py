from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from auto_speech_journal.config import ModelConfig
from auto_speech_journal.model_download import (
    FINAL_SPEC,
    PREVIEW_SPEC,
    VAD_SPEC,
    ModelDownloadError,
    ModelVerificationError,
    default_runtime_model_manifest,
    ensure_models,
    load_runtime_model_manifest,
    verify_models,
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _small_manifest(tmp_path: Path) -> tuple[Path, dict[tuple[str, str], bytes]]:
    manifest = json.loads(default_runtime_model_manifest().read_text(encoding="utf-8"))
    payloads: dict[tuple[str, str], bytes] = {}
    for model in manifest["models"]:
        for file in model["files"]:
            payload = f"{model['name']}:{file['path']}".encode()
            file["size"] = len(payload)
            file["sha256"] = _sha256(payload)
            payloads[(model["repository"], file["path"])] = payload
    path = tmp_path / "runtime-models-v1.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, payloads


def _downloader(payloads: dict[tuple[str, str], bytes], calls: list[dict[str, object]]):
    def download(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        local_dir = Path(str(kwargs["local_dir"]))
        target = local_dir / str(kwargs["filename"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payloads[(str(kwargs["repo_id"]), str(kwargs["filename"]))])
        return str(target)

    return download


def test_packaged_manifest_pins_only_required_ready_to_run_files() -> None:
    manifest = load_runtime_model_manifest()
    models = {model.destination: model for model in manifest.models}

    assert manifest.release == "runtime-models-v1"
    assert manifest.provider == "huggingface"
    assert models[PREVIEW_SPEC.install_path].revision == PREVIEW_SPEC.revision
    assert models[VAD_SPEC.install_path].revision == VAD_SPEC.revision
    assert models[FINAL_SPEC.install_path].revision == FINAL_SPEC.revision
    assert tuple(file.path for file in models[PREVIEW_SPEC.install_path].files) == (
        "encoder.int8.onnx",
        "decoder.int8.onnx",
        "tokens.txt",
    )
    assert tuple(file.path for file in models[FINAL_SPEC.install_path].files) == (
        "config.json",
        "model.bin",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    )
    assert all(len(model.revision) == 40 for model in manifest.models)
    assert all(model.revision in model.source.url for model in manifest.models)
    assert all(model.license.spdx and model.source.url for model in manifest.models)
    assert all(
        len(file.sha256) == 64 and file.size > 0
        for model in manifest.models
        for file in model.files
    )


def test_ensure_models_uses_official_hub_contract_and_exact_revisions(tmp_path: Path) -> None:
    manifest_path, payloads = _small_manifest(tmp_path)
    calls: list[dict[str, object]] = []
    progress: list[tuple[str, int, int]] = []

    paths = ensure_models(
        ModelConfig(),
        tmp_path / "models",
        progress=lambda *value: progress.append(value),
        manifest_path=manifest_path,
        downloader=_downloader(payloads, calls),
    )

    assert paths.preview_encoder.is_file()
    assert paths.vad_model.is_file()
    assert (paths.final_dir / "model.bin").is_file()
    assert len(calls) == 9
    expected_revisions = {
        PREVIEW_SPEC.key: PREVIEW_SPEC.revision,
        VAD_SPEC.key: VAD_SPEC.revision,
        FINAL_SPEC.key: FINAL_SPEC.revision,
    }
    for call in calls:
        assert call["revision"] == expected_revisions[call["repo_id"]]
        assert call["local_dir"]
        assert call["token"] is False
        assert "cache_dir" not in call
    assert progress[-1][1] == progress[-1][2]


def test_verified_local_files_are_reused_without_network(tmp_path: Path) -> None:
    manifest_path, payloads = _small_manifest(tmp_path)
    models_dir = tmp_path / "models"
    ensure_models(
        ModelConfig(),
        models_dir,
        manifest_path=manifest_path,
        downloader=_downloader(payloads, []),
    )

    def unexpected(**_kwargs: object) -> str:
        raise AssertionError("verified Hugging Face files should be reused")

    ensure_models(
        ModelConfig(),
        models_dir,
        manifest_path=manifest_path,
        downloader=unexpected,
    )


def test_corrupt_local_file_is_force_downloaded(tmp_path: Path) -> None:
    manifest_path, payloads = _small_manifest(tmp_path)
    models_dir = tmp_path / "models"
    ensure_models(
        ModelConfig(),
        models_dir,
        manifest_path=manifest_path,
        downloader=_downloader(payloads, []),
    )
    target = models_dir / PREVIEW_SPEC.install_path / "encoder.int8.onnx"
    target.write_bytes(b"corrupt")
    calls: list[dict[str, object]] = []

    ensure_models(
        ModelConfig(),
        models_dir,
        manifest_path=manifest_path,
        downloader=_downloader(payloads, calls),
    )

    assert len(calls) == 1
    assert calls[0]["force_download"] is True


def test_download_failure_can_be_retried_without_changing_program(tmp_path: Path) -> None:
    manifest_path, payloads = _small_manifest(tmp_path)
    models_dir = tmp_path / "models"
    attempts = 0

    def flaky(**kwargs: object) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("offline")
        return _downloader(payloads, [])(**kwargs)

    with pytest.raises(ModelDownloadError, match="offline"):
        ensure_models(
            ModelConfig(),
            models_dir,
            manifest_path=manifest_path,
            downloader=flaky,
        )

    paths = ensure_models(
        ModelConfig(),
        models_dir,
        manifest_path=manifest_path,
        downloader=_downloader(payloads, []),
    )
    assert paths.final_dir.is_dir()


def test_wrong_download_hash_is_rejected(tmp_path: Path) -> None:
    manifest_path, _payloads = _small_manifest(tmp_path)

    def corrupt(**kwargs: object) -> str:
        target = Path(str(kwargs["local_dir"])) / str(kwargs["filename"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"wrong")
        return str(target)

    with pytest.raises(ModelVerificationError, match="wrong size|SHA-256"):
        ensure_models(
            ModelConfig(),
            tmp_path / "models",
            manifest_path=manifest_path,
            downloader=corrupt,
        )


def test_manifest_rejects_floating_revision(tmp_path: Path) -> None:
    raw = json.loads(default_runtime_model_manifest().read_text(encoding="utf-8"))
    raw["models"][0]["revision"] = "main"
    path = tmp_path / "runtime-models-v1.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ModelVerificationError, match="40-character commit"):
        load_runtime_model_manifest(path)


def test_verify_models_reports_missing_runtime_files(tmp_path: Path) -> None:
    with pytest.raises(ModelVerificationError, match="missing"):
        verify_models(ModelConfig(), tmp_path / "models")
