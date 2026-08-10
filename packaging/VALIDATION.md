# Packaging Validation

The release package is intentionally small: a PyInstaller onedir application, an unsigned Inno
Setup, wheel, source distribution, checksums, and Traditional Chinese release notes.

## Application gates

```powershell
uv lock --check
uv sync --frozen --no-editable --extra dev
$env:PYTHONPATH = (Join-Path $PWD "src")
uv run --no-sync ruff check src tests tools packaging
uv run --no-sync pytest --cov=auto_speech_journal --cov-fail-under=75
uv run --no-sync pre-commit run --all-files
uv run --no-sync python -m auto_speech_journal self-test --no-model-check --no-microphone-check
```

Regular CI validates the embedded `src/auto_speech_journal/runtime-models-v1.json` schema, pinned
full revisions, hashes, and reference inference behavior. There is no separate model release
workflow. Do not weaken the model inference or reference-audio tests.

## Python artifacts

```powershell
uv build
uv run --no-sync python tools/verify_wheel_contents.py
```

Wheel and sdist must not contain runtime model payloads, CUDA runtime DLLs, recordings, databases,
logs, caches, or other user state.

## Windows Setup

```powershell
.\tools\build_windows_installer.ps1 -ReleaseBuild
```

Acceptance requires:

- installation without elevation to `%LOCALAPPDATA%\Programs\AutoSpeechJournal\app`;
- a CPU-safe payload with no model or GPU download in Setup;
- no launcher, `current.json`, versioned application directories, or repair shortcut;
- first launch reaches the consent wizard;
- the App can download pinned models through `huggingface_hub` and resume after a failure;
- uninstall removes application-owned files while preserving
  `%LOCALAPPDATA%\AutoSpeechJournal` and external journals.

The Setup and inner EXE are expected to be unsigned for `v0.3.1`. Missing Authenticode must not
fail packaging or release validation. Release notes must warn about Unknown publisher/SmartScreen
and instruct users to verify the published SHA-256. Never instruct users to disable Windows
Defender.

## Release gates

CI, CodeQL, and Windows install/start/uninstall E2E must pass. Generate checksums for every
published artifact:

```powershell
Get-FileHash .\artifacts\windows\setup\*.exe -Algorithm SHA256
Get-FileHash .\dist\* -Algorithm SHA256
```

A versioned release must use a clean `main` commit, an annotated tag that matches
`pyproject.toml`, and immutable assets published under that version.
