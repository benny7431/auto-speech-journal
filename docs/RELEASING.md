# Releasing

`v0.3.0` 延續最小發布流程。主要資產為未簽章 Windows Setup、wheel、source distribution、
`SHA256SUMS.txt` 與繁體中文 Release Notes。

## Release requirements

1. `pyproject.toml` 是唯一版本來源，`CHANGELOG.md` 有相同版本段落。
2. 發布 commit 已合併到 `main`。
3. CI、CodeQL 與 Windows 安裝 E2E 全部通過。
4. Wheel、sdist 與 Setup 都由同一 commit 建置。
5. 每個發布檔案都有 SHA-256；既有版本資產不覆寫，修正使用新的 patch version。

沒有獨立 models workflow、GitHub model Release、自訂 SBOM/runtime inventory、SignPath 或
Authenticode gate。模型 manifest 隨 Python package 版本化，相關 schema、hash 與推論測試
併入一般 CI。

## Windows Setup policy

Setup 直接安裝到：

```text
%LOCALAPPDATA%\Programs\AutoSpeechJournal\app
```

沒有 launcher、`current.json`、多版本目錄或安裝器內建 rollback framework。Setup 不下載
Hugging Face 模型或 NVIDIA CUDA 元件。首次啟動後，App 才透過 `huggingface_hub` 從固定
commit 取得模型。

`v0.3.0` Setup 與內層 EXE 允許未簽章。Release Notes 必須以繁體中文清楚標示 Windows
可能顯示 **Unknown publisher／未知的發行者** 或 Microsoft Defender SmartScreen。請使用者
核對 `SHA256SUMS.txt`，不得要求停用 Windows Defender。

未來取得合適憑證後可再加入簽章，但目前不是發布條件。

## Tag workflow

確認上述門檻後，才從乾淨的 `main` 建立 tag：

```powershell
git tag -a v0.3.0 -m "發布 Auto Speech Journal v0.3.0"
git push origin v0.3.0
```

Tag workflow 只需要：

1. 驗證版本與 changelog。
2. 等待 CI 與 CodeQL。
3. 建置並驗證 wheel、sdist 與 unsigned Setup。
4. 在 Windows runner 執行安裝、首次啟動與解除安裝 E2E。
5. 產生 `SHA256SUMS.txt` 與繁體中文 Release Notes。
6. 以單一發布步驟建立 pre-release，Release Notes 必須包含未簽章提示。

## Verify a downloaded Setup

```powershell
Get-Content .\SHA256SUMS.txt
Get-FileHash .\AutoSpeechJournal-Setup-0.3.0-x64.exe -Algorithm SHA256
```

SHA-256 必須完全相同。SmartScreen 提示不代表需要關閉 Defender；來源或 hash 無法確認時，
不要執行檔案。

解除安裝只移除 `%LOCALAPPDATA%\Programs\AutoSpeechJournal\app`、捷徑與程式擁有的
啟動項目。它必須保留 `%LOCALAPPDATA%\AutoSpeechJournal` 以及所有外部日記資料夾。
