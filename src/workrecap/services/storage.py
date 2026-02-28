"""DB + Vector 통합 저장 서비스.

파일 쓰기는 normalizer/summarizer가 이미 수행하므로,
StorageService는 PostgreSQL + ChromaDB 저장만 담당한다.
모든 에러는 graceful degradation — 로깅만 하고 파이프라인을 중단하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as date_type
from typing import Any

from workrecap.exceptions import StorageError

logger = logging.getLogger(__name__)


class StorageService:
    """PostgreSQL + ChromaDB 통합 저장 서비스."""

    def __init__(
        self,
        postgres,
        vector_db,
        embedding,
    ) -> None:
        self._postgres = postgres
        self._vector_db = vector_db
        self._embedding = embedding

    async def init_db(self) -> None:
        """PostgreSQL 테이블 초기화."""
        await self._postgres.init_db()

    # ── Async 메서드 ──

    async def save_activities(
        self,
        date_str: str,
        activities: list[dict[str, Any]],
        stats: dict[str, Any],
    ) -> None:
        """활동+통계를 PostgreSQL에 저장. 실패 시 로깅만."""
        try:
            date_val = date_type.fromisoformat(date_str)
            await self._postgres.save_activities(date_val, activities)
            await self._postgres.save_stats(stats)
        except (StorageError, Exception) as e:
            logger.warning("Storage save_activities failed for %s: %s", date_str, e)

    async def save_summary(
        self,
        level: str,
        date_key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """요약을 PostgreSQL + VectorDB에 저장. 실패 시 로깅만."""
        # 1. PostgreSQL
        try:
            await self._postgres.save_summary(level, date_key, content, metadata)
        except (StorageError, Exception) as e:
            logger.warning("Storage save_summary (PG) failed for %s/%s: %s", level, date_key, e)

        # 2. VectorDB
        try:
            doc_id = f"{level}_{date_key}"
            embeddings = self._embedding.embed_documents([content])
            vector_metadata = {
                "level": level,
                "date_key": date_key,
            }
            self._vector_db.add_documents(
                ids=[doc_id],
                embeddings=embeddings,
                documents=[content],
                metadatas=[vector_metadata],
            )
        except (StorageError, Exception) as e:
            logger.warning("Storage save_summary (Vector) failed for %s/%s: %s", level, date_key, e)

    async def search_summaries(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """시맨틱 검색으로 요약을 찾는다."""
        query_embeddings = self._embedding.embed_queries([query])
        results = self._vector_db.search(query_embeddings, n_results=n_results)

        output: list[dict[str, Any]] = []
        if results and "documents" in results:
            for i in range(len(results["ids"][0])):
                output.append(
                    {
                        "id": results["ids"][0][i],
                        "content": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i],
                        "distance": results["distances"][0][i] if "distances" in results else None,
                    }
                )
        return output

    async def close(self) -> None:
        """모든 클라이언트 정리."""
        await self._postgres.close()
        self._vector_db.close()
        self._embedding.close()

    # ── Sync wrapper (Orchestrator가 sync이므로) ──

    def init_db_sync(self) -> None:
        """init_db의 동기 버전."""
        asyncio.run(self.init_db())

    def save_activities_sync(
        self,
        date_str: str,
        activities: list[dict[str, Any]],
        stats: dict[str, Any],
    ) -> None:
        """save_activities의 동기 버전."""
        asyncio.run(self.save_activities(date_str, activities, stats))

    def save_summary_sync(
        self,
        level: str,
        date_key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """save_summary의 동기 버전."""
        asyncio.run(self.save_summary(level, date_key, content, metadata))

    def search_summaries_sync(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """search_summaries의 동기 버전."""
        return asyncio.run(self.search_summaries(query, n_results=n_results))

    def close_sync(self) -> None:
        """close의 동기 버전."""
        asyncio.run(self.close())
