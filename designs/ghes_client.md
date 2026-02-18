# Phase 1-1: GHESClient 상세 설계

## 목적

GHES REST API v3 호출을 담당하는 HTTP 클라이언트.
retry, rate limit 대응, pagination 등 HTTP 수준의 관심사를 캡슐화하여
FetcherService가 비즈니스 로직에만 집중할 수 있게 한다.

---

## 위치

`src/workrecap/infra/ghes_client.py`

## 의존성

- `httpx` (HTTP client)
- `time` (backoff sleep)
- `logging`
- `workrecap.exceptions.FetchError`

---

## 상세 구현

```python
import logging
import time

import httpx

from workrecap.exceptions import FetchError

logger = logging.getLogger(__name__)

# 상수
MAX_RETRIES = 3
BACKOFF_BASE = 2.0          # seconds, exponential backoff base
SEARCH_RATE_LIMIT = 30      # req/min for search API
REQUEST_TIMEOUT = 30.0      # seconds


class GHESClient:
    """GHES REST API v3 HTTP client with retry and rate limit handling."""

    def __init__(self, base_url: str, token: str) -> None:
        """
        Args:
            base_url: GHES 인스턴스 URL (e.g., "https://github.example.com")
            token: Personal Access Token
        """
        self._base_url = base_url.rstrip("/")
        self._api_base = f"{self._base_url}/api/v3"
        self._client = httpx.Client(
            base_url=self._api_base,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=REQUEST_TIMEOUT,
        )

    def close(self) -> None:
        """HTTP client 종료."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Public API ──

    def search_issues(self, query: str, page: int = 1, per_page: int = 30) -> dict:
        """
        Search API 호출.

        Args:
            query: 검색 쿼리 (e.g., "type:pr author:user updated:2025-02-16")
            page: 페이지 번호 (1-based)
            per_page: 페이지당 결과 수 (max 100)

        Returns:
            {"total_count": int, "items": list[dict]}

        Raises:
            FetchError: API 호출 실패 시 (retry 소진 후)
        """
        return self._request_with_retry(
            "GET",
            "/search/issues",
            params={"q": query, "page": page, "per_page": per_page},
        )

    def get_pr(self, owner: str, repo: str, number: int) -> dict:
        """PR 상세 정보 조회."""
        return self._request_with_retry(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}"
        )

    def get_pr_files(self, owner: str, repo: str, number: int) -> list[dict]:
        """PR 변경 파일 목록. 페이지네이션 포함."""
        return self._paginate(f"/repos/{owner}/{repo}/pulls/{number}/files")

    def get_pr_comments(self, owner: str, repo: str, number: int) -> list[dict]:
        """PR review comments + issue comments 통합."""
        # review comments (코드 라인 코멘트)
        review_comments = self._paginate(
            f"/repos/{owner}/{repo}/pulls/{number}/comments"
        )
        # issue comments (일반 코멘트)
        issue_comments = self._paginate(
            f"/repos/{owner}/{repo}/issues/{number}/comments"
        )
        return review_comments + issue_comments

    def get_pr_reviews(self, owner: str, repo: str, number: int) -> list[dict]:
        """PR 리뷰 목록."""
        return self._paginate(f"/repos/{owner}/{repo}/pulls/{number}/reviews")

    def search_commits(self, query: str, page: int = 1, per_page: int = 30) -> dict:
        """
        Commit Search API 호출. cloak-preview Accept 헤더 필요.

        Args:
            query: 검색 쿼리 (e.g., "author:user committer-date:2025-02-16")
            page: 페이지 번호 (1-based)
            per_page: 페이지당 결과 수 (max 100)

        Returns:
            {"total_count": int, "items": list[dict]}
        """
        return self._request_with_retry(
            "GET",
            "/search/commits",
            params={"q": query, "page": page, "per_page": per_page},
            extra_headers={"Accept": "application/vnd.github.cloak-preview+json"},
        )

    def get_commit(self, owner: str, repo: str, sha: str) -> dict:
        """Commit 상세 정보 조회."""
        return self._request_with_retry(
            "GET", f"/repos/{owner}/{repo}/commits/{sha}"
        )

    def get_issue(self, owner: str, repo: str, number: int) -> dict:
        """Issue 상세 정보 조회."""
        return self._request_with_retry(
            "GET", f"/repos/{owner}/{repo}/issues/{number}"
        )

    def get_issue_comments(self, owner: str, repo: str, number: int) -> list[dict]:
        """Issue 코멘트 목록. 페이지네이션 포함."""
        return self._paginate(f"/repos/{owner}/{repo}/issues/{number}/comments")

    # ── Internal ──

    def _request_with_retry(
        self, method: str, path: str, params: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict | list:
        """
        HTTP 요청 + retry (429/5xx 시 exponential backoff).

        최대 MAX_RETRIES 회 재시도. 모두 실패하면 FetchError 발생.
        """
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.request(
                    method, path, params=params, headers=extra_headers,
                )

                # Rate limit 처리: 429
                if response.status_code == 429:
                    retry_after = self._get_retry_after(response)
                    logger.warning(
                        "Rate limited (429). Retry after %.1fs (attempt %d/%d)",
                        retry_after, attempt + 1, MAX_RETRIES,
                    )
                    if attempt < MAX_RETRIES:
                        time.sleep(retry_after)
                        continue
                    raise FetchError(
                        f"Rate limit exceeded after {MAX_RETRIES} retries: {path}"
                    )

                # 5xx 서버 에러: retry
                if response.status_code >= 500:
                    logger.warning(
                        "Server error %d on %s (attempt %d/%d)",
                        response.status_code, path, attempt + 1, MAX_RETRIES,
                    )
                    if attempt < MAX_RETRIES:
                        time.sleep(BACKOFF_BASE ** attempt)
                        continue
                    raise FetchError(
                        f"Server error {response.status_code} after {MAX_RETRIES} retries: {path}"
                    )

                # 4xx 클라이언트 에러 (429 제외): 즉시 실패
                if response.status_code >= 400:
                    raise FetchError(
                        f"Client error {response.status_code}: {path} - {response.text}"
                    )

                return response.json()

            except httpx.HTTPError as e:
                last_error = e
                logger.warning(
                    "HTTP error on %s (attempt %d/%d): %s",
                    path, attempt + 1, MAX_RETRIES, e,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_BASE ** attempt)
                    continue

        raise FetchError(f"Request failed after {MAX_RETRIES} retries: {path}") from last_error

    def _get_retry_after(self, response: httpx.Response) -> float:
        """429 응답에서 retry-after 시간(초) 추출. 없으면 기본 60초."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return 60.0

    def _paginate(self, path: str, per_page: int = 100) -> list[dict]:
        """
        Link header 기반 자동 페이지네이션.
        모든 페이지의 결과를 합산하여 반환.
        """
        all_items: list[dict] = []
        page = 1

        while True:
            response_data = self._request_with_retry(
                "GET", path, params={"page": page, "per_page": per_page}
            )

            if isinstance(response_data, list):
                if not response_data:
                    break
                all_items.extend(response_data)
                if len(response_data) < per_page:
                    break
                page += 1
            else:
                # dict 응답이면 단일 페이지
                all_items.append(response_data)
                break

        return all_items
```

