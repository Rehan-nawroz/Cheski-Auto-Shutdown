"""Logging setup.

An unattended run has to be auditable afterwards, so everything also goes to a
rotating file in ``%APPDATA%\\CheskiAutoShutdown\\logs\\cheski.log``.  If that
directory cannot be created the app still runs; logging is best-effort.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import log_dir

LOGGER_NAME = "cheski"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(
    level: str | int = "INFO",
    log_file: Path | str | None = None,
    console: bool = True,
) -> logging.Logger:
    """Configure and return the package logger.

    Idempotent: calling it twice will not duplicate handlers.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level if isinstance(level, int) else getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False

    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        try:
            existing.close()
        except Exception:  # pragma: no cover - defensive
            pass

    formatter = logging.Formatter(_FORMAT)

    target = Path(log_file) if log_file is not None else log_dir() / "cheski.log"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            target, maxBytes=512 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        # A read-only or missing profile directory must not stop the app.
        pass

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """Return a child logger, e.g. ``get_logger("monitor")``."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
