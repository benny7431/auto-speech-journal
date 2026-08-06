# Building and Development

## Prerequisites

- Windows 10/11 x64
- PowerShell 5.1 or newer
- Python 3.11
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Git

模型與麥克風不是一般單元測試、lint 或 wheel build 的必要條件。

## Create the locked environment

```powershell
uv sync --frozen --no-editable --extra dev
$env:PYTHONPATH = (Join-Path $PWD "src")
```

`--frozen` 確保 `uv.lock` 沒有被隱式更新。修改 dependency 後執行 `uv lock`，檢查 diff，
再重新跑完整門檻。

## Quality gates

```powershell
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

需要完整硬體／模型驗證時，使用 `install.ps1` 建立的環境執行 `self-test
--deep-model-check --test-microphone`。CPU 安裝需加入 `--allow-cpu-finalizer`。

## Build distributions

```powershell
uv build
uv run --no-sync python tools/verify_wheel_contents.py
```

輸出位於 `dist/`：一個 wheel 與一個 source distribution。Verifier 會確認 entry point、
metadata、LICENSE、第三方聲明、QML、192 張場景與禁止的 runtime/user data。

## Run from source

```powershell
uv run --no-sync python -m auto_speech_journal run
```

第一次啟動仍需明確選擇麥克風。不要用真實使用者資料執行測試或 README 擷取。

## Render the privacy-safe README demo

```powershell
uv run --no-sync python tools/render_readme_demo.py
```

工具使用正式 QML 與純記憶體 synthetic controller，輸出
`docs/images/auto-speech-journal-demo.gif`。它不載入麥克風、模型、SQLite、網路或使用者
runtime 路徑。

## Repository text contract

`.gitattributes` 固定 Python/QML/Markdown/YAML/TOML/JSON 為 LF，PowerShell 為 CRLF。
`install.ps1` 與 `uninstall.ps1` 另須保留 UTF-8 BOM 以相容 Windows PowerShell 5.1。
不要以會移除 BOM 或一次格式化整個 repository 的工具改寫這兩個檔案。

## Focused tools

- `tools/replay_fault_recovery.py`: 重播 crash boundaries。
- `tools/benchmark_preview_latency.py`: 以已授權測試音訊量測 preview latency。
- `tools/validate_scene_assets.py --strict`: 驗證完整場景矩陣與 digest。
- `tools/verify_wheel_contents.py`: 驗證發布物邊界。
