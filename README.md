<p align="center">
  <img src="src/auto_speech_journal/assets/brand/journal-ink-icon.png"
       width="88" alt="聲跡日記圖示">
</p>

<p align="center">
  <strong>繁體中文</strong> · <a href="README.en.md">English</a>
</p>

<h1 align="center">聲跡日記</h1>

<p align="center">
  把每天說過的話，整理成只留在自己電腦裡的日記。
</p>

<p align="center">
  <code>Windows 10/11</code> · <code>Python 3.11</code> ·
  <code>Local-first</code> · <code>繁體中文</code>
</p>

<p align="center">
  <a href="https://github.com/benny7431/auto-speech-journal/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/benny7431/auto-speech-journal/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/benny7431/auto-speech-journal/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/benny7431/auto-speech-journal/actions/workflows/codeql.yml/badge.svg"></a>
  <a href="https://github.com/benny7431/auto-speech-journal/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/benny7431/auto-speech-journal?include_prereleases"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green.svg"></a>
  <img alt="Python 3.11" src="https://img.shields.io/badge/python-3.11-blue.svg">
  <img alt="Windows 10/11" src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4.svg">
</p>

![聲跡日記合成操作示範：錄音、即時預覽、修正與 Markdown 同步](docs/images/auto-speech-journal-demo.gif)

<details>
<summary>查看靜態工作台畫面</summary>

![聲跡日記的今日聲音時間軸](docs/images/speech-journal-workspace.png)

</details>

**聲跡日記（Auto Speech Journal）** 是一套 Windows 本機常駐語音日誌。它可依你的選擇在登入後
啟動，持續監聽你選定的麥克風，先顯示低延遲逐字預覽，再以 Whisper 產生較準確的
最終文字，最後依台北時間整理成每小時一份 Markdown。

辨識、儲存與介面都在本機執行。網路只在安裝或補齊模型時使用；日常錄音不需要把音訊
或文字送到雲端。

> [!IMPORTANT]
> 目前是 `0.2.0` 預發行版，僅支援 Windows WASAPI、中文辨識與 `Asia/Taipei` 時區。
> 安裝不需要先接上麥克風；第一次啟動 App 時再明確選擇收音來源。

## 目錄

