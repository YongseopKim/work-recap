"""Thread-safe pool of GHESClient instances."""

import logging
import queue
from collections.abc import Generator
from contextlib import contextmanager

from git_recap.infra.ghes_client import GHESClient

logger = logging.getLogger(__name__)


class GHESClientPool:
    """Queue-based thread-safe pool of GHESClient instances.

    Each client has its own httpx.Client (connection pool), making
    concurrent enrichment requests safe across threads.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        size: int = 5,
        search_interval: float = 2.0,
    ) -> None:
        self._size = size
        self._pool: queue.Queue[GHESClient] = queue.Queue(maxsize=size)
        self._clients: list[GHESClient] = []

        for _ in range(size):
            c = GHESClient(base_url, token, search_interval=search_interval)
            self._clients.append(c)
            self._pool.put(c)

        logger.info("GHESClientPool created: size=%d", size)

    @property
    def size(self) -> int:
        return self._size

    def acquire(self, timeout: float = 30.0) -> GHESClient:
        """Get a client from the pool. Blocks until one is available."""
        try:
            return self._pool.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"Could not acquire client from pool within {timeout}s") from None

    def release(self, client: GHESClient) -> None:
        """Return a client to the pool."""
        self._pool.put_nowait(client)

    @contextmanager
    def client(self, timeout: float = 30.0) -> Generator[GHESClient]:
        """Context manager: acquire → yield → release."""
        c = self.acquire(timeout=timeout)
        try:
            yield c
        finally:
            self.release(c)

    def close(self) -> None:
        """Close all clients in the pool."""
        for c in self._clients:
            c.close()
        logger.info("GHESClientPool closed: %d clients", len(self._clients))
