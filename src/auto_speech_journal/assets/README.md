# Packaged assets

This directory holds the only bitmaps that ship inside the wheel.

The accepted ImageGen title-bar mark lives at `brand/journal-ink-icon.png`; its generation
prompt, dimensions, and SHA-256 are recorded in the adjacent JSON manifest. Runtime UI must
not reference generator cache paths.

The journal previously shipped a 12 month x 8 state x 2 variant matrix of generated scene
backgrounds under `scenes/`, plus particle sprites under `particles/`. Both were removed when
the interface moved to plain paper: the recorder state now reads from typography and a single
low-opacity month tint. The removal is recorded in `CHANGELOG.md`, and the assets remain
recoverable from Git history if that decision is ever revisited.

Fonts are deliberately not package assets. Personal fonts are discovered from the workstation's
runtime font directory (and from the source checkout's local `字體` directory during development).
The wheel excludes `assets/fonts`, and the installer removes any legacy copy from its staging tree.
Local notices are centralized under the repository-root `聲明` directory; neither directory is a
distribution artifact.
