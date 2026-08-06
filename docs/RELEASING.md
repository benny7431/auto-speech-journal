# Releasing

公開 `v0.2.0` Release 包含一個已簽章 Windows Setup、wheel、source distribution、
checksums、CycloneDX SBOM、測試摘要與 model manifest。Setup 是一般使用者的主要入口；
`install.ps1` / `uninstall.ps1` 只保留給開發與救援。

## Version and immutability policy

- `pyproject.toml` 是唯一版本來源；tag 必須是對應的 `vMAJOR.MINOR.PATCH`。
- `CHANGELOG.md` 必須有同版本段落；`v0.*` 以 GitHub pre-release 發布。
- `models-v1` 是獨立、不可變 Release。內容錯誤時建立 `models-v2`，不得覆寫資產。
- 已建立的版本 Release 或已簽資產不得 `--clobber`；修正後發新的 patch tag。
- 沒有有效且有 timestamp 的 SignPath Foundation 簽章，workflow 必須在建立 Release 前失敗。

## One-time GitHub and SignPath configuration

安裝 SignPath GitHub App，並建立能簽署 ZIP 內 inner EXE 與單一 Setup EXE 的兩個 artifact
configuration。Repository 設定必須提供：

| Kind | Name |
| --- | --- |
| Secret | `SIGNPATH_API_TOKEN` |
| Variable | `SIGNPATH_ORGANIZATION_ID` |
| Variable | `SIGNPATH_PROJECT_SLUG` |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` |
| Variable | `SIGNPATH_PROGRAM_ARTIFACT_CONFIGURATION` |
| Variable | `SIGNPATH_SETUP_ARTIFACT_CONFIGURATION` |
| Variable | `SIGNPATH_EXPECTED_PUBLISHER` |
| Variable | `MODELS_V1_REFERENCE_TRANSCRIPT_APPROVED_SHA` |
| Variable | `MODELS_V1_LICENSE_REVIEW_APPROVED_SHA` |
| Variable | `WINDOWS_RELEASE_MATRIX_APPROVED_TAG` |
| Variable | `WINDOWS_10_CPU_E2E_APPROVED_SHA` |
| Variable | `WINDOWS_11_CPU_E2E_APPROVED_SHA` |
| Variable | `WINDOWS_11_NVIDIA_E2E_APPROVED_SHA` |

缺少任一值會硬失敗，不會改成上傳 unsigned Setup。SignPath Foundation 申請未核准時，先
停止發行並改設付費 OV Authenticode policy；不可繞過 verifier。

所有 `*_APPROVED_SHA` 都必須精確等於欲發布 tag 的 commit SHA，矩陣 tag 則必須精確
等於 `GITHUB_REF_NAME`。`packaging/models/reference-audio-gate.json` 仍為 `blocked` 時，
即使手動設定變數也無法建立 `models-v1`，避免以變數繞過實際轉錄驗證。

## Build immutable models-v1

手動執行 `.github/workflows/models.yml`。Workflow 會：

1. 若 `models-v1` 已存在立刻失敗。
2. 下載固定 revision、驗證來源 hash，轉換 Whisper 為 CTranslate2 float16。
3. Paraformer 只保留 `encoder.int8.onnx`、`decoder.int8.onnx`、`tokens.txt`。
4. 建立 deterministic、每檔小於 2 GiB 的 ZIP 與逐檔 hash/size manifest。
5. 附上 license、來源 revision、transform provenance、checksums 與 GitHub attestation。
6. 深度載入模型；正式 sign-off 另依 [BUILDING.md](BUILDING.md) 完成已授權參考音訊 gate。

Workflow 只允許從已 review 的 `main` 執行。產生後以 `gh attestation verify` 驗證
`models-v1.json` 與 `models-v1.sha256` 的 signer workflow，再把 manifest 的 64 位 SHA-256
寫入 `packaging/manifests/models-v1.sha256` 並經 PR 合併。公開 tag 必須同時符合 attestation、
source pin 與 manifest 語意驗證；repository 內 placeholder 永遠不可用於公開發行。

## Prepare v0.2.0

在 `codex/` branch：

1. 更新 `pyproject.toml`、`uv.lock`、`CHANGELOG.md`、notices 與文件。
2. 執行 [BUILDING.md](BUILDING.md) 全部品質門檻及三台實機矩陣。
3. 推送 PR，等待 CI、Windows package、CodeQL 全綠並 merge。
4. 從乾淨且同步的 `main` 建立 tag：

```powershell
git tag -a v0.2.0 -m "Auto Speech Journal v0.2.0"
git push origin v0.2.0
```

## Signed tag workflow

`.github/workflows/release.yml` 的順序固定為：

1. 驗證 tag 位於 `main`、版本、changelog，並等待該 commit 的 CI 與 CodeQL 成功。
2. 驗證正式 `models-v1` manifest 的 GitHub attestation、source pin 與固定資產清單。
3. PyInstaller 建 inner GUI/CLI、stable launchers、licenses/notices 與 locked runtime SBOM。
4. 上傳 unsigned program workflow artifact，向 SignPath 提交第一次 signing request。
5. 驗證四個 inner EXE 的 publisher、timestamp、版本、整棵檔案樹不變與 frozen QML probe。
6. 以簽章後 inner files 建 Inno Setup；上傳後提交第二次 SignPath request。
7. 驗證 Setup Authenticode、publisher、timestamp，實際安裝後逐檔比對內嵌 signed tree。
8. 產生 SHA-256 與 verification receipt，並為 Setup、wheel、sdist、SBOM 建 provenance。
9. 一次建立 draft Release 並附上所有資產，最後才切換成 pre-release。

SignPath action 使用官方 `submit-signing-request@v2`，且必須引用前一步
`actions/upload-artifact` 的 `artifact-id`。兩次 request 不可合併或顛倒。

## Verify the published release

```powershell
gh release download v0.2.0 --repo benny7431/auto-speech-journal
Get-Content .\SHA256SUMS.txt
gh attestation verify .\AutoSpeechJournal-Setup-0.2.0-x64.exe `
  --repo benny7431/auto-speech-journal
Get-AuthenticodeSignature .\AutoSpeechJournal-Setup-0.2.0-x64.exe | Format-List
```

確認 checksum、attestation、Publisher、timestamp、`TEST-SUMMARY.md` workflow/commit，並在
乾淨 Windows 10/11 非管理員帳號雙擊完成安裝→模型→首次設定→錄音→升級→解除安裝。

## Failure handling

建立 draft 前的任何錯誤都不會公開資產。同 tag workflow 不做修復性 clobber；簽章、模型、
測試或 metadata 有錯時，保留歷史、修正 source、提高 patch version、重新跑完整流程。
