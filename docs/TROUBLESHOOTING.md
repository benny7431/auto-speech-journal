# Troubleshooting

先從 App 的 **系統狀態 → 結束程式** 完整停止背景程序，再執行修復命令。公開回報前請
閱讀 [PRIVACY.md](../PRIVACY.md)；不要上傳錄音、逐字稿、資料庫、設定或完整日誌。

## Baseline checks

```powershell
$Cli = "$env:LOCALAPPDATA\Programs\AutoSpeechJournal\AutoSpeechJournal.CLI.exe"

& $Cli installer-probe --isolated
& $Cli startup status
Get-ScheduledTask -TaskName "Auto Speech Journal"
Get-Content "$env:LOCALAPPDATA\AutoSpeechJournal\logs\journal.log" -Tail 80
```

若從 source checkout 執行，先設定：

```powershell
$env:PYTHONPATH = (Join-Path $PWD "src")
uv run --no-sync python -m auto_speech_journal self-test --no-model-check --no-microphone-check
```

## WASAPI and microphone access

1. 到 **設定 → 隱私權與安全性 → 麥克風**，確認桌面應用程式可以使用麥克風。
2. 在 App 設定頁重新掃描裝置，選擇「跟隨 Windows 預設」或唯一的固定 WASAPI 端點。
3. 同名且無法安全區分的端點不能固定綁定；請改用 Windows 預設或停用重複裝置。
4. 特殊 WASAPI loopback capture 目前不在支援範圍。

重新選擇並測試 800 ms 輸入：

```powershell
& $Cli setup --test-microphone
```

固定裝置失效時，App 可能暫用 Windows 預設並顯示 fallback。原偏好不會被覆寫；裝置
恢復後，由使用者按 **切回偏好裝置**，避免錄音中途無預警切換。

## CUDA and CPU mode

簽章 Setup 會依固定 CUDA manifest 建議 GPU 模式；偵測或實際 CTranslate2 CUDA probe
失敗時保留 CPU fallback。CUDA runtime 與模型下載是兩個獨立流程。修復 GPU runtime：

```powershell
& $Cli repair gpu
```

開發／救援用 `install.ps1` 可用 `-NoCuda` 強制 CPU。

深度檢查 CUDA：

```powershell
& $Cli self-test `
  --deep-model-check --test-microphone
```

CPU 安裝需額外加入 `--allow-cpu-finalizer`。若 CUDA DLL、驅動或 VRAM 不足造成失敗，
先保留 `state.db*` 與 spool，再用 `-NoCuda` 重新安裝；不要手動刪除待處理 FLAC。

## Missing or damaged models

模型只會在安裝、首次設定補齊或明確修復時連網。重新下載固定 Hugging Face commit
revision 並驗證大小與 SHA-256：

```powershell
& $Cli repair models
```

下載依 `runtime-models-v1.json` 逐檔執行，保留 `.part` 續傳並支援 Range fallback、重試、
磁碟 preflight、SHA-256 與原子替換。中斷或模型下載失敗不會回滾已安裝程式；再次執行
同一命令即可繼續。來源只包含可直接執行的 ONNX／CTranslate2 檔案，不會安裝 Torch、
Transformers 或 Safetensors，也不會在本機轉換模型。

需要檢查完整檔案 digest 與實際推論時使用 `self-test --deep-model-check`。模型路徑位於
`%LOCALAPPDATA%\AutoSpeechJournal\models`；不要從 Issue 下載別人提供的未知權重取代。

## Scheduled task and startup

```powershell
Get-ScheduledTask -TaskName "Auto Speech Journal" | Format-List *
Get-ScheduledTaskInfo -TaskName "Auto Speech Journal"
Start-ScheduledTask -TaskName "Auto Speech Journal"
```

工作排程應指向 `%LOCALAPPDATA%\Programs\AutoSpeechJournal\AutoSpeechJournal.exe` 的穩定
launcher，而不是原始碼
資料夾。若安裝中斷，重新執行 `install.ps1`；安裝器會備份並回復既有 app、設定、
SQLite/WAL 與排程狀態。

## SQLite, WAL and spool recovery

SQLite 是權威狀態，Markdown 可重建。修復前：

1. 完整結束 App，確認沒有 `auto_speech_journal` Python 程序。
2. 一起備份 `state.db`、`state.db-wal`、`state.db-shm` 與 `spool`。
3. 重新啟動，讓內建 recovery 先執行；查看日誌中的 `Storage recovery`。
4. 不要用文字編輯器修改 SQLite，也不要只刪除 WAL/SHM 或仍被引用的 FLAC。

若 Markdown 缺漏但 SQLite 正常，重新啟動會重建 dirty hours。手動修改 Markdown 不會
回寫資料庫，下一次重建可能覆蓋該修改。

## Logs and safe issue reports

日誌位於 `%LOCALAPPDATA%\AutoSpeechJournal\logs\journal.log*`，每個最多 5 MiB、最多五個
備份。分享前只摘錄與失敗時間相鄰的最少行數，並移除使用者名稱、絕對路徑、裝置
endpoint ID、逐字內容及 token。安全問題依 [SECURITY.md](../SECURITY.md) 處理。

## Complete removal

`uninstall.ps1` 會保留使用者資料。要完整刪除，先結束 App、執行解除安裝，再人工刪除：

- `%LOCALAPPDATA%\AutoSpeechJournal`
- `config.json` 中指定的紀錄資料夾（預設 `%USERPROFILE%\Documents\語音紀錄`）

Windows 備份、檔案歷程或同步軟體的副本需在相應工具中另行清除。
