from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

NormalizerFactory = Callable[[], Any]

OPENCC_CONFIG = "s2tw"
OPENCC_MISSING_MESSAGE = "OpenCC is not installed"


class OpenCcTextNormalizer:
    """Lazily build, cache, and apply an OpenCC ``s2tw`` converter.

    OpenCC stays a delayed import: the converter is only constructed on the
    first ``normalize`` call. ``factory`` replaces that construction, which lets
    callers inject a plain callable that has no ``convert`` attribute.
    ``error_type`` is the exception the owning engine raises when OpenCC cannot
    be imported.
    """

    def __init__(
        self,
        factory: NormalizerFactory | None = None,
        *,
        error_type: type[Exception] = RuntimeError,
    ) -> None:
        self._factory = factory
        self._error_type = error_type
        self._normalizer: Any | None = None

    def _build(self) -> Any:
        if self._factory is not None:
            return self._factory()
        try:
            opencc = importlib.import_module("opencc")
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise self._error_type(OPENCC_MISSING_MESSAGE) from exc
        return opencc.OpenCC(OPENCC_CONFIG)

    def normalize(self, text: str) -> str:
        if self._normalizer is None:
            self._normalizer = self._build()
        converter = getattr(self._normalizer, "convert", self._normalizer)
        return str(converter(text)).strip()


__all__ = [
    "OPENCC_CONFIG",
    "OPENCC_MISSING_MESSAGE",
    "NormalizerFactory",
    "OpenCcTextNormalizer",
]
