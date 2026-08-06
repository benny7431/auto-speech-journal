from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from auto_speech_journal import model_download
from auto_speech_journal.finalizer_engine import FasterWhisperFinalizer
from auto_speech_journal.model_download import (
    FINAL_SPEC,
    PREVIEW_SPEC,
    DirectModelSpec,
    HuggingFaceModelSpec,
)
from auto_speech_journal.preview_engine import SherpaPreviewEngine
from auto_speech_journal.types import CapturedSegment


class FakeStream:
    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.finished = False

    def accept_waveform(self, sample_rate, samples):
        assert sample_rate == 16_000
        assert len(samples)
        self.recognizer.accepted += 1

    def input_finished(self):
        self.finished = True


class FakeRecognizer:
    def __init__(self):
        self.accepted = 0
        self.decoded = 0
        self.reset_count = 0
        self.hotwords = []
        self.results = [("简体预览", False), ("简体完成", True)]

    def create_stream(self, *args, **kwargs):
        self.hotwords.append(kwargs.get("hotwords") or (args[0] if args else ""))
        return FakeStream(self)

    def is_ready(self, _stream):
        return self.decoded < self.accepted

    def decode_stream(self, _stream):
        self.decoded += 1

    def is_endpoint(self, _stream):
        return self.results[self.accepted - 1][1]

    def get_result(self, _stream):
        return self.results[self.accepted - 1][0] if self.accepted else ""

    def reset(self, _stream):
        self.reset_count += 1


def test_sherpa_preview_is_traditional_and_replaces_partial():
    recognizer = FakeRecognizer()
    engine = SherpaPreviewEngine(
        recognizer_factory=lambda: recognizer,
        normalizer_factory=lambda: lambda text: (
            text.replace("简", "簡").replace("体", "體").replace("预", "預").replace("览", "覽")
        ),
        supports_hotwords=True,
    )
    engine.warmup()
    assert engine.update_hotwords(["简体"]) is True

    first = engine.accept(np.ones(1_600, dtype=np.float32))
    second = engine.accept(np.ones(1_600, dtype=np.float32))

    assert first.text == "簡體預覽"
    assert first.raw_text == "简体预览"
    assert first.changed and not first.is_endpoint
    assert second.text == "簡體完成"
    assert second.raw_text == "简体完成"
    assert second.is_endpoint
    assert recognizer.hotwords[-1] == "簡體"
    assert recognizer.reset_count == 1


class SegmentText:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self, device):
        self.device = device

    def transcribe(self, _path, **kwargs):
        assert kwargs["language"] == "zh"
        if self.device == "cuda":
            def broken():
                raise RuntimeError("CUDA out of memory")
                yield None

            return broken(), object()
        return iter([SegmentText("简体"), SegmentText(" 记录")]), object()


def _captured(audio_path: Path) -> CapturedSegment:
    started = datetime(2026, 7, 12, tzinfo=UTC)
    return CapturedSegment(
        segment_id="segment-1",
        audio_path=audio_path,
        started_at_utc=started,
        ended_at_utc=started + timedelta(seconds=1),
        preview_text="預覽",
        duration_ms=1_000,
    )


