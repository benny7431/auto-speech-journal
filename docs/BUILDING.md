# Building and Development

## Prerequisites

- Windows 10/11 x64
- Windows PowerShell 5.1 or newer
- Python 3.11 and [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Git
- Building Setup: Inno Setup `6.7.3` (`ISCC.exe`)

一般單元測試、lint、wheel 與無模型的 frozen probe 不需要麥克風或 NVIDIA GPU。

## Locked environment and quality gates

```powershell
uv sync --frozen --no-editable --extra dev
$env:PYTHONPATH = (Join-Path $PWD "src")
uv run --no-sync ruff check src tests tools
uv run --no-sync pytest `
  --cov=auto_speech_journal `
  --cov-report=term-missing `
  --cov-report=xml:coverage.xml `
  --cov-fail-under=75
uv run --no-sync python -m auto_speech_journal self-test `
  --no-model-check --no-microphone-check
uv run --no-sync python tools/validate_scene_assets.py --strict
uv run --no-sync pre-commit run --all-files
```

`--frozen` 禁止隱式修改 `uv.lock`。版本與 dependency 改變後先執行 `uv lock` 並檢查 diff。

## Build Python distributions

```powershell
uv build
uv run --no-sync python tools/verify_wheel_contents.py
```

版本只從 `pyproject.toml` 取得；wheel verifier、PyInstaller resource、launcher、Inno 與檔名
不可另外硬碼版本。輸出位於 `dist/`。

## Build the Windows package

```powershell
winget install --id JRSoftware.InnoSetup -e --version 6.7.3
.\tools\build_windows_installer.ps1 -Stage All
.\tools\verify_windows_installer.ps1 -AllowUnsigned
```

建置腳本使用 `uv.lock` 內固定的 `PyInstaller 6.16.0` 產生 onedir payload：

- `AutoSpeechJournal.exe`: 無 console GUI，固定執行 `run`。
- `AutoSpeechJournal.CLI.exe`: console 維護／診斷入口。
- 原生 .NET Framework stable GUI/CLI launcher：只接受 `current.json` 的固定 target 名稱，
  驗證版本相對路徑，並依 Windows quoting 規則轉發 argv。

輸出在 `artifacts/windows/`。`verify_windows_installer.ps1` 會拒絕 Torch、Transformers、
NVIDIA runtime、模型、使用者狀態或本機字體混入 frozen payload，並執行真正的
`installer-probe --isolated` 以載入凍結後的 Qt/QML。`-AllowUnsigned` 只允許 PR 的內部
artifact；公開發行不可帶這個參數。

分段簽章建置使用：

```powershell
# 先產生內層程式，交由 SignPath 簽章
.\tools\build_windows_installer.ps1 -Stage Application `
  -ReleaseBuild -ModelManifestPath .\models-v1.json

# 以簽章後 payload/launcher 建外層 Setup
.\tools\build_windows_installer.ps1 -Stage Installer `
  -ReleaseBuild `
  -AppPayloadPath .\signed\payload `
  -LauncherPath .\signed\launchers `
  -ModelManifestPath .\models-v1.json
```

`-ReleaseBuild` 會拒絕 repository 內的 model placeholder；必須使用不可變 `models-v1`
Release 所附、通過 GitHub attestation 且 SHA-256 已提交到 source 的正式 manifest。

## Installer test matrix

PR workflow 執行 unsigned、非管理員、`/NOMODELS /NOGPU` 的安裝→frozen probe→解除安裝，
並驗證 config、SQLite/WAL、spool、models dummy state 全部保留。版本 pre-release 前另需完成：

1. Windows 10 x64 CPU。
2. Windows 11 x64 CPU。
3. Windows 11 x64 NVIDIA：自動偵測、wheel hash、DLL extraction、CTranslate2 CUDA probe。
4. v0.1 legacy app/task 遷移與故障注入 rollback。
5. 已授權、可重現的參考音訊轉錄 fixture（記錄來源、SHA-256 與預期文字）。Repository
   目前不含個人錄音，因此這是建立 `models-v1` 及實機 release sign-off 的人工硬門檻。

## Text and generated artifacts

Repository 的 Python/QML/Markdown/YAML/TOML/JSON 使用 LF；PowerShell 使用 CRLF。所有
`.ps1`（包含 packaging runner）保留 UTF-8 BOM 以相容 Windows PowerShell 5.1。runtime
資料、模型、簽章憑證、token 與個人錄音不得加入 repository。

## Focused tools

- `tools/replay_fault_recovery.py`: 重播 crash boundaries。
- `tools/benchmark_preview_latency.py`: 量測 preview latency。
- `tools/validate_scene_assets.py --strict`: 驗證完整場景矩陣與 digest。
- `tools/verify_wheel_contents.py`: 驗證 Python 發行物邊界。
- `tools/verify_windows_installer.ps1`: 驗證 frozen payload、QML probe 與 Authenticode。
