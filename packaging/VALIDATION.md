# Release Artifact Validation

These checks are release gates, not informational reports. Any non-zero exit blocks publication
of the corresponding artifact.

## Unsigned Windows release policy

The `v0.2.0` Setup and inner executables are intentionally allowed to ship without Authenticode.
The Windows verifier must not fail solely because `Get-AuthenticodeSignature` reports `NotSigned`,
and no SignPath, OV Authenticode, certificate, publisher, or timestamp secret is required. This
exception does not weaken the remaining release gates: the full test suite, CodeQL, Windows install
E2E, SHA-256 checksums, CycloneDX SBOM, GitHub artifact attestation, frozen runtime inventory, and
runtime-model reference inference must all pass.

README and release notes must disclose that Windows can show Unknown publisher or Microsoft
Defender SmartScreen for the unsigned installer and direct users to verify its SHA-256 and artifact
attestation. They must not instruct users to disable Windows Defender. A future release may add
signing after a suitable certificate becomes available without changing these validation gates.

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
model-conversion, or packaging-only distributions.

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

## Runtime Hugging Face models and reference transcription

The committed runtime manifest is the installer and repair authority. It must name only
ready-to-run ONNX or CTranslate2 files, group them into atomic model directories, and pin every
source by Hugging Face repository, full 40-character commit revision, path, byte size, SHA-256,
license, and provenance. Validate its schema and exact runtime allowlist with:

```powershell
uv run --no-sync python packaging/models/validate_runtime_model_manifest.py `
  --manifest packaging/manifests/runtime-models-v1.json
```

The validator rejects floating revisions, unsafe or duplicate paths, missing metadata, unexpected
Paraformer/Whisper/VAD files, and any source-model or conversion-only artifact. Setup and
`repair models` must not install Torch, Transformers, or Safetensors and must not convert a model
on the user's computer. Download URLs must be derived as
`https://huggingface.co/<repository>/resolve/<40-hex-revision>/<file-path>` rather than accepted as
arbitrary manifest input. The separately validated CUDA manifest remains the only source for
optional NVIDIA runtime wheels.

For a release candidate, provision all manifest files into a clean model root through the normal
resumable download path, then run the inference and reference-audio gate:

```powershell
uv run --no-sync python packaging/models/verify_runtime_models.py `
  --manifest packaging/manifests/runtime-models-v1.json `
  --models-dir <path> `
  --reference-spec packaging/models/reference-audio-gate.json `
  --repository-root .
```

This gate re-verifies every downloaded size and SHA-256, loads Paraformer Preview and Silero VAD,
and runs CPU Whisper plus Preview against the reviewed reference recording. It does not build,
repackage, publish, or attest a GitHub model Release.

The committed gate uses the Apache-2.0 `test_wavs/2.wav` fixture from the pinned Paraformer Hugging
Face revision. `reference-audio-gate.json` locks its repository, 40-character revision, source path,
source URL, license, audio SHA-256, bounded duration, reviewed non-empty Preview and Traditional
Chinese final transcripts, and normalized transcript hashes. Release automation additionally
requires `RUNTIME_MODELS_REFERENCE_TRANSCRIPT_APPROVED_SHA` to equal the tagged commit; changing
the fixture or expected text without a fresh review therefore blocks publication. License and
provenance documentation for the recording remain mandatory.

Changing the gate to `ready` without those machine-verifiable fields still fails validation.
