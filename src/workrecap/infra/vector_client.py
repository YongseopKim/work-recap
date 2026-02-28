"""ChromaDB 벡터 데이터베이스 클라이언트."""

from __future__ import annotations

import logging
from typing import Any

import chromadb
from chromadb.config import Settings

from workrecap.config import AppConfig
from workrecap.exceptions import StorageError

logger = logging.getLogger(__name__)


class VectorDBClient:
    """ChromaDB HTTP 클라이언트."""

    def __init__(self, config: AppConfig) -> None:
        self.host = config.chroma_host
        self.port = config.chroma_port
        self.collection_name = config.chroma_collection

        try:
            self.client = chromadb.HttpClient(
                host=self.host,
                port=str(self.port),
                settings=Settings(allow_reset=True),
            )
            self.collection = self.client.get_or_create_collection(name=self.collection_name)
        except Exception as e:
            raise StorageError(f"ChromaDB connection failed ({self.host}:{self.port}): {e}") from e

    def add_documents(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        """임베딩된 문서 추가 (upsert)."""
        try:
            self.collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
        except Exception as e:
            raise StorageError(f"ChromaDB upsert failed: {e}") from e

    def search(
        self,
        query_embeddings: list[list[float]],
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """시맨틱 검색."""
        try:
            return self.collection.query(
                query_embeddings=query_embeddings,
                n_results=n_results,
                where=where,
            )
        except Exception as e:
            raise StorageError(f"ChromaDB search failed: {e}") from e

    def delete_by_metadata(self, filter: dict[str, Any]) -> None:
        """메타데이터 필터 기반 삭제."""
        try:
            self.collection.delete(where=filter)
        except Exception as e:
            raise StorageError(f"ChromaDB delete failed: {e}") from e

    def close(self) -> None:
        """클라이언트 정리."""
        pass
