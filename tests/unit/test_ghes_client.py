import httpx
import pytest
import respx

from workrecap.exceptions import FetchError
from workrecap.infra.ghes_client import GHESClient, RATE_LIMIT_MAX_RETRIES

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
            return_value=httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "items": [{"number": 1, "title": "Test PR"}],
                },
            )
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
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
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
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
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
        """Rate limit retries up to RATE_LIMIT_MAX_RETRIES (7), then raises."""
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        route = respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "0"})
        )
        with pytest.raises(FetchError, match="Rate limit exceeded"):
            client.search_issues("test")
        # 1 initial + RATE_LIMIT_MAX_RETRIES retries
        assert route.call_count == RATE_LIMIT_MAX_RETRIES + 1

    @respx.mock
    def test_raises_fetch_error_after_max_retries_500(self, client, monkeypatch):
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        respx.get(f"{API_BASE}/search/issues").mock(return_value=httpx.Response(500))
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
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        route = respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        result = client.search_issues("test")
        assert route.call_count == 2
        assert result["total_count"] == 0

    @respx.mock
    def test_rate_limit_and_server_error_counters_independent(self, client, monkeypatch):
        """Rate limit (7 max) and server error (3 max) counters are independent.

        A request can survive mixed errors: e.g., 2 rate limits + 2 server errors
        + success = 5 total attempts, which the old single-counter design (3 max)
        would have failed on.
        """
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        route = respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),  # rate limit #1
                httpx.Response(429, headers={"Retry-After": "0"}),  # rate limit #2
                httpx.Response(500),  # server error #1
                httpx.Response(500),  # server error #2
                httpx.Response(200, json={"total_count": 0, "items": []}),  # success
            ]
        )
        result = client.search_issues("test")
        assert route.call_count == 5
        assert result["total_count"] == 0


class TestPagination:
    @respx.mock
    def test_single_page(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1/files").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"filename": "a.py", "additions": 1, "deletions": 0, "status": "added"},
                ],
            )
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
            return_value=httpx.Response(
                200, json={"number": 42, "title": "Test", "state": "closed"}
            )
        )
        result = client.get_pr("org", "repo", 42)
        assert result["number"] == 42

    @respx.mock
    def test_get_pr_files(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1/files").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"filename": "a.py", "additions": 5, "deletions": 2, "status": "modified"},
                ],
            )
        )
        result = client.get_pr_files("org", "repo", 1)
        assert len(result) == 1
        assert result[0]["filename"] == "a.py"

    @respx.mock
    def test_get_pr_comments_merges_review_and_issue(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1/comments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "a"},
                        "body": "review comment",
                        "created_at": "2025-01-01T00:00:00Z",
                        "html_url": "u1",
                    },
                ],
            )
        )
        respx.get(f"{API_BASE}/repos/org/repo/issues/1/comments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "b"},
                        "body": "issue comment",
                        "created_at": "2025-01-01T00:00:00Z",
                        "html_url": "u2",
                    },
                ],
            )
        )
        result = client.get_pr_comments("org", "repo", 1)
        assert len(result) == 2

    @respx.mock
    def test_get_pr_reviews(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1/reviews").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "r"},
                        "state": "APPROVED",
                        "body": "",
                        "submitted_at": "2025-01-01T00:00:00Z",
                        "html_url": "u",
                    },
                ],
            )
        )
        result = client.get_pr_reviews("org", "repo", 1)
        assert len(result) == 1
        assert result[0]["state"] == "APPROVED"


class TestCommitEndpoints:
    @respx.mock
    def test_search_commits_with_accept_header(self, client):
        route = respx.get(f"{API_BASE}/search/commits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "items": [{"sha": "abc123"}],
                },
            )
        )
        result = client.search_commits("author:user committer-date:2025-02-16")
        assert result["total_count"] == 1
        assert route.called
        request = route.calls[0].request
        assert "cloak-preview" in request.headers.get("Accept", "")

    @respx.mock
    def test_search_commits_passes_pagination(self, client):
        route = respx.get(f"{API_BASE}/search/commits").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )
        client.search_commits("test", page=2, per_page=50)
        request = route.calls[0].request
        assert "page=2" in str(request.url)
        assert "per_page=50" in str(request.url)

    @respx.mock
    def test_get_commit(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/commits/abc123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "sha": "abc123",
                    "commit": {"message": "fix bug"},
                    "html_url": "https://example.com/commit/abc123",
                },
            )
        )
        result = client.get_commit("org", "repo", "abc123")
        assert result["sha"] == "abc123"


