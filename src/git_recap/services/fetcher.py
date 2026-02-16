"""GHES PR 활동 수집 서비스."""

import json
import logging
import re
from pathlib import Path

from git_recap.config import AppConfig
from git_recap.exceptions import FetchError
from git_recap.infra.ghes_client import GHESClient
from git_recap.models import (
    Comment,
    FileChange,
    PRRaw,
    Review,
    save_json,
    load_json,
)

logger = logging.getLogger(__name__)

DEFAULT_NOISE_PATTERNS = [
    re.compile(r"^LGTM!?$", re.IGNORECASE),
    re.compile(r"^\+1$"),
    re.compile(r"^:shipit:$"),
    re.compile(r"^Ship it!?$", re.IGNORECASE),
]

BOT_SUFFIXES = ["[bot]", "-bot"]


class FetcherService:
    def __init__(self, config: AppConfig, ghes_client: GHESClient) -> None:
        self._config = config
        self._client = ghes_client
        self._username = config.username

    def fetch(self, target_date: str) -> Path:
        """
        지정 날짜의 PR 활동을 수집하여 파일로 저장.

        Args:
            target_date: "YYYY-MM-DD"

        Returns:
            저장된 파일 경로 (data/raw/{Y}/{M}/{D}/prs.json)
        """
        pr_map = self._search_prs(target_date)

        prs: list[PRRaw] = []
        for pr_api_url, pr_basic in pr_map.items():
            try:
                enriched = self._enrich(pr_basic)
                prs.append(enriched)
            except FetchError:
                logger.warning("Failed to enrich PR %s, skipping", pr_api_url)

        output_path = self._save(target_date, prs)
        self._update_checkpoint(target_date)

        logger.info("Fetched %d PRs for %s → %s", len(prs), target_date, output_path)
        return output_path

    # ── 3축 검색 + dedup ──

    def _search_prs(self, target_date: str) -> dict[str, dict]:
        """3축 쿼리로 PR 검색 후 API URL 기준 dedup."""
        axes = [
            f"author:{self._username}",
            f"reviewed-by:{self._username}",
            f"commenter:{self._username}",
        ]

        pr_map: dict[str, dict] = {}

        for qualifier in axes:
            query = f"type:pr {qualifier} updated:{target_date}"
            try:
                items = self._search_all_pages(query)
            except FetchError:
                if "reviewed-by" in qualifier:
                    logger.warning("reviewed-by qualifier not supported, skipping")
                    continue
                raise

            for item in items:
                api_url = item.get("pull_request", {}).get("url", item["url"])
                if api_url not in pr_map:
                    pr_map[api_url] = item

        return pr_map

    def _search_all_pages(self, query: str) -> list[dict]:
        """Search API 전체 페이지 수집."""
        all_items: list[dict] = []
        page = 1

        while True:
            result = self._client.search_issues(query, page=page, per_page=100)
            items = result.get("items", [])
            all_items.extend(items)

            if len(items) < 100:
                break
            page += 1

        return all_items

    # ── PR Enrich ──

    def _enrich(self, pr_basic: dict) -> PRRaw:
        """기본 PR 정보에 files, comments, reviews를 추가 수집."""
        pr_api_url = pr_basic.get("pull_request", {}).get("url", "")
        owner, repo, number = self._parse_pr_url(pr_api_url)

        pr_detail = self._client.get_pr(owner, repo, number)

        raw_files = self._client.get_pr_files(owner, repo, number)
        raw_comments = self._client.get_pr_comments(owner, repo, number)
        raw_reviews = self._client.get_pr_reviews(owner, repo, number)

        filtered_comments = [
            c for c in raw_comments if not self._is_noise_comment(c)
        ]
        filtered_reviews = [
            r for r in raw_reviews if not self._is_noise_review(r)
        ]

        return PRRaw(
            url=pr_detail["html_url"],
            api_url=pr_detail["url"],
            number=pr_detail["number"],
            title=pr_detail["title"],
            body=pr_detail.get("body") or "",
            state=pr_detail["state"],
            is_merged=pr_detail.get("merged", False),
            created_at=pr_detail["created_at"],
            updated_at=pr_detail["updated_at"],
            merged_at=pr_detail.get("merged_at"),
            repo=f"{owner}/{repo}",
            labels=[label["name"] for label in pr_detail.get("labels", [])],
            author=pr_detail["user"]["login"],
            files=[
                FileChange(
                    filename=f["filename"],
                    additions=f["additions"],
                    deletions=f["deletions"],
                    status=f["status"],
                )
                for f in raw_files
            ],
            comments=[
                Comment(
                    author=c["user"]["login"],
                    body=c.get("body") or "",
                    created_at=c["created_at"],
                    url=c["html_url"],
                )
                for c in filtered_comments
            ],
            reviews=[
                Review(
                    author=r["user"]["login"],
                    state=r["state"],
                    body=r.get("body") or "",
                    submitted_at=r["submitted_at"],
                    url=r["html_url"],
                )
                for r in filtered_reviews
            ],
        )

    @staticmethod
    def _parse_pr_url(api_url: str) -> tuple[str, str, int]:
        """PR API URL에서 owner, repo, number 추출."""
        parts = api_url.rstrip("/").split("/")
        pulls_idx = parts.index("pulls")
        owner = parts[pulls_idx - 2]
        repo = parts[pulls_idx - 1]
        number = int(parts[pulls_idx + 1])
        return owner, repo, number

    # ── 노이즈 필터링 ──

    @staticmethod
    def _is_bot_user(login: str) -> bool:
        login_lower = login.lower()
        return any(login_lower.endswith(suffix) for suffix in BOT_SUFFIXES)

    @staticmethod
    def _is_noise_comment(comment: dict) -> bool:
        author = comment.get("user", {}).get("login", "")
        if FetcherService._is_bot_user(author):
            return True

        body = (comment.get("body") or "").strip()
        if not body:
            return True

        return any(pattern.match(body) for pattern in DEFAULT_NOISE_PATTERNS)

    @staticmethod
    def _is_noise_review(review: dict) -> bool:
        author = review.get("user", {}).get("login", "")
        return FetcherService._is_bot_user(author)

    # ── 저장 ──

    def _save(self, target_date: str, prs: list[PRRaw]) -> Path:
        output_dir = self._config.date_raw_dir(target_date)
        output_path = output_dir / "prs.json"
        save_json(prs, output_path)
        return output_path

    def _update_checkpoint(self, target_date: str) -> None:
        cp_path = self._config.checkpoints_path
        cp_path.parent.mkdir(parents=True, exist_ok=True)

        checkpoints = {}
        if cp_path.exists():
            checkpoints = load_json(cp_path)

        checkpoints["last_fetch_date"] = target_date

        with open(cp_path, "w", encoding="utf-8") as f:
            json.dump(checkpoints, f, indent=2)
