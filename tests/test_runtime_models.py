from __future__ import annotations

import hashlib
import json
import runpy
import threading
from collections import namedtuple
from pathlib import Path

import pytest

from auto_speech_journal import model_download, runtime_models
from auto_speech_journal.model_download import ModelVerificationError
from auto_speech_journal.provisioning import ProvisioningError, VerificationError
from auto_speech_journal.runtime_models import (
    RUNTIME_MODEL_MARKER,
    RuntimeModelManifestError,
    calculate_runtime_model_disk_bytes,
    huggingface_download_url,
    load_runtime_model_manifest,
    provision_runtime_models,
    verify_installed_runtime_models,
)

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_MANIFEST = ROOT / "packaging" / "manifests" / "runtime-models-v1.json"
VALIDATOR = runpy.run_path(
    str(ROOT / "packaging" / "models" / "validate_runtime_model_manifest.py")
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _model_record(
    *,
    name: str = "test-model",
    destination: str = "ready-model",
    files: dict[str, bytes] | None = None,
    revision: str = "a" * 40,
) -> dict[str, object]:
    payloads = files or {"config.json": b"{}", "model.bin": b"model"}
    return {
        "name": name,
        "repository": "owner/ready-runtime-model",
        "revision": revision,
        "format": "ctranslate2-float16",
        "destination": destination,
        "license": {
            "spdx": "MIT",
            "url": "https://example.test/license",
        },
        "source": {
            "url": "https://example.test/source",
            "description": "Ready-to-run test fixture.",
        },
        "files": [
            {"path": path, "size": len(value), "sha256": _sha256(value)}
            for path, value in payloads.items()
        ],
    }


def _write_manifest(
    tmp_path: Path,
    *,
    models: list[dict[str, object]] | None = None,
    release: str = "runtime-models-v1-test",
) -> tuple[Path, object]:
    path = tmp_path / "runtime-models-v1.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release": release,
                "provider": "huggingface",
                "models": models or [_model_record()],
            }
        ),
        encoding="utf-8",
    )
    return path, load_runtime_model_manifest(path)


def _payloads(model_record: dict[str, object]) -> dict[str, bytes]:
    files = model_record["files"]
    assert isinstance(files, list)
    values = {"config.json": b"{}", "model.bin": b"model"}
    return {str(file["path"]): values[str(file["path"])] for file in files}


def _install(
    manifest,
    destination: Path,
    payloads: dict[str, bytes],
):
    def downloader(model, file, part: Path, progress) -> None:
        del model
        part.parent.mkdir(parents=True, exist_ok=True)
        content = payloads[file.path]
        part.write_bytes(content)
        progress(len(content), len(content))

    return provision_runtime_models(manifest, destination, downloader=downloader)


def test_canonical_manifest_is_exactly_pinned_to_ready_hugging_face_files() -> None:
    manifest = VALIDATOR["validate_runtime_model_manifest"](CANONICAL_MANIFEST)

    assert manifest.release == "runtime-models-v1"
    assert manifest.provider == "huggingface"
    assert {model.format for model in manifest.models} == {
        "sherpa-onnx-paraformer-int8",
        "sherpa-onnx-silero-vad-v4",
        "ctranslate2-float16",
    }
    assert all(len(model.revision) == 40 for model in manifest.models)
    assert all(set(model.revision) <= set("0123456789abcdef") for model in manifest.models)
    assert not any(
        file.path.endswith((".safetensors", ".pt", ".pth"))
        for model in manifest.models
        for file in model.files
    )
    assert {
        file.path
        for model in manifest.models
        for file in model.files
        if model.name == "whisper-large-v3-turbo"
    } == {
        "config.json",
        "model.bin",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    }


def test_canonical_validator_rejects_source_or_digest_drift(tmp_path: Path) -> None:
    raw = json.loads(CANONICAL_MANIFEST.read_text(encoding="utf-8"))
    raw["models"][0]["revision"] = "b" * 40
    raw["models"][0]["files"][0]["sha256"] = "c" * 64
    mutated = tmp_path / "runtime-models-v1.json"
    mutated.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected revision|unexpected file inventory"):
        VALIDATOR["validate_runtime_model_manifest"](mutated)


