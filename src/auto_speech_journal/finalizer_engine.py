from __future__ import annotations

import gc
import importlib
import logging
import os
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .text_normalize import NormalizerFactory, OpenCcTextNormalizer
from .transcript_quality import is_pathological_repetition
from .types import CapturedSegment, FinalResult

LOGGER = logging.getLogger(__name__)


class FinalizerEngineError(RuntimeError):
    pass


ModelFactory = Callable[[str, str, str], Any]


@dataclass(frozen=True, slots=True)
class FinalizerProbe:
    active_device: str
    compute_type: str
    latency_ms: int
    text: str


def register_nvidia_dll_directories() -> list[Any]:
    """Register pip-installed NVIDIA DLL folders before importing CTranslate2."""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return []
    try:
        nvidia = importlib.import_module("nvidia")
        root = Path(next(iter(nvidia.__path__)))
    except (ImportError, StopIteration, AttributeError):
        return []

    handles: list[Any] = []
    directories: list[Path] = []
    for component in ("cublas", "cudnn", "cuda_nvrtc"):
        directory = root / component / "bin"
        if directory.is_dir():
            directories.append(directory)
            handles.append(os.add_dll_directory(str(directory)))
    # CTranslate2 loads CUDA libraries with Win32 LoadLibrary at inference
    # time. AddDllDirectory covers extension imports, while a process-local
    # PATH prefix covers those delayed loads without changing global Windows
    # configuration or the recorder/preview worker environments.
    current_path = os.environ.get("PATH", "")
    existing = {item.casefold() for item in current_path.split(os.pathsep) if item}
    additions = [str(item) for item in directories if str(item).casefold() not in existing]
    if additions:
        os.environ["PATH"] = os.pathsep.join([*additions, current_path])
    return handles


