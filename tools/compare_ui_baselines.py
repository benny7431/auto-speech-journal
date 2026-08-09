"""Compare two UI baseline directories rendered by ``render_ui_baselines.py``.

Refactors that are meant to be visually inert are checked by rendering baselines
before and after and asserting the two sets match. Byte comparison is too strict
for that: the software renderer disagrees with itself by a single channel step on
a handful of antialiased subpixels. A layout change, by contrast, moves whole
glyphs and edges and shows up as a channel delta in the hundreds.

So the gate is the peak per-channel delta, not the hash.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

# One step of channel noise is antialiasing; anything a human could see is far above.
DEFAULT_TOLERANCE = 2


@dataclass(frozen=True, slots=True)
class Comparison:
    name: str
    peak_delta: int
    changed_ratio: float
    note: str = ""
    tolerance: int = DEFAULT_TOLERANCE

    @property
    def failed(self) -> bool:
        return bool(self.note) or self.peak_delta > self.tolerance


def compare_directories(
    before: Path,
    after: Path,
    *,
    tolerance: int = DEFAULT_TOLERANCE,
) -> list[Comparison]:
    results: list[Comparison] = []
    for path in sorted(before.glob("*.png")):
        counterpart = after / path.name
        if not counterpart.exists():
            results.append(
                Comparison(path.name, 0, 0.0, "missing in after", tolerance)
            )
            continue

        left = Image.open(path).convert("RGB")
        right = Image.open(counterpart).convert("RGB")
        if left.size != right.size:
            note = f"size {left.size[0]}x{left.size[1]} != {right.size[0]}x{right.size[1]}"
            results.append(Comparison(path.name, 255, 1.0, note, tolerance))
            continue

        difference = ImageChops.difference(left, right).convert("L")
        histogram = difference.histogram()
        peak = max((value for value, count in enumerate(histogram) if count), default=0)
        changed = sum(histogram[1:]) / float(left.size[0] * left.size[1])
        results.append(Comparison(path.name, peak, changed, "", tolerance))

    for path in sorted(after.glob("*.png")):
        if not (before / path.name).exists():
            results.append(
                Comparison(path.name, 0, 0.0, "missing in before", tolerance)
            )
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument(
        "--tolerance",
        type=int,
        default=DEFAULT_TOLERANCE,
        help="highest per-channel delta still treated as renderer noise",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only the images that failed",
    )
    args = parser.parse_args(argv)

    results = compare_directories(args.before, args.after, tolerance=args.tolerance)
    if not results:
        print(f"no baselines found under {args.before}", file=sys.stderr)
        return 2

    failures = [result for result in results if result.failed]
    shown = failures if args.quiet else results
    if shown:
        print(f"{'image':<44}{'peak':>6}{'changed%':>10}  note")
        for result in shown:
            marker = "FAIL" if result.failed else "    "
            print(
                f"{result.name:<44}{result.peak_delta:>6}"
                f"{100.0 * result.changed_ratio:>10.3f}  {marker} {result.note}"
            )

    print(
        f"\n{len(results) - len(failures)}/{len(results)} baselines match "
        f"within a per-channel delta of {args.tolerance}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
