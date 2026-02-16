import httpx
import pytest
import respx

from git_recap.exceptions import FetchError
from git_recap.infra.ghes_client import GHESClient, MAX_RETRIES

BASE_URL = "https://github.example.com"
API_BASE = f"{BASE_URL}/api/v3"


@pytest.fixture
def client():
    c = GHESClient(BASE_URL, "test-token")
    yield c
    c.close()


class TestGHESClientInit:
    def test_creates_client_with_auth_header(self):
        with GHESClient(BASE_URL, "test-token") as c:
            assert c._client.headers["Authorization"] == "token test-token"
            assert "application/vnd.github.v3+json" in c._client.headers["Accept"]

    def test_base_url_trailing_slash_stripped(self):
        with GHESClient(f"{BASE_URL}/", "t") as c:
            assert c._api_base == API_BASE


class TestContextManager:
    def test_closes_client(self):
        c = GHESClient(BASE_URL, "t")
        c.close()
        assert c._client.is_closed


class TestSearchIssues:
    @respx.mock
    def test_returns_search_results(self, client):
        respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(200, json={
                "total_count": 1,
                "items": [{"number": 1, "title": "Test PR"}],
            })
        )
        result = client.search_issues("type:pr author:user")
        assert result["total_count"] == 1
        assert len(result["items"]) == 1

    @respx.mock
    def test_passes_query_and_pagination_params(self, client):
        route = respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )
        client.search_issues("type:pr author:user", page=2, per_page=50)
        assert route.called
        request = route.calls[0].request
        assert "type%3Apr" in str(request.url) or "type:pr" in str(request.url)
        assert "page=2" in str(request.url)
        assert "per_page=50" in str(request.url)


class TestRetry:
    @respx.mock
    def test_retries_on_429(self, client, monkeypatch):
        monkeypatch.setattr("git_recap.infra.ghes_client.time.sleep", lambda _: None)
        route = respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        result = client.search_issues("test")
        assert route.call_count == 2
        assert result["total_count"] == 0

    @respx.mock
    def test_retries_on_500(self, client, monkeypatch):
        monkeypatch.setattr("git_recap.infra.ghes_client.time.sleep", lambda _: None)
        route = respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(500),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        result = client.search_issues("test")
        assert route.call_count == 2
        assert result["total_count"] == 0

    @respx.mock
    def test_raises_fetch_error_after_max_retries_429(self, client, monkeypatch):
        monkeypatch.setattr("git_recap.infra.ghes_client.time.sleep", lambda _: None)
        respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "0"})
        )
        with pytest.raises(FetchError, match="Rate limit exceeded"):
            client.search_issues("test")

    @respx.mock
    def test_raises_fetch_error_after_max_retries_500(self, client, monkeypatch):
        monkeypatch.setattr("git_recap.infra.ghes_client.time.sleep", lambda _: None)
        respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(500)
        )
        with pytest.raises(FetchError, match="Server error"):
            client.search_issues("test")

    @respx.mock
    def test_no_retry_on_4xx(self, client):
        route = respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(404, text="Not found")
        )
        with pytest.raises(FetchError, match="Client error 404"):
            client.search_issues("test")
        assert route.call_count == 1

    @respx.mock
    def test_no_retry_on_422(self, client):
        route = respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(422, text="Validation failed")
        )
        with pytest.raises(FetchError, match="Client error 422"):
            client.search_issues("test")
        assert route.call_count == 1

    @respx.mock
    def test_retries_on_network_error(self, client, monkeypatch):
        monkeypatch.setattr("git_recap.infra.ghes_client.time.sleep", lambda _: None)
        route = respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        result = client.search_issues("test")
        assert route.call_count == 2
        assert result["total_count"] == 0


class TestPagination:
    @respx.mock
    def test_single_page(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1/files").mock(
            return_value=httpx.Response(200, json=[
                {"filename": "a.py", "additions": 1, "deletions": 0, "status": "added"},
            ])
        )
        result = client.get_pr_files("org", "repo", 1)
        assert len(result) == 1

    @respx.mock
    def test_multi_page(self, client):
        page1 = [{"id": i} for i in range(100)]
        page2 = [{"id": i} for i in range(100, 130)]
        route = respx.get(f"{API_BASE}/repos/org/repo/pulls/1/files").mock(
            side_effect=[
                httpx.Response(200, json=page1),
                httpx.Response(200, json=page2),
            ]
        )
        result = client.get_pr_files("org", "repo", 1)
        assert len(result) == 130
        assert route.call_count == 2

    @respx.mock
    def test_empty_result(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1/files").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = client.get_pr_files("org", "repo", 1)
        assert result == []


class TestPREndpoints:
    @respx.mock
    def test_get_pr(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/42").mock(
            return_value=httpx.Response(200, json={
                "number": 42, "title": "Test", "state": "closed"
            })
        )
        result = client.get_pr("org", "repo", 42)
        assert result["number"] == 42

    @respx.mock
    def test_get_pr_files(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1/files").mock(
            return_value=httpx.Response(200, json=[
                {"filename": "a.py", "additions": 5, "deletions": 2, "status": "modified"},
            ])
        )
        result = client.get_pr_files("org", "repo", 1)
        assert len(result) == 1
        assert result[0]["filename"] == "a.py"

    @respx.mock
    def test_get_pr_comments_merges_review_and_issue(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1/comments").mock(
            return_value=httpx.Response(200, json=[
                {"user": {"login": "a"}, "body": "review comment",
                 "created_at": "2025-01-01T00:00:00Z", "html_url": "u1"},
            ])
        )
        respx.get(f"{API_BASE}/repos/org/repo/issues/1/comments").mock(
            return_value=httpx.Response(200, json=[
                {"user": {"login": "b"}, "body": "issue comment",
                 "created_at": "2025-01-01T00:00:00Z", "html_url": "u2"},
            ])
        )
        result = client.get_pr_comments("org", "repo", 1)
        assert len(result) == 2

    @respx.mock
    def test_get_pr_reviews(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1/reviews").mock(
            return_value=httpx.Response(200, json=[
                {"user": {"login": "r"}, "state": "APPROVED", "body": "",
                 "submitted_at": "2025-01-01T00:00:00Z", "html_url": "u"},
            ])
        )
        result = client.get_pr_reviews("org", "repo", 1)
        assert len(result) == 1
        assert result[0]["state"] == "APPROVED"


class TestRetryAfterHeader:
    @respx.mock
    def test_uses_retry_after_header(self, client, monkeypatch):
        sleep_values = []
        monkeypatch.setattr(
            "git_recap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )
        respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "5"}),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        client.search_issues("test")
        assert sleep_values[0] == 5.0

    @respx.mock
    def test_default_retry_after_when_missing(self, client, monkeypatch):
        sleep_values = []
        monkeypatch.setattr(
            "git_recap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )
        respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        client.search_issues("test")
        assert sleep_values[0] == 60.0
