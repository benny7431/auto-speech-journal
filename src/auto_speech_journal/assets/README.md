# Offline scene assets

This directory is the only production runtime source for the journal's generated scene artwork.

- `manifest.json` describes the complete 12 month x 8 state x 2 variant matrix.
- No generated bitmap is represented as complete until it has been inspected, copied into this
  directory, and validated as `status: "ready"`.
- `planned_prompt` is the production prompt prepared before generation. Once an image is
  accepted, copy the exact prompt actually used into `final_prompt`, set `status` to
  `"ready"`, and record its decoded dimensions and SHA-256 digest.
- Runtime assets are named `MM-state-variant.webp`; absolute paths and machine-local generator
  cache references are forbidden.
- Each non-listening workspace state is an edit of that month's accepted listening master. Each
  compact image is a true recomposition of the matching workspace scene.

Development validation permits pending assets:

```powershell
uv run --no-sync python tools/validate_scene_assets.py
```

The production/package gate requires all 192 decoded WebP files:

```powershell
uv run --no-sync python tools/validate_scene_assets.py --strict
```

Manifest schema v2 contains the complete matrix: `compact` is 1024x768 and `workspace` is
1536x1024. Development validation permits `status: "planned"`; `--strict` requires every entry
to be `ready` with decoded dimensions and SHA-256 digests. `derived_from` records each state's
workspace listening master or each compact image's matching workspace source.

Prototype images stay outside this package. Set `AUTO_SPEECH_JOURNAL_SCENE_DIR` to an ignored
prototype directory to preview variant files; a missing override safely falls back to the
packaged v2 scene.

The accepted ImageGen title-bar mark lives at `brand/journal-ink-icon.png`; its generation
prompt, dimensions, and SHA-256 are recorded in the adjacent JSON manifest. Runtime UI must
not reference generator cache paths.

Fonts are deliberately not package assets. Personal fonts are discovered from the workstation's
runtime font directory (and from the source checkout's local `字體` directory during development).
The wheel excludes `assets/fonts`, and the installer removes any legacy copy from its staging tree.
Local notices are centralized under the repository-root `聲明` directory; neither directory is a
distribution artifact.
