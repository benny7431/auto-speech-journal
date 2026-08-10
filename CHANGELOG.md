# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-08-10

### 移除

- 移除由 12 個月份、8 種狀態與 2 個變體組成的 192 張 WebP 場景背景、粒子貼圖、
  `SceneArt.qml`、`AmbientSoundRiver.qml`、`TodayParticleLayer.qml`、
  `PigmentAbsorption.qml`、`scene_assets.py`，以及 9 支場景與粒子工具。介面改以紙面、
  字體與單一低透明度月份色調呈現；wheel 不再封裝約 41 MB 的場景美術，UI 測試時間
  約縮短一半。已移除資產仍可從 Git 歷史取回。
- 移除只用於預覽原型場景矩陣的 `AUTO_SPEECH_JOURNAL_SCENE_DIR`，以及
  `tools/render_ui_baselines.py` 的 `--scene-dir` 選項。

### 修正

- 修正 `tools/render_ui_baselines.py` 未設定 `onboarding_completed`，導致每次擷取都無法
  通過視窗尺寸檢查的問題。
- 基準圖改用 reduced-motion 路徑，避免同一 revision 重複執行時有最多約五分之一的
  像素漂移。
- 修正 gate matrix 的 `default` 與 `large` 視窗尺寸被無聲壓成相同尺寸的問題，並讓
  工具抽屜納入基準圖驗證。
- 移除 `install.ps1` 對已刪除場景資產的過時驗證門檻，避免 source/CUDA 安裝流程因
  不再發行的檔案而失敗。
- `SystemSheet` 與 `HoursSheet` 補上 bottom anchor，並可捲動、裁切內容，避免內容區
  高度變成 0，或在最大日記字級時超出抽屜而無法查看。

### 變更

- 展開工作區重建為時間軸稿本：左側時間邊欄與墨線串接內容，片段依文字長度流動排列，
  小時以橫線與大字分隔，只有需要處理的片段顯示狀態節點。
- 日期移入標題列；精簡錄音器使用完整寬度，狀態、音量與待處理數量共用同一條基線；
  設定抽屜改為六個具標題的區塊，並固定儲存操作。
- `qml/Theme.qml` 成為色彩、間距與節奏的單一來源，回歸測試會確認重建介面沒有殘留
  色碼字面值。
- `JournalWindow.qml` 拆分為較小的 QML 元件，降低主視窗的版面與狀態協調負擔。

### 新增

- 新增 `tools/compare_ui_baselines.py`，以每通道峰值差比較兩組基準圖，讓無視覺差異的
  重構可以被驗證，而不只依賴人工判斷。

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

[Unreleased]: https://github.com/benny7431/auto-speech-journal/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/benny7431/auto-speech-journal/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/benny7431/auto-speech-journal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/benny7431/auto-speech-journal/releases/tag/v0.1.0
