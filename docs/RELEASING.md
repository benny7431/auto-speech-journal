# Releasing

目前只發布 source distribution 與 wheel；不發布 PyPI package、EXE/MSI 或簽章安裝器。

## Version policy

- `pyproject.toml` 是 package version 的來源。
- Git tag 使用 `vMAJOR.MINOR.PATCH`；tag 必須與 package version 一致。
- `v0.*` 或含 `-rc`、`-beta` 等後綴的 tag 會建立 GitHub pre-release。
- `CHANGELOG.md` 必須有對應的 `## [VERSION]` 區段。

## Prepare a release

在 `codex/` feature branch：

1. 更新版本與 `CHANGELOG.md` 日期、內容及 compare links。
2. 若 dependency 或模型改變，同步更新 `uv.lock` 與 `THIRD_PARTY_NOTICES.md`。
3. 執行 [BUILDING.md](BUILDING.md) 的全部門檻。
4. 確認 `git status --short` 乾淨，推送並開 PR。
5. 等待 Windows CI 與 CodeQL 全綠，再用 merge commit 合併。

## Tag and publish

```powershell
git switch main
git pull --ff-only origin main
git tag -a v0.1.0 -m "Auto Speech Journal v0.1.0"
git push origin v0.1.0
```

`.github/workflows/release.yml` 會在乾淨的 Windows runner：

1. 驗證 tag 與 `pyproject.toml`。
2. 重跑 Ruff、Pytest/coverage、隔離 self-test、192 assets 與 wheel verifier。
3. 建立 wheel 與 source distribution。
4. 從 CHANGELOG 抽出 release notes。
5. 建立 `SHA256SUMS.txt` 與含 run URL/commit/gates 的 `TEST-SUMMARY.md`。
6. 建立或修復同名 GitHub release，重跑時以 `--clobber` 更新 assets。

## Verify the published release

下載 release 的四個資產到空目錄：

```powershell
gh release download v0.1.0 --repo benny7431/auto-speech-journal
Get-Content .\SHA256SUMS.txt
Get-ChildItem auto_speech_journal-0.1.0* -File | ForEach-Object {
  Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
}
```

確認 wheel、sdist 的雜湊與 `SHA256SUMS.txt` 一致，`TEST-SUMMARY.md` 指向正確 workflow，
release 被標示為 pre-release，且 source tag 指向已通過檢查的 merge commit。

## Failed or partial release

不要移動或強制重寫已公開 tag。若 workflow 在建立 release 前失敗，修正後以新 commit
與新版本 tag 發布；若只有 asset upload 中斷，在相同 tag 上重新執行 workflow，它會修復
同名 release。若內容本身錯誤，保留歷史並以 patch version 發布修正。