- [功能重點](#功能重點)
- [快速開始](#快速開始)
- [日常操作](#日常操作)
- [辨識與保存流程](#辨識與保存流程)
- [資料、隱私與檔案位置](#資料隱私與檔案位置)
- [診斷與修復](#診斷與修復)
- [解除安裝](#解除安裝)
- [文件、政策與參與](#文件政策與參與)
- [開發與驗證](#開發與驗證)
- [目前限制](#目前限制)
- [使用 Codex 與 GPT-5.6](#使用-codex-與-gpt-56)

## 功能重點

| 功能 | 說明 |
| --- | --- |
| 全程本機辨識 | Sherpa-ONNX 提供即時預覽，Faster-Whisper 產生最終文字 |
| 雙層介面 | 最上層精簡浮窗顯示音量、語音活動與預覽；展開後查看今日聲音時間軸 |
| 當機可恢復 | 音訊先安全寫入 spool；完成最終轉錄與 Markdown 原子寫入後才刪除 |
| 可管理校正字典 | 修正後的片段會鎖定；可查看已學詞與次數、刪除或清空詞語，並停用自動學習 |
| 容易帶走 | 預設輸出一般 Markdown，不綁定專用閱讀器 |
| 麥克風可切換 | 可固定使用某個 WASAPI 端點，或跟隨 Windows 預設，切換不需重開 App |
| 選用登入自啟 | 首次設定明確同意後，才建立指向穩定 launcher 的個人工作排程 |
| 離線視覺 | 月份、狀態場景與粒子效果隨程式安裝，執行時不呼叫生成式影像服務 |

## 快速開始

### 1. 系統需求

- Windows 10/11 x64
- 錄音時需要可用的 WASAPI 麥克風，以及 Windows 麥克風權限
- 首次下載模型需要網路；Setup 會在下載前顯示精確空間需求
- NVIDIA GPU 為選用；偵測或 CUDA probe 失敗時會自動使用 CPU

### 2. 下載並安裝

從 [GitHub Releases](https://github.com/benny7431/auto-speech-journal/releases) 下載已簽章的
`AutoSpeechJournal-Setup-0.2.0-x64.exe`，雙擊後依畫面安裝。不需要 Python、`uv`、Git、
PowerShell 或管理員權限。

Setup 預設下載固定版本模型，顯示進度、ETA，並允許取消後續傳。GPU 選項
預設自動偵測，也可在安裝畫面取消或強制嘗試。安裝完成後穩定 launcher 位於
`%LOCALAPPDATA%\Programs\AutoSpeechJournal\AutoSpeechJournal.exe`，版本內容位於
`versions\0.2.0`。日記預設輸出到：

```text
%USERPROFILE%\Documents\語音紀錄\YYYY-MM-DD\YYYY-MM-DD_HH.md
```

### 3. 完成首次設定

新安裝預設不錄音，也不會暗中綁定某台電腦的裝置。第一次啟動時請主動選擇：

- **跟隨 Windows 預設**：預設輸入端點改變時，App 會在片段邊界安全切換。
- **固定裝置**：持續偏好指定的 WASAPI 麥克風。
- **稍後設定**：先進入主介面但不錄音；提示會保留，之後可從設定頁選擇。

還需選擇日記資料夾、是否登入自啟（預設關閉）與是否開啟更新提示
（預設關閉）。只有最後按下 **開始錄音** 才會儲存同意並啟動 worker。設定會寫入
`%LOCALAPPDATA%\AutoSpeechJournal\config.json`，重新安裝時會沿用。

<details>
<summary><strong>安裝器實際會做什麼？</strong></summary>

1. 把內含 CPU runtime 的新版本放入獨立 `versions\<version>`。
2. 執行實際 QML／Qt 隔離 readiness probe；失敗不會切換版本。
3. 原子更新 `current.json`，穩定 launcher 才開始指向新版本。
4. 下載版本化模型，並以檔案大小、SHA-256 與解壓後清單驗證。
5. 選用 GPU 時只從固定 URL 下載三個 NVIDIA wheel，只解出 runtime DLL 並做 CUDA probe。
6. 模型或 GPU 失敗不會卸載可用的 CPU 程式；可從開始選單續傳／修復。
7. 舊 `AutoSpeechJournal\app` 會重用原有設定與資料；只在驗證舊 task action 後才遷移自啟。

</details>

## 日常操作

### 精簡浮窗

- 顯示目前狀態、麥克風音量、語音活動、待處理數量與即時預覽。
- 按 **暫停錄音** 只會停止接收新音訊；已安全入列的片段仍會完成轉錄。
- 按 **今日紀錄** 展開完整工作台。
- 精簡浮窗右上角的 **X** 只會最小化，不會停止錄音。

### 今日聲音時間軸

- 依小時顯示今天的所有耐久片段；尚未保存的即時預覽固定顯示在頂部。
- 按 **修正** 可修改最近的轉錄。人工修正優先於之後抵達的模型結果。
- **設定** 可選擇麥克風、重新掃描與測試所選裝置，也可調整日記字體、14–26 px
  介面字級、紀錄路徑與辨識參數。
- **字典** 可查看已學詞與累計次數、刪除單一詞、清空全部詞語，或停用後續自動學習；這些操作不會解除人工修正鎖定。
- **時段管理** 可永久刪除某個小時的資料庫紀錄、Markdown 與尚存暫存音訊。
- 工作台右上角的 **X** 只會收回精簡浮窗。
- 要真正停止錄音與程式，請使用 **系統狀態 → 結束程式**。

設定頁顯示最近五筆實際變更，完整歷程保存在
`%LOCALAPPDATA%\AutoSpeechJournal\settings-history.jsonl`。單純載入設定、資料遷移
或按下沒有變更的儲存，不會產生假紀錄。

固定裝置暫時失效時，App 會保留原偏好並嘗試使用當下的 Windows 預設。介面會分別顯示
「偏好裝置」與「目前收音裝置」及 fallback 原因；偏好裝置恢復後只會提示，由你按
**切回偏好裝置**，不會在錄音中突然自動切回。

## 辨識與保存流程

```mermaid
flowchart LR
    A["WASAPI 麥克風"] --> B["VAD 與串流預覽"]
    B --> C["FLAC spool"]
    B --> D[("SQLite 狀態")]
    C --> E["Faster-Whisper 最終辨識"]
    E --> D
    D --> F["原子重建每小時 Markdown"]
    F --> G["刪除已完成的暫存音訊"]
```

- 串流預覽保留 300 ms 起音，第一個結果立即顯示，後續最多每 350 ms 更新。
- 最終辨識超過 10 秒時，程式會先發布預覽文字；最終結果回來後再原位取代。
- 音訊片段會先以 FLAC 安全寫入 spool。只有 SQLite 狀態與 Markdown 都完成後，該片段
  才會刪除。
- 當機、磁碟鎖定或辨識暫時失敗時，尚未完成的片段會保留，重新啟動後繼續處理。
- 若結束程式時仍有音訊等候耐久寫入，App 會保持開啟並重試，不會為了逾時而強制終止
  recorder；寫入恢復後再按一次 **結束程式** 即可。
- SQLite 是權威狀態；Markdown 是可重建輸出。請不要把手動修改 Markdown 當成回寫
  方式，下一次重建可能覆蓋那些修改。

## 資料、隱私與檔案位置

日常執行不會上傳音訊或逐字稿。固定版本模型只在安裝或手動補齊時下載；模型就緒後，
錄音、VAD、預覽、最終辨識、正體轉換與匯出都可離線完成。

| 內容 | 預設位置 |
| --- | --- |
| 穩定 launcher | `%LOCALAPPDATA%\Programs\AutoSpeechJournal\AutoSpeechJournal.exe` |
| 版本副本 | `%LOCALAPPDATA%\Programs\AutoSpeechJournal\versions\<version>` |
| 設定 | `%LOCALAPPDATA%\AutoSpeechJournal\config.json` |
| SQLite 狀態 | `%LOCALAPPDATA%\AutoSpeechJournal\state.db` |
| 辨識模型 | `%LOCALAPPDATA%\AutoSpeechJournal\models` |
| 待處理音訊 | `%LOCALAPPDATA%\AutoSpeechJournal\spool` |
| 執行日誌 | `%LOCALAPPDATA%\AutoSpeechJournal\logs\journal.log` |
| 設定歷程 | `%LOCALAPPDATA%\AutoSpeechJournal\settings-history.jsonl` |
| 選用本機字體 | `%LOCALAPPDATA%\AutoSpeechJournal\fonts` |
| Markdown 日記 | `%USERPROFILE%\Documents\語音紀錄` |

本機字體資料夾可放入 TTF/OTF，再從設定頁重新掃描。字體不是 wheel 或安裝包的一部分；
沒有額外字體時，介面會使用可用的系統字體。

## 診斷與修復

先從 **系統狀態 → 結束程式** 停止應用程式，再於 PowerShell 執行需要的命令。

```powershell
$Cli = "$env:LOCALAPPDATA\Programs\AutoSpeechJournal\AutoSpeechJournal.CLI.exe"
& $Cli repair models
& $Cli repair gpu
& $Cli repair runtime
& $Cli installer-probe --isolated
& $Cli startup status
```

### 常見情況

**App 啟動後沒有錄音**

新安裝或曾選擇「稍後設定」時，請在首次畫面或設定頁選擇固定裝置或
「跟隨 Windows 預設」。安裝器本身不會要求麥克風，也不會替你開始錄音。

**安裝時取消下載後顯示缺少模型**

連上網路後從開始選單選擇 **Repair models**。`.part` 會續傳，既有日記、設定與
待處理片段會保留。

**程式顯示降級狀態**

查看 UTF-8 日誌 `%LOCALAPPDATA%\AutoSpeechJournal\logs\journal.log`。已完成收錄但尚未
轉錄的音訊仍留在 spool，修復問題並重新啟動後會繼續處理。

**按 X 後程式仍在錄音**

這是預期行為。請用 **系統狀態 → 結束程式** 完整停止程式。

## 解除安裝

先從應用程式內完整結束，再從 **Windows 設定 → 應用程式** 移除 Auto Speech Journal。
預設只移除程式、捷徑與本專案擁有的自啟 task，並保留：

- Markdown 日記
- `config.json` 與 `settings-history.jsonl`
- SQLite/WAL
- 模型與 spool
- 日誌與本機字體

因此之後重新安裝仍可沿用設定，並恢復尚未完成的片段。
解除安裝畫面另有預設未勾選的「移除模型/cache」與需二次確認的「移除本機狀態」；
外部 Markdown 日記資料夾永遠不會自動刪除。

## 文件、政策與參與

| 入口 | 內容 |
| --- | --- |
| [架構](docs/ARCHITECTURE.md) | 收音、佇列、復原、匯出與刪除邊界 |
| [疑難排解](docs/TROUBLESHOOTING.md) | WASAPI、CUDA/CPU、模型、工作排程、SQLite/WAL 與 spool |
| [建置](docs/BUILDING.md) | 開發環境、品質門檻、wheel 與合成 Demo |
| [發布](docs/RELEASING.md) | tag、pre-release、checksum 與發布後驗證 |
| [隱私政策](PRIVACY.md) | 收音條件、資料位置、連網時機、保存與刪除方式 |
| [第三方聲明](THIRD_PARTY_NOTICES.md) | runtime、CUDA、建模套件與下載模型的授權來源 |
| [安全政策](SECURITY.md) | 支援版本、安全聯絡方式與禁止公開附加的敏感資料 |
| [貢獻指南](CONTRIBUTING.md) | Issue、PR、診斷資料去識別化與本機驗證流程 |
| [版本歷程](CHANGELOG.md) | Keep a Changelog 格式的版本變更 |

## 開發與驗證

在 PowerShell、Python 3.11 與 `uv` 環境下執行：

```powershell
uv sync --frozen --no-editable --extra dev
$env:PYTHONPATH = (Join-Path $PWD "src")

uv run --no-sync pytest --cov=auto_speech_journal --cov-report=term-missing `
  --cov-report=xml
uv run --no-sync ruff check src tests tools
uv run --no-sync python -m auto_speech_journal self-test `
  --no-model-check --no-microphone-check
uv run --no-sync python tools/validate_scene_assets.py --strict
uv build
uv run --no-sync python tools/verify_wheel_contents.py
uv run --no-sync pre-commit run --all-files
uv run --no-sync python tools/render_readme_demo.py
```

完整說明請見 [建置文件](docs/BUILDING.md)。`tools/replay_fault_recovery.py` 可重播當機
邊界；場景、Demo 與打包 QA 工具集中在 `tools/`。

<details>
<summary><strong>專案結構</strong></summary>

```text
src/auto_speech_journal/
├── cli.py, __main__.py        # CLI 入口
├── ui.py, ui_models.py        # PySide6 / QML 介面橋接
├── controller.py, workers.py  # 狀態協調與背景工作
├── audio.py                   # WASAPI 收音與 FLAC spool
├── preview_engine.py          # Sherpa-ONNX 串流預覽
├── finalizer_engine.py        # Faster-Whisper 最終辨識
├── storage.py, exporter.py    # SQLite 與 Markdown 匯出
└── qml/, assets/              # 介面與離線視覺資產

tests/                         # Pytest 回歸測試
packaging/                     # PyInstaller、launcher、Inno、model/CUDA manifests
tools/                         # 復原、資產、效能與打包 QA
install.ps1, uninstall.ps1     # 僅供開發／救援的舊 PowerShell 流程
```

</details>

## 目前限制

- 僅支援 Windows WASAPI、Python 3.11、中文辨識與台北時區。
- 麥克風清單包含 WASAPI 虛擬輸入，但不支援特殊 loopback capture；無法安全區分的
  同名端點不能固定綁定，請改用 Windows 預設或停用重複端點。
- 公開 Setup 依賴 SignPath Foundation 審核與簽章；沒有有效簽章時 release workflow 會直接失敗。
- 專案原始碼與專案自有發行資產採 MIT License；第三方內容仍受各自條款約束。
- 個人本機字體與其聲明不屬於發行內容。

## 授權

本專案原始碼與專案自有發行資產採 [MIT License](LICENSE)。第三方套件、模型、字體與其他
外部內容仍受各自的授權條款約束；完整清單請見
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 使用 Codex 與 GPT-5.6

本專案由開發者在 OpenAI Codex 中與 GPT-5.6 協作迭代。開發者負責產品方向、功能
取捨、隱私與效能決策，以及 Windows 實機驗證；所有生成的修改都經過人工檢視與測試。

Codex 用於直接閱讀與修改專案中的 Python、QML 與 PowerShell 檔案，並執行 Pytest、
Ruff、建置及本機自我檢查。這讓每次修改都能根據實際程式碼、命令輸出與執行結果繼續
迭代，而不是只產生未驗證的程式片段。

GPT-5.6 用於將產品需求拆成可執行步驟、追蹤錄音到最終文字的資料流程、分析介面與
持久化問題，以及針對失敗案例設計修補與回歸測試。關鍵決策包括本機辨識、音訊先落盤、
SQLite 作為權威狀態，以及可重建的 Markdown 輸出。
