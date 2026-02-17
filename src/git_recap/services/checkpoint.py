"""Thread-safe checkpoint update utility."""

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def update_checkpoint(cp_path: Path, key: str, value: str) -> None:
    """Atomically read-modify-write a checkpoint key. Thread-safe."""
    with _lock:
        cp_path.parent.mkdir(parents=True, exist_ok=True)

        checkpoints: dict = {}
        if cp_path.exists():
            with open(cp_path, encoding="utf-8") as f:
                checkpoints = json.load(f)

        existing = checkpoints.get(key, "")
        if value > existing:
            checkpoints[key] = value
            with open(cp_path, "w", encoding="utf-8") as f:
                json.dump(checkpoints, f, indent=2)
            logger.debug("Checkpoint updated: %s = %s", key, value)