class TestIssueEndpoints:
    @respx.mock
    def test_get_issue(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/issues/10").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 10,
                    "title": "Bug report",
                    "state": "open",
                },
            )
        )
        result = client.get_issue("org", "repo", 10)
        assert result["number"] == 10
        assert result["title"] == "Bug report"

    @respx.mock
    def test_get_issue_comments(self, client):
        respx.get(f"{API_BASE}/repos/org/repo/issues/10/comments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "user1"},
                        "body": "comment",
                        "created_at": "2025-01-01T00:00:00Z",
                        "html_url": "u1",
                    },
                ],
            )
        )
        result = client.get_issue_comments("org", "repo", 10)
        assert len(result) == 1
        assert result[0]["user"]["login"] == "user1"

    @respx.mock
    def test_get_issue_comments_pagination(self, client):
        page1 = [{"id": i} for i in range(100)]
        page2 = [{"id": i} for i in range(100, 120)]
        respx.get(f"{API_BASE}/repos/org/repo/issues/10/comments").mock(
            side_effect=[
                httpx.Response(200, json=page1),
                httpx.Response(200, json=page2),
            ]
        )
        result = client.get_issue_comments("org", "repo", 10)
        assert len(result) == 120


class TestExtraHeaders:
    @respx.mock
    def test_extra_headers_passed_to_request(self, client):
        """extra_headers가 _request_with_retry에 전달된다."""
        route = respx.get(f"{API_BASE}/search/commits").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )
        client.search_commits("test")
        request = route.calls[0].request
        assert "cloak-preview" in request.headers.get("Accept", "")

    @respx.mock
    def test_no_extra_headers_uses_default(self, client):
        """extra_headers 없으면 기본 Accept 헤더 사용."""
        route = respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )
        client.search_issues("test")
        request = route.calls[0].request
        assert "v3+json" in request.headers.get("Accept", "")


class TestRateLimitRetry403:
    @respx.mock
    def test_retries_on_403_rate_limit(self, client, monkeypatch):
        """403 with 'rate limit' in body → retry → success."""
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        route = respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(
                    403,
                    json={"message": "API rate limit exceeded"},
                ),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        result = client.search_issues("test")
        assert route.call_count == 2
        assert result["total_count"] == 0

    @respx.mock
    def test_403_rate_limit_uses_retry_after_header(self, client, monkeypatch):
        """Respects Retry-After header on 403 rate limit."""
        sleep_values = []
        monkeypatch.setattr(
            "workrecap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(
                    403,
                    headers={"Retry-After": "10"},
                    json={"message": "API rate limit exceeded"},
                ),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        client.search_issues("test")
        assert sleep_values[0] == 10.0

    @respx.mock
    def test_403_rate_limit_default_retry_after(self, client, monkeypatch):
        """Without Retry-After header on 403, uses exponential backoff via _compute_rate_limit_wait."""
        sleep_values = []
        monkeypatch.setattr(
            "workrecap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(403, json={"message": "rate limit exceeded"}),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        client.search_issues("test")
        # attempt=0 → 2^0 = 1.0s exponential backoff
        assert sleep_values[0] == 1.0

    @respx.mock
    def test_403_rate_limit_exhausts_retries(self, client, monkeypatch):
        """All attempts return 403 rate limit → raises FetchError after RATE_LIMIT_MAX_RETRIES."""
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        route = respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(403, json={"message": "API rate limit exceeded"})
        )
        with pytest.raises(FetchError, match="Rate limit exceeded"):
            client.search_issues("test")
        assert route.call_count == RATE_LIMIT_MAX_RETRIES + 1

    @respx.mock
    def test_403_permission_denied_no_retry(self, client):
        """403 without 'rate limit' → immediate fail, no retry."""
        route = respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(
                403, json={"message": "Resource not accessible by integration"}
            )
        )
        with pytest.raises(FetchError, match="Client error 403"):
            client.search_issues("test")
        assert route.call_count == 1

    @respx.mock
    def test_403_rate_limit_with_plain_text_body(self, client, monkeypatch):
        """Non-JSON body with 'rate limit' text still detected."""
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        route = respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(403, text="rate limit exceeded"),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        result = client.search_issues("test")
        assert route.call_count == 2
        assert result["total_count"] == 0