def test_application_contract_rejects_schema_valid_model_source_substitution(
    tmp_path: Path,
) -> None:
    canonical = load_runtime_model_manifest(CANONICAL_MANIFEST)
    model_download._validate_manifest_contract(canonical)
    raw = json.loads(CANONICAL_MANIFEST.read_text(encoding="utf-8"))
    raw["models"][2]["revision"] = "b" * 40
    substituted = tmp_path / "runtime-models-v1.json"
    substituted.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ModelVerificationError, match="do not match"):
        model_download._validate_manifest_contract(
            load_runtime_model_manifest(substituted)
        )


def test_legacy_github_model_bundle_release_tools_are_removed() -> None:
    obsolete = (
        "packaging/manifests/models-v1.json",
        "packaging/manifests/models-v1.sha256",
        "packaging/models/build_model_bundle.py",
        "packaging/models/validate_model_manifest.py",
        "packaging/models/verify_model_release_assets.py",
    )

    assert [path for path in obsolete if (ROOT / path).exists()] == []


def test_runtime_model_manifest_does_not_mix_cuda_runtime_wheels() -> None:
    raw = CANONICAL_MANIFEST.read_text(encoding="utf-8").casefold()

    assert "nvidia" not in raw
    assert ".whl" not in raw
    assert "cuda-runtime" not in raw
    assert (ROOT / "packaging" / "manifests" / "cuda-runtime-v1.json").is_file()


def test_hugging_face_url_is_derived_from_repository_revision_and_file_path(
    tmp_path: Path,
) -> None:
    record = _model_record(files={"tokenizer-files/tokens.json": b"tokens"})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    model = manifest.models[0]

    assert huggingface_download_url(model, model.files[0]) == (
        "https://huggingface.co/owner/ready-runtime-model/resolve/"
        f"{'a' * 40}/tokenizer-files/tokens.json"
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw["models"][0].__setitem__("revision", "main"), "full 40-character"),
        (
            lambda raw: raw["models"][0].__setitem__(
                "url", "https://huggingface.co/owner/repo/resolve/main/model.bin"
            ),
            "unexpected fields",
        ),
        (
            lambda raw: raw["models"][0]["files"][0].__setitem__("path", "../escape.bin"),
            "stay inside",
        ),
        (
            lambda raw: raw["models"][0]["files"][0].__setitem__("sha256", "0" * 64),
            "invalid SHA-256",
        ),
        (lambda raw: raw["models"][0].pop("license"), "declare license metadata"),
        (lambda raw: raw["models"][0].pop("source"), "declare source metadata"),
    ],
)
def test_manifest_rejects_floating_injectable_or_unsafe_authority(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    raw = {
        "schema_version": 1,
        "release": "runtime-models-v1-test",
        "provider": "huggingface",
        "models": [_model_record()],
    }
    mutation(raw)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RuntimeModelManifestError, match=message):
        load_runtime_model_manifest(path)


