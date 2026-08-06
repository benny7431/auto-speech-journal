from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest

from auto_speech_journal import provisioning
from auto_speech_journal.provisioning import (
    ManifestError,
    ProvisionAsset,
    ProvisionManifest,
    RequiredFile,
    VerificationError,
    download_resumable,
    find_manifest,
    load_manifest,
    provision,
)


class _Response:
    def __init__(
        self,
        body: bytes,
        status: int,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = io.BytesIO(body)
        self._status = status
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def getcode(self) -> int:
        return self._status

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def _zip_payload() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr("bundle/model.bin", b"model-payload")
        bundle.writestr("bundle/config.json", b"{}")
    return output.getvalue()


def test_download_resumes_from_existing_part(tmp_path) -> None:
    destination = tmp_path / "asset.part"
    destination.write_bytes(b"abc")
    requests = []

    def opener(request, _timeout):
        requests.append(request)
        return _Response(b"def", 206, headers={"Content-Range": "bytes 3-5/6"})

    download_resumable(
        "https://example.test/asset",
        destination,
        expected_size=6,
        opener=opener,
    )

    assert destination.read_bytes() == b"abcdef"
    assert requests[0].get_header("Range") == "bytes=3-"


def test_download_discards_partial_when_resumed_content_range_is_invalid(tmp_path) -> None:
    destination = tmp_path / "asset.part"
    destination.write_bytes(b"abc")
    requests = []
    sleeps = []

    def opener(request, _timeout):
        requests.append(request)
        if len(requests) == 1:
            return _Response(
                b"def",
                206,
                headers={"Content-Range": "bytes 0-2/6"},
            )
        assert request.get_header("Range") is None
        return _Response(b"abcdef", 200)

    download_resumable(
        "https://example.test/asset",
        destination,
        expected_size=6,
        opener=opener,
        sleep=sleeps.append,
    )

    assert destination.read_bytes() == b"abcdef"
    assert requests[0].get_header("Range") == "bytes=3-"
    assert sleeps == [1]


@pytest.mark.parametrize(
    "content_range",
    ["bytes 3-5/99", "bytes 3-4/6"],
)
def test_download_restarts_when_content_range_total_or_end_is_inconsistent(
    tmp_path,
    content_range: str,
) -> None:
    destination = tmp_path / "asset.part"
    destination.write_bytes(b"abc")
    requests = []

    def opener(request, _timeout):
        requests.append(request)
        if len(requests) == 1:
            return _Response(b"def", 206, headers={"Content-Range": content_range})
        return _Response(b"abcdef", 200)

    download_resumable(
        "https://example.test/asset",
        destination,
        expected_size=6,
        opener=opener,
        sleep=lambda _delay: None,
    )

    assert destination.read_bytes() == b"abcdef"
    assert requests[0].get_header("Range") == "bytes=3-"
    assert requests[1].get_header("Range") is None


def test_download_restarts_when_server_ignores_range(tmp_path) -> None:
    destination = tmp_path / "asset.part"
    destination.write_bytes(b"stale")

    def opener(request, _timeout):
        assert request.get_header("Range") == "bytes=5-"
        return _Response(b"fresh-payload", 200)

    download_resumable(
        "https://example.test/asset",
        destination,
        expected_size=len(b"fresh-payload"),
        opener=opener,
    )

    assert destination.read_bytes() == b"fresh-payload"


def test_download_restarts_from_zero_after_range_not_satisfiable(tmp_path) -> None:
    destination = tmp_path / "asset.part"
    destination.write_bytes(b"stale")
    requests = []

    def opener(request, _timeout):
        requests.append(request)
        if len(requests) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                416,
                "Range Not Satisfiable",
                hdrs=None,
                fp=None,
            )
        assert request.get_header("Range") is None
        return _Response(b"fresh-payload", 200)

    download_resumable(
        "https://example.test/asset",
        destination,
        expected_size=len(b"fresh-payload"),
        opener=opener,
        sleep=lambda _delay: None,
    )

    assert destination.read_bytes() == b"fresh-payload"
    assert requests[0].get_header("Range") == "bytes=5-"


