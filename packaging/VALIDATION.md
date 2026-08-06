# Release Artifact Validation

These checks are release gates, not informational reports. Any non-zero exit blocks the
corresponding artifact from signing or publication.

## CUDA manifest

The committed CUDA manifest must describe exactly the three Windows x64 wheels selected by the
`cuda` extra in `uv.lock`. Names, versions, immutable Python Hosted URLs, compressed sizes, and
SHA-256 digests must match the lock file.

```powershell
uv run --no-sync python packaging/manifests/validate_cuda_manifest.py `
  --manifest packaging/manifests/cuda-runtime-v1.json `
  --lock uv.lock
```

Run this before copying the CUDA manifest into either the application staging directory or the
Inno Setup input directory.

## CycloneDX and frozen runtime inventory

`runtime_inventory.py` treats the base dependency closure in `uv.lock` as the runtime authority.
It requires a CycloneDX 1.5 root matching `pyproject.toml`, exact locked runtime component
versions, all direct runtime distributions in the PyInstaller payload, and no dev, CUDA,
model-build, or packaging-only distributions.

The PyInstaller spec should collect the destination names from both analyses and write the
inventory after `MERGE`:

```python
import runpy

inventory = runpy.run_path(str(ROOT / "packaging/windows/runtime_inventory.py"))
analysis_entries = [
    entry[0]
    for analysis in (gui_analysis, cli_analysis)
    for toc in (analysis.pure, analysis.binaries, analysis.datas)
    for entry in toc
]
inventory["write_frozen_inventory"](
    analysis_entries,
    Path(os.environ["ASJ_FROZEN_INVENTORY_FILE"]),
    project_name="auto-speech-journal",
    project_version=os.environ["ASJ_PROJECT_VERSION"],
)
```

The build script must set both environment variables before invoking PyInstaller, copy the
generated JSON to the root of the onedir payload, and validate it against the generated SBOM:

```powershell
uv run --no-sync python packaging/windows/runtime_inventory.py validate `
  --sbom artifacts/windows/application/AutoSpeechJournal.cdx.json `
  --inventory artifacts/windows/application/payload/frozen-runtime-inventory.json `
  --lock uv.lock `
  --pyproject pyproject.toml `
  --payload artifacts/windows/application/payload
```

Do not hand-author the frozen inventory. It is derived from PyInstaller Analysis and must travel
inside the payload whose contents it describes.

## Final models-v1 assets and reference transcription

The final gate verifies each of the four release files against `models-v1.json`, safely extracts
the exact declared inventory into a clean directory, loads Preview and VAD from the re-extracted
files, and runs CPU Whisper plus Preview against a reviewed reference recording.

```powershell
uv run --no-sync python packaging/models/verify_model_release_assets.py `
  --manifest artifacts/models-v1/models-v1.json `
  --assets-dir artifacts/models-v1 `
  --reference-spec packaging/models/reference-audio-gate.json `
  --repository-root .
```

The repository currently carries an explicit `status: blocked` reference specification because
no redistributable reference recording has been reviewed and committed. This is intentional: the
models workflow must fail before attestation or publication until all of the following are added:

1. A redistributable 16 kHz mono WAV or FLAC fixture under the repository.
2. Its exact SHA-256 and bounded duration metadata.
3. Reviewed non-empty Preview and Traditional Chinese final transcripts.
4. SHA-256 values of the NFKC and whitespace-normalized transcripts.
5. License/provenance documentation for the recording.

Changing the gate to `ready` without those machine-verifiable fields still fails validation.
