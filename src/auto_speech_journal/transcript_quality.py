from __future__ import annotations

import zlib

MIN_REPETITION_BYTES = 48
MAX_COMPRESSION_RATIO = 2.4


def compression_ratio(text: str) -> float:
    encoded = text.encode("utf-8")
    if not encoded:
        return 0.0
    return len(encoded) / len(zlib.compress(encoded))


def is_pathological_repetition(text: str) -> bool:
    """Detect the highly compressible decoder loops produced by Whisper."""

    encoded = text.strip().encode("utf-8")
    return (
        len(encoded) >= MIN_REPETITION_BYTES
        and len(encoded) / len(zlib.compress(encoded)) > MAX_COMPRESSION_RATIO
    )


__all__ = ["compression_ratio", "is_pathological_repetition"]
