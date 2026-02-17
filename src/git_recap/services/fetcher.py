"""GHES PR/Commit/Issue 활동 수집 서비스."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from git_recap.infra.client_pool import GHESClientPool
    from git_recap.services.daily_state import DailyStateStore
    from git_recap.services.fetch_progress import FetchProgressStore

from git_recap.config import AppConfig
from git_recap.exceptions import FetchError
from git_recap.infra.ghes_client import GHESClient
from git_recap.services.date_utils import date_range, monthly_chunks
from git_recap.models import (
    Comment,
    CommitRaw,
    FileChange,
    IssueRaw,
    PRRaw,
    Review,
    save_json,
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
    def __init__(
        self,
        config: AppConfig,
        ghes_client: GHESClient,
        daily_state: "DailyStateStore | None" = None,
        max_workers: int = 1,
        client_pool: "GHESClientPool | None" = None,
        progress_store: "FetchProgressStore | None" = None,
    ) -> None:
        self._config = config
        self._client = ghes_client
        self._username = config.username
        self._daily_state = daily_state
        self._max_workers = max_workers
        self._client_pool = client_pool
        self._progress_store = progress_store

    def fetch(
        self,
        target_date: str,
        types: set[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Path]:
        """
        지정 날짜의 PR/Commit/Issue 활동을 수집하여 파일로 저장.

        Args:
            target_date: "YYYY-MM-DD"
            types: 수집할 타입 {"prs", "commits", "issues"}. None이면 전부.
            progress: 진행 상황 콜백.

        Returns:
            타입별 저장 경로 dict (예: {"prs": Path, "commits": Path})
        """
        active = types or {"prs", "commits", "issues"}
        logger.info("Fetching %s for %s", ", ".join(sorted(active)), target_date)
        if progress:
            progress(f"Fetching {target_date}...")
        results: dict[str, Path] = {}

        if "prs" in active:
            pr_map = self._search_prs(target_date)
            logger.info("Found %d PRs for %s", len(pr_map), target_date)
            prs: list[PRRaw] = []
            for pr_api_url, pr_basic in pr_map.items():
                try:
                    logger.debug("Enriching PR %s", pr_api_url)
                    enriched = self._enrich(pr_basic)
                    prs.append(enriched)
                except FetchError:
                    logger.warning("Failed to enrich PR %s, skipping", pr_api_url)
            results["prs"] = self._save(target_date, prs)

        if "commits" in active:
            commits = self._fetch_commits(target_date)
            logger.info("Found %d commits for %s", len(commits), target_date)
            results["commits"] = self._save_commits(target_date, commits)

        if "issues" in active:
            issues = self._fetch_issues(target_date)
            logger.info("Found %d issues for %s", len(issues), target_date)
            results["issues"] = self._save_issues(target_date, issues)

        self._update_checkpoint(target_date)

        logger.info("Fetch complete for %s → %s", target_date, results)
        return results

    def fetch_range(
        self,
        since: str,
        until: str,
        types: set[str] | None = None,
        force: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> list[dict]:
        """월 단위 chunk 검색 → 날짜별 enrich/save. 실패 시 계속 진행."""
        active = types or {"prs", "commits", "issues"}
        all_dates = date_range(since, until)
        logger.info("fetch_range %s..%s (%d dates, force=%s)", since, until, len(all_dates), force)
        if progress:
            progress(f"Fetching {since}..{until} ({len(all_dates)} dates)")
        results: list[dict] = []
        processed: set[str] = set()

        # Determine stale dates for range narrowing
        if not force and self._daily_state is not None:
            stale = set(self._daily_state.stale_dates("fetch", all_dates))
            logger.info("Stale dates: %d/%d", len(stale), len(all_dates))
            if not stale:
                return [{"date": d, "status": "skipped"} for d in all_dates]
            # Pre-skip non-stale dates
            for d in all_dates:
                if d not in stale:
                    processed.add(d)
                    results.append({"date": d, "status": "skipped"})
            # Narrow API range to min..max of stale dates
            sorted_stale = sorted(stale)
            chunks = monthly_chunks(sorted_stale[0], sorted_stale[-1])
        else:
            stale = None  # no narrowing, use per-date check
            chunks = monthly_chunks(since, until)

        use_parallel = self._max_workers > 1

        for chunk_start, chunk_end in chunks:
            chunk_key = f"{chunk_start}__{chunk_end}"
            logger.debug("Processing chunk %s..%s", chunk_start, chunk_end)
            if progress:
                progress(f"  Fetch chunk {chunk_start}..{chunk_end}")
            try:
                # Try to load cached search results (resumable)
                cached = (
                    self._progress_store.load_chunk_search(chunk_key)
                    if self._progress_store
                    else None
                )
                if cached is not None:
                    logger.info("Resuming chunk %s from cache", chunk_key)
                    buckets = cached
                else:
                    # Range search per chunk (always sequential — throttled)
                    pr_map: dict[str, dict] = {}
                    commit_items: list[dict] = []
                    issue_map: dict[str, dict] = {}

                    if "prs" in active:
                        pr_map = self._search_prs_range(chunk_start, chunk_end)
                    if "commits" in active:
                        commit_items = self._search_commits_range(chunk_start, chunk_end)
                    if "issues" in active:
                        issue_map = self._search_issues_range(chunk_start, chunk_end)

                    # Bucket by date
                    buckets = self._bucket_by_date(pr_map, commit_items, issue_map)

                    # Cache search results for resumability
                    if self._progress_store:
                        self._progress_store.save_chunk_search(chunk_key, buckets)

                # Determine which dates to process in this chunk
                chunk_dates = date_range(chunk_start, chunk_end)
                dates_to_process: list[str] = []
                for d in chunk_dates:
                    if d in processed:
                        continue
                    processed.add(d)
                    if not force:
                        if stale is not None:
                            if d not in stale:
                                results.append({"date": d, "status": "skipped"})
                                continue
                        elif self._is_date_fetched(d):
                            results.append({"date": d, "status": "skipped"})
                            continue
                    dates_to_process.append(d)

                if use_parallel and len(dates_to_process) > 1:
                    chunk_results = self._process_dates_parallel(dates_to_process, buckets, active)
                else:
                    chunk_results = self._process_dates_sequential(
                        dates_to_process, buckets, active
                    )
                results.extend(chunk_results)

                # Clear chunk cache after all dates processed
                if self._progress_store:
                    self._progress_store.clear_chunk(chunk_key)

            except Exception as e:
                # Chunk-level failure: mark all unprocessed dates in chunk as failed
                chunk_dates = date_range(chunk_start, chunk_end)
                for d in chunk_dates:
                    if d not in processed:
                        processed.add(d)
                        results.append({"date": d, "status": "failed", "error": str(e)})

        # Handle dates not covered by any chunk (shouldn't happen but safety)
        for d in all_dates:
            if d not in processed:
                results.append({"date": d, "status": "failed", "error": "not in any chunk"})

        return results

    def _process_dates_sequential(
        self, dates: list[str], buckets: dict, active: set[str]
    ) -> list[dict]:
        """Process dates sequentially (default path)."""
        results: list[dict] = []
        for d in dates:
            try:
                bucket = buckets.get(d, {"prs": {}, "commits": [], "issues": {}})
                self._save_date_from_bucket(d, bucket, active)
                self._update_checkpoint(d)
                results.append({"date": d, "status": "success"})
            except Exception as e:
                logger.warning("Failed to process date %s: %s", d, e)
                results.append({"date": d, "status": "failed", "error": str(e)})
        return results

    def _process_dates_parallel(
        self, dates: list[str], buckets: dict, active: set[str]
    ) -> list[dict]:
        """Process dates in parallel using ThreadPoolExecutor."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: list[dict] = []

        def process_one(d: str) -> dict:
            try:
                bucket = buckets.get(d, {"prs": {}, "commits": [], "issues": {}})
                self._save_date_from_bucket(d, bucket, active)
                self._update_checkpoint(d)
                return {"date": d, "status": "success"}
            except Exception as e:
                logger.warning("Failed to process date %s: %s", d, e)
                return {"date": d, "status": "failed", "error": str(e)}

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(process_one, d): d for d in dates}
            for future in as_completed(futures):
                results.append(future.result())
        return results

    def _save_date_from_bucket(self, date_str: str, bucket: dict, active: set[str]) -> None:
        """bucket 데이터를 날짜별 파일로 enrich+save.

        When max_workers > 1 and client_pool is available, enrichment runs
        in parallel using ThreadPoolExecutor.
        """
        use_parallel = self._max_workers > 1 and self._client_pool is not None

        if "prs" in active:
            if use_parallel:
                prs = self._enrich_prs_parallel(bucket["prs"])
            else:
                prs = self._enrich_prs_sequential(bucket["prs"])
            self._save(date_str, prs)

        if "commits" in active:
            if use_parallel:
                commits = self._enrich_commits_parallel(bucket["commits"])
            else:
                commits = self._enrich_commits_sequential(bucket["commits"])
            self._save_commits(date_str, commits)

        if "issues" in active:
            if use_parallel:
                issues = self._enrich_issues_parallel(bucket["issues"])
            else:
                issues = self._enrich_issues_sequential(bucket["issues"])
            self._save_issues(date_str, issues)

    def _enrich_prs_sequential(self, pr_map: dict[str, dict]) -> list[PRRaw]:
        prs: list[PRRaw] = []
        for pr_api_url, pr_basic in pr_map.items():
            try:
                prs.append(self._enrich(pr_basic))
            except FetchError:
                logger.warning("Failed to enrich PR %s, skipping", pr_api_url)
        return prs

    def _enrich_prs_parallel(self, pr_map: dict[str, dict]) -> list[PRRaw]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        pool = self._client_pool
        prs: list[PRRaw] = []

        def enrich_one(pr_api_url: str, pr_basic: dict) -> PRRaw | None:
            client = pool.acquire()
            try:
                return self._enrich(pr_basic, client=client)
            except FetchError:
                logger.warning("Failed to enrich PR %s, skipping", pr_api_url)
                return None
            finally:
                pool.release(client)

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(enrich_one, url, basic): url for url, basic in pr_map.items()
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    prs.append(result)
        return prs

    def _enrich_commits_sequential(self, commit_items: list[dict]) -> list[CommitRaw]:
        commits: list[CommitRaw] = []
        for item in commit_items:
            try:
                commits.append(self._enrich_commit(item))
            except Exception:
                logger.warning(
                    "Failed to enrich commit %s, skipping",
                    item.get("sha", "unknown"),
                )
        return commits

    def _enrich_commits_parallel(self, commit_items: list[dict]) -> list[CommitRaw]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        pool = self._client_pool
        commits: list[CommitRaw] = []

        def enrich_one(item: dict) -> CommitRaw | None:
            client = pool.acquire()
            try:
                return self._enrich_commit(item, client=client)
            except Exception:
                logger.warning(
                    "Failed to enrich commit %s, skipping",
                    item.get("sha", "unknown"),
                )
                return None
            finally:
                pool.release(client)

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(enrich_one, item): item for item in commit_items}
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    commits.append(result)
        return commits

    def _enrich_issues_sequential(self, issue_map: dict[str, dict]) -> list[IssueRaw]:
        issues: list[IssueRaw] = []
        for api_url, item in issue_map.items():
            try:
                issues.append(self._enrich_issue(item))
            except Exception:
                logger.warning("Failed to enrich issue %s, skipping", api_url)
        return issues

    def _enrich_issues_parallel(self, issue_map: dict[str, dict]) -> list[IssueRaw]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        pool = self._client_pool
        issues: list[IssueRaw] = []

        def enrich_one(api_url: str, item: dict) -> IssueRaw | None:
            client = pool.acquire()
            try:
                return self._enrich_issue(item, client=client)
            except Exception:
                logger.warning("Failed to enrich issue %s, skipping", api_url)
                return None
            finally:
                pool.release(client)

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(enrich_one, url, item): url for url, item in issue_map.items()
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    issues.append(result)
        return issues

    @staticmethod
    def _bucket_by_date(
        pr_map: dict[str, dict],
        commit_items: list[dict],
        issue_map: dict[str, dict],
    ) -> dict[str, dict]:
        """검색 결과를 날짜별로 분류."""
        buckets: dict[str, dict] = {}

        def ensure_bucket(d: str) -> dict:
            if d not in buckets:
                buckets[d] = {"prs": {}, "commits": [], "issues": {}}
            return buckets[d]

        for url, item in pr_map.items():
            d = item["updated_at"][:10]
            ensure_bucket(d)["prs"][url] = item

        for item in commit_items:
            d = item["commit"]["committer"]["date"][:10]
            ensure_bucket(d)["commits"].append(item)

        for url, item in issue_map.items():
            d = item["updated_at"][:10]
            ensure_bucket(d)["issues"][url] = item

        return buckets

    def _is_date_fetched(self, date_str: str) -> bool:
        """daily_state 있으면 timestamp 기반, 없으면 파일 존재 체크."""
        if self._daily_state is not None:
            return not self._daily_state.is_fetch_stale(date_str)
        raw_dir = self._config.date_raw_dir(date_str)
        return all((raw_dir / f).exists() for f in ("prs.json", "commits.json", "issues.json"))

    # ── Range 검색 ──

    @staticmethod
    def _warn_if_truncated(count: int, query: str) -> None:
        """수집된 결과가 1000건 이상이면 truncation warning."""
        if count >= 1000:
            logger.warning(
                "Search results may be truncated (%d >= 1000) for query: %s",
                count,
                query,
            )

    def _search_prs_range(self, start: str, end: str) -> dict[str, dict]:
        """날짜 범위로 PR 3축 검색 + dedup."""
        axes = [
            f"author:{self._username}",
            f"reviewed-by:{self._username}",
            f"commenter:{self._username}",
        ]
        pr_map: dict[str, dict] = {}
        for qualifier in axes:
            query = f"type:pr {qualifier} updated:{start}..{end}"
            try:
                items = self._search_all_pages(query)
                self._warn_if_truncated(len(items), query)
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

    def _search_commits_range(self, start: str, end: str) -> list[dict]:
        """날짜 범위로 커밋 검색."""
        query = f"author:{self._username} committer-date:{start}..{end}"
        try:
            items = self._search_all_commit_pages(query)
            self._warn_if_truncated(len(items), query)
            return items
        except FetchError:
            logger.warning("Commit range search not supported, skipping")
            return []

    def _search_issues_range(self, start: str, end: str) -> dict[str, dict]:
        """날짜 범위로 Issue 2축 검색 + dedup."""
        axes = [
            f"type:issue author:{self._username} updated:{start}..{end}",
            f"type:issue commenter:{self._username} updated:{start}..{end}",
        ]
        issue_map: dict[str, dict] = {}
        for query in axes:
            try:
                items = self._search_all_pages(query)
                self._warn_if_truncated(len(items), query)
            except FetchError:
                logger.warning("Issue range search failed for query '%s', skipping", query)
                continue
            for item in items:
                api_url = item["url"]
                if api_url not in issue_map:
                    issue_map[api_url] = item
        return issue_map

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

    def _enrich(self, pr_basic: dict, client: GHESClient | None = None) -> PRRaw:
        """기본 PR 정보에 files, comments, reviews를 추가 수집."""
        c = client or self._client
        pr_api_url = pr_basic.get("pull_request", {}).get("url", "")
        owner, repo, number = self._parse_pr_url(pr_api_url)

        pr_detail = c.get_pr(owner, repo, number)

        raw_files = c.get_pr_files(owner, repo, number)
        raw_comments = c.get_pr_comments(owner, repo, number)
        raw_reviews = c.get_pr_reviews(owner, repo, number)

        filtered_comments = [c for c in raw_comments if not self._is_noise_comment(c)]
        filtered_reviews = [r for r in raw_reviews if not self._is_noise_review(r)]

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
                    patch=f.get("patch", ""),
                )
                for f in raw_files
            ],
            comments=[
                Comment(
                    author=c["user"]["login"],
                    body=c.get("body") or "",
                    created_at=c["created_at"],
                    url=c["html_url"],
                    path=c.get("path") or "",
                    line=c.get("line") or c.get("original_line") or 0,
                    diff_hunk=c.get("diff_hunk") or "",
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

    # ── Commit 수집 ──

    def _fetch_commits(self, target_date: str) -> list[CommitRaw]:
        """커밋 검색 + enrich. GHES 미지원 시 빈 리스트 반환."""
        query = f"author:{self._username} committer-date:{target_date}"
        try:
            items = self._search_all_commit_pages(query)
        except FetchError:
            logger.warning("Commit search not supported, skipping")
            return []

        commits: list[CommitRaw] = []
        for item in items:
            try:
                commits.append(self._enrich_commit(item))
            except Exception:
                logger.warning("Failed to enrich commit %s, skipping", item.get("sha", "unknown"))
        return commits

    def _search_all_commit_pages(self, query: str) -> list[dict]:
        """Commit Search API 전체 페이지 수집."""
        all_items: list[dict] = []
        page = 1

        while True:
            result = self._client.search_commits(query, page=page, per_page=100)
            items = result.get("items", [])
            all_items.extend(items)

            if len(items) < 100:
                break
            page += 1

        return all_items

    def _enrich_commit(self, item: dict, client: GHESClient | None = None) -> CommitRaw:
        """검색 결과를 CommitRaw로 변환. get_commit으로 files 포함 상세 조회."""
        c = client or self._client
        repo_full = item["repository"]["full_name"]
        sha = item["sha"]
        owner, repo = repo_full.split("/", 1)

        detail = c.get_commit(owner, repo, sha)

        raw_files = detail.get("files", [])
        return CommitRaw(
            sha=sha,
            url=detail["html_url"],
            api_url=detail["url"],
            message=detail["commit"]["message"],
            author=item["author"]["login"] if item.get("author") else "",
            repo=repo_full,
            committed_at=detail["commit"]["committer"]["date"],
            files=[
                FileChange(
                    filename=f["filename"],
                    additions=f["additions"],
                    deletions=f["deletions"],
                    status=f["status"],
                    patch=f.get("patch", ""),
                )
                for f in raw_files
            ],
        )

    # ── Issue 수집 ──

    def _fetch_issues(self, target_date: str) -> list[IssueRaw]:
        """Issue 2축 검색 + enrich. 실패 시 빈 리스트 반환."""
        axes = [
            f"type:issue author:{self._username} updated:{target_date}",
            f"type:issue commenter:{self._username} updated:{target_date}",
        ]

        issue_map: dict[str, dict] = {}
        for query in axes:
            try:
                items = self._search_all_pages(query)
            except FetchError:
                logger.warning("Issue search failed for query '%s', skipping", query)
                continue

            for item in items:
                api_url = item["url"]
                if api_url not in issue_map:
                    issue_map[api_url] = item

        issues: list[IssueRaw] = []
        for api_url, item in issue_map.items():
            try:
                issues.append(self._enrich_issue(item))
            except Exception:
                logger.warning("Failed to enrich issue %s, skipping", api_url)
        return issues

    def _enrich_issue(self, item: dict, client: GHESClient | None = None) -> IssueRaw:
        """Issue 검색 결과를 IssueRaw로 변환."""
        c = client or self._client
        api_url = item["url"]
        owner, repo, number = self._parse_issue_url(api_url)

        detail = c.get_issue(owner, repo, number)
        raw_comments = c.get_issue_comments(owner, repo, number)

        filtered_comments = [c for c in raw_comments if not self._is_noise_comment(c)]

        return IssueRaw(
            url=detail["html_url"],
            api_url=detail["url"],
            number=detail["number"],
            title=detail["title"],
            body=detail.get("body") or "",
            state=detail["state"],
            created_at=detail["created_at"],
            updated_at=detail["updated_at"],
            closed_at=detail.get("closed_at"),
            repo=f"{owner}/{repo}",
            labels=[label["name"] for label in detail.get("labels", [])],
            author=detail["user"]["login"],
            comments=[
                Comment(
                    author=c["user"]["login"],
                    body=c.get("body") or "",
                    created_at=c["created_at"],
                    url=c["html_url"],
                    path=c.get("path") or "",
                    line=c.get("line") or c.get("original_line") or 0,
                    diff_hunk=c.get("diff_hunk") or "",
                )
                for c in filtered_comments
            ],
        )

    @staticmethod
    def _parse_issue_url(api_url: str) -> tuple[str, str, int]:
        """Issue API URL에서 owner, repo, number 추출."""
        parts = api_url.rstrip("/").split("/")
        issues_idx = parts.index("issues")
        owner = parts[issues_idx - 2]
        repo = parts[issues_idx - 1]
        number = int(parts[issues_idx + 1])
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

    def _save_commits(self, target_date: str, commits: list[CommitRaw]) -> Path:
        output_dir = self._config.date_raw_dir(target_date)
        output_path = output_dir / "commits.json"
        save_json(commits, output_path)
        return output_path

    def _save_issues(self, target_date: str, issues: list[IssueRaw]) -> Path:
        output_dir = self._config.date_raw_dir(target_date)
        output_path = output_dir / "issues.json"
        save_json(issues, output_path)
        return output_path

    def _update_checkpoint(self, target_date: str) -> None:
        from git_recap.services.checkpoint import update_checkpoint

        update_checkpoint(self._config.checkpoints_path, "last_fetch_date", target_date)

        if self._daily_state is not None:
            self._daily_state.set_timestamp("fetch", target_date)
