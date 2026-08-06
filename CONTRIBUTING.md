# Contributing

感謝協助改善 Auto Speech Journal。專案以 Windows 11、Python 3.11、PowerShell 5.1 與
本機優先資料處理為主要契約。

## Before opening an issue

先搜尋既有 Issue，並閱讀 [README](README.md)、[隱私說明](PRIVACY.md)及
[疑難排解](docs/TROUBLESHOOTING.md)。安全問題請依 [SECURITY.md](SECURITY.md) 處理。

公開內容不得包含錄音、逐字稿、`state.db*`、FLAC、完整 `config.json`、設定歷程、
未去識別化日誌、使用者名稱、絕對路徑、裝置 endpoint ID 或憑證。請只提供重現問題
所需的最少資訊。

## Development setup

從 PowerShell 執行：

```powershell
uv sync --no-editable --extra dev
$env:PYTHONPATH = (Join-Path $PWD "src")
uv run --no-sync pytest
uv run --no-sync ruff check src tests tools
uv build
uv run --no-sync python tools/verify_wheel_contents.py
```

不需要模型或麥克風的 smoke test：

```powershell
uv run --no-sync python -m auto_speech_journal self-test --no-model-check --no-microphone-check
```

完整環境與建置方式見 [docs/BUILDING.md](docs/BUILDING.md)。

## Change expectations

- 使用四格縮排、型別註記與 100 字元行寬；Ruff 規則由 `pyproject.toml` 定義。
- 重型或 native import 應延後，使離線及 controller 測試仍可 import。
- SQLite 是權威資料，Markdown 是可重建輸出；不得繞過既有 crash recovery 契約。
- 修改 `install.ps1` 或 `uninstall.ps1` 時保留 UTF-8 BOM、PowerShell 5.1 與 CRLF。
- 不要提交模型、錄音、資料庫、runtime 設定、日誌、個人字體或生成器 cache。
- 行為變更需加入聚焦 regression test；使用者可見變更需更新 CHANGELOG 與文件。

## Pull requests

PR 應保持單一目的，連結相關 Issue，說明資料保存、安裝或隱私風險，並列出實際執行的
驗證命令。UI 變更請附無個資截圖或合成 Demo。建議使用 Conventional Commit 的簡短
imperative subject，例如 `fix: preserve spool recovery state`。

提交 PR 即表示貢獻內容可依本專案 [MIT License](LICENSE) 散布；本專案目前沒有 CLA。
