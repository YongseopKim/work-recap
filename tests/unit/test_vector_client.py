"""VectorDBClient 테스트."""

from unittest.mock import MagicMock, patch

import pytest

from workrecap.config import AppConfig
from workrecap.exceptions import StorageError
from workrecap.infra.vector_client import VectorDBClient


@pytest.fixture
def config():
    return AppConfig(
        ghes_url="u",
        ghes_token="t",
        username="u",
        chroma_host="192.168.0.2",
        chroma_port=9000,
        chroma_collection="test_collection",
    )


@pytest.fixture
def mock_chroma_client():
    with patch("workrecap.infra.vector_client.chromadb") as mock_chromadb:
        mock_client = MagicMock()
        mock_chromadb.HttpClient.return_value = mock_client
        mock_collection = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        yield mock_client, mock_collection, mock_chromadb


class TestVectorDBClient:
    def test_add_documents(self, config, mock_chroma_client):
        """문서 추가 시 collection.upsert 호출."""
        _, mock_collection, _ = mock_chroma_client
        client = VectorDBClient(config)

        client.add_documents(
            ids=["doc1"],
            embeddings=[[0.1, 0.2]],
            documents=["test doc"],
            metadatas=[{"level": "daily"}],
        )
        mock_collection.upsert.assert_called_once_with(
            ids=["doc1"],
            embeddings=[[0.1, 0.2]],
            documents=["test doc"],
            metadatas=[{"level": "daily"}],
        )

    def test_search(self, config, mock_chroma_client):
        """검색 시 collection.query 호출."""
        _, mock_collection, _ = mock_chroma_client
        mock_collection.query.return_value = {
            "ids": [["doc1"]],
            "documents": [["content"]],
            "distances": [[0.1]],
            "metadatas": [[{"level": "daily"}]],
        }
        client = VectorDBClient(config)

        result = client.search([[0.1, 0.2]], n_results=3)
        mock_collection.query.assert_called_once_with(
            query_embeddings=[[0.1, 0.2]],
            n_results=3,
            where=None,
        )
        assert result["ids"] == [["doc1"]]

    def test_delete_by_metadata(self, config, mock_chroma_client):
        """메타데이터 필터 기반 삭제 시 collection.delete 호출."""
        _, mock_collection, _ = mock_chroma_client
        client = VectorDBClient(config)

        client.delete_by_metadata({"level": "daily"})
        mock_collection.delete.assert_called_once_with(where={"level": "daily"})

    def test_connection_error_raises_storage_error(self, config):
        """ChromaDB 접속 실패 시 StorageError raise."""
        with patch("workrecap.infra.vector_client.chromadb") as mock_chromadb:
            mock_chromadb.HttpClient.side_effect = Exception("Connection refused")
            with pytest.raises(StorageError, match="ChromaDB"):
                VectorDBClient(config)

    def test_add_documents_error_raises_storage_error(self, config, mock_chroma_client):
        """upsert 실패 시 StorageError raise."""
        _, mock_collection, _ = mock_chroma_client
        mock_collection.upsert.side_effect = Exception("write failed")
        client = VectorDBClient(config)

        with pytest.raises(StorageError, match="ChromaDB"):
            client.add_documents(ids=["x"], embeddings=[[1.0]], documents=["y"])

    def test_close(self, config, mock_chroma_client):
        """close() 호출 시 에러 없이 완료."""
        client = VectorDBClient(config)
        client.close()
