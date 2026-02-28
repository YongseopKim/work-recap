"""StorageService 테스트."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from workrecap.exceptions import StorageError
from workrecap.services.storage import StorageService


@pytest.fixture
def mock_postgres():
    pg = AsyncMock()
    pg.init_db = AsyncMock()
    pg.save_activities = AsyncMock()
    pg.save_stats = AsyncMock()
    pg.save_summary = AsyncMock()
    pg.get_activities = AsyncMock(return_value=[])
    pg.get_stats = AsyncMock(return_value=None)
    pg.get_summary = AsyncMock(return_value=None)
    pg.close = AsyncMock()
    return pg


@pytest.fixture
def mock_vector():
    vdb = MagicMock()
    vdb.add_documents = MagicMock()
    vdb.search = MagicMock(
        return_value={
            "ids": [["doc1"]],
            "documents": [["content"]],
            "distances": [[0.1]],
            "metadatas": [[{"level": "daily"}]],
        }
    )
    vdb.close = MagicMock()
    return vdb


@pytest.fixture
def mock_embedding():
    emb = MagicMock()
    emb.embed_documents = MagicMock(return_value=[[0.1, 0.2, 0.3]])
    emb.embed_queries = MagicMock(return_value=[[0.4, 0.5, 0.6]])
    emb.close = MagicMock()
    return emb


@pytest.fixture
def storage(mock_postgres, mock_vector, mock_embedding):
    return StorageService(mock_postgres, mock_vector, mock_embedding)


class TestStorageServiceSave:
    @pytest.mark.asyncio
    async def test_save_activities_writes_to_postgres(self, storage, mock_postgres):
        """save_activities가 PostgreSQL에 활동+통계를 저장."""
        acts = [{"kind": "pr_authored", "external_id": 1}]
        stats = {"date": "2025-02-16", "github": {"authored_count": 1}}

        await storage.save_activities("2025-02-16", acts, stats)

        mock_postgres.save_activities.assert_awaited_once()
        mock_postgres.save_stats.assert_awaited_once_with(stats)

    @pytest.mark.asyncio
    async def test_save_summary_writes_to_postgres_and_vector(
        self, storage, mock_postgres, mock_vector, mock_embedding
    ):
        """save_summary가 PostgreSQL + VectorDB에 저장."""
        await storage.save_summary("daily", "2025-02-16", "# Summary")

        mock_postgres.save_summary.assert_awaited_once_with(
            "daily", "2025-02-16", "# Summary", None
        )
        mock_embedding.embed_documents.assert_called_once_with(["# Summary"])
        mock_vector.add_documents.assert_called_once()

    @pytest.mark.asyncio
    async def test_postgres_failure_logged_not_raised(self, storage, mock_postgres, caplog):
        """PostgreSQL 실패 시 로깅만, 예외 안 던짐."""
        mock_postgres.save_activities.side_effect = StorageError("DB down")

        # 예외 없이 완료되어야 함
        await storage.save_activities("2025-02-16", [{"kind": "pr"}], {})

    @pytest.mark.asyncio
    async def test_chromadb_failure_logged_not_raised(self, storage, mock_vector, caplog):
        """ChromaDB 실패 시 로깅만, 예외 안 던짐."""
        mock_vector.add_documents.side_effect = StorageError("Vector down")

        # 예외 없이 완료되어야 함
        await storage.save_summary("daily", "2025-02-16", "# Summary")


class TestStorageServiceSearch:
    @pytest.mark.asyncio
    async def test_search_summaries_returns_results(self, storage, mock_embedding, mock_vector):
        """search_summaries가 결과를 반환."""
        results = await storage.search_summaries("authentication", n_results=3)

        mock_embedding.embed_queries.assert_called_once_with(["authentication"])
        mock_vector.search.assert_called_once()
        assert len(results) == 1
        assert results[0]["id"] == "doc1"
        assert results[0]["content"] == "content"
        assert results[0]["distance"] == 0.1


class TestStorageServiceSync:
    def test_save_activities_sync(self, storage, mock_postgres):
        """save_activities_sync가 동기적으로 동작."""
        acts = [{"kind": "pr_authored"}]
        stats = {"date": "2025-02-16"}

        storage.save_activities_sync("2025-02-16", acts, stats)
        mock_postgres.save_activities.assert_awaited_once()

    def test_save_summary_sync(self, storage, mock_postgres, mock_vector, mock_embedding):
        """save_summary_sync가 동기적으로 동작."""
        storage.save_summary_sync("daily", "2025-02-16", "# Summary")
        mock_postgres.save_summary.assert_awaited_once()

    def test_search_summaries_sync(self, storage, mock_embedding, mock_vector):
        """search_summaries_sync가 동기적으로 동작."""
        results = storage.search_summaries_sync("query")
        assert isinstance(results, list)


class TestStorageServiceLifecycle:
    @pytest.mark.asyncio
    async def test_close_disposes_clients(
        self, storage, mock_postgres, mock_vector, mock_embedding
    ):
        """close() 시 모든 클라이언트 정리."""
        await storage.close()
        mock_postgres.close.assert_awaited_once()
        mock_vector.close.assert_called_once()
        mock_embedding.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_db(self, storage, mock_postgres):
        """init_db가 PostgreSQL init_db 호출."""
        await storage.init_db()
        mock_postgres.init_db.assert_awaited_once()
