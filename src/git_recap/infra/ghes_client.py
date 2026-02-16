"""GHES REST API v3 HTTP client with retry and rate limit handling."""

import logging
import time

import httpx

from git_recap.exceptions import FetchError

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 2.0
REQUEST_TIMEOUT = 30.0


class GHESClient:
    """GHES REST API v3 HTTP client with retry and rate limit handling."""

    def __init__(self, base_url: str, token: str) -> None:
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

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Public API ──

    def search_issues(self, query: str, page: int = 1, per_page: int = 30) -> dict:
        """Search API 호출. 429 시 자동 retry with backoff."""
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
        review_comments = self._paginate(
            f"/repos/{owner}/{repo}/pulls/{number}/comments"
        )
        issue_comments = self._paginate(
            f"/repos/{owner}/{repo}/issues/{number}/comments"
        )
        return review_comments + issue_comments

    def get_pr_reviews(self, owner: str, repo: str, number: int) -> list[dict]:
        """PR 리뷰 목록."""
        return self._paginate(f"/repos/{owner}/{repo}/pulls/{number}/reviews")

    # ── Internal ──

    def _request_with_retry(
        self, method: str, path: str, params: dict | None = None
    ) -> dict | list:
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.request(method, path, params=params)

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

        raise FetchError(
            f"Request failed after {MAX_RETRIES} retries: {path}"
        ) from last_error

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

        return all_items