class FasterWhisperFinalizer:
    def __init__(
        self,
        model_path: Path,
        *,
        language: str = "zh",
        prefer_cuda: bool = True,
        cuda_compute_type: str = "int8_float16",
        cpu_compute_type: str = "int8",
        deadline_ms: int = 10_000,
        beam_size: int = 5,
        model_factory: ModelFactory | None = None,
        normalizer_factory: NormalizerFactory | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model_path = Path(model_path)
        self.language = language
        self.prefer_cuda = prefer_cuda
        self.cuda_compute_type = cuda_compute_type
        self.cpu_compute_type = cpu_compute_type
        self.deadline_ms = deadline_ms
        self.beam_size = beam_size
        self._model_factory = model_factory
        self._normalizer = OpenCcTextNormalizer(
            normalizer_factory, error_type=FinalizerEngineError
        )
        self._monotonic = monotonic
        self._model: Any | None = None
        self._device = ""
        self._compute_type = ""
        self._dll_handles: list[Any] = []
        self._hotwords: tuple[str, ...] = ()
        self.last_fallback_reason: str | None = None
        self.last_deadline_exceeded = False
        self.last_normalization_error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def active_device(self) -> str:
        return self._device

    def _default_model_factory(self, path: str, device: str, compute_type: str) -> Any:
        if device == "cuda":
            self._dll_handles.extend(register_nvidia_dll_directories())
        try:
            faster_whisper = importlib.import_module("faster_whisper")
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise FinalizerEngineError("faster-whisper is not installed") from exc
        return faster_whisper.WhisperModel(
            path,
            device=device,
            compute_type=compute_type,
            local_files_only=True,
        )

    def _load_for(self, device: str) -> None:
        compute_type = self.cuda_compute_type if device == "cuda" else self.cpu_compute_type
        factory = self._model_factory or self._default_model_factory
        model = factory(str(self.model_path), device, compute_type)
        self._model = model
        self._device = device
        self._compute_type = compute_type

    def warmup(self) -> None:
        if self._model is not None:
            return
        if not self.model_path.is_dir():
            raise FinalizerEngineError(f"faster-whisper model is missing: {self.model_path}")
        self.last_fallback_reason = None
        if self.prefer_cuda:
            try:
                self._load_for("cuda")
                return
            except Exception as exc:
                self.last_fallback_reason = f"CUDA model initialization failed: {exc}"
                self._discard_model()
        try:
            self._load_for("cpu")
        except Exception as exc:
            prefix = f"{self.last_fallback_reason}; " if self.last_fallback_reason else ""
            raise FinalizerEngineError(f"{prefix}CPU model initialization failed: {exc}") from exc

    def _discard_model(self) -> None:
        self._model = None
        self._device = ""
        self._compute_type = ""
        gc.collect()

    def _transcribe_once(
        self, audio_path: Path, *, use_hotwords: bool = True
    ) -> tuple[str, Any]:
        kwargs: dict[str, Any] = {
            "language": self.language,
            "task": "transcribe",
            "beam_size": self.beam_size,
            "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            "condition_on_previous_text": False,
            "vad_filter": False,
            "word_timestamps": False,
        }
        if use_hotwords and self._hotwords:
            kwargs["hotwords"] = ", ".join(self._hotwords)
        segments, info = self._model.transcribe(str(audio_path), **kwargs)
        materialized = list(segments)
        text = "".join(str(getattr(segment, "text", "")) for segment in materialized).strip()
        return text, info

    def _transcribe_with_fallback(
        self, audio_path: Path, *, use_hotwords: bool = True
    ) -> str:
        self.warmup()
        try:
            text, _info = self._transcribe_once(audio_path, use_hotwords=use_hotwords)
            return text
        except Exception as exc:
            if self._device != "cuda":
                raise
            self.last_fallback_reason = f"CUDA transcription failed: {exc}"
            self._discard_model()
            self._load_for("cpu")
            text, _info = self._transcribe_once(audio_path, use_hotwords=use_hotwords)
            return text

    def _normalize(self, text: str) -> str:
        # No empty-text shortcut here: ``transcribe`` already rejects a blank
        # transcription before this point, so a missing OpenCC must still
        # surface as a normalization failure rather than a silent "".
        return self._normalizer.normalize(text)

    def _profile(self) -> str:
        profile = f"faster-whisper:{self._device}:{self._compute_type}:s2tw"
        if self.last_fallback_reason:
            profile += ":fallback"
        if self.last_deadline_exceeded:
            profile += ":late"
        if self.last_normalization_error:
            profile += ":raw-zh"
        return profile

    def transcribe(self, segment: CapturedSegment) -> FinalResult:
        started = self._monotonic()
        self.last_deadline_exceeded = False
        self.last_normalization_error = None
        if not segment.audio_path.is_file():
            return FinalResult(
                segment_id=segment.segment_id,
                raw_text="",
                normalized_text=segment.preview_text,
                engine_profile="faster-whisper:unavailable",
                success=False,
                error=f"audio file is missing: {segment.audio_path}",
            )
        try:
            raw_text = self._transcribe_with_fallback(segment.audio_path)
            if not raw_text.strip():
                raise FinalizerEngineError("final transcription was empty for a VAD segment")
            if is_pathological_repetition(raw_text) and self._hotwords:
                LOGGER.warning(
                    "Repeating final transcript for %s; retrying without hotwords",
                    segment.segment_id,
                )
                raw_text = self._transcribe_with_fallback(
                    segment.audio_path, use_hotwords=False
                )
            if is_pathological_repetition(raw_text):
                preview_text = segment.preview_text.strip()
                if not preview_text:
                    raise FinalizerEngineError(
                        "pathological repetitive transcription had no usable preview"
                    )
                latency_ms = round((self._monotonic() - started) * 1000)
                self.last_deadline_exceeded = latency_ms > self.deadline_ms
                return FinalResult(
                    segment_id=segment.segment_id,
                    raw_text=raw_text,
                    normalized_text=preview_text,
                    engine_profile=f"{self._profile()}:repeat-filter:preview",
                    success=True,
                    latency_ms=latency_ms,
                )
            try:
                normalized = self._normalize(raw_text)
            except Exception as exc:
                self.last_normalization_error = str(exc)
                raise FinalizerEngineError(
                    f"Traditional Chinese conversion failed: {exc}"
                ) from exc
            latency_ms = round((self._monotonic() - started) * 1000)
            self.last_deadline_exceeded = latency_ms > self.deadline_ms
            return FinalResult(
                segment_id=segment.segment_id,
                raw_text=raw_text,
                normalized_text=normalized,
                engine_profile=self._profile(),
                success=True,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = round((self._monotonic() - started) * 1000)
            self.last_deadline_exceeded = latency_ms > self.deadline_ms
            profile = self._profile() if self._device else "faster-whisper:unavailable"
            return FinalResult(
                segment_id=segment.segment_id,
                raw_text="",
                normalized_text=segment.preview_text,
                engine_profile=profile,
                success=False,
                error=str(exc),
                latency_ms=latency_ms,
            )

    def probe(self, audio_path: Path) -> FinalizerProbe:
        path = Path(audio_path)
        if not path.is_file():
            raise FinalizerEngineError(f"probe audio file is missing: {path}")
        started = self._monotonic()
        try:
            text = self._transcribe_with_fallback(path)
        except Exception as exc:
            raise FinalizerEngineError(f"faster-whisper probe failed: {exc}") from exc
        return FinalizerProbe(
            active_device=self._device,
            compute_type=self._compute_type,
            latency_ms=round((self._monotonic() - started) * 1000),
            text=text,
        )

    def update_hotwords(self, hotwords: Sequence[str]) -> None:
        self._hotwords = tuple(dict.fromkeys(word.strip() for word in hotwords if word.strip()))

    def close(self) -> None:
        self._discard_model()
        while self._dll_handles:
            handle = self._dll_handles.pop()
            with suppress(Exception):
                handle.close()


FinalizerEngine = FasterWhisperFinalizer


__all__ = [
    "FasterWhisperFinalizer",
    "FinalizerEngine",
    "FinalizerEngineError",
    "FinalizerProbe",
    "register_nvidia_dll_directories",
]
