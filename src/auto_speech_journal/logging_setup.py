from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

LOG_NAME = "auto_speech_journal"


def configure_logging(logs_dir: Path, *, verbose: bool = False) -> Path:
    """Configure application logging once and return the active log file."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "journal.log"

    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(processName)s %(threadName)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if verbose:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    logging.captureWarnings(True)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("filelock").setLevel(logging.WARNING)
    return log_file


def install_exception_hook(logger: logging.Logger | None = None) -> None:
    """Log otherwise-unhandled main-thread exceptions before Python exits."""
    target = logger or logging.getLogger(LOG_NAME)
    original = sys.excepthook

    def excepthook(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        if issubclass(exception_type, KeyboardInterrupt):
            original(exception_type, exception, traceback)
            return
        target.critical("Unhandled exception", exc_info=(exception_type, exception, traceback))
        original(exception_type, exception, traceback)

    sys.excepthook = excepthook
