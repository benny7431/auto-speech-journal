# Releasing

公開 `v0.2.0` Release 包含一個已簽章 Windows Setup、wheel、source distribution、
checksums、CycloneDX SBOM、測試摘要與 runtime model manifest。Setup 是一般使用者的主要入口；
`install.ps1` / `uninstall.ps1` 只保留給開發與救援。

## Version and immutability policy

- `pyproject.toml` 是唯一版本來源；tag 必須是對應的 `vMAJOR.MINOR.PATCH`。
- `CHANGELOG.md` 必須有同版本段落；`v0.*` 以 GitHub pre-release 發布。
- `packaging/manifests/runtime-models-v1.json` 隨 source 版本化；每個 Hugging Face 來源必須
  固定完整 40 位 commit revision。清單語意或檔案內容改變時建立下一版 manifest，不能
  讓既有版本改指不同位元組。
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
| Variable | `RUNTIME_MODELS_REFERENCE_TRANSCRIPT_APPROVED_SHA` |
| Variable | `WINDOWS_RELEASE_MATRIX_APPROVED_TAG` |
| Variable | `WINDOWS_10_CPU_E2E_APPROVED_SHA` |
| Variable | `WINDOWS_11_CPU_E2E_APPROVED_SHA` |
| Variable | `WINDOWS_11_NVIDIA_E2E_APPROVED_SHA` |

缺少任一值會硬失敗，不會改成上傳 unsigned Setup。SignPath Foundation 申請未核准時，先
停止發行並改設付費 OV Authenticode policy；不可繞過 verifier。

所有 `*_APPROVED_SHA` 都必須精確等於欲發布 tag 的 commit SHA，矩陣 tag 則必須精確
等於 `GITHUB_REF_NAME`。`packaging/models/reference-audio-gate.json` 必須維持 `ready`、通過
固定來源與 SHA-256 驗證，並實際完成轉錄；不得以 repository variable、刪除測試或略過
實際轉錄來繞過。

## Validate the runtime Hugging Face models

不建立或依賴 GitHub `models-v1` Release。Setup 與 `repair models` 直接依
`packaging/manifests/runtime-models-v1.json` 從 Hugging Face 下載：

1. Paraformer INT8 ONNX：`encoder.int8.onnx`、`decoder.int8.onnx`、`tokens.txt`。
2. Whisper large-v3-turbo CTranslate2 float16：`config.json`、`model.bin`、
   `preprocessor_config.json`、`tokenizer.json`、`vocabulary.json`。
3. Sherpa Silero VAD：`silero_vad.onnx`。

每個 entry 必須含 repository、完整 revision、遠端 file path、安裝路徑、精確大小、SHA-256、
授權與來源。驗證器必須拒絕浮動 `main`／`latest`、未宣告檔案、重複安裝路徑及不安全路徑。
下載 URL 只能由程式組成
`https://huggingface.co/<repository>/resolve/<40-hex-revision>/<file-path>`，manifest 不接受任意
下載網域或帶認證資訊的 URL。
安裝與修復只取得上述可直接執行的 ONNX／CTranslate2 檔案；不能安裝 Torch、Transformers
或 Safetensors，也不能在使用者電腦轉換模型。CUDA runtime 固定 NVIDIA PyPI wheels 的
下載與 probe 是獨立流程。

PR 與 tag workflow 仍須下載並驗證 manifest 宣告的實際位元組，執行 Preview、VAD 與 CPU
Whisper 載入，並以 [BUILDING.md](BUILDING.md) 所述的已授權參考音訊做轉錄 gate。這些安全
測試不因移除 GitHub model Release 而移除或降級。

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
2. 驗證 committed `runtime-models-v1.json` 的 Hugging Face 40 位 revision、固定檔案清單、
   size/SHA-256、授權來源、實際下載位元組與參考音訊推論結果。
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

模型下載失敗只會把安裝結果標示為「模型尚未就緒」，不回滾已驗證的應用程式；使用者可在
首次設定或 `repair models` 以 `.part` 續傳。這個降級不適用於正式發布驗證：SignPath 簽章、
Windows 10/11 CPU/NVIDIA 實機矩陣及 runtime 模型參考音訊 gate 仍是公開發行硬門檻。
