"""PostgresClient 테스트."""

from datetime import date as date_type
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workrecap.exceptions import StorageError
from workrecap.infra.postgres_client import (
    ActivityDB,
    DailyStatsDB,
    PostgresClient,
    SummaryDB,
)


@pytest.fixture
def mock_engine():
    """AsyncEngine mock."""
    engine = AsyncMock()
    engine.dispose = AsyncMock()
    return engine


@pytest.fixture
def mock_session():
    """AsyncSession mock."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.fixture
def client(mock_engine, mock_session):
    """PostgresClient with mocked engine and session."""
    with patch("workrecap.infra.postgres_client.create_async_engine", return_value=mock_engine):
        from workrecap.config import AppConfig

        config = AppConfig(ghes_url="u", ghes_token="t", username="u")
        pg = PostgresClient(config)
        pg.async_session_maker = MagicMock(return_value=mock_session)
        return pg


class TestPostgresClientSave:
    @pytest.mark.asyncio
    async def test_save_activities_inserts_new(self, client, mock_session):
        """새 활동을 저장할 때 session.add 호출."""
        # execute returns empty (no existing)
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        activities = [
            {
                "external_id": 123,
                "kind": "pr_authored",
                "ts": "2025-02-16T10:00:00Z",
                "repo": "org/repo",
                "title": "Fix bug",
                "url": "https://github.com/org/repo/pull/123",
                "source": "github",
            }
        ]
        await client.save_activities(date_type(2025, 2, 16), activities)
        mock_session.add.assert_called()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_stats_inserts_new(self, client, mock_session):
        """새 통계 저장."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        stats = {"date": "2025-02-16", "github": {"authored_count": 3}}
        await client.save_stats(stats)
        mock_session.add.assert_called()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_summary_inserts_new(self, client, mock_session):
        """새 요약 저장."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        await client.save_summary("daily", "2025-02-16", "# Summary")
        mock_session.add.assert_called()
        mock_session.commit.assert_awaited_once()


class TestPostgresClientRead:
    @pytest.mark.asyncio
    async def test_get_activities(self, client, mock_session):
        """날짜별 activities 조회."""
        mock_act = MagicMock(spec=ActivityDB)
        mock_act.data = {"kind": "pr_authored", "title": "Fix"}
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_act]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await client.get_activities("2025-02-16")
        assert len(result) == 1
        assert result[0]["kind"] == "pr_authored"

    @pytest.mark.asyncio
    async def test_get_stats(self, client, mock_session):
        """날짜별 stats 조회."""
        mock_stats = MagicMock(spec=DailyStatsDB)
        mock_stats.github_stats = {"authored_count": 3}
        mock_stats.confluence_stats = {}
        mock_stats.jira_stats = {}
        mock_stats.date = date_type(2025, 2, 16)
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_stats
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await client.get_stats("2025-02-16")
        assert result is not None
        assert result["github"]["authored_count"] == 3

    @pytest.mark.asyncio
    async def test_get_stats_not_found(self, client, mock_session):
        """없는 날짜의 stats 조회 시 None 반환."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await client.get_stats("2099-01-01")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_summary(self, client, mock_session):
        """레벨+키로 summary 조회."""
        mock_summ = MagicMock(spec=SummaryDB)
        mock_summ.content = "# Daily Summary"
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_summ
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await client.get_summary("daily", "2025-02-16")
        assert result == "# Daily Summary"

    @pytest.mark.asyncio
    async def test_get_summary_not_found(self, client, mock_session):
        """없는 summary 조회 시 None 반환."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await client.get_summary("daily", "2099-01-01")
        assert result is None


class TestPostgresClientErrors:
    @pytest.mark.asyncio
    async def test_save_activities_error_raises_storage_error(self, client, mock_session):
        """DB 에러 시 StorageError raise."""
        mock_session.execute = AsyncMock(side_effect=Exception("DB down"))

        with pytest.raises(StorageError, match="PostgreSQL"):
            await client.save_activities(date_type(2025, 2, 16), [{"external_id": 1, "kind": "pr"}])

    @pytest.mark.asyncio
    async def test_save_summary_error_raises_storage_error(self, client, mock_session):
        """summary 저장 실패 시 StorageError raise."""
        mock_session.execute = AsyncMock(side_effect=Exception("DB down"))

        with pytest.raises(StorageError, match="PostgreSQL"):
            await client.save_summary("daily", "2025-02-16", "content")

    @pytest.mark.asyncio
    async def test_close_disposes_engine(self, client, mock_engine):
        """close() 시 engine.dispose() 호출."""
        await client.close()
        mock_engine.dispose.assert_awaited_once()