class TestRetryAfterHeader:
    @respx.mock
    def test_uses_retry_after_header(self, client, monkeypatch):
        sleep_values = []
        monkeypatch.setattr(
            "workrecap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "5"}),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        client.search_issues("test")
        assert sleep_values[0] == 5.0

    def test_get_retry_after_returns_header_value(self, client):
        """_get_retry_after parses Retry-After header as float."""
        resp = httpx.Response(429, headers={"Retry-After": "42"})
        assert client._get_retry_after(resp) == 42.0

    def test_get_retry_after_returns_none_when_missing(self, client):
        """_get_retry_after returns None when no Retry-After header present.

        This enables callers to fall through to smarter strategies
        (X-RateLimit-Reset or exponential backoff) instead of a fixed 60s.
        """
        resp = httpx.Response(429)
        assert client._get_retry_after(resp) is None

    def test_get_retry_after_returns_none_on_invalid_value(self, client):
        """_get_retry_after returns None for non-numeric Retry-After header."""
        resp = httpx.Response(429, headers={"Retry-After": "not-a-number"})
        assert client._get_retry_after(resp) is None

    @respx.mock
    def test_default_retry_after_when_missing(self, client, monkeypatch):
        """Without Retry-After header, uses _compute_rate_limit_wait (exponential backoff)."""
        sleep_values = []
        monkeypatch.setattr(
            "workrecap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )
        # jitter = 1.0 → no randomization, so we can assert exact values
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        client.search_issues("test")
        # attempt=0 → 2^0 = 1.0s (exponential backoff tier 3)
        assert sleep_values[0] == 1.0


class TestComputeRateLimitWait:
    """Tests for _compute_rate_limit_wait: three-tier wait strategy.

    Strategy priority:
    1. Retry-After header → exact server-specified wait
    2. X-RateLimit-Reset → compute seconds until reset window
    3. Exponential backoff min(2^attempt, 300s) → safe fallback
    All results get ±25% jitter to prevent thundering herd.
    """

    def test_uses_retry_after_header_when_present(self, client, monkeypatch):
        """Tier 1: Retry-After header takes priority over everything else."""
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        resp = httpx.Response(429, headers={"Retry-After": "30"})
        wait = client._compute_rate_limit_wait(resp, attempt=0)
        assert wait == 30.0  # jitter factor 1.0 → no change

    def test_uses_ratelimit_reset_header_when_no_retry_after(self, client, monkeypatch):
        """Tier 2: X-RateLimit-Reset timestamp when no Retry-After."""
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        monkeypatch.setattr("workrecap.infra.ghes_client.time.time", lambda: 1000.0)
        resp = httpx.Response(429, headers={"X-RateLimit-Reset": "1060"})
        wait = client._compute_rate_limit_wait(resp, attempt=0)
        # 1060 - 1000 + 1 buffer = 61, jitter 1.0
        assert wait == 61.0

    def test_uses_exponential_backoff_when_no_headers(self, client, monkeypatch):
        """Tier 3: Exponential backoff min(2^attempt, 300) when no headers."""
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        resp = httpx.Response(429)
        # attempt 0 → 2^0 = 1s, attempt 3 → 2^3 = 8s, attempt 8 → min(256, 300) = 256s
        assert client._compute_rate_limit_wait(resp, attempt=0) == 1.0
        assert client._compute_rate_limit_wait(resp, attempt=3) == 8.0
        assert client._compute_rate_limit_wait(resp, attempt=8) == 256.0

    def test_exponential_backoff_caps_at_300s(self, client, monkeypatch):
        """Backoff never exceeds RATE_LIMIT_BACKOFF_MAX (300s = 5 min)."""
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: 1.0)
        resp = httpx.Response(429)
        # attempt 9 → 2^9 = 512 > 300 → capped at 300
        assert client._compute_rate_limit_wait(resp, attempt=9) == 300.0

    def test_jitter_applied(self, client, monkeypatch):
        """Jitter ±25% randomizes the wait to prevent thundering herd."""
        # jitter_factor = 0.75 (lower bound of ±25%)
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: a)
        resp = httpx.Response(429, headers={"Retry-After": "100"})
        wait = client._compute_rate_limit_wait(resp, attempt=0)
        assert wait == pytest.approx(75.0)  # 100 * 0.75

    def test_minimum_wait_is_1_second(self, client, monkeypatch):
        """Wait time never drops below 1 second even with jitter."""
        monkeypatch.setattr("workrecap.infra.ghes_client.random.uniform", lambda a, b: a)
        resp = httpx.Response(429, headers={"Retry-After": "0.5"})
        wait = client._compute_rate_limit_wait(resp, attempt=0)
        assert wait >= 1.0


