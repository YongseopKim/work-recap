"""Logging configuration for work-recap."""

import logging
import sys
from datetime import datetime
from pathlib import Path

_configured = False

NOISY_LOGGERS = ("httpx", "httpcore", "openai", "anthropic", "urllib3", "google")


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the work-recap package.

    - Output to stderr (keeps typer.echo stdout clean)
    - Format: HH:MM:SS LEVEL [module.name] message
    - Silences noisy third-party loggers to WARNING
    - Idempotent: safe to call multiple times
    """
    global _configured
    if _configured:
        return
    _configured = True

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S")
    )

    root = logging.getLogger("workrecap")
    root.setLevel(level)
    root.addHandler(handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def setup_file_logging(log_dir: Path) -> logging.FileHandler:
    """Add a file handler that captures DEBUG-level logs.

    Creates .log/YYYYMMDD_HHMMSS.log. Returns the handler for cleanup.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{timestamp}.log"

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger("workrecap")
    root.addHandler(handler)
    return handler


def reset_logging() -> None:
    """Reset logging state. For testing only."""
    global _configured
    _configured = False
    root = logging.getLogger("workrecap")
    root.handlers.clear()
    root.setLevel(logging.WARNING)
