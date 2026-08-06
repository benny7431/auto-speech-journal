# 隱私說明

最後更新：2026-08-06

Auto Speech Journal 是本機優先的 Windows 錄音筆記工具。本文件說明程式在目前
`0.2.x` 版本如何使用麥克風、保存資料、連線及刪除資料。

## 何時使用麥克風

新安裝不會自行選定麥克風。首次啟動時，使用者必須明確選擇 Windows 預設裝置、
指定 WASAPI 裝置，或暫不設定。選定裝置並啟動錄音後，程式會持續讀取該輸入；暫停或
結束程式後停止讀取。若啟用登入時啟動，後續登入時程式可能自動開始使用先前選定的
麥克風。

程式沒有遙測、廣告 SDK、帳號系統或雲端逐字稿服務，也不會主動上傳錄音或筆記。

## 本機保存的資料

| 資料 | 預設位置 | 用途與保存方式 |
| --- | --- | --- |
| 設定 | `%LOCALAPPDATA%\AutoSpeechJournal\config.json` | 麥克風模式、輸出目錄與辨識設定 |
| 設定歷程 | `%LOCALAPPDATA%\AutoSpeechJournal\settings-history.jsonl` | 設定變更稽核紀錄 |
| SQLite | `%LOCALAPPDATA%\AutoSpeechJournal\state.db*` | 權威狀態、時間、預覽、定稿與人工修正文字 |
| 暫存音訊 | `%LOCALAPPDATA%\AutoSpeechJournal\spool\*.flac` | 在最終辨識與輸出完成前提供可復原來源 |
| 日誌 | `%LOCALAPPDATA%\AutoSpeechJournal\logs\journal.log*` | 錯誤與狀態診斷，可能含裝置名稱及本機路徑 |
| 模型 | `%LOCALAPPDATA%\AutoSpeechJournal\models` | 使用者明確安裝或修復時下載的固定版本模型 |
| 本機字體 | `%LOCALAPPDATA%\AutoSpeechJournal\fonts` | 使用者自行加入的選用字體 |
| Markdown | `%USERPROFILE%\Documents\語音紀錄` | 可閱讀、可由 SQLite 重建的每小時筆記 |

日誌每個檔案上限 5 MiB，保留目前檔案及最多五個輪替備份。日誌設計上不以逐字稿為
內容，但例外訊息仍可能暴露裝置名稱、使用者名稱或檔案路徑；分享前必須人工檢查並
去識別化。

## 音訊、SQLite 與 Markdown 的關係

語音片段會先成為 durable FLAC，再寫入 SQLite 並排入最終辨識。成功寫入最終文字、
原子更新對應 Markdown 且確認清理後，該 FLAC 才會刪除。若辨識、資料庫或輸出失敗，
FLAC 可能保留以便復原。SQLite 是權威資料；Markdown 是可重建輸出。

## 何時連網

一般 `run` 執行路徑不需要網路。下列明確操作會連線：

- `uv sync` 或安裝程序從 Python、PyTorch 或 NVIDIA 套件來源取得相依套件。
- `install.ps1` 未使用 `-SkipModelDownload`，或執行 `download-models` 時，從 GitHub
  Releases 與 Hugging Face 下載固定版本模型。
- 使用者自行開啟 README、Issue 或其他外部連結。

程式目前沒有遠端 API、雲端同步或背景更新檢查。

## 刪除資料

- UI 的「永久刪除時段」會刪除該時段的 SQLite 紀錄、重建或移除 Markdown，並清除仍
  存在的對應 FLAC。
- `uninstall.ps1` 只移除排程與安裝副本，刻意保留設定、資料庫、錄音、模型、日誌、
  字體與 Markdown，避免誤刪筆記。
- 若要完整清除，先結束程式並執行 `uninstall.ps1`，確認背景程序已停止後，再刪除
  `%LOCALAPPDATA%\AutoSpeechJournal` 及實際設定的紀錄資料夾。

Windows 備份、檔案歷程、第三方同步軟體或磁碟復原副本不受本程式控制。

## 問題回報

公開 Issue 不得附上錄音、逐字稿、資料庫、完整設定或未去識別化日誌。一般問題請依
[貢獻指南](CONTRIBUTING.md)整理最小化資訊；安全問題請先閱讀[安全政策](SECURITY.md)。