class TestRateLimitJitter:
    """Verify jitter is applied during actual rate limit retries, not just in isolation."""

    @respx.mock
    def test_jitter_varies_sleep_times_across_retries(self, client, monkeypatch):
        """With real random, consecutive rate limit waits are not identical.

        This is crucial for parallel workers (GHESClientPool) to avoid
        thundering herd: all workers waking up at the exact same time.
        """
        sleep_values = []
        monkeypatch.setattr(
            "workrecap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )
        # Use real random (don't mock it) — jitter should vary
        respx.get(f"{API_BASE}/search/issues").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "10"}),
                httpx.Response(429, headers={"Retry-After": "10"}),
                httpx.Response(200, json={"total_count": 0, "items": []}),
            ]
        )
        client.search_issues("test")
        # Both waits should be in the jitter range [7.5, 12.5] (10 ± 25%)
        assert len(sleep_values) == 2
        for v in sleep_values:
            assert 7.5 <= v <= 12.5, f"Wait {v} outside jitter range [7.5, 12.5]"


class TestSearchThrottle:
    @respx.mock
    def test_search_throttle_delays_between_calls(self, monkeypatch):
        """2nd search_issues call sleeps for the interval."""
        clock = [1000.0]
        sleep_values = []

        def fake_monotonic():
            return clock[0]

        def fake_sleep(v):
            sleep_values.append(v)
            clock[0] += v

        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", fake_monotonic)
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", fake_sleep)

        c = GHESClient(BASE_URL, "test-token", search_interval=2.0)
        respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )

        c.search_issues("test")
        c.search_issues("test")
        c.close()

        # First call: no throttle sleep. Second call: should sleep ~2.0s
        assert any(v == pytest.approx(2.0) for v in sleep_values)

    @respx.mock
    def test_no_throttle_on_first_search_call(self, monkeypatch):
        """1st call doesn't sleep for throttle."""
        sleep_values = []
        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", lambda: 100.0)
        monkeypatch.setattr(
            "workrecap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )

        c = GHESClient(BASE_URL, "test-token", search_interval=2.0)
        respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )

        c.search_issues("test")
        c.close()

        assert sleep_values == []

    @respx.mock
    def test_throttle_applies_to_search_commits(self, monkeypatch):
        """search_commits is also throttled."""
        clock = [1000.0]
        sleep_values = []

        def fake_monotonic():
            return clock[0]

        def fake_sleep(v):
            sleep_values.append(v)
            clock[0] += v

        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", fake_monotonic)
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", fake_sleep)

        c = GHESClient(BASE_URL, "test-token", search_interval=2.0)
        respx.get(f"{API_BASE}/search/commits").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )

        c.search_commits("test")
        c.search_commits("test")
        c.close()

        assert any(v == pytest.approx(2.0) for v in sleep_values)

    @respx.mock
    def test_no_throttle_on_rest_api_calls(self, monkeypatch):
        """get_pr is NOT throttled."""
        sleep_values = []
        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", lambda: 0.0)
        monkeypatch.setattr(
            "workrecap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )

        c = GHESClient(BASE_URL, "test-token", search_interval=2.0)
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1").mock(
            return_value=httpx.Response(200, json={"number": 1})
        )

        c.get_pr("org", "repo", 1)
        c.get_pr("org", "repo", 1)
        c.close()

        assert sleep_values == []

    @respx.mock
    def test_throttle_cross_method(self, monkeypatch):
        """search_issues then search_commits → throttled."""
        clock = [1000.0]
        sleep_values = []

        def fake_monotonic():
            return clock[0]

        def fake_sleep(v):
            sleep_values.append(v)
            clock[0] += v

        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", fake_monotonic)
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", fake_sleep)

        c = GHESClient(BASE_URL, "test-token", search_interval=2.0)
        respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )
        respx.get(f"{API_BASE}/search/commits").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )

        c.search_issues("test")
        c.search_commits("test")
        c.close()

        assert any(v == pytest.approx(2.0) for v in sleep_values)

    @respx.mock
    def test_throttle_zero_interval(self, monkeypatch):
        """search_interval=0 disables throttle."""
        sleep_values = []
        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", lambda: 0.0)
        monkeypatch.setattr(
            "workrecap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )

        c = GHESClient(BASE_URL, "test-token", search_interval=0)
        respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )

        c.search_issues("test")
        c.search_issues("test")
        c.close()

        assert sleep_values == []

    @respx.mock
    def test_throttle_sufficient_elapsed_time(self, monkeypatch):
        """Enough natural time passed → no sleep."""
        clock = [1000.0]
        sleep_values = []

        def fake_monotonic():
            return clock[0]

        def fake_sleep(v):
            sleep_values.append(v)
            clock[0] += v

        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", fake_monotonic)
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", fake_sleep)

        c = GHESClient(BASE_URL, "test-token", search_interval=2.0)
        respx.get(f"{API_BASE}/search/issues").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )

        c.search_issues("test")
        clock[0] = 1005.0  # 5s elapsed > 2s interval
        c.search_issues("test")
        c.close()

        assert sleep_values == []


