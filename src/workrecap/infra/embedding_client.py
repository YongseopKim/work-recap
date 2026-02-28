"""TEI (Text Embeddings Inference) HTTP 임베딩 클라이언트."""

from __future__ import annotations

import httpx

from workrecap.config import AppConfig
from workrecap.exceptions import StorageError


class EmbeddingClient:
    """TEI HTTP API를 통한 원격 임베딩 클라이언트."""

    def __init__(self, config: AppConfig) -> None:
        self._tei_url = config.tei_url
        self._client = httpx.Client(timeout=30.0)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """TEI /embed 엔드포인트 호출."""
        try:
            resp = self._client.post(
                f"{self._tei_url}/embed",
                json={"inputs": texts},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as e:
            raise StorageError(f"TEI connection failed ({self._tei_url}): {e}") from e
        except httpx.HTTPStatusError as e:
            raise StorageError(f"TEI HTTP error ({e.response.status_code}): {e}") from e
        except httpx.HTTPError as e:
            raise StorageError(f"TEI request failed: {e}") from e

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        """검색 쿼리 임베딩."""
        return self._embed(queries)

    def embed_documents(self, documents: list[str]) -> list[list[float]]:
        """문서 임베딩."""
        return self._embed(documents)

    def close(self) -> None:
        """HTTP 클라이언트 종료."""
        self._client.close()
