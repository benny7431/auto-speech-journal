"""PyInstaller GUI entry point.

The installed GUI executable always starts the application.  Administrative
commands are intentionally kept in AutoSpeechJournal.CLI.exe.
"""

from __future__ import annotations

import sys

from auto_speech_journal.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["run", *sys.argv[1:]]))
