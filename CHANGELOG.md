# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-06

### Added

- Signed, per-user Windows x64 Setup built from a PyInstaller onedir application and Inno Setup.
- Stable native GUI/CLI launchers with atomic `current.json` version switching and rollback.
- Resumable manifest-based model provisioning, NVIDIA auto-detection, and CPU fallback.
- First-run consent flow for journal folder, startup, microphone testing, and update notifications.
- Repair, installer readiness, graceful shutdown, and owned startup-task CLI commands.
- CycloneDX SBOM, build provenance, immutable model bundle, and two-stage SignPath workflows.

### Changed

- End users no longer need Python, `uv`, Git, PowerShell, or administrator access.
- Application binaries now live under `%LOCALAPPDATA%\Programs\AutoSpeechJournal\versions` while
  runtime state remains under `%LOCALAPPDATA%\AutoSpeechJournal`.
- Sign-in startup and update checks default to off and require explicit user consent.

### Fixed

- Normalized Python package names so Dependabot can update pinned versions reliably.
- Preserve configuration, SQLite/WAL, spool, models, logs, and external Markdown journals during
  upgrades and default uninstall.

### Security

- Public releases fail closed without timestamped SignPath signatures for inner executables and
  Setup; signed assets are never replaced in place.
- CUDA wheel URLs, sizes, and SHA-256 digests are pinned, and model archives are verified before
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
