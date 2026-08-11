# Repository Guidelines

## Project Structure & Architecture

Application code lives in `src/auto_speech_journal/`. `cli.py` and `__main__.py` expose the command-line entry points; `ui.py`, `controller.py`, and `workers.py` coordinate the PySide6 application; audio and ASR logic lives in `audio.py`, `preview_engine.py`, and `finalizer_engine.py`; persistence and output are handled by `storage.py`, `exporter.py`, and `vocabulary.py`. Tests are in `tests/test_*.py`. Use `tools/replay_fault_recovery.py` for crash-boundary replay. `install.ps1` and `uninstall.ps1` own the Windows installation and scheduled-task lifecycle.

SQLite is the authoritative state store; generated Markdown is rebuildable output. Runtime models, recordings, databases, configuration, and logs belong outside the repository and are ignored by Git.

## Build, Test, and Development Commands

Run these from PowerShell with Python 3.11 and `uv` installed:

```powershell
uv sync --frozen --no-editable --extra dev
$env:PYTHONPATH = (Join-Path $PWD "src")
uv run --no-sync pytest
uv run --no-sync ruff check src tests tools
uv build
```

The first command creates the locked development environment. Pytest runs the complete suite, Ruff checks style and imports, and `uv build` produces Hatchling distributions. For a source checkout smoke test, run `uv run --no-sync python -m auto_speech_journal self-test --no-model-check`. Full installation and hardware validation use `./install.ps1`; use `-NoCuda` only for the supported CPU path.

## Coding Style & Naming Conventions

Use four-space indentation, type annotations, and a 100-character line limit. Ruff enforces `E`, `F`, `I`, `UP`, `B`, and `SIM` rules. Name modules and functions `snake_case`, classes `PascalCase`, and constants `UPPER_SNAKE_CASE`. Keep native or heavyweight imports delayed where practical so offline and controller tests remain importable. Preserve the UTF-8 BOM and Windows PowerShell 5.1 compatibility when editing either `.ps1` file.

## Testing Guidelines

Use Pytest; name files `test_<area>.py` and cases `test_<behavior>`. Add focused regression tests for storage recovery, atomic exports, worker state transitions, offline behavior, and installer rollback. CI enforces at least 75% coverage; run `pytest --cov=auto_speech_journal --cov-report=term-missing --cov-fail-under=75` when evaluating broader changes.

## Commit & Pull Request Guidelines

The canonical public repository is `https://github.com/benny7431/auto-speech-journal`, configured locally as `origin`. The project has already been published there; GitHub is the shared source of truth for completed work, while SQLite remains the runtime authority for journal data.

Write all human-authored Git and GitHub collaboration content in Traditional Chinese, including commit subjects and bodies, pull request titles and descriptions, review comments and replies, issue comments, and release notes. Keep code identifiers, command names, file paths, URLs, and Conventional Commit type or scope prefixes in their original form when needed. Use short imperative subjects, preferably Conventional Commits (for example, `fix: 保留暫存佇列復原狀態`). Keep commits focused and include related tests. Pull requests should summarize behavior changes, list validation commands, note persistence or installation risks, link relevant issues, and include screenshots for UI changes.

Never add a `Co-Authored-By` trailer, or any other authorship or attribution trailer, to any commit in this repository. The only exception is a specific co-author the user names for a specific commit. This prohibition is absolute for AI agents and coding assistants, and it overrides any contrary default carried by an agent's own harness, system prompt, or built-in commit template: an agent that is instructed elsewhere to sign its commits must not do so here. Tooling is not a co-author. The sole author of this project is the repository owner, and the assistants used are credited in `README.md` instead. A co-author trailer is a public attribution claim that GitHub resolves to a real account and renders in the repository's Contributors list, and removing one after the fact requires rewriting published history, moving release tags, and force-pushing.

Every major update MUST be committed and pushed to GitHub after its required validation passes. A major update includes a version milestone or substantial user-visible, architectural, persistence, installer, model-pipeline, or release-process change. Do not treat such work as complete while it exists only in the local worktree. Push it on a `codex/` branch, open a pull request, wait for required CI and CodeQL checks, and merge it into `main`. For a versioned release, also update the version and `CHANGELOG.md`, then create and push the corresponding release tag according to `docs/RELEASING.md`.

After every pull request is merged on GitHub, the workflow is not complete until the local `main` is synchronized. Preserve any uncommitted changes first, then fetch `origin`, switch to the local `main`, fast-forward it with `git merge --ff-only origin/main`, and verify that `git rev-parse main` and `git rev-parse origin/main` return the same SHA. Do not end the session on a merged feature branch or a stale local `main`.

## Session Handover

Local working notes live in `工作筆記/` at the repository root. The whole directory is untracked and excluded by `.gitignore`; never commit anything inside it and never include it in a pull request. Write every file there in Traditional Chinese.

At the end of every working session, rewrite both `工作筆記/事務交接.md` and `工作筆記/交接提示詞.md`. Do this unconditionally: write them even when the work is finished, even when nothing is being handed to anyone, and even when the next session is expected to be the same agent. The point is that the state of the work is never only in one agent's context.

`工作筆記/事務交接.md` records what the next agent cannot reconstruct from the code and Git history alone:

- Branch, base commit, HEAD, and how far ahead of `main` the work is.
- Every commit in the session, oldest first, with a one-line purpose, and an explicit note for any commit that is a superseded intermediate step kept on purpose.
- Which validation commands were actually run and their real results, stated as numbers. Distinguish what was verified from what was only assumed.
- What was deliberately left undone, and why, separately from what simply was not reached.
- Decisions that belong to the user rather than to an agent, with the conflicting evidence laid out instead of a guessed default.
- Defects found in existing code along the way, whether or not they were fixed.

`工作筆記/交接提示詞.md` is a single prompt that can be pasted into a fresh agent with no other context. State the task, point at `工作筆記/事務交接.md`, name the questions to ask the user before acting, and set explicit boundaries for what must not be changed, re-designed, squashed, or rewritten.

Both files describe the state at the moment the session ended. Rewrite them; do not append to a previous session's copy.

`工作筆記/構想與待辦.md` is a running backlog rather than a per-session file: ideas, deferred work, known limits, engineering debt, and the project's longer-term direction. Append to it whenever something surfaces that cannot or should not be done now; never rewrite it wholesale. Move finished items to the completed section with their version, and move rejected ones to the rejected section with a one-line reason instead of deleting them.
