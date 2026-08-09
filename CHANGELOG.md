# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed

- The generated scene-art system: the 12 month x 8 state x 2 variant matrix of 192 WebP
  backgrounds, the particle sprites, `SceneArt.qml`, `AmbientSoundRiver.qml`,
  `TodayParticleLayer.qml`, `PigmentAbsorption.qml`, `scene_assets.py`, and the nine scene and
  particle tools. Photographic backgrounds forced three compensating translucent washes under
  every text surface and still left the handwriting fonts low-contrast, so the interface now
  reads from paper, type, and a single low-opacity month tint. The wheel drops from roughly
  41 MB of packaged art, and the UI test suite runs in half the time. The assets remain
  recoverable from Git history.
- `AUTO_SPEECH_JOURNAL_SCENE_DIR` and the `--scene-dir` option of `tools/render_ui_baselines.py`,
  which existed only to preview prototype scene matrices.

### Fixed

- `tools/render_ui_baselines.py` produced no usable baselines: it left `onboarding_completed`
  unset so every grab failed the window-size check, its output drifted by up to a fifth of the
  pixels between runs of the same revision, and the gate matrix silently collapsed its
  `default` and `large` window sizes onto one clamped size.

### Added

- `tools/compare_ui_baselines.py`, which compares two baseline directories on peak per-channel
  delta so a visually inert refactor can be demonstrated rather than asserted.

## [0.2.0] - 2026-08-06

### Added

- Per-user Windows 10/11 x64 Setup that may be formally released without Authenticode signing.
- First-run consent wizard for local storage, journal folder, startup, microphone, and explicit
  recording start.
- Direct download of ready-to-run ONNX and CTranslate2 models from immutable Hugging Face commits
  through `huggingface_hub`.
- Transactional journal-folder and microphone testing that preserves the last working settings.

### Changed

- Setup installs the PyInstaller onedir application directly to
  `%LOCALAPPDATA%\Programs\AutoSpeechJournal\app`.
- Setup is CPU-safe and downloads neither models nor GPU components; the first-run App owns model
  download, Hugging Face caching, retries, verification, and resume.
- `install.ps1` remains the advanced source/CUDA installation path, with `-NoCuda` for CPU use.
  It no longer creates a second, incompatible login-startup task.
- Release validation is reduced to normal CI, CodeQL, Windows installation E2E, wheel/sdist,
  unsigned Setup, checksums, and test summary.

### Removed

- Stable launcher, `current.json`, versioned application directories, custom rollback/runtime
  inventory, repair shortcuts, and the standalone model release workflow.
- SignPath Foundation, paid OV certificate, signing secrets, inner-EXE signing, Setup signing, and
  all Authenticode hard gates.

### Fixed

- Existing v3 settings migrate without forcing established users through onboarding again.
- Incomplete onboarding cannot start recording or create login startup.
- Uninstall preserves configuration, SQLite/WAL, spool, models, cache, and external journals.
- Reinstall replaces the fixed application payload cleanly so removed DLL/QML files cannot linger.

### Security

- Runtime model repositories, full commits, paths, sizes, SHA-256 values, licenses, and sources are
  pinned in the packaged manifest.
- Documentation warns that the unsigned installer may trigger Unknown publisher or SmartScreen,
  requires SHA-256 verification, and never asks users to disable Windows Defender.

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
