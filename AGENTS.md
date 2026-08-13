# Repository Guidelines

## Architecture

Application code lives in `src/auto_speech_journal/`, tests in `tests/`, and maintenance helpers in `tools/`. Use `tools/replay_fault_recovery.py` for crash-boundary replay. The Inno Setup installer owns the supported Windows installation, and first-run setup owns the login-startup task; `install.ps1` and `uninstall.ps1` are the separate advanced source/CUDA path and create no login schedule.

SQLite is authoritative; generated Markdown is rebuildable. Keep models, recordings, databases, configuration, and logs outside the repository and Git.

## Development

Use PowerShell, Python 3.11, and `uv`:

```powershell
uv sync --frozen --no-editable --extra dev
$env:PYTHONPATH = (Join-Path $PWD "src")
uv run --no-sync pytest
uv run --no-sync ruff check src tests tools
uv build
```

For broad changes, run coverage with `--cov=auto_speech_journal --cov-report=term-missing --cov-fail-under=75`. Smoke-test source checkouts with `uv run --no-sync python -m auto_speech_journal self-test --no-model-check`; use `./install.ps1` only for the advanced source/CUDA path, with `-NoCuda` for CPU.

Use four-space indentation, type annotations, a 100-character line limit, Ruff rules `E`, `F`, `I`, `UP`, `B`, and `SIM`, and Pytest names `test_<area>.py` / `test_<behavior>`. Add focused regressions for storage recovery, atomic exports, worker transitions, offline behavior, and installer rollback. Delay native or heavyweight imports where practical. Preserve the UTF-8 BOM and Windows PowerShell 5.1 compatibility in `.ps1` files.

## GitHub Workflow

`origin` is `https://github.com/benny7431/auto-speech-journal`; GitHub is the shared source of truth for completed work. Write all human-authored Git and GitHub collaboration text in Traditional Chinese, while keeping identifiers, paths, URLs, commands, and Conventional Commit prefixes unchanged. Keep commits focused with related tests; PRs must summarize behavior, validation, persistence or installation risks, linked issues, and UI screenshots when applicable.

Never add `Co-Authored-By` or any other authorship or attribution trailer unless the user names a specific co-author for that specific commit. Tooling and assistants are not co-authors; credit them in `README.md` instead.

Every major update—version milestone or substantial user-visible, architectural, persistence, installer, model-pipeline, or release-process change—MUST pass required validation, use a `codex/` branch and PR, wait for CI and CodeQL, and merge into `main`. For releases, also update the version and `CHANGELOG.md`, then create and push the tag specified by `docs/RELEASING.md`.

After a PR merges, preserve uncommitted work, fetch `origin`, switch to `main`, fast-forward with `git merge --ff-only origin/main`, and verify `main` and `origin/main` resolve to the same SHA.

## Session Notes

`工作筆記/` is untracked, ignored, Traditional Chinese only, and must never enter Git or a PR.

At every session end, rewrite `工作筆記/事務交接.md` with branch/base/HEAD/ahead state, session commits, actual validation and numeric results, deliberately deferred versus unreached work, unresolved user decisions with conflicting evidence, and defects found. Rewrite `工作筆記/交接提示詞.md` as a standalone next-session prompt naming the task, the handover file, questions to ask, and boundaries not to change, redesign, squash, or rewrite.

Keep `工作筆記/構想與待辦.md` as a concise event log with only 收件匣、尚未實現的功能、願景、工程債、已完成、已否決. Use one Traditional Chinese bullet or short paragraph per event; do not pre-design solutions, architecture, priorities, acceptance criteria, risks, or PR splits. When work starts, treat entries only as background and re-check the current goal, code, runtime, documentation, and external services. Merge duplicates, remove resolved or stale detail, move shipped items to 已完成 with their version, and move rejected items to 已否決 with a one-line reason.
