# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-06

### Added

- Per-user Windows x64 Setup built from a PyInstaller onedir application and Inno Setup.
- Stable native GUI/CLI launchers with atomic `current.json` version switching and rollback.
- Resumable direct Hugging Face model provisioning from immutable commit revisions, NVIDIA
  auto-detection, and CPU fallback.
- First-run consent flow for journal folder, startup, microphone testing, and update notifications.
- Repair, installer readiness, graceful shutdown, and owned startup-task CLI commands.
- CycloneDX SBOM, artifact attestations, build provenance, a versioned runtime-model manifest, and
  fail-closed release validation for tests, CodeQL, Windows installation E2E, and checksums.

### Changed

- End users no longer need Python, `uv`, Git, PowerShell, or administrator access.
- Application binaries now live under `%LOCALAPPDATA%\Programs\AutoSpeechJournal\versions` while
  runtime state remains under `%LOCALAPPDATA%\AutoSpeechJournal`.
- Sign-in startup and update checks default to off and require explicit user consent.
- Setup and `repair models` now fetch ready-to-run ONNX and CTranslate2 files directly from
  Hugging Face; they do not install Torch, Transformers, or Safetensors or convert models locally.
- The `v0.2.0` release policy permits unsigned Setup and inner executables. Authenticode can be
  restored after a suitable certificate becomes available, but its absence no longer blocks the
  release.

### Fixed

- Normalized Python package names so Dependabot can update pinned versions reliably.
- Preserve configuration, SQLite/WAL, spool, models, logs, and external Markdown journals during
  upgrades and default uninstall.
- Serialize model repair across Setup, CLI, and the first-run wizard, and recover an interrupted
  model-directory swap before resuming its verified `.part` downloads.
- Treat a missing or unavailable legacy Task Scheduler entry as a manual-start degradation instead
  of rolling back an already validated application activation.

### Security

- Release documentation identifies the unsigned installer and possible Windows Unknown publisher
  or SmartScreen prompts. Users verify SHA-256 and GitHub artifact attestations without disabling
  Windows Defender.
- CUDA wheel URLs, sizes, and SHA-256 digests remain pinned in a separate manifest. Every runtime
  model file is pinned by Hugging Face repository, full commit revision, size, and SHA-256 before
  atomic installation.

## [0.1.0] - 2026-08-06

### Added

- Local-first Windows microphone journal with explicit first-run device consent.
- Streaming Sherpa-ONNX preview, Silero VAD segmentation and Faster-Whisper finalization.
- Durable FLAC spool, SQLite authority, atomic hourly Markdown exports and crash recovery.
- Traditional Chinese correction learning, microphone switching and PySide6/QML desktop UI.
- Complete offline matrix of 192 verified seasonal scene assets.
- Source installer, uninstaller, self-test, model verification and packaging checks.

### Security

- Pinned model revisions, sizes and SHA-256 digests.
- Runtime path excludes telemetry and remote transcription APIs.

[Unreleased]: https://github.com/benny7431/auto-speech-journal/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/benny7431/auto-speech-journal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/benny7431/auto-speech-journal/releases/tag/v0.1.0
