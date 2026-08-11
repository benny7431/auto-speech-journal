"""Play the isolated demo prompt through a selected physical output device."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import sounddevice as sd
import soundfile as sf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--delay", type=float, default=0.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.delay > 0:
        time.sleep(args.delay)
    samples, sample_rate = sf.read(args.audio, dtype="float32", always_2d=True)
    sd.play(samples, sample_rate, device=args.device, blocking=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
