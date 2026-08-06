from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .native_runtime import register_onnxruntime_dll_directory


class PreviewEngineError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PreviewHypothesis:
    # ``text`` is the normalized s2tw text kept for API compatibility.
    text: str
    is_endpoint: bool
    changed: bool
    raw_text: str = ""

    @property
    def normalized_text(self) -> str:
        return self.text


RecognizerFactory = Callable[[], Any]
NormalizerFactory = Callable[[], Any]


class SherpaPreviewEngine:
    def __init__(
        self,
        model_dir: Path | None = None,
        *,
        encoder: Path | None = None,
        decoder: Path | None = None,
        tokens: Path | None = None,
        sample_rate: int = 16_000,
        num_threads: int = 1,
        provider: str = "cpu",
        endpoint_silence_ms: int = 2_000,
        max_utterance_seconds: float = 300.0,
        recognizer_factory: RecognizerFactory | None = None,
        normalizer_factory: NormalizerFactory | None = None,
        supports_hotwords: bool = False,
    ) -> None:
        if model_dir is not None:
            model_dir = Path(model_dir)
            encoder = encoder or model_dir / "encoder.int8.onnx"
            decoder = decoder or model_dir / "decoder.int8.onnx"
            tokens = tokens or model_dir / "tokens.txt"
        self.encoder = Path(encoder) if encoder is not None else None
        self.decoder = Path(decoder) if decoder is not None else None
        self.tokens = Path(tokens) if tokens is not None else None
        self.sample_rate = sample_rate
        self.num_threads = num_threads
        self.provider = provider
        self.endpoint_silence_ms = endpoint_silence_ms
        self.max_utterance_seconds = max_utterance_seconds
        self._recognizer_factory = recognizer_factory
        self._normalizer_factory = normalizer_factory
        self._normalizer: Any | None = None
        self.supports_hotwords = supports_hotwords
        self._recognizer: Any | None = None
        self._stream: Any | None = None
        self._last_text = ""
        self._last_raw_text = ""
        self._hotwords: tuple[str, ...] = ()
        self._hotwords_applied = False

    @property
    def loaded(self) -> bool:
        return self._recognizer is not None

    @property
    def hotwords_applied(self) -> bool:
        return self._hotwords_applied

    def _build_recognizer(self) -> Any:
        if self._recognizer_factory is not None:
            return self._recognizer_factory()
        required = (self.encoder, self.decoder, self.tokens)
        if any(path is None or not path.is_file() for path in required):
            raise PreviewEngineError(
                "sherpa preview model is incomplete; encoder.int8.onnx, "
                "decoder.int8.onnx, and tokens.txt are required"
            )
        try:
            register_onnxruntime_dll_directory()
            sherpa = importlib.import_module("sherpa_onnx")
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise PreviewEngineError("sherpa-onnx is not installed") from exc
        return sherpa.OnlineRecognizer.from_paraformer(
            tokens=str(self.tokens),
            encoder=str(self.encoder),
            decoder=str(self.decoder),
            num_threads=self.num_threads,
            sample_rate=self.sample_rate,
            feature_dim=80,
            provider=self.provider,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=self.endpoint_silence_ms / 1000,
            rule2_min_trailing_silence=self.endpoint_silence_ms / 1000,
            rule3_min_utterance_length=self.max_utterance_seconds,
        )

    def _new_stream(self) -> Any:
        recognizer = self._recognizer
        if recognizer is None:
            raise PreviewEngineError("recognizer is not loaded")
        self._hotwords_applied = not self._hotwords
        if self._hotwords and self.supports_hotwords:
            encoded = "/".join(self._hotwords)
            try:
                stream = recognizer.create_stream(hotwords=encoded)
                self._hotwords_applied = True
                return stream
            except TypeError:
                try:
                    stream = recognizer.create_stream(encoded)
                    self._hotwords_applied = True
                    return stream
                except TypeError:
                    pass
        return recognizer.create_stream()

    def warmup(self) -> None:
        if self._recognizer is None:
            self._recognizer = self._build_recognizer()
        if self._stream is None:
            self._stream = self._new_stream()

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        if self._normalizer is None:
            if self._normalizer_factory is not None:
                self._normalizer = self._normalizer_factory()
            else:
                try:
                    opencc = importlib.import_module("opencc")
                except ImportError as exc:  # pragma: no cover - packaging failure
                    raise PreviewEngineError("OpenCC is not installed") from exc
                self._normalizer = opencc.OpenCC("s2tw")
        converter = getattr(self._normalizer, "convert", self._normalizer)
        return str(converter(text)).strip()

    def normalize_text(self, text: str) -> str:
        return self._normalize_text(text)

    def _result_text(self, result: Any) -> tuple[str, str]:
        if result is None:
            return "", ""
        if isinstance(result, str):
            raw = result.strip()
        else:
            raw = str(getattr(result, "text", result)).strip()
        return raw, self._normalize_text(raw)

    def _decode_ready(self) -> None:
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)

    def accept(self, samples: Any, *, sample_rate: int | None = None) -> PreviewHypothesis:
        self.warmup()
        rate = sample_rate or self.sample_rate
        try:
            self._stream.accept_waveform(rate, samples)
            self._decode_ready()
            endpoint = bool(self._recognizer.is_endpoint(self._stream))
            raw_text, text = self._result_text(
                self._recognizer.get_result(self._stream)
            )
        except Exception as exc:
            raise PreviewEngineError(f"sherpa streaming decode failed: {exc}") from exc
        changed = text != self._last_text or raw_text != self._last_raw_text
        self._last_text = text
        self._last_raw_text = raw_text
        hypothesis = PreviewHypothesis(
            text=text,
            is_endpoint=endpoint,
            changed=changed,
            raw_text=raw_text,
        )
        if endpoint:
            self._recognizer.reset(self._stream)
            self._last_text = ""
            self._last_raw_text = ""
        return hypothesis

    def finish(self) -> PreviewHypothesis:
        if self._stream is None:
            return PreviewHypothesis(text="", is_endpoint=True, changed=False)
        try:
            if hasattr(self._stream, "input_finished"):
                self._stream.input_finished()
                self._decode_ready()
            raw_text, text = self._result_text(
                self._recognizer.get_result(self._stream)
            )
        except Exception as exc:
            raise PreviewEngineError(f"sherpa preview flush failed: {exc}") from exc
        changed = text != self._last_text or raw_text != self._last_raw_text
        self._last_text = ""
        self._last_raw_text = ""
        self._stream = self._new_stream()
        return PreviewHypothesis(
            text=text,
            is_endpoint=True,
            changed=changed,
            raw_text=raw_text,
        )

    def reset(self) -> None:
        self._last_text = ""
        self._last_raw_text = ""
        if self._recognizer is None:
            self._stream = None
            return
        if self._stream is not None:
            try:
                self._recognizer.reset(self._stream)
                return
            except Exception:
                pass
        self._stream = self._new_stream()

    def update_hotwords(self, hotwords: Sequence[str]) -> bool:
        normalized = tuple(
            dict.fromkeys(self._normalize_text(word.strip()) for word in hotwords if word.strip())
        )
        self._hotwords = normalized
        if self._recognizer is not None:
            self._stream = self._new_stream()
            self._last_text = ""
            self._last_raw_text = ""
        return self._hotwords_applied

    def close(self) -> None:
        self._stream = None
        self._recognizer = None
        self._last_text = ""
        self._last_raw_text = ""


PreviewEngine = SherpaPreviewEngine


__all__ = [
    "PreviewEngine",
    "PreviewEngineError",
    "PreviewHypothesis",
    "SherpaPreviewEngine",
]
