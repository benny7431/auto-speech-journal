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

Use short imperative subjects, preferably Conventional Commits (for example, `fix: preserve spool recovery state`). Keep commits focused and include related tests. Pull requests should summarize behavior changes, list validation commands, note persistence or installation risks, link relevant issues, and include screenshots for UI changes.

Every major update MUST be committed and pushed to GitHub after its required validation passes. A major update includes a version milestone or substantial user-visible, architectural, persistence, installer, model-pipeline, or release-process change. Do not treat such work as complete while it exists only in the local worktree. Push it on a `codex/` branch, open a pull request, wait for required CI and CodeQL checks, and merge it into `main`. For a versioned release, also update the version and `CHANGELOG.md`, then create and push the corresponding release tag according to `docs/RELEASING.md`.