def test_download_retries_with_backoff(tmp_path) -> None:
    attempts = []
    sleeps = []

    def opener(_request, _timeout):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("temporary failure")
        return _Response(b"payload", 200)

    destination = tmp_path / "asset.part"
    download_resumable(
        "https://example.test/asset",
        destination,
        expected_size=7,
        opener=opener,
        sleep=sleeps.append,
    )

    assert destination.read_bytes() == b"payload"
    assert len(attempts) == 2
    assert sleeps == [1]


def test_download_backoff_is_exponential_and_capped_by_retry_budget(tmp_path) -> None:
    attempts = []
    sleeps = []

    def opener(_request, _timeout):
        attempts.append(True)
        if len(attempts) < 4:
            raise OSError("temporary failure")
        return _Response(b"payload", 200)

    destination = tmp_path / "asset.part"
    download_resumable(
        "https://example.test/asset",
        destination,
        expected_size=7,
        opener=opener,
        sleep=sleeps.append,
    )

    assert destination.read_bytes() == b"payload"
    assert len(attempts) == 4
    assert sleeps == [1, 2, 4]


def test_manifest_rejects_placeholder_and_unsafe_destination(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release": "test-assets-v1",
                "assets": [
                    {
                        "name": "preview",
                        "url": "PLACEHOLDER",
                        "sha256": "0" * 64,
                        "size": 1,
                        "destination": "../escape",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError):
        load_manifest(manifest_path)


def test_provision_verifies_and_atomically_reuses_archive(tmp_path) -> None:
    payload = _zip_payload()
    model_digest = hashlib.sha256(b"model-payload").hexdigest()
    asset = ProvisionAsset(
        name="preview",
        url="https://example.test/preview.zip",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        installed_size=15,
        destination="preview",
        archive="zip",
        strip_prefix="bundle",
        required_files=(
            RequiredFile("model.bin", len(b"model-payload"), model_digest),
            RequiredFile("config.json", 2, hashlib.sha256(b"{}").hexdigest()),
        ),
    )
    manifest = ProvisionManifest(1, "test-assets-v1", (asset,))
    destination = tmp_path / "models"
    (destination / "preview").mkdir(parents=True)
    (destination / "preview" / "old.bin").write_bytes(b"old")
    events = []
    downloads = []

    def downloader(_asset, part: Path, progress) -> None:
        downloads.append(part)
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(payload)
        progress(len(payload), len(payload))

    result = provision(manifest, destination, downloader=downloader, progress=events.append)

    assert result.installed == ("preview",)
    assert (destination / "preview" / "model.bin").read_bytes() == b"model-payload"
    assert not (destination / "preview" / "old.bin").exists()
    assert events[-1].status == "complete"

    def unexpected_download(*_args) -> None:
        raise AssertionError("verified installed asset should be reused")

    reused = provision(manifest, destination, downloader=unexpected_download)
    assert reused.reused == ("preview",)
    assert len(downloads) == 1


def test_hash_failure_preserves_destination_and_discards_corrupt_part(tmp_path) -> None:
    expected = b"right"
    payload = b"wrong"
    asset = ProvisionAsset(
        name="vad",
        url="https://example.test/vad.onnx",
        sha256=hashlib.sha256(expected).hexdigest(),
        size=len(payload),
        installed_size=len(payload),
        destination="vad.onnx",
    )
    manifest = ProvisionManifest(1, "test-assets-v1", (asset,))
    destination = tmp_path / "models"
    destination.mkdir()
    target = destination / "vad.onnx"
    target.write_bytes(b"old")

    attempts = []

    def downloader(_asset, part: Path, _progress) -> None:
        attempts.append(part)
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(payload)

    with pytest.raises(VerificationError):
        provision(manifest, destination, downloader=downloader)

    assert target.read_bytes() == b"old"
    assert len(attempts) == 2
    assert not (destination / ".downloads" / "test-assets-v1" / "vad.part").exists()

    def repaired_download(_asset, part: Path, _progress) -> None:
        part.write_bytes(expected)

    repaired = provision(manifest, destination, downloader=repaired_download)
    assert repaired.installed == ("vad",)
    assert target.read_bytes() == expected


def test_disk_preflight_blocks_before_download(tmp_path) -> None:
    asset = ProvisionAsset(
        name="large",
        url="https://example.test/large.bin",
        sha256=hashlib.sha256(b"x").hexdigest(),
        size=1,
        installed_size=100,
        destination="large.bin",
    )
    manifest = ProvisionManifest(1, "test-assets-v1", (asset,))
    DiskUsage = namedtuple("DiskUsage", "total used free")

    with pytest.raises(Exception, match="insufficient disk space"):
        provision(
            manifest,
            tmp_path / "models",
            downloader=lambda *_args: (_ for _ in ()).throw(AssertionError("downloaded")),
            disk_usage=lambda _path: DiskUsage(100, 100, 0),
        )


def test_atomic_replace_failure_restores_previous_model(tmp_path, monkeypatch) -> None:
    payload = _zip_payload()
    asset = ProvisionAsset(
        name="preview",
        url="https://example.test/preview.zip",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        installed_size=15,
        destination="preview",
        archive="zip",
        strip_prefix="bundle",
        required_files=(
            RequiredFile("model.bin"),
            RequiredFile("config.json"),
        ),
    )
    manifest = ProvisionManifest(1, "test-assets-v1", (asset,))
    destination = tmp_path / "models"
    target = destination / "preview"
    target.mkdir(parents=True)
    (target / "old.bin").write_bytes(b"old")
    real_replace = provisioning.os.replace

    def fail_payload_swap(source, target_path) -> None:
        if Path(target_path) == target and Path(source).name.startswith("payload-"):
            raise OSError("injected swap failure")
        real_replace(source, target_path)

    monkeypatch.setattr(provisioning.os, "replace", fail_payload_swap)

    def downloader(_asset, part: Path, _progress) -> None:
        part.write_bytes(payload)

    with pytest.raises(OSError, match="injected swap failure"):
        provision(manifest, destination, downloader=downloader)

    assert (target / "old.bin").read_bytes() == b"old"
    assert not (target / "model.bin").exists()


def test_file_marker_commit_failure_restores_previous_asset(tmp_path, monkeypatch) -> None:
    previous = b"previous"
    replacement = b"replacement"
    asset = ProvisionAsset(
        name="vad",
        url="https://example.test/vad.onnx",
        sha256=hashlib.sha256(replacement).hexdigest(),
        size=len(replacement),
        installed_size=len(replacement),
        destination="vad.onnx",
    )
    manifest = ProvisionManifest(1, "test-assets-v1", (asset,))
    destination = tmp_path / "models"
    destination.mkdir()
    target = destination / "vad.onnx"
    marker = destination / "vad.onnx.asj-manifest.json"
    target.write_bytes(previous)
    marker.write_text('{"old": true}\n', encoding="utf-8")
    real_replace = provisioning.os.replace

    def fail_marker_commit(source, target_path) -> None:
        if Path(target_path) == marker and ".staging" in Path(source).parts:
            raise OSError("injected marker failure")
        real_replace(source, target_path)

    monkeypatch.setattr(provisioning.os, "replace", fail_marker_commit)

    def downloader(_asset, part: Path, _progress) -> None:
        part.write_bytes(replacement)

    with pytest.raises(OSError, match="injected marker failure"):
        provision(manifest, destination, downloader=downloader)

    assert target.read_bytes() == previous
    assert json.loads(marker.read_text(encoding="utf-8")) == {"old": True}
    assert (destination / ".downloads/test-assets-v1/vad.part").read_bytes() == replacement


def test_find_manifest_reaches_stable_install_root_from_versioned_cli(tmp_path) -> None:
    executable = tmp_path / "versions" / "0.2.0" / "AutoSpeechJournal.CLI.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"cli")
    manifest = tmp_path / "manifests" / "runtime-models-v1.json"
    manifest.parent.mkdir()
    manifest.write_text("{}\n", encoding="utf-8")

    assert (
        find_manifest(
            "runtime-models-v1.json",
            runtime_root=tmp_path / "runtime",
            executable=executable,
        )
        == manifest
    )
