# Releasing

`v0.3.2` 是第一個標示為 Stable／Latest 的正式版，並延續最小發布流程。主要資產為未簽章
Windows Setup、wheel、source distribution、`SHA256SUMS.txt` 與繁體中文 Release Notes。

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

安裝目錄、模型邊界與解除安裝保留範圍見 [架構文件](ARCHITECTURE.md)。

`v0.3.2` Setup 與內層 EXE 允許未簽章。Release Notes 必須以繁體中文清楚標示 Windows
可能顯示 **Unknown publisher／未知的發行者** 或 Microsoft Defender SmartScreen。請使用者
核對 `SHA256SUMS.txt`，不得要求停用 Windows Defender。

未來取得合適憑證後可再加入簽章，但目前不是發布條件。

## Tag workflow

確認上述門檻後，才從乾淨的 `main` 建立 tag：

```powershell
git tag -a v0.3.2 -m "發布 Auto Speech Journal v0.3.2"
git push origin v0.3.2
```

發布類型只依 `pyproject.toml` 的版本判定，tag 仍必須與該版本完全相同：

- 標準 `X.Y.Z` 版本與 `vX.Y.Z` tag 發布為 Stable，並明確標示為 Latest。
- 帶 `a`、`b`、`rc` 或 `dev` 後綴的 canonical PEP 440 版本發布為 Pre-release，且不標示
  為 Latest，例如 `0.4.0rc1`／`v0.4.0rc1`。
- 不使用會被建置工具正規化成不同檔名的 `0.4.0-rc.1` 等非 canonical 寫法。

Tag workflow 只需要：

1. 驗證版本與 changelog。
2. 等待 CI 與 CodeQL。
3. 建置並驗證 wheel、sdist 與 unsigned Setup。
4. 在 Windows runner 執行安裝、首次啟動與解除安裝 E2E。
5. 產生 `SHA256SUMS.txt` 與繁體中文 Release Notes：

   ```powershell
   Get-FileHash .\artifacts\windows\setup\*.exe -Algorithm SHA256
   Get-FileHash .\dist\* -Algorithm SHA256
   ```

6. 依版本建立 Stable／Latest 或 Pre-release，Release Notes 先列下載與變更，最後保留一行
   未簽章提示。

## Verify a downloaded Setup

```powershell
Get-Content .\SHA256SUMS.txt
Get-FileHash .\AutoSpeechJournal-Setup-0.3.2-x64.exe -Algorithm SHA256
```

SHA-256 必須完全相同。SmartScreen 提示不代表需要關閉 Defender；來源或 hash 無法確認時，
不要執行檔案。
