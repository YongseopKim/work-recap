"""EmbeddingClient (TEI HTTP) 테스트."""

import httpx
import pytest
import respx

from workrecap.config import AppConfig
from workrecap.exceptions import StorageError
from workrecap.infra.embedding_client import EmbeddingClient

TEI_URL = "http://192.168.0.2:8090"


@pytest.fixture
def config():
    return AppConfig(
        ghes_url="https://ghes.example.com",
        ghes_token="token",
        username="user",
        tei_url=TEI_URL,
    )


@pytest.fixture
def client(config):
    return EmbeddingClient(config)


class TestEmbeddingClient:
    @respx.mock
    def test_embed_queries_returns_vectors(self, client):
        """embed_queries가 TEI API를 호출하고 벡터 리스트를 반환."""
        respx.post(f"{TEI_URL}/embed").mock(
            return_value=httpx.Response(200, json=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
        )
        result = client.embed_queries(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3]
        assert result[1] == [0.4, 0.5, 0.6]

    @respx.mock
    def test_embed_documents_returns_vectors(self, client):
        """embed_documents가 TEI API를 호출하고 벡터 리스트를 반환."""
        respx.post(f"{TEI_URL}/embed").mock(return_value=httpx.Response(200, json=[[1.0, 2.0]]))
        result = client.embed_documents(["doc content"])
        assert len(result) == 1
        assert result[0] == [1.0, 2.0]

    @respx.mock
    def test_connection_error_raises_storage_error(self, client):
        """TEI 접속 실패 시 StorageError raise."""
        respx.post(f"{TEI_URL}/embed").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(StorageError, match="TEI"):
            client.embed_queries(["test"])

    @respx.mock
    def test_http_error_raises_storage_error(self, client):
        """TEI HTTP 에러 시 StorageError raise."""
        respx.post(f"{TEI_URL}/embed").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        with pytest.raises(StorageError, match="TEI"):
            client.embed_queries(["test"])

    def test_uses_config_tei_url(self):
        """config에서 tei_url을 올바르게 사용."""
        config = AppConfig(
            ghes_url="u",
            ghes_token="t",
            username="u",
            tei_url="http://custom-host:9999",
        )
        client = EmbeddingClient(config)
        assert client._tei_url == "http://custom-host:9999"

    @respx.mock
    def test_embed_sends_correct_payload(self, client):
        """TEI API에 올바른 JSON payload를 전송."""
        route = respx.post(f"{TEI_URL}/embed").mock(return_value=httpx.Response(200, json=[[0.1]]))
        client.embed_queries(["test text"])
        assert route.called
        request = route.calls[0].request
        import json

        body = json.loads(request.content)
        assert body["inputs"] == ["test text"]