def test_provision_stages_entire_group_then_atomically_repairs_and_reuses(tmp_path: Path) -> None:
    record = _model_record()
    payloads = _payloads(record)
    _path, manifest = _write_manifest(tmp_path, models=[record])
    destination = tmp_path / "models"
    target = destination / "ready-model"
    target.mkdir(parents=True)
    (target / "old.bin").write_bytes(b"preserve-until-commit")
    failed_once = False
    calls: list[str] = []

    def interrupted(model, file, part: Path, progress) -> None:
        nonlocal failed_once
        del model
        calls.append(file.path)
        part.parent.mkdir(parents=True, exist_ok=True)
        if file.path == "model.bin" and not failed_once:
            failed_once = True
            part.write_bytes(payloads[file.path][:2])
            progress(2, len(payloads[file.path]))
            raise ProvisioningError("simulated disconnect")
        content = payloads[file.path]
        if file.path == "model.bin" and part.is_file():
            offset = part.stat().st_size
            assert part.read_bytes() == content[:offset]
            with part.open("ab") as handle:
                handle.write(content[offset:])
        else:
            part.write_bytes(content)
        progress(len(content), len(content))

    with pytest.raises(ProvisioningError, match="simulated disconnect"):
        provision_runtime_models(manifest, destination, downloader=interrupted)

    assert (target / "old.bin").read_bytes() == b"preserve-until-commit"
    partial = destination / ".downloads/runtime-models-v1-test/ready-model/model.bin.part"
    assert partial.read_bytes() == b"mo"

    result = provision_runtime_models(manifest, destination, downloader=interrupted)

    assert result.installed == ("test-model",)
    assert calls.count("config.json") == 1
    assert calls.count("model.bin") == 2
    assert not (target / "old.bin").exists()
    assert (target / "model.bin").read_bytes() == b"model"
    assert (target / RUNTIME_MODEL_MARKER).is_file()
    verify_installed_runtime_models(manifest, destination)

    reused = provision_runtime_models(
        manifest,
        destination,
        downloader=lambda *_args: pytest.fail("verified model was downloaded again"),
    )
    assert reused.reused == ("test-model",)


def test_hash_mismatch_preserves_installed_directory_and_repair_succeeds(tmp_path: Path) -> None:
    record = _model_record(files={"model.bin": b"right"})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    destination = tmp_path / "models"
    target = destination / "ready-model"
    target.mkdir(parents=True)
    (target / "old.bin").write_bytes(b"old")
    attempts = []

    def corrupt(_model, _file, part: Path, _progress) -> None:
        attempts.append(part)
        part.write_bytes(b"wrong")

    with pytest.raises(VerificationError, match="wrong SHA-256"):
        provision_runtime_models(manifest, destination, downloader=corrupt)

    assert len(attempts) == 2
    assert (target / "old.bin").read_bytes() == b"old"
    assert not (
        destination / ".downloads/runtime-models-v1-test/ready-model/model.bin.part"
    ).exists()

    repaired = _install(manifest, destination, {"model.bin": b"right"})
    assert repaired.installed == ("test-model",)
    assert (target / "model.bin").read_bytes() == b"right"


def test_runtime_model_progress_reports_total_and_eta(tmp_path: Path) -> None:
    content = b"0123456789"
    record = _model_record(files={"model.bin": content})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    events = []
    ticks = iter(float(value) for value in range(20))

    def downloader(_model, _file, part: Path, progress) -> None:
        part.write_bytes(content)
        progress(5, len(content))
        progress(len(content), len(content))

    provision_runtime_models(
        manifest,
        tmp_path / "models",
        downloader=downloader,
        progress=events.append,
        clock=lambda: next(ticks),
    )

    halfway = next(
        event
        for event in events
        if event.status == "downloading" and event.completed == 5
    )
    assert halfway.total == len(content)
    assert halfway.eta_seconds == 2
    assert events[-1].status == "complete"


def test_atomic_group_swap_failure_restores_previous_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _model_record(files={"model.bin": b"replacement"})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    destination = tmp_path / "models"
    target = destination / "ready-model"
    target.mkdir(parents=True)
    (target / "old.bin").write_bytes(b"old")
    staged = destination / ".downloads/runtime-models-v1-test/ready-model"
    real_replace = runtime_models.os.replace

    def fail_group_commit(source, target_path) -> None:
        if Path(source) == staged and Path(target_path) == target:
            raise OSError("injected group swap failure")
        real_replace(source, target_path)

    monkeypatch.setattr(runtime_models.os, "replace", fail_group_commit)

    with pytest.raises(OSError, match="injected group swap failure"):
        _install(manifest, destination, {"model.bin": b"replacement"})

    assert (target / "old.bin").read_bytes() == b"old"
    assert not (target / "model.bin").exists()


