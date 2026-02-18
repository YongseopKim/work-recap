"""GHES REST API v3 HTTP client with retry and rate limit handling."""

import logging
import threading
import time

import httpx

from workrecap.exceptions import FetchError

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 2.0
REQUEST_TIMEOUT = 30.0


class GHESClient:
    """GHES REST API v3 HTTP client with retry and rate limit handling."""

    def __init__(self, base_url: str, token: str, *, search_interval: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        if "api.github.com" in self._base_url:
            self._api_base = self._base_url
        else:
            self._api_base = f"{self._base_url}/api/v3"
        self._client = httpx.Client(
            base_url=self._api_base,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        self._search_interval = search_interval
        self._last_search_time: float = 0.0
        self._throttle_lock = threading.Lock()
        self._rate_limit_remaining: int | None = None
        self._rate_limit_reset: int | None = None
        self._rate_limit_lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Public API ──

    def search_issues(self, query: str, page: int = 1, per_page: int = 30) -> dict:
        """Search API 호출. 429 시 자동 retry with backoff."""
        self._throttle_search()
        return self._request_with_retry(
            "GET",
            "/search/issues",
            params={"q": query, "page": page, "per_page": per_page},
        )

    def get_pr(self, owner: str, repo: str, number: int) -> dict:
        """PR 상세 정보 조회."""
        return self._request_with_retry("GET", f"/repos/{owner}/{repo}/pulls/{number}")

    def get_pr_files(self, owner: str, repo: str, number: int) -> list[dict]:
        """PR 변경 파일 목록. 페이지네이션 포함."""
        return self._paginate(f"/repos/{owner}/{repo}/pulls/{number}/files")

    def get_pr_comments(self, owner: str, repo: str, number: int) -> list[dict]:
        """PR review comments + issue comments 통합."""
        review_comments = self._paginate(f"/repos/{owner}/{repo}/pulls/{number}/comments")
        issue_comments = self._paginate(f"/repos/{owner}/{repo}/issues/{number}/comments")
        return review_comments + issue_comments

    def get_pr_reviews(self, owner: str, repo: str, number: int) -> list[dict]:
        """PR 리뷰 목록."""
        return self._paginate(f"/repos/{owner}/{repo}/pulls/{number}/reviews")

    def search_commits(self, query: str, page: int = 1, per_page: int = 30) -> dict:
        """Commit Search API 호출. cloak-preview Accept 헤더 필요."""
        self._throttle_search()
        return self._request_with_retry(
            "GET",
            "/search/commits",
            params={"q": query, "page": page, "per_page": per_page},
            extra_headers={"Accept": "application/vnd.github.cloak-preview+json"},
        )

    def get_commit(self, owner: str, repo: str, sha: str) -> dict:
        """Commit 상세 정보 조회."""
        return self._request_with_retry("GET", f"/repos/{owner}/{repo}/commits/{sha}")

    def get_issue(self, owner: str, repo: str, number: int) -> dict:
        """Issue 상세 정보 조회."""
        return self._request_with_retry("GET", f"/repos/{owner}/{repo}/issues/{number}")

    def get_issue_comments(self, owner: str, repo: str, number: int) -> list[dict]:
        """Issue 코멘트 목록. 페이지네이션 포함."""
        return self._paginate(f"/repos/{owner}/{repo}/issues/{number}/comments")

    # ── Internal ──

    def _throttle_search(self) -> None:
        """Rate-limit Search API calls to stay under 30 req/min.

        Thread-safe: uses a lock to serialize concurrent search calls.
        """
        if self._search_interval <= 0:
            return
        with self._throttle_lock:
            now = time.monotonic()
            elapsed = now - self._last_search_time
            if self._last_search_time > 0 and elapsed < self._search_interval:
                wait = self._search_interval - elapsed
                logger.debug("Search throttle: sleeping %.1fs", wait)
                time.sleep(wait)
            self._last_search_time = time.monotonic()

    def _request_with_retry(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict | list:
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.debug("Request: %s %s params=%s", method, path, params)
                response = self._client.request(
                    method,
                    path,
                    params=params,
                    headers=extra_headers,
                )
                logger.debug("Response: %s %s → %d", method, path, response.status_code)

                if response.status_code == 429:
                    retry_after = self._get_retry_after(response)
                    logger.warning(
                        "Rate limited (429). Retry after %.1fs (attempt %d/%d)",
                        retry_after,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    if attempt < MAX_RETRIES:
                        time.sleep(retry_after)
                        continue
                    raise FetchError(f"Rate limit exceeded after {MAX_RETRIES} retries: {path}")

                if response.status_code == 403 and self._is_rate_limit_403(response):
                    retry_after = self._get_retry_after(response)
                    logger.warning(
                        "Rate limited (403). Retry after %.1fs (attempt %d/%d)",
                        retry_after,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    if attempt < MAX_RETRIES:
                        time.sleep(retry_after)
                        continue
                    raise FetchError(f"Rate limit exceeded after {MAX_RETRIES} retries: {path}")

                if response.status_code >= 500:
                    logger.warning(
                        "Server error %d on %s (attempt %d/%d)",
                        response.status_code,
                        path,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    if attempt < MAX_RETRIES:
                        time.sleep(BACKOFF_BASE**attempt)
                        continue
                    raise FetchError(
                        f"Server error {response.status_code} after {MAX_RETRIES} retries: {path}"
                    )

                if response.status_code >= 400:
                    raise FetchError(
                        f"Client error {response.status_code}: {path} - {response.text}"
                    )

                self._track_rate_limit(response)
                return response.json()

            except httpx.HTTPError as e:
                last_error = e
                logger.warning(
                    "HTTP error on %s (attempt %d/%d): %s",
                    path,
                    attempt + 1,
                    MAX_RETRIES,
                    e,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_BASE**attempt)
                    continue

        raise FetchError(f"Request failed after {MAX_RETRIES} retries: {path}") from last_error

    def _track_rate_limit(self, response: httpx.Response) -> None:
        """Track X-RateLimit-Remaining/Reset from response headers.

        Warns when approaching limit, sleeps when critically low.
        """
        remaining_str = response.headers.get("X-RateLimit-Remaining")
        reset_str = response.headers.get("X-RateLimit-Reset")
        if remaining_str is None:
            return

        try:
            remaining = int(remaining_str)
        except (ValueError, TypeError):
            return

        reset_ts: int | None = None
        if reset_str:
            try:
                reset_ts = int(reset_str)
            except (ValueError, TypeError):
                pass

        with self._rate_limit_lock:
            self._rate_limit_remaining = remaining
            self._rate_limit_reset = reset_ts

        if remaining < 10:
            if reset_ts is not None:
                wait = max(0, reset_ts - time.time()) + 1
                logger.warning(
                    "Rate limit critical: %d remaining, waiting %.0fs until reset",
                    remaining,
                    wait,
                )
                time.sleep(wait)
            else:
                logger.warning("Rate limit critical: %d remaining, no reset header", remaining)
        elif remaining < 100:
            logger.warning("Rate limit low: %d remaining", remaining)

    @staticmethod
    def _is_rate_limit_403(response: httpx.Response) -> bool:
        """Detect GitHub 403 responses that indicate rate limiting."""
        return "rate limit" in response.text.lower()

    def _get_retry_after(self, response: httpx.Response) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return 60.0

    def _paginate(self, path: str, per_page: int = 100) -> list[dict]:
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
                all_items.append(response_data)
                break

        logger.debug("Paginate %s → %d items (%d pages)", path, len(all_items), page)
        return all_items
