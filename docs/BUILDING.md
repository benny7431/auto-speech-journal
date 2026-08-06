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
uv run --no-sync python packaging/models/validate_runtime_model_manifest.py `
  --manifest packaging/manifests/runtime-models-v1.json
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
.\tools\build_windows_installer.ps1 -Stage All `
  -ReleaseBuild `
  -ModelManifestPath .\packaging\manifests\runtime-models-v1.json
.\tools\verify_windows_installer.ps1
```

建置腳本使用 `uv.lock` 內固定的 `PyInstaller 6.16.0` 產生 onedir payload：

- `AutoSpeechJournal.exe`: 無 console GUI，固定執行 `run`。
- `AutoSpeechJournal.CLI.exe`: console 維護／診斷入口。
- 原生 .NET Framework stable GUI/CLI launcher：只接受 `current.json` 的固定 target 名稱，
  驗證版本相對路徑，並依 Windows quoting 規則轉發 argv。

輸出在 `artifacts/windows/`。`verify_windows_installer.ps1` 會拒絕 Torch、Transformers、
Safetensors、NVIDIA runtime、模型、使用者狀態或本機字體混入 frozen payload，並執行真正的
`installer-probe --isolated` 以載入凍結後的 Qt/QML。`v0.2.0` 的內層 EXE 與 Setup 預期為
未簽章；Authenticode 缺失不是 verifier 或正式 Release 的失敗條件。

正式未簽章建置也可分段執行：

```powershell
# 先產生內層程式與 stable launchers
.\tools\build_windows_installer.ps1 -Stage Application `
  -ReleaseBuild `
  -ModelManifestPath .\packaging\manifests\runtime-models-v1.json

# 直接使用已驗證的未簽章 payload/launchers 建立 Setup
.\tools\build_windows_installer.ps1 -Stage Installer `
  -ReleaseBuild `
  -AppPayloadPath .\artifacts\windows\application\payload `
  -LauncherPath .\artifacts\windows\application\launchers `
  -ModelManifestPath .\packaging\manifests\runtime-models-v1.json

.\tools\verify_windows_installer.ps1
```

未來取得合適的程式碼簽章憑證後，可以在不改變 payload、測試與 provenance 契約的前提下
重新加入簽章階段；目前不需要任何 SignPath、OV Authenticode 或其他簽章 secret。正式 Release
仍必須通過完整測試、CodeQL、Windows 安裝 E2E、SHA-256、SBOM、artifact attestation 與模型
參考音訊驗證。Windows 可能對未簽章 Setup 顯示未知發行者或 SmartScreen 提示；測試與文件
不得要求使用者停用 Windows Defender。

`packaging/manifests/runtime-models-v1.json` 是 repository 內版本化的 runtime 供應清單。
`-ReleaseBuild` 會要求每個 Hugging Face 來源使用完整 40 位 commit revision，並檢查允許的
ONNX／CTranslate2 檔案清單、精確 byte size、SHA-256、授權與來源欄位。不得使用
`main`、`latest`、branch/tag 或 redirect 後未綁定 revision 的 URL。

Setup 與 `repair models` 只下載 manifest 內可直接執行的檔案，不安裝 Torch、Transformers
或 Safetensors，也不呼叫模型轉換器。`.part` 續傳、Range fallback、重試、磁碟 preflight、
逐檔 SHA-256 與原子替換都屬於 installer/provisioner 測試契約。CUDA runtime 另由
`packaging/manifests/cuda-runtime-v1.json` 管理，不得混入 runtime model manifest。

## Installer test matrix

PR workflow 執行 unsigned、非管理員、`/NOMODELS /NOGPU` 的安裝→frozen probe，接著用
固定 Hugging Face VAD 實際驗證 Range 續傳、損壞 `.part` 重試、已安裝檔修復、錯誤 hash
不回滾程式，再解除安裝並確認 config、SQLite/WAL、spool 與 models 全部保留。版本
pre-release 前另需完成：

1. Windows 10 x64 CPU。
2. Windows 11 x64 CPU。
3. Windows 11 x64 NVIDIA：自動偵測、wheel hash、DLL extraction、CTranslate2 CUDA probe。
4. v0.1 legacy app/task 遷移與故障注入 rollback。
5. 已授權、可重現的參考音訊轉錄 fixture：目前固定為 Paraformer Hugging Face repository
   `8e40c43232a1c5c66c82111efc5820d3accca11b` 的 Apache-2.0 `test_wavs/2.wav`，並鎖定來源、
   音訊 SHA-256、預期文字及文字 SHA-256。每個 release commit 仍須人工核准，且不得刪除或
   弱化實際 Preview、VAD、CPU Whisper 推論來讓 release 通過。

## Text and generated artifacts

Repository 的 Python/QML/Markdown/YAML/TOML/JSON 使用 LF；PowerShell 使用 CRLF。所有
`.ps1`（包含 packaging runner）保留 UTF-8 BOM 以相容 Windows PowerShell 5.1。runtime
資料、模型、secret、token 與個人錄音不得加入 repository。

## Focused tools

- `tools/replay_fault_recovery.py`: 重播 crash boundaries。
- `tools/benchmark_preview_latency.py`: 量測 preview latency。
- `tools/validate_scene_assets.py --strict`: 驗證完整場景矩陣與 digest。
- `tools/verify_wheel_contents.py`: 驗證 Python 發行物邊界。
- `tools/verify_windows_installer.ps1`: 驗證 frozen payload、QML probe、SBOM 與安裝器內容邊界；
  `v0.2.0` 不以 Authenticode 是否存在作為通過條件。
