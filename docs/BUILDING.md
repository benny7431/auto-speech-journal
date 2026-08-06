# Building and Development

## Requirements

- Windows 10/11 x64
- Python 3.11
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Inno Setup 6.7.3 only when building `Setup.exe`

## Development checks

```powershell
uv lock --check
uv sync --frozen --no-editable --extra dev
$env:PYTHONPATH = (Join-Path $PWD "src")

uv run --no-sync ruff check src tests tools packaging
uv run --no-sync pytest `
  --cov=auto_speech_journal `
  --cov-report=term-missing `
  --cov-report=xml:coverage.xml `
  --cov-fail-under=75
uv run --no-sync pre-commit run --all-files
uv run --no-sync python -m auto_speech_journal self-test `
  --no-model-check --no-microphone-check
uv run --no-sync python tools/validate_scene_assets.py --strict
```

模型測試使用 package 內的 `src/auto_speech_journal/runtime-models-v1.json`。清單只允許
可直接執行的 ONNX／CTranslate2 檔案，並固定 Hugging Face repository、完整 commit、
大小及 SHA-256。使用者端不得安裝 Torch、Transformers 或執行模型轉換。

## Python distributions

```powershell
uv build
uv run --no-sync python tools/verify_wheel_contents.py
```

版本只取自 `pyproject.toml`。Wheel 與 sdist 不含模型、CUDA runtime、錄音、資料庫或
其他使用者狀態。

## Unsigned Windows Setup

```powershell
winget install --id JRSoftware.InnoSetup -e --version 6.7.3
.\tools\build_windows_installer.ps1 -ReleaseBuild
```

輸出位於 `artifacts/windows/`。PyInstaller 產生單一 onedir payload，Inno Setup 直接將它
安裝到：

```text
%LOCALAPPDATA%\Programs\AutoSpeechJournal\app
```

這個 MVP 沒有 stable launcher、`current.json`、`versions\<version>`、自訂 runtime
inventory 或額外 SBOM pipeline。Setup 不下載模型，也不下載或安裝 NVIDIA CUDA 元件；
它只安裝可在 CPU 模式啟動的應用程式。

Setup 與內層 EXE 目前沒有 Authenticode 簽章。這是允許的正式輸出，不需要 SignPath、
OV 憑證或簽章 secrets。

## Windows package acceptance

Windows workflow 必須在乾淨的非管理員帳號完成：

1. 建置 PyInstaller onedir 與 Inno Setup。
2. 安裝到固定 `app` 目錄。
3. 啟動 App 並確認首次設定出現，Setup 本身未下載模型或 GPU 元件。
4. 解除安裝程式。
5. 確認 `%LOCALAPPDATA%\AutoSpeechJournal` 及外部日記資料夾未被刪除。

模型下載由首次設定中的 App 執行。它使用 `huggingface_hub`、固定完整 commit、HF cache
與內建 retry；失敗時保留 App 與既有資料，使用者可從首次設定重試。

## Advanced source install and CUDA

`install.ps1` 保留給進階使用者與開發者。這是獨立的 source/uv 安裝路徑，不是
`Setup.exe` 的修補器。它不建立第二套登入排程；除非指定 `-NoStart`，安裝完成只會直接
啟動一次 source App。正式版登入自啟仍由 Setup 安裝的 App 與首次設定管理。

```powershell
# 進階 CUDA 路徑
.\install.ps1

# 明確使用 CPU
.\install.ps1 -NoCuda
```

正式 Setup 永遠走 CPU-safe 路徑；CUDA 問題不得讓首次設定、資料庫或日記資料損毀。
