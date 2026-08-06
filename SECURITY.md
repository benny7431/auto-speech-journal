# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.2.x | Yes, best effort |
| 0.1.x | Security fixes only |
| Earlier or untagged snapshots | No |

## Reporting a vulnerability

本專案目前沒有專用安全信箱，也尚未啟用 GitHub Private Vulnerability Reporting。

請建立一個**不含漏洞細節**的公開 Issue，只寫明「需要安全聯絡」以及可公開的聯絡方式；
維護者會再協調後續管道。不要在公開 Issue、PR、Discussion 或 commit 中提供 exploit、
敏感重現步驟或可識別個人資料。

在建立任何公開項目前，請確認沒有附上：

- 錄音、逐字稿或 Markdown 筆記。
- `state.db`、`state.db-wal`、`state.db-shm` 或 spool FLAC。
- `config.json`、`settings-history.jsonl` 或完整環境變數。
- 未去識別化日誌、Windows 使用者名稱、絕對路徑、裝置 endpoint ID 或 token。

一般功能錯誤請使用 bug report，並遵守 [CONTRIBUTING.md](CONTRIBUTING.md) 的資料最小化
規則。本專案目前不承諾固定回覆或修補時限。
