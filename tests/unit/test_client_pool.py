"""Tests for GHESClientPool."""

import threading

import pytest

from git_recap.infra.client_pool import GHESClientPool


@pytest.fixture
def pool():
    p = GHESClientPool(
        base_url="https://github.example.com",
        token="test-token",
        size=3,
        search_interval=0,
    )
    yield p
    p.close()


class TestGHESClientPool:
    def test_creates_pool_with_correct_size(self, pool):
        assert pool.size == 3

    def test_acquire_returns_client(self, pool):
        client = pool.acquire()
        assert client is not None
        pool.release(client)

    def test_acquire_release_roundtrip(self):
        p = GHESClientPool(
            base_url="https://github.example.com",
            token="test-token",
            size=1,
            search_interval=0,
        )
        c1 = p.acquire()
        p.release(c1)
        c2 = p.acquire()
        assert c2 is c1  # same client reused (pool of 1)
        p.release(c2)
        p.close()

    def test_acquire_timeout_raises(self):
        """Pool of 1, acquire 2 → timeout."""
        p = GHESClientPool(
            base_url="https://github.example.com",
            token="test-token",
            size=1,
            search_interval=0,
        )
        c1 = p.acquire()
        with pytest.raises(TimeoutError):
            p.acquire(timeout=0.1)
        p.release(c1)
        p.close()

    def test_context_manager(self):
        p = GHESClientPool(
            base_url="https://github.example.com",
            token="test-token",
            size=2,
            search_interval=0,
        )
        with p.client() as client:
            assert client is not None
        p.close()

    def test_concurrent_access(self, pool):
        """3 threads acquire from pool of 3 concurrently."""
        acquired = []
        barrier = threading.Barrier(3)
        errors = []

        def worker():
            try:
                c = pool.acquire(timeout=5.0)
                acquired.append(c)
                barrier.wait(timeout=5.0)
                pool.release(c)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(acquired) == 3
        # All 3 clients should be different instances
        assert len(set(id(c) for c in acquired)) == 3

    def test_close_closes_all_clients(self):
        p = GHESClientPool(
            base_url="https://github.example.com",
            token="test-token",
            size=2,
            search_interval=0,
        )
        # Acquire and release to populate
        c1 = p.acquire()
        c2 = p.acquire()
        p.release(c1)
        p.release(c2)
        p.close()
        assert c1._client.is_closed
        assert c2._client.is_closed
