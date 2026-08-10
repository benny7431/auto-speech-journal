# 隱私說明

最後更新：2026-08-10

Auto Speech Journal 是本機優先的 Windows 應用程式。收音、辨識、校正學習、SQLite
保存與 Markdown 匯出都在使用者電腦執行；沒有遙測或雲端逐字稿服務。

## 同意與麥克風

首次啟動會說明本機儲存與模型下載，再讓使用者選擇日記資料夾、登入自啟及麥克風。
只有按下「開始錄音」才會開啟麥克風；選擇「稍後設定」會保持錄音與自啟關閉。

## 本機資料

設定、SQLite/WAL、durable audio spool、日誌、cache、模型與校正資料位於：

```text
%LOCALAPPDATA%\AutoSpeechJournal
```

Markdown 日記位於使用者選擇的資料夾。日誌可能包含裝置名稱與本機路徑；公開分享前
請去識別化，且不要上傳錄音、逐字稿、資料庫或完整設定。

## 連網時機

Setup 不下載模型或 GPU 元件。首次設定或使用者執行 `download-models` 時，App 透過
`huggingface_hub` 從固定完整 commit 下載 manifest 指定的檔案，並使用 Hugging Face
cache/retry。一般 HTTP 連線資訊（例如 IP、user agent、repository 與檔案路徑）會由服務
提供者接收。

進階 `install.ps1` source/CUDA 路徑可能連線至 Python 套件來源。更新檢查預設關閉；
使用者 opt-in 後每 24 小時最多查詢一次 GitHub Release metadata，不會上傳錄音或日記。

## 解除安裝與刪除

解除安裝只移除 App、捷徑及本專案擁有的自啟項目，保留
`%LOCALAPPDATA%\AutoSpeechJournal` 與所有外部日記資料夾，避免誤刪使用者資料。確定不再
需要時，請先備份再手動刪除保留資料。Windows 備份、檔案歷程或第三方同步副本不受
本程式控制。

## 未簽章安裝器

`v0.3.1` Setup 沒有 Authenticode 簽章，Windows 可能顯示未知的發行者或 Microsoft
Defender SmartScreen。請只從專案 GitHub Release 下載並核對 SHA-256；不要為了安裝而
停用 Windows Defender。
