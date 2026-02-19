"""BatchStateStore 단위 테스트."""

import pytest

from workrecap.services.batch_state import BatchStateStore


@pytest.fixture
def store(tmp_path):
    return BatchStateStore(tmp_path / "state" / "batch_jobs.json")


class TestBatchStateStore:
    def test_save_and_get_job(self, store):
        store.save_job(
            batch_id="batch-123",
            provider="anthropic",
            task="daily",
            custom_ids=["day-01", "day-02"],
        )
        jobs = store.get_active_jobs()
        assert len(jobs) == 1
        assert jobs[0]["batch_id"] == "batch-123"
        assert jobs[0]["provider"] == "anthropic"
        assert jobs[0]["task"] == "daily"
        assert jobs[0]["custom_ids"] == ["day-01", "day-02"]
        assert jobs[0]["status"] == "submitted"
        assert "submitted_at" in jobs[0]

    def test_update_status(self, store):
        store.save_job("batch-1", "openai", "enrich", ["r1"])
        store.update_status("batch-1", "completed")

        jobs = store.get_active_jobs()
        # completed jobs are no longer "active"
        assert len(jobs) == 0

    def test_get_active_excludes_completed_and_failed(self, store):
        store.save_job("b1", "anthropic", "daily", ["r1"])
        store.save_job("b2", "anthropic", "daily", ["r2"])
        store.save_job("b3", "anthropic", "daily", ["r3"])

        store.update_status("b1", "completed")
        store.update_status("b2", "failed")

        active = store.get_active_jobs()
        assert len(active) == 1
        assert active[0]["batch_id"] == "b3"

    def test_remove_job(self, store):
        store.save_job("b1", "anthropic", "daily", ["r1"])
        store.remove_job("b1")

        jobs = store.get_active_jobs()
        assert len(jobs) == 0

    def test_remove_nonexistent_job_is_noop(self, store):
        store.remove_job("nonexistent")  # should not raise

    def test_persistence(self, tmp_path):
        path = tmp_path / "state" / "batch_jobs.json"
        store1 = BatchStateStore(path)
        store1.save_job("b1", "anthropic", "daily", ["r1"])

        # New instance reads from same file
        store2 = BatchStateStore(path)
        jobs = store2.get_active_jobs()
        assert len(jobs) == 1
        assert jobs[0]["batch_id"] == "b1"

    def test_thread_safety(self, store):
        """동시 접근 시 데이터 손실 없음."""
        import threading

        def save(i):
            store.save_job(f"b-{i}", "anthropic", "daily", [f"r-{i}"])

        threads = [threading.Thread(target=save, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 10 should be present and active
        active = store.get_active_jobs()
        assert len(active) == 10

    def test_get_job(self, store):
        store.save_job("b1", "anthropic", "daily", ["r1"])
        job = store.get_job("b1")
        assert job is not None
        assert job["batch_id"] == "b1"

        assert store.get_job("nonexistent") is None