def test_interrupted_directory_swap_restores_verified_backup_without_network(
    tmp_path: Path,
) -> None:
    record = _model_record(files={"model.bin": b"model"})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    destination = tmp_path / "models"
    _install(manifest, destination, {"model.bin": b"model"})
    target = destination / "ready-model"
    backup = destination / "ready-model.old-interrupted"
    target.replace(backup)

    result = provision_runtime_models(
        manifest,
        destination,
        downloader=lambda *_args: pytest.fail("verified backup should be recovered"),
    )

    assert result.reused == ("test-model",)
    assert target.is_dir()
    assert not backup.exists()
    verify_installed_runtime_models(manifest, destination)


def test_concurrent_runtime_model_provisioning_is_rejected_before_staging(
    tmp_path: Path,
) -> None:
    record = _model_record(files={"model.bin": b"model"})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    destination = tmp_path / "models"
    entered = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    def slow_downloader(_model, _file, part: Path, progress) -> None:
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"model")
        entered.set()
        if not release.wait(10):
            raise AssertionError("concurrency test timed out")
        progress(5, 5)

    def run_first() -> None:
        try:
            provision_runtime_models(manifest, destination, downloader=slow_downloader)
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=run_first)
    thread.start()
    assert entered.wait(10)
    try:
        with pytest.raises(ProvisioningError, match="already in progress"):
            provision_runtime_models(
                manifest,
                destination,
                downloader=lambda *_args: pytest.fail("second provisioner reached staging"),
            )
    finally:
        release.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert failures == []
    verify_installed_runtime_models(manifest, destination)


def test_exact_inventory_marker_and_hash_are_required(tmp_path: Path) -> None:
    record = _model_record(files={"model.bin": b"model"})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    destination = tmp_path / "models"
    _install(manifest, destination, {"model.bin": b"model"})
    target = destination / "ready-model"

    (target / "unexpected.bin").write_bytes(b"extra")
    with pytest.raises(ProvisioningError, match="inventory mismatch"):
        verify_installed_runtime_models(manifest, destination)
    (target / "unexpected.bin").unlink()

    marker = target / RUNTIME_MODEL_MARKER
    marker.write_text("{}", encoding="utf-8")
    with pytest.raises(ProvisioningError, match="marker does not match"):
        verify_installed_runtime_models(manifest, destination)


def test_exact_legacy_files_are_reused_without_network_and_receive_marker(tmp_path: Path) -> None:
    record = _model_record(files={"config.json": b"{}", "model.bin": b"model"})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    destination = tmp_path / "models"
    target = destination / "ready-model"
    target.mkdir(parents=True)
    (target / "config.json").write_bytes(b"{}")
    (target / "model.bin").write_bytes(b"model")

    result = provision_runtime_models(
        manifest,
        destination,
        downloader=lambda *_args: pytest.fail("legacy exact files should be hard-linked"),
    )

    assert result.installed == ("test-model",)
    assert (target / RUNTIME_MODEL_MARKER).is_file()
    verify_installed_runtime_models(manifest, destination)


def test_disk_preflight_counts_remaining_part_with_twenty_percent_headroom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _model_record(files={"model.bin": b"0123456789"})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    destination = tmp_path / "models"
    part = destination / ".downloads/runtime-models-v1-test/ready-model/model.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"0123")
    monkeypatch.setattr(runtime_models, "_MINIMUM_DISK_HEADROOM", 0)

    assert calculate_runtime_model_disk_bytes(manifest, destination) == 8
    DiskUsage = namedtuple("DiskUsage", "total used free")
    with pytest.raises(ProvisioningError, match="insufficient disk space"):
        provision_runtime_models(
            manifest,
            destination,
            downloader=lambda *_args: pytest.fail("preflight must run before download"),
            disk_usage=lambda _path: DiskUsage(100, 93, 7),
        )


def test_disk_preflight_counts_full_corrupt_part_as_a_fresh_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _model_record(files={"model.bin": b"0123456789"})
    _path, manifest = _write_manifest(tmp_path, models=[record])
    destination = tmp_path / "models"
    part = destination / ".downloads/runtime-models-v1-test/ready-model/model.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"xxxxxxxxxx")
    monkeypatch.setattr(runtime_models, "_MINIMUM_DISK_HEADROOM", 0)

    assert calculate_runtime_model_disk_bytes(manifest, destination) == 12