def test_finalizer_retries_cpu_after_cuda_generator_failure(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    audio_path = tmp_path / "segment.flac"
    audio_path.write_bytes(b"audio")
    loads = []
    ticks = iter([100.0, 100.02])

    def factory(path, device, compute_type):
        loads.append((path, device, compute_type))
        return FakeModel(device)

    engine = FasterWhisperFinalizer(
        model_dir,
        model_factory=factory,
        normalizer_factory=lambda: lambda text: text.replace("简体", "簡體").replace(
            "记录", "記錄"
        ),
        monotonic=lambda: next(ticks),
        deadline_ms=10,
    )
    result = engine.transcribe(_captured(audio_path))

    assert result.success
    assert result.raw_text == "简体 记录"
    assert result.normalized_text == "簡體 記錄"
    assert result.latency_ms == 20
    assert ":cpu:int8:" in result.engine_profile
    assert result.engine_profile.endswith(":fallback:late")
    assert [load[1] for load in loads] == ["cuda", "cpu"]


def test_finalizer_keeps_preview_when_audio_is_missing(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    engine = FasterWhisperFinalizer(model_dir, model_factory=lambda *_args: FakeModel("cpu"))

    result = engine.transcribe(_captured(tmp_path / "missing.flac"))

    assert not result.success
    assert result.normalized_text == "預覽"
    assert "missing" in (result.error or "")


def test_finalizer_does_not_replace_nonempty_preview_with_empty_final(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    audio_path = tmp_path / "segment.flac"
    audio_path.write_bytes(b"audio")

    class EmptyModel:
        def transcribe(self, _path, **_kwargs):
            return iter([]), object()

    engine = FasterWhisperFinalizer(
        model_dir,
        prefer_cuda=False,
        model_factory=lambda *_args: EmptyModel(),
        normalizer_factory=lambda: lambda text: text,
    )
    result = engine.transcribe(_captured(audio_path))

    assert not result.success
    assert result.normalized_text == "預覽"
    assert "empty" in (result.error or "")


def test_finalizer_preserves_audio_when_traditional_conversion_fails(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    audio_path = tmp_path / "speech.flac"
    audio_path.write_bytes(b"audio")

    class TextModel:
        def transcribe(self, *_args, **_kwargs):
            return [SimpleNamespace(text="简体文字")], SimpleNamespace()

    def broken_normalizer():
        def convert(_text):
            raise RuntimeError("OpenCC unavailable")

        return convert

    engine = FasterWhisperFinalizer(
        model_dir,
        prefer_cuda=False,
        model_factory=lambda *_args: TextModel(),
        normalizer_factory=broken_normalizer,
    )

    result = engine.transcribe(_captured(audio_path))

    assert not result.success
    assert result.normalized_text == "預覽"
    assert "Traditional Chinese conversion failed" in (result.error or "")


def test_finalizer_treats_empty_vad_segment_as_retry_even_without_preview(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    audio_path = tmp_path / "segment.flac"
    audio_path.write_bytes(b"audio")
    segment = _captured(audio_path)
    segment = CapturedSegment(
        segment_id=segment.segment_id,
        audio_path=segment.audio_path,
        started_at_utc=segment.started_at_utc,
        ended_at_utc=segment.ended_at_utc,
        preview_text="",
        duration_ms=segment.duration_ms,
    )

    class EmptyModel:
        def transcribe(self, _path, **_kwargs):
            return iter([]), object()

    engine = FasterWhisperFinalizer(
        model_dir,
        prefer_cuda=False,
        model_factory=lambda *_args: EmptyModel(),
        normalizer_factory=lambda: lambda text: text,
    )

    result = engine.transcribe(segment)

    assert not result.success
    assert result.normalized_text == ""
    assert "empty" in (result.error or "")


def test_finalizer_retries_repetitive_hotword_loop_without_hotwords(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    audio_path = tmp_path / "segment.flac"
    audio_path.write_bytes(b"audio")

    class HotwordLoopModel:
        def __init__(self) -> None:
            self.calls = []

        def transcribe(self, _path, **kwargs):
            self.calls.append(kwargs)
            text = "及時," * 60 if kwargs.get("hotwords") else "正常轉錄"
            return iter([SegmentText(text)]), object()

    model = HotwordLoopModel()
    engine = FasterWhisperFinalizer(
        model_dir,
        prefer_cuda=False,
        model_factory=lambda *_args: model,
        normalizer_factory=lambda: lambda text: text,
    )
    engine.update_hotwords(["及時"])

    result = engine.transcribe(_captured(audio_path))

    assert result.success
    assert result.normalized_text == "正常轉錄"
    assert model.calls[0]["hotwords"] == "及時"
    assert "hotwords" not in model.calls[1]
    assert model.calls[0]["temperature"] == (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def test_finalizer_uses_preview_when_repetition_survives_retry(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    audio_path = tmp_path / "segment.flac"
    audio_path.write_bytes(b"audio")

    class RepeatingModel:
        def transcribe(self, _path, **_kwargs):
            return iter([SegmentText("何時," * 60)]), object()

    engine = FasterWhisperFinalizer(
        model_dir,
        prefer_cuda=False,
        model_factory=lambda *_args: RepeatingModel(),
        normalizer_factory=lambda: lambda text: text,
    )

    result = engine.transcribe(_captured(audio_path))

    assert result.success
    assert result.normalized_text == "預覽"
    assert result.raw_text.startswith("何時,何時,")
    assert result.engine_profile.endswith(":repeat-filter:preview")


def test_finalizer_probe_runs_real_decode_path_and_reports_profile(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    audio_path = tmp_path / "silence.flac"
    audio_path.write_bytes(b"silence")
    ticks = iter([5.0, 5.01])

    class SilentModel:
        def transcribe(self, _path, **_kwargs):
            return iter([]), object()

    engine = FasterWhisperFinalizer(
        model_dir,
        model_factory=lambda _path, device, _compute: SilentModel(),
        monotonic=lambda: next(ticks),
    )

    probe = engine.probe(audio_path)

    assert probe.active_device == "cuda"
    assert probe.compute_type == "int8_float16"
    assert probe.latency_ms == 10
    assert probe.text == ""


def test_official_whisper_manifest_is_pinned_to_openai_source():
    assert FINAL_SPEC.repo_id == "openai/whisper-large-v3-turbo"
    assert FINAL_SPEC.revision == "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"
    assert FINAL_SPEC.source_model_file == "model.safetensors"
    assert FINAL_SPEC.source_model_sha256 == (
        "542566a422ae4f3fd23f1ba11add198fca01bbf82e66e6a2857b3f608b1eb9d1"
    )
    assert PREVIEW_SPEC.digest_algorithm == "sha256"
    assert PREVIEW_SPEC.digest == (
        "5462a1fce42693deae572af1e8c4687124b12aa85fe61ff4d3168bb5280e205f"
    )


def test_huggingface_source_is_verified_before_injected_conversion(tmp_path):
    source_bytes = b"official-openai-weights"
    spec = HuggingFaceModelSpec(
        key="test-whisper",
        revision="fixed-revision",
        repo_id="openai/test-whisper",
        install_path="converted",
        source_files=("config.json", "model.safetensors", "tokenizer.json"),
        source_model_file="model.safetensors",
        source_model_size=len(source_bytes),
        source_model_sha256=hashlib.sha256(source_bytes).hexdigest(),
        converted_files=("config.json", "model.bin", "tokenizer.json"),
        quantization="float16",
    )
    calls = []

    def snapshot_download(**kwargs):
        calls.append((kwargs["repo_id"], kwargs["revision"]))
        local = Path(kwargs["local_dir"])
        (local / "config.json").write_text("{}", encoding="utf-8")
        (local / "tokenizer.json").write_text("{}", encoding="utf-8")
        (local / "model.safetensors").write_bytes(source_bytes)
        return str(local)

    def convert(source, destination, received_spec):
        assert (source / "model.safetensors").read_bytes() == source_bytes
        assert received_spec is spec
        destination.mkdir()
        (destination / "config.json").write_text("{}", encoding="utf-8")
        (destination / "tokenizer.json").write_text("{}", encoding="utf-8")
        (destination / "model.bin").write_bytes(b"ct2")

    model_download._ensure_huggingface_model(
        tmp_path,
        spec,
        progress=None,
        snapshot_download=snapshot_download,
        convert_model=convert,
    )

    assert calls == [("openai/test-whisper", "fixed-revision")]
    assert (tmp_path / "converted" / "model.bin").read_bytes() == b"ct2"
    marker = json.loads(
        (tmp_path / "converted" / ".model-manifest.json").read_text(encoding="utf-8")
    )
    assert marker["files"]["model.bin"]["sha256"] == hashlib.sha256(b"ct2").hexdigest()
    model_download._verify_marker_files(tmp_path / "converted", spec)
    (tmp_path / "converted" / "model.bin").write_bytes(b"corrupt")
    with pytest.raises(model_download.ModelVerificationError):
        model_download._verify_marker_files(tmp_path / "converted", spec)
    model_download._ensure_huggingface_model(
        tmp_path,
        spec,
        progress=None,
        snapshot_download=snapshot_download,
        convert_model=convert,
    )
    assert len(calls) == 2
    assert (tmp_path / "converted" / "model.bin").read_bytes() == b"ct2"


def test_corrupt_existing_preview_model_is_redownloaded(tmp_path):
    contents = {
        "encoder.int8.onnx": b"encoder",
        "decoder.int8.onnx": b"decoder",
        "tokens.txt": b"tokens",
    }
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:bz2") as archive:
        for name, data in contents.items():
            info = tarfile.TarInfo(f"preview/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    archive_bytes = archive_buffer.getvalue()
    spec = DirectModelSpec(
        key="test-preview",
        revision="fixed",
        url="https://example.invalid/preview.tar.bz2",
        size=len(archive_bytes),
        digest_algorithm="sha256",
        digest=hashlib.sha256(archive_bytes).hexdigest(),
        install_path="preview",
        archive=True,
        required_files=tuple(contents),
    )
    downloads = []

    def download(_url, destination, _progress):
        downloads.append(destination)
        destination.write_bytes(archive_bytes)

    model_download._ensure_direct_model(
        tmp_path,
        spec,
        progress=None,
        download_file=download,
    )
    assert len(downloads) == 1
    (tmp_path / "preview" / "encoder.int8.onnx").write_bytes(b"corrupt")

    model_download._ensure_direct_model(
        tmp_path,
        spec,
        progress=None,
        download_file=download,
    )

    assert len(downloads) == 2
    assert (tmp_path / "preview" / "encoder.int8.onnx").read_bytes() == b"encoder"