---

## Rate Limit 전략

| API 종류 | Rate Limit | 대응 |
|---|---|---|
| Search API (issues, commits) | 30 req/min | 429 응답 시 Retry-After 헤더 참조 후 대기 |
| Core API (PR, commit, issue, files 등) | 5000 req/hr | 대부분 충분. 429 시 동일 backoff |
| 5xx 에러 | - | Exponential backoff (2^attempt초) |

---

## 테스트 명세

### test_ghes_client.py

`respx`를 사용하여 httpx 요청을 mock한다.

```python
"""tests/unit/test_ghes_client.py"""

class TestGHESClientInit:
    def test_creates_client_with_auth_header(self):
        """Authorization header가 올바르게 설정된다."""
        with GHESClient("https://github.example.com", "test-token") as client:
            assert client._client.headers["Authorization"] == "token test-token"

    def test_base_url_trailing_slash_stripped(self):
        """base_url 끝의 /가 제거된다."""
        with GHESClient("https://github.example.com/", "t") as client:
            assert client._api_base == "https://github.example.com/api/v3"

class TestSearchIssues:
    def test_returns_search_results(self, respx_mock):
        """정상 Search API 응답 파싱."""

    def test_passes_query_and_pagination_params(self, respx_mock):
        """query, page, per_page 파라미터가 전달된다."""

class TestRetry:
    def test_retries_on_429(self, respx_mock):
        """429 응답 시 retry 후 성공."""

    def test_retries_on_500(self, respx_mock):
        """500 응답 시 retry 후 성공."""

    def test_raises_fetch_error_after_max_retries(self, respx_mock):
        """MAX_RETRIES 초과 시 FetchError 발생."""

    def test_no_retry_on_4xx(self, respx_mock):
        """4xx (429 제외) 시 즉시 FetchError."""

    def test_retries_on_httpx_error(self, respx_mock):
        """네트워크 에러 시 retry 후 성공."""

class TestPagination:
    def test_single_page(self, respx_mock):
        """단일 페이지 결과."""

    def test_multi_page(self, respx_mock):
        """여러 페이지 결과 합산."""

    def test_empty_result(self, respx_mock):
        """빈 결과."""

class TestPREndpoints:
    def test_get_pr(self, respx_mock):
        """PR 상세 조회."""

    def test_get_pr_files(self, respx_mock):
        """PR 파일 목록 조회."""

    def test_get_pr_comments_merges_review_and_issue(self, respx_mock):
        """review comments + issue comments 통합."""

    def test_get_pr_reviews(self, respx_mock):
        """PR 리뷰 목록 조회."""

class TestCommitEndpoints:
    def test_search_commits(self, respx_mock):
        """Commit Search API 호출 + cloak-preview Accept 헤더."""

    def test_search_commits_pagination(self, respx_mock):
        """query, page, per_page 파라미터가 전달된다."""

    def test_get_commit(self, respx_mock):
        """Commit 상세 조회."""

class TestIssueEndpoints:
    def test_get_issue(self, respx_mock):
        """Issue 상세 조회."""

    def test_get_issue_comments(self, respx_mock):
        """Issue 코멘트 목록 조회 (페이지네이션 포함)."""

class TestContextManager:
    def test_closes_client(self):
        """with 문 종료 시 client가 닫힌다."""
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 1.1.1 | GHESClient 기본 구조 (init, close, context manager) | TestGHESClientInit, TestContextManager |
| 1.1.2 | `_request_with_retry()` — 정상 요청 | TestSearchIssues |
| 1.1.3 | `_request_with_retry()` — 429/5xx retry + 4xx 즉시 실패 | TestRetry |
| 1.1.4 | `_paginate()` — 페이지네이션 | TestPagination |
| 1.1.5 | PR 엔드포인트 메서드 (get_pr, get_pr_files, get_pr_comments, get_pr_reviews) | TestPREndpoints |
| 1.1.6 | `_request_with_retry()` — extra_headers 파라미터 지원 | TestCommitEndpoints |
| 1.1.7 | Commit 엔드포인트 (search_commits, get_commit) | TestCommitEndpoints |
| 1.1.8 | Issue 엔드포인트 (get_issue, get_issue_comments) | TestIssueEndpoints |
