"""PostgreSQL 비동기 클라이언트."""

from __future__ import annotations

import logging
from datetime import date as date_type, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Column, Field, JSON, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from workrecap.config import AppConfig
from workrecap.exceptions import StorageError

logger = logging.getLogger(__name__)


class ActivityDB(SQLModel, table=True):
    """정규화된 활동 레코드 (PostgreSQL 저장용)."""

    __tablename__ = "activities"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    date: date_type = Field(index=True)
    source: str = Field(default="github", index=True)
    kind: str = Field(index=True)
    external_id: str = Field(index=True)
    ts: datetime
    repo: str = Field(index=True)
    title: str
    url: str
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True


class DailyStatsDB(SQLModel, table=True):
    """일일 통계 (PostgreSQL 저장용)."""

    __tablename__ = "daily_stats"

    date: date_type = Field(primary_key=True)
    github_stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    confluence_stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    jira_stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SummaryDB(SQLModel, table=True):
    """계층적 요약 리포트 (PostgreSQL 저장용)."""

    __tablename__ = "summaries"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    level: str = Field(index=True)
    date_key: str = Field(index=True)
    content: str
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PostgresClient:
    """PostgreSQL 비동기 클라이언트."""

    def __init__(self, config: AppConfig) -> None:
        self.engine = create_async_engine(config.postgres_url, echo=False)
        self.async_session_maker = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init_db(self) -> None:
        """테이블 생성."""
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
        except Exception as e:
            raise StorageError(f"PostgreSQL init_db failed: {e}") from e

    # ── Write 메서드 ──

    async def save_activities(self, date_val: date_type, activities: list[dict]) -> None:
        """활동 내역 저장 (Upsert)."""
        try:
            async with self.async_session_maker() as session:
                for act in activities:
                    ext_id = str(act.get("external_id", ""))
                    kind = act.get("kind", "")

                    statement = select(ActivityDB).where(
                        ActivityDB.date == date_val,
                        ActivityDB.external_id == ext_id,
                        ActivityDB.kind == kind,
                    )
                    results = await session.execute(statement)
                    existing = results.scalars().first()

                    ts_str = act.get("ts", "")
                    try:
                        ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        ts_dt = datetime.utcnow()

                    if existing:
                        existing.data = act
                        existing.ts = ts_dt
                        existing.repo = act.get("repo", "")
                        existing.title = act.get("title", "")
                        existing.url = act.get("url", "")
                        session.add(existing)
                    else:
                        new_act = ActivityDB(
                            date=date_val,
                            source=act.get("source", "github"),
                            kind=kind,
                            external_id=ext_id,
                            ts=ts_dt,
                            repo=act.get("repo", ""),
                            title=act.get("title", ""),
                            url=act.get("url", ""),
                            data=act,
                        )
                        session.add(new_act)

                await session.commit()
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"PostgreSQL save_activities failed: {e}") from e

    async def save_stats(self, stats: dict) -> None:
        """통계 저장."""
        date_str = stats.get("date", "")
        if not date_str:
            return

        try:
            date_val = date_type.fromisoformat(date_str)
            async with self.async_session_maker() as session:
                statement = select(DailyStatsDB).where(DailyStatsDB.date == date_val)
                results = await session.execute(statement)
                existing = results.scalars().first()

                if existing:
                    existing.github_stats = stats.get("github", {})
                    existing.confluence_stats = stats.get("confluence", {})
                    existing.jira_stats = stats.get("jira", {})
                    existing.updated_at = datetime.utcnow()
                    session.add(existing)
                else:
                    new_stats = DailyStatsDB(
                        date=date_val,
                        github_stats=stats.get("github", {}),
                        confluence_stats=stats.get("confluence", {}),
                        jira_stats=stats.get("jira", {}),
                    )
                    session.add(new_stats)

                await session.commit()
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"PostgreSQL save_stats failed: {e}") from e

    async def save_summary(
        self,
        level: str,
        date_key: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """요약 리포트 저장."""
        try:
            async with self.async_session_maker() as session:
                statement = select(SummaryDB).where(
                    SummaryDB.level == level,
                    SummaryDB.date_key == date_key,
                )
                results = await session.execute(statement)
                existing = results.scalars().first()

                if existing:
                    existing.content = content
                    existing.metadata_json = metadata or {}
                    existing.updated_at = datetime.utcnow()
                    session.add(existing)
                else:
                    new_summary = SummaryDB(
                        level=level,
                        date_key=date_key,
                        content=content,
                        metadata_json=metadata or {},
                    )
                    session.add(new_summary)

                await session.commit()
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"PostgreSQL save_summary failed: {e}") from e

    # ── Read 메서드 ──

    async def get_activities(self, date_str: str) -> list[dict]:
        """날짜별 activities 조회."""
        try:
            date_val = date_type.fromisoformat(date_str)
            async with self.async_session_maker() as session:
                statement = select(ActivityDB).where(ActivityDB.date == date_val)
                results = await session.execute(statement)
                rows = results.scalars().all()
                return [row.data for row in rows]
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"PostgreSQL get_activities failed: {e}") from e

    async def get_stats(self, date_str: str) -> dict | None:
        """날짜별 stats 조회."""
        try:
            date_val = date_type.fromisoformat(date_str)
            async with self.async_session_maker() as session:
                statement = select(DailyStatsDB).where(DailyStatsDB.date == date_val)
                results = await session.execute(statement)
                row = results.scalars().first()
                if row is None:
                    return None
                return {
                    "date": row.date.isoformat(),
                    "github": row.github_stats,
                    "confluence": row.confluence_stats,
                    "jira": row.jira_stats,
                }
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"PostgreSQL get_stats failed: {e}") from e

    async def get_summary(self, level: str, date_key: str) -> str | None:
        """레벨+키로 summary 조회."""
        try:
            async with self.async_session_maker() as session:
                statement = select(SummaryDB).where(
                    SummaryDB.level == level,
                    SummaryDB.date_key == date_key,
                )
                results = await session.execute(statement)
                row = results.scalars().first()
                if row is None:
                    return None
                return row.content
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"PostgreSQL get_summary failed: {e}") from e

    async def close(self) -> None:
        """엔진 종료."""
        await self.engine.dispose()