class TestAdaptiveRateLimit:
    @respx.mock
    def test_tracks_rate_limit_headers(self, monkeypatch):
        """Rate limit remaining is tracked from response headers."""
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        c = GHESClient(BASE_URL, "test-token", search_interval=0)
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1").mock(
            return_value=httpx.Response(
                200,
                json={"number": 1},
                headers={
                    "X-RateLimit-Remaining": "500",
                    "X-RateLimit-Reset": "1700000000",
                },
            )
        )
        c.get_pr("org", "repo", 1)
        assert c._rate_limit_remaining == 500
        assert c._rate_limit_reset == 1700000000
        c.close()

    @respx.mock
    def test_warns_when_remaining_low(self, monkeypatch, caplog):
        """Logs warning when remaining < 100."""
        import logging

        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", lambda _: None)
        c = GHESClient(BASE_URL, "test-token", search_interval=0)
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1").mock(
            return_value=httpx.Response(
                200,
                json={"number": 1},
                headers={
                    "X-RateLimit-Remaining": "50",
                    "X-RateLimit-Reset": "1700000000",
                },
            )
        )
        with caplog.at_level(logging.WARNING, logger="workrecap.infra.ghes_client"):
            c.get_pr("org", "repo", 1)
        assert any("rate limit" in r.message.lower() for r in caplog.records)
        c.close()

    @respx.mock
    def test_waits_when_remaining_critical(self, monkeypatch):
        """Sleeps until reset when remaining < 10."""
        sleep_values = []

        def fake_sleep(v):
            sleep_values.append(v)

        def fake_time():
            return 1700000000 - 5  # 5 seconds before reset

        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", fake_sleep)
        monkeypatch.setattr("workrecap.infra.ghes_client.time.time", fake_time)
        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", lambda: 1000.0)

        c = GHESClient(BASE_URL, "test-token", search_interval=0)
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1").mock(
            return_value=httpx.Response(
                200,
                json={"number": 1},
                headers={
                    "X-RateLimit-Remaining": "5",
                    "X-RateLimit-Reset": "1700000000",
                },
            )
        )

        c.get_pr("org", "repo", 1)
        # Should have waited for ~6 seconds (5 + 1 buffer)
        assert any(v >= 5 for v in sleep_values)
        c.close()

    @respx.mock
    def test_no_wait_when_remaining_sufficient(self, monkeypatch):
        """No wait when remaining > 100."""
        sleep_values = []
        monkeypatch.setattr(
            "workrecap.infra.ghes_client.time.sleep",
            lambda v: sleep_values.append(v),
        )
        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", lambda: 1000.0)

        c = GHESClient(BASE_URL, "test-token", search_interval=0)
        respx.get(f"{API_BASE}/repos/org/repo/pulls/1").mock(
            return_value=httpx.Response(
                200,
                json={"number": 1},
                headers={
                    "X-RateLimit-Remaining": "4000",
                    "X-RateLimit-Reset": "1700000000",
                },
            )
        )
        c.get_pr("org", "repo", 1)
        assert sleep_values == []
        c.close()


class TestThreadSafeThrottle:
    def test_concurrent_search_calls_serialized(self, monkeypatch):
        """3 threads calling search concurrently should be serialized by lock."""
        import threading

        sleep_times = []
        clock = [1000.0]
        clock_lock = threading.Lock()

        def fake_monotonic():
            with clock_lock:
                return clock[0]

        def fake_sleep(v):
            sleep_times.append(v)
            with clock_lock:
                clock[0] += v

        monkeypatch.setattr("workrecap.infra.ghes_client.time.monotonic", fake_monotonic)
        monkeypatch.setattr("workrecap.infra.ghes_client.time.sleep", fake_sleep)

        c = GHESClient(BASE_URL, "test-token", search_interval=2.0)

        # Simulate that _request_with_retry just returns immediately
        c._request_with_retry = lambda *a, **kw: {"total_count": 0, "items": []}

        errors = []

        def call_search():
            try:
                c.search_issues("test")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_search) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        c.close()

        assert not errors
        # After 3 calls: first call no sleep, 2nd and 3rd should sleep
        assert len(sleep_times) >= 2
