from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts" / "today-river-production-v2" / "prompts"
GLOBAL_STYLE = (
    "Japanese lifestyle magazine editorial illustration, matte digital chalk pastel and dry "
    "gouache on subtly fibrous paper, soft simplified geometry, low saturation, humid "
    "atmospheric depth, gentle imperfect edges, sophisticated and quiet."
)
GLOBAL_CONSTRAINTS = (
    "No people, faces, bodies, hands, silhouettes, animals, readable writing, letters, "
    "numerals, logos, brands, icons, microphones, audio hardware, UI controls, watermarks, "
    "borders, mockup frames, photorealism, glossy 3D, hard vector edges, or watercolor blooms. "
    "All notebook and paper surfaces must be completely blank."
)
MONTHS = {
    "01": (
        "frost-window winter morning",
        "an ivory and frost-blue room with a wool throw, steaming tea, a warm ceramic lamp, "
        "a bare twig in a small bottle, a blank notebook, and thin winter sunlight",
    ),
    "02": (
        "plum-violet late winter",
        "a dusty-violet room with a plum blossom twig, cocoa cup, folded knit cloth, a small "
        "brass-toned lamp, blank papers, and soft mauve dusk light",
    ),
    "03": (
        "tender-green early spring rain",
        "a celadon room with a rain-soft window, translucent umbrella resting by the wall, "
        "sprouting plant, glass of water, blank notebook, and fresh diffuse air",
    ),
    "04": (
        "rain-mirror spring desk",
        "a fog-blue reflective room with a wet window, pressed leaves, glass carafe, pale "
        "ceramic dish, layered blank paper, and gentle April rain",
    ),
    "05": (
        "emerald early-summer canopy",
        "an emerald and moss room filtered through foliage, with hydrangea-like bloom shapes, "
        "woven basket, cool tea, linen, blank notebook, and dappled light",
    ),
    "06": (
        "indigo monsoon interior",
        "an indigo and muted-teal room with long rain currents on glass, blue linen, glass "
        "pitcher, simple bowl, blank papers, and a sheltered low lamp",
    ),
    "07": (
        "midsummer traces of home",
        "a humid pearl and celadon July interior with quiet household objects, moving air, "
        "misted windows, tea, linen, blank paper, and changing light",
    ),
    "08": (
        "teal high-summer storm",
        "a teal and sea-green room with a storm-bright window, clear iced water, folded blue "
        "towel, unbranded tabletop fan shape, blank notebook, and humid air",
    ),
    "09": (
        "moonlit early autumn reeds",
        "a moon-silver and reed-beige room with pampas stems, teapot and cup, blank notebook, "
        "woven mat, and a cool blue evening window",
    ),
    "10": (
        "amber autumn afternoon",
        "an amber and burnt-apricot room with pears, dry leaves, warm scarf, ceramic lamp, "
        "blank papers, and long golden shadows",
    ),
    "11": (
        "twilight-violet early winter",
        "a violet and dusty-plum room with a knitted blanket, small kettle, dried branches, "
        "blank notebook, dim blue window, and restrained warm lamp",
    ),
    "12": (
        "ivory winter-star mist",
        "a pearl-ivory room with pale-gold oranges, unmarked candles, wool cloth, closed blank "
        "notebook, winter window, and sparse reflected light flecks",
    ),
}
STATES = {
    "starting": (
        "room waking: closed notebook, lamp beginning to glow, first light entering, sparse "
        "objects emerging from mist"
    ),
    "listening": (
        "calm attentive equilibrium: open blank notebook, steady cup or vessel, diffuse light, "
        "and barely perceptible moving air"
    ),
    "capturing": (
        "active but not urgent: a curtain or paper edge lifted by air, water surface rippling, "
        "stronger directional light, and slightly more spatial energy"
    ),
    "finalizing": (
        "settling into order: papers aligned, pen capped, vessels upright, light becoming even, "
        "and a consolidated composition"
    ),
    "paused": (
        "deliberate stillness: tied or closed notebook, motion suspended, saturation softened, "
        "and a quiet resting arrangement"
    ),
    "degraded": (
        "reduced reliability: denser haze or rain, uneven light, one slightly displaced object, "
        "and a restrained amber accent while remaining readable"
    ),
    "error": (
        "decisive disruption: stormier contrast, one sharply displaced harmless object, and a "
        "restrained rust-coral thread or accent without danger symbols"
    ),
    "stopped": (
        "closed stillness: emptying light, closed notebook, very few objects, almost no motion, "
        "and a pale restful finish rather than darkness"
    ),
}


def prompt_for(month: str, state: str, variant: str) -> str:
    theme, scene = MONTHS[month]
    header = (
        f'Primary request: Create scene for month {month}, state "{state}". '
        f'Output variant "{variant}".'
    )
    if variant == "workspace" and state == "listening":
        request = (
            f"Create the {theme} listening workspace scene as a new image. Scene: {scene}. "
            f"State treatment: {STATES[state]}."
        )
        inputs = "Input images: none; this is the monthly listening master."
    elif variant == "workspace":
        request = (
            f"Edit Image 1 into the {theme} {state} workspace scene. Change only atmosphere, "
            f"object arrangement and state energy. State treatment: {STATES[state]}."
        )
        inputs = (
            "Input images: Image 1 is this month's accepted listening workspace master. "
            "Preserve the room, viewpoint, palette, paper texture and monthly identity."
        )
    else:
        request = (
            f"Recompose Image 1 into the compact 4:3 version of the same {theme} {state} scene. "
            "Preserve its exact objects, palette, light and state; add nothing. Place the "
            "strongest object cluster in the left 35 percent and create a pale low-detail "
            "feather zone across the right half for status text. This must be a true "
            "recomposition, not a crop."
        )
        inputs = "Input images: Image 1 is the matching accepted workspace scene edit target."
    composition = (
        "3:2 landscape, 1536x1024 intended output, full bleed, crop-safe edges, calm low-detail "
        "center and right reading regions, natural interior depth"
        if variant == "workspace"
        else "4:3 landscape, 1024x768 intended output, thumbnail-readable left cluster"
    )
    return "\n".join(
        (
            "Use case: stylized-concept",
            "Asset type: offline desktop voice-journal background illustration",
            header,
            inputs,
            f"Scene/backdrop: {scene}.",
            f"Subject/state: {request}",
            f"Style/medium: {GLOBAL_STYLE}",
            f"Composition/framing: {composition}.",
            f"Constraints: {GLOBAL_CONSTRAINTS}",
        )
    )


def build_prompts(output_dir: Path, *, overwrite: bool = False) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    preserved = 0
    for month in MONTHS:
        for state in STATES:
            for variant in ("compact", "workspace"):
                destination = output_dir / f"{month}-{state}-{variant}.txt"
                if destination.exists() and not overwrite:
                    preserved += 1
                    continue
                destination.write_text(
                    prompt_for(month, state, variant) + "\n",
                    encoding="utf-8",
                )
                written += 1
    return written, preserved


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build reproducible v2 scene prompts")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    written, preserved = build_prompts(args.output_dir, overwrite=args.overwrite)
    print(f"Wrote {written} prompts; preserved {preserved} existing prompts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
