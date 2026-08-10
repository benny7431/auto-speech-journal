# 疑難排解

## 基本診斷

安裝版 CLI 位於：

```text
%LOCALAPPDATA%\Programs\AutoSpeechJournal\app\AutoSpeechJournal.CLI.exe
```

在 PowerShell 執行：

```powershell
$cli = Join-Path $env:LOCALAPPDATA "Programs\AutoSpeechJournal\app\AutoSpeechJournal.CLI.exe"
& $cli self-test --no-model-check --no-microphone-check
Get-Content "$env:LOCALAPPDATA\AutoSpeechJournal\logs\journal.log" -Tail 200
```

## Windows 顯示未知的發行者或 SmartScreen

`v0.3.1` 安裝器目前沒有 Authenticode 簽章，因此 Windows 可能顯示
**Unknown publisher／未知的發行者** 或 Microsoft Defender SmartScreen。

只從本專案的 GitHub Release 下載，並將檔案的 SHA-256 與 `SHA256SUMS.txt` 比對：

```powershell
Get-FileHash .\AutoSpeechJournal-Setup-0.3.1-x64.exe -Algorithm SHA256
```

不要停用 Windows Defender。來源或 hash 無法確認時，不要執行該檔案。

## 模型尚未就緒

Setup 不下載模型。首次設定會由 App 使用 `huggingface_hub`，從 manifest 固定的完整
Hugging Face commit 下載可直接執行的 ONNX／CTranslate2 檔案。下載使用 Hugging Face
cache 與 retry，不會安裝 Torch、Transformers，也不會在本機轉換模型。

網路中斷或下載失敗時：

1. 保留首次設定畫面，稍後重新按下載。
2. 或執行 `AutoSpeechJournal.CLI.exe download-models`。
3. 確認 `%LOCALAPPDATA%\AutoSpeechJournal\models` 有足夠空間。

失敗不會移除已安裝 App，也不會刪除設定、SQLite、spool 或日記。

## 麥克風沒有聲音

1. 開啟 Windows「隱私權與安全性 > 麥克風」，允許桌面應用程式使用麥克風。
2. 在首次設定選擇 Windows 預設裝置或指定裝置。
3. 執行 `AutoSpeechJournal.CLI.exe setup --test-microphone`。

首次設定按下「開始錄音」前，程式不會開啟麥克風。「稍後設定」也不會建立自啟項目。

## CUDA 或 NVIDIA 問題

正式 Setup 只保證 CPU-safe 啟動，不下載 GPU runtime。`install.ps1` 是進階
source/uv 安裝路徑，可選擇 CUDA；需要排除 GPU 問題時使用：

```powershell
.\install.ps1 -NoCuda
```

CPU fallback 不影響既有資料。

## 資料與解除安裝

主要本機狀態位於：

```text
%LOCALAPPDATA%\AutoSpeechJournal
```

其中包含設定、SQLite/WAL、spool、logs、cache 與模型。解除安裝只移除 App、捷徑與
本專案建立的自啟項目；上述 runtime 資料及外部日記資料夾會保留。
