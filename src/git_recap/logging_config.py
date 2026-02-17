"""Logging configuration for git-recap."""

import logging
import sys

_configured = False

NOISY_LOGGERS = ("httpx", "httpcore", "openai", "anthropic", "urllib3")


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the git-recap package.

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

    root = logging.getLogger("git_recap")
    root.setLevel(level)
    root.addHandler(handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def reset_logging() -> None:
    """Reset logging state. For testing only."""
    global _configured
    _configured = False
    root = logging.getLogger("git_recap")
    root.handlers.clear()
    root.setLevel(logging.WARNING)
