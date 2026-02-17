# Phase 1-2~4: FetcherService 상세 설계

## 목적

GHES Search API를 통해 특정 사용자의 특정 날짜 PR, Commit, Issue 활동을 수집하고,
각 항목별 상세 정보를 enrich하여 `data/raw/` 에 저장한다.

---

## 위치

`src/git_recap/services/fetcher.py`

## 의존성

- `git_recap.config.AppConfig`
- `git_recap.infra.ghes_client.GHESClient`
- `git_recap.models.PRRaw, CommitRaw, IssueRaw, FileChange, Comment, Review, save_json, load_json`
- `git_recap.exceptions.FetchError`

---

## 상세 구현

### 클래스 구조

```python
import logging
import re
from pathlib import Path

from git_recap.config import AppConfig
from git_recap.exceptions import FetchError
from git_recap.infra.ghes_client import GHESClient
from git_recap.models import (
    Comment, CommitRaw, FileChange, IssueRaw, PRRaw, Review, save_json, load_json,
)

logger = logging.getLogger(__name__)

# 노이즈 패턴 (컴파일된 정규식)
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
        지정 날짜의 PR/Commit/Issue 활동을 수집하여 파일로 저장.

        Args:
            target_date: "YYYY-MM-DD"

        Returns:
            저장된 파일 경로 (data/raw/{Y}/{M}/{D}/prs.json)
        """
        # 1. PR 파이프라인: 3축 검색 + dedup + enrich
        pr_urls_map = self._search_prs(target_date)

        prs: list[PRRaw] = []
        for pr_api_url, pr_basic in pr_urls_map.items():
            try:
                enriched = self._enrich(pr_basic)
                prs.append(enriched)
            except FetchError:
                logger.warning("Failed to enrich PR %s, skipping", pr_api_url)

        output_path = self._save(target_date, prs)

        # 2. Commit 파이프라인
        commits = self._fetch_commits(target_date)
        self._save_commits(target_date, commits)

        # 3. Issue 파이프라인
        issues = self._fetch_issues(target_date)
        self._save_issues(target_date, issues)

        # 4. checkpoint 갱신
        self._update_checkpoint(target_date)

        logger.info(
            "Fetched %d PRs, %d commits, %d issues for %s → %s",
            len(prs), len(commits), len(issues), target_date, output_path,
        )
        return output_path
```

### 3축 검색 + dedup

```python
    def _search_prs(self, target_date: str) -> dict[str, dict]:
        """
        3축 쿼리로 PR 검색 후 API URL 기준 dedup.

        쿼리 축:
          1. author:{username}
          2. reviewed-by:{username}
          3. commenter:{username}

        Returns:
            {api_url: pr_basic_dict} — 중복 제거된 PR 맵
        """
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
                # reviewed-by 미지원 시 (422) 스킵
                if "reviewed-by" in qualifier:
                    logger.warning(
                        "reviewed-by qualifier not supported, skipping"
                    )
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
```

### PR Enrich

```python
    def _enrich(self, pr_basic: dict) -> PRRaw:
        """
        기본 PR 정보에 files, comments, reviews를 추가 수집하여 PRRaw 생성.

        Args:
            pr_basic: Search API에서 반환된 issue 형태의 PR dict

        Returns:
            PRRaw (완전한 PR 데이터)
        """
        # Search API는 issue 형태 → PR API URL 추출
        pr_api_url = pr_basic.get("pull_request", {}).get("url", "")
        # URL에서 owner, repo, number 파싱
        owner, repo, number = self._parse_pr_url(pr_api_url)

        # PR 상세 조회
        pr_detail = self._client.get_pr(owner, repo, number)

        # 추가 데이터 수집
        raw_files = self._client.get_pr_files(owner, repo, number)
        raw_comments = self._client.get_pr_comments(owner, repo, number)
        raw_reviews = self._client.get_pr_reviews(owner, repo, number)

        # 노이즈 필터링
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
            labels=[l["name"] for l in pr_detail.get("labels", [])],
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
        """
        PR API URL에서 owner, repo, number 추출.

        e.g., "https://github.example.com/api/v3/repos/org/repo/pulls/42"
             → ("org", "repo", 42)
        """
        # /repos/{owner}/{repo}/pulls/{number}
        parts = api_url.rstrip("/").split("/")
        # [..., "repos", owner, repo, "pulls", number]
        pulls_idx = parts.index("pulls")
        owner = parts[pulls_idx - 2]
        repo = parts[pulls_idx - 1]
        number = int(parts[pulls_idx + 1])
        return owner, repo, number
```

### 노이즈 필터링

```python
    @staticmethod
    def _is_bot_user(login: str) -> bool:
        """bot 사용자 여부 판별."""
        login_lower = login.lower()
        return any(login_lower.endswith(suffix) for suffix in BOT_SUFFIXES)

    @staticmethod
    def _is_noise_comment(comment: dict) -> bool:
        """노이즈 코멘트 판별 (bot author 또는 LGTM 등)."""
        author = comment.get("user", {}).get("login", "")
        if FetcherService._is_bot_user(author):
            return True

        body = (comment.get("body") or "").strip()
        if not body:
            return True

        return any(pattern.match(body) for pattern in DEFAULT_NOISE_PATTERNS)

    @staticmethod
    def _is_noise_review(review: dict) -> bool:
        """노이즈 리뷰 판별 (bot author)."""
        author = review.get("user", {}).get("login", "")
        return FetcherService._is_bot_user(author)
```

### Commit 수집

```python
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
                logger.warning("Failed to enrich commit %s, skipping",
                               item.get("sha", "unknown"))
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

    def _enrich_commit(self, item: dict) -> CommitRaw:
        """검색 결과를 CommitRaw로 변환. get_commit으로 files 포함 상세 조회."""
        repo_full = item["repository"]["full_name"]
        sha = item["sha"]
        owner, repo = repo_full.split("/", 1)

        detail = self._client.get_commit(owner, repo, sha)

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
                )
                for f in raw_files
            ],
        )
```

### Issue 수집

```python
    def _fetch_issues(self, target_date: str) -> list[IssueRaw]:
        """Issue 2축 검색(author + commenter) + enrich. 실패 시 빈 리스트 반환."""
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

    def _enrich_issue(self, item: dict) -> IssueRaw:
        """Issue 검색 결과를 IssueRaw로 변환."""
        api_url = item["url"]
        owner, repo, number = self._parse_issue_url(api_url)

        detail = self._client.get_issue(owner, repo, number)
        raw_comments = self._client.get_issue_comments(owner, repo, number)

        filtered_comments = [
            c for c in raw_comments if not self._is_noise_comment(c)
        ]

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
```

### 파일 저장 + Checkpoint

```python
    def _save(self, target_date: str, prs: list[PRRaw]) -> Path:
        """PRRaw 목록을 JSON 파일로 저장."""
        output_dir = self._config.date_raw_dir(target_date)
        output_path = output_dir / "prs.json"
        save_json(prs, output_path)
        return output_path

    def _save_commits(self, target_date: str, commits: list[CommitRaw]) -> Path:
        """CommitRaw 목록을 JSON 파일로 저장."""
        output_dir = self._config.date_raw_dir(target_date)
        output_path = output_dir / "commits.json"
        save_json(commits, output_path)
        return output_path

    def _save_issues(self, target_date: str, issues: list[IssueRaw]) -> Path:
        """IssueRaw 목록을 JSON 파일로 저장."""
        output_dir = self._config.date_raw_dir(target_date)
        output_path = output_dir / "issues.json"
        save_json(issues, output_path)
        return output_path

    def _update_checkpoint(self, target_date: str) -> None:
        """마지막 성공 날짜를 checkpoints.json에 기록."""
        cp_path = self._config.checkpoints_path
        cp_path.parent.mkdir(parents=True, exist_ok=True)

        checkpoints = {}
        if cp_path.exists():
            checkpoints = load_json(cp_path)

        checkpoints["last_fetch_date"] = target_date

        import json
        with open(cp_path, "w", encoding="utf-8") as f:
            json.dump(checkpoints, f, indent=2)
```

### Range-Based Fetch (fetch_range)

day-by-day `fetch()` 호출 시 하루당 6개 Search API 호출이 필요하다 (PR 3축 + Commit 1 + Issue 2).
6년 기준 ~13,000회 → ~7.3시간 소요. `fetch_range()`는 월 단위 검색으로 전환하여
~432회 → ~14분으로 단축한다 (~30배).

```python
    def fetch_range(
        self, since: str, until: str,
        types: set[str] | None = None,
        force: bool = False,
    ) -> list[dict]:
        """월 단위 chunk 검색 → 날짜별 enrich/save. 실패 시 계속 진행."""
```

**흐름:**
1. `monthly_chunks(since, until)` → 월 단위 (start, end) 쌍 목록
2. 각 chunk에 대해 range search 호출 (쿼리: `updated:{start}..{end}`)
3. `_bucket_by_date()` → `updated_at`/`committer_date`의 날짜 부분으로 분류
4. 각 날짜 순회: skip 판단 → enrich + save → checkpoint
5. 실패 시 해당 날짜만 `{"status": "failed"}`로 기록, 다음 날짜 계속

```python
    @staticmethod
    def _bucket_by_date(
        pr_map: dict[str, dict],
        commit_items: list[dict],
        issue_map: dict[str, dict],
    ) -> dict[str, dict]:
        """검색 결과를 날짜별로 분류.
        반환: {"2020-03-15": {"prs": {url: item}, "commits": [...], "issues": {url: item}}}"""

    def _is_date_fetched(self, date_str: str) -> bool:
        """prs.json + commits.json + issues.json 3개 모두 존재하면 True."""

    def _search_prs_range(self, start: str, end: str) -> dict[str, dict]:
        """날짜 범위로 PR 3축 검색 + dedup. 1000건 초과 시 warning."""

    def _search_commits_range(self, start: str, end: str) -> list[dict]:
        """날짜 범위로 커밋 검색. GHES 미지원 시 빈 리스트."""

    def _search_issues_range(self, start: str, end: str) -> dict[str, dict]:
        """날짜 범위로 Issue 2축 검색 + dedup."""

    @staticmethod
    def _warn_if_truncated(count: int, query: str) -> None:
        """수집 결과가 1000건 이상이면 truncation warning 로깅."""
```

---

## 출력 파일

| 파일 | 내용 |
|---|---|
| `data/raw/{Y}/{M}/{D}/prs.json` | `list[PRRaw]` — PR 원시 데이터 |
| `data/raw/{Y}/{M}/{D}/commits.json` | `list[CommitRaw]` — Commit 원시 데이터 |
| `data/raw/{Y}/{M}/{D}/issues.json` | `list[IssueRaw]` — Issue 원시 데이터 |

---

## GHES API 응답 구조 참고

### Search API 응답 (`GET /search/issues?q=type:pr ...`)

```json
{
  "total_count": 2,
  "items": [
    {
      "url": "https://ghes/api/v3/repos/org/repo/issues/42",
      "html_url": "https://ghes/org/repo/pull/42",
      "number": 42,
      "title": "Add feature",
      "user": {"login": "testuser"},
      "state": "closed",
      "created_at": "2025-02-16T09:00:00Z",
      "updated_at": "2025-02-16T15:00:00Z",
      "pull_request": {
        "url": "https://ghes/api/v3/repos/org/repo/pulls/42"
      }
    }
  ]
}
```

### PR Detail (`GET /repos/{owner}/{repo}/pulls/{number}`)

```json
{
  "url": "https://ghes/api/v3/repos/org/repo/pulls/42",
  "html_url": "https://ghes/org/repo/pull/42",
  "number": 42,
  "title": "Add feature",
  "body": "Description",
  "state": "closed",
  "merged": true,
  "created_at": "2025-02-16T09:00:00Z",
  "updated_at": "2025-02-16T15:00:00Z",
  "merged_at": "2025-02-16T14:00:00Z",
  "user": {"login": "testuser"},
  "labels": [{"name": "feature"}]
}
```

### PR Files (`GET /repos/{owner}/{repo}/pulls/{number}/files`)

```json
[
  {"filename": "src/main.py", "additions": 10, "deletions": 3, "status": "modified"}
]
```

### PR Comments (`GET /repos/{owner}/{repo}/pulls/{number}/comments`)

```json
[
  {
    "user": {"login": "reviewer1"},
    "body": "Looks good!",
    "created_at": "2025-02-16T11:00:00Z",
    "html_url": "https://ghes/org/repo/pull/42#comment-1"
  }
]
```

### PR Reviews (`GET /repos/{owner}/{repo}/pulls/{number}/reviews`)

```json
[
  {
    "user": {"login": "reviewer1"},
    "state": "APPROVED",
    "body": "",
    "submitted_at": "2025-02-16T12:00:00Z",
    "html_url": "https://ghes/org/repo/pull/42#review-1"
  }
]
```

### Commit Search API 응답 (`GET /search/commits?q=author:user ...`)

`Accept: application/vnd.github.cloak-preview+json` 헤더 필요.

```json
{
  "total_count": 1,
  "items": [
    {
      "sha": "abc123",
      "repository": {"full_name": "org/repo"},
      "author": {"login": "testuser"},
      "commit": {
        "message": "Add new feature",
        "committer": {"date": "2025-02-16T14:00:00Z"}
      }
    }
  ]
}
```

### Commit Detail (`GET /repos/{owner}/{repo}/commits/{sha}`)

```json
{
  "sha": "abc123",
  "url": "https://ghes/api/v3/repos/org/repo/commits/abc123",
  "html_url": "https://ghes/org/repo/commit/abc123",
  "commit": {
    "message": "Add new feature",
    "committer": {"date": "2025-02-16T14:00:00Z"}
  },
  "files": [
    {"filename": "src/main.py", "additions": 10, "deletions": 3, "status": "modified"}
  ]
}
```

### Issue Detail (`GET /repos/{owner}/{repo}/issues/{number}`)

```json
{
  "url": "https://ghes/api/v3/repos/org/repo/issues/5",
  "html_url": "https://ghes/org/repo/issues/5",
  "number": 5,
  "title": "Bug report",
  "body": "Description",
  "state": "open",
  "created_at": "2025-02-16T09:00:00Z",
  "updated_at": "2025-02-16T12:00:00Z",
  "closed_at": null,
  "user": {"login": "testuser"},
  "labels": [{"name": "bug"}]
}
```

### Issue Comments (`GET /repos/{owner}/{repo}/issues/{number}/comments`)

```json
[
  {
    "user": {"login": "commenter1"},
    "body": "I can reproduce this.",
    "created_at": "2025-02-16T10:00:00Z",
    "html_url": "https://ghes/org/repo/issues/5#issuecomment-1"
  }
]
```

---

## 테스트 명세

### test_fetcher.py

GHESClient를 mock하여 FetcherService의 비즈니스 로직을 검증한다.

```python
"""tests/unit/test_fetcher.py"""

# GHESClient를 mock으로 주입. 실제 HTTP 호출 없음.


class TestSearchPrs:
    def test_three_axis_search(self, fetcher, mock_client):
        """3축 쿼리 (author, reviewed-by, commenter)가 모두 호출된다."""

    def test_dedup_by_api_url(self, fetcher, mock_client):
        """동일 PR이 여러 축에서 나오면 1개로 dedup."""

    def test_reviewed_by_fallback_on_422(self, fetcher, mock_client):
        """reviewed-by 422 에러 시 해당 축만 스킵하고 나머지 계속."""

    def test_pagination(self, fetcher, mock_client):
        """100개 초과 결과 시 다음 페이지 요청."""


class TestEnrich:
    def test_creates_pr_raw_from_api(self, fetcher, mock_client):
        """API 응답으로 PRRaw 객체가 올바르게 생성된다."""

    def test_pr_body_none_becomes_empty_string(self, fetcher, mock_client):
        """body가 null이면 빈 문자열로 변환."""

    def test_labels_extracted(self, fetcher, mock_client):
        """labels에서 name만 추출."""

    def test_merged_at_preserved(self, fetcher, mock_client):
        """merged_at 값이 보존된다 (None 포함)."""


class TestParseprUrl:
    def test_standard_url(self):
        """표준 API URL 파싱."""
        owner, repo, num = FetcherService._parse_pr_url(
            "https://ghes/api/v3/repos/org/repo/pulls/42"
        )
        assert (owner, repo, num) == ("org", "repo", 42)

    def test_nested_org_url(self):
        """org 이름에 하이픈 포함."""
        owner, repo, num = FetcherService._parse_pr_url(
            "https://ghes/api/v3/repos/my-org/my-repo/pulls/7"
        )
        assert (owner, repo, num) == ("my-org", "my-repo", 7)


class TestNoiseFiltering:
    def test_bot_comment_filtered(self):
        """bot 사용자의 코멘트가 필터링된다."""

    def test_lgtm_comment_filtered(self):
        """'LGTM' 코멘트가 필터링된다."""

    def test_plus_one_comment_filtered(self):
        """'+1' 코멘트가 필터링된다."""

    def test_empty_body_comment_filtered(self):
        """빈 body 코멘트가 필터링된다."""

    def test_normal_comment_kept(self):
        """일반 코멘트는 유지된다."""

    def test_bot_review_filtered(self):
        """bot 사용자의 리뷰가 필터링된다."""

    def test_normal_review_kept(self):
        """일반 리뷰는 유지된다."""


class TestFetchCommits:
    def test_commit_search_and_enrich(self, fetcher, mock_client, tmp_data_dir):
        """커밋 검색 후 enrich하여 commits.json 생성."""

    def test_commit_search_not_supported(self, fetcher, mock_client, tmp_data_dir):
        """Commit Search API 미지원 시 빈 리스트로 graceful skip."""

    def test_enrich_commit_failure_skips(self, fetcher, mock_client, tmp_data_dir):
        """개별 커밋 enrich 실패 시 해당 커밋만 스킵."""

    def test_pagination(self, fetcher, mock_client):
        """100개 초과 결과 시 다음 페이지 요청."""


class TestFetchIssues:
    def test_two_axis_search(self, fetcher, mock_client, tmp_data_dir):
        """2축 쿼리 (author, commenter)로 Issue 검색."""

    def test_dedup_by_api_url(self, fetcher, mock_client, tmp_data_dir):
        """동일 Issue가 여러 축에서 나오면 1개로 dedup."""

    def test_enrich_issue(self, fetcher, mock_client, tmp_data_dir):
        """Issue enrich 후 IssueRaw 객체 생성."""

    def test_issue_search_failure_skips_axis(self, fetcher, mock_client, tmp_data_dir):
        """개별 축 검색 실패 시 해당 축만 스킵."""

    def test_enrich_issue_failure_skips(self, fetcher, mock_client, tmp_data_dir):
        """개별 Issue enrich 실패 시 해당 Issue만 스킵."""


class TestParseIssueUrl:
    def test_standard_url(self):
        """표준 Issue API URL 파싱."""
        owner, repo, num = FetcherService._parse_issue_url(
            "https://ghes/api/v3/repos/org/repo/issues/5"
        )
        assert (owner, repo, num) == ("org", "repo", 5)


class TestFetch:
    def test_full_pipeline(self, fetcher, mock_client, tmp_data_dir):
        """fetch() 호출 시 prs.json + commits.json + issues.json 생성 + checkpoint 갱신."""

    def test_empty_result(self, fetcher, mock_client, tmp_data_dir):
        """검색 결과 없으면 빈 배열 JSON 생성."""

    def test_enrich_failure_skips_pr(self, fetcher, mock_client, tmp_data_dir):
        """개별 PR enrich 실패 시 해당 PR만 스킵."""

    def test_output_path_matches_date(self, fetcher, mock_client, tmp_data_dir):
        """출력 경로가 날짜 구조를 따른다."""


class TestCheckpoint:
    def test_creates_checkpoint_file(self, fetcher, mock_client, tmp_data_dir):
        """checkpoints.json이 생성된다."""

    def test_updates_existing_checkpoint(self, fetcher, mock_client, tmp_data_dir):
        """기존 checkpoint가 있으면 갱신."""
```

### conftest.py (fetcher 전용 fixtures)

```python
"""tests/unit/conftest.py 또는 tests/conftest.py에 추가"""

@pytest.fixture
def mock_client():
    """GHESClient mock. 각 테스트에서 return_value 설정."""
    client = Mock(spec=GHESClient)
    # 기본: 빈 검색 결과
    client.search_issues.return_value = {"total_count": 0, "items": []}
    client.get_pr.return_value = {}
    client.get_pr_files.return_value = []
    client.get_pr_comments.return_value = []
    client.get_pr_reviews.return_value = []
    client.search_commits.return_value = {"total_count": 0, "items": []}
    client.get_commit.return_value = {}
    client.get_issue.return_value = {}
    client.get_issue_comments.return_value = []
    return client

@pytest.fixture
def fetcher(test_config, mock_client):
    return FetcherService(test_config, mock_client)
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 1.2.1 | `_parse_pr_url()` 구현 | TestParsePrUrl |
| 1.2.2 | `_search_all_pages()` + `_search_prs()` 구현 (3축 + dedup) | TestSearchPrs |
| 1.2.3 | 노이즈 필터링 (`_is_bot_user`, `_is_noise_comment`, `_is_noise_review`) | TestNoiseFiltering |
| 1.2.4 | `_enrich()` 구현 | TestEnrich |
| 1.2.5 | `_save()` + `_update_checkpoint()` 구현 | TestCheckpoint |
| 1.2.6 | `fetch()` 통합 | TestFetch |
| 1.2.7 | `_fetch_commits()` + `_search_all_commit_pages()` + `_enrich_commit()` 구현 | TestFetchCommits |
| 1.2.8 | `_fetch_issues()` + `_enrich_issue()` + `_parse_issue_url()` 구현 | TestFetchIssues, TestParseIssueUrl |
| 1.2.9 | `_save_commits()` + `_save_issues()` 구현 | TestFetch (통합) |
| 1.2.10 | `monthly_chunks()` (date_utils) | TestMonthlyChunks |
| 1.2.11 | `_is_date_fetched()` | TestIsDateFetched |
| 1.2.12 | `_bucket_by_date()` | TestBucketByDate |
| 1.2.13 | `_search_prs_range()` / `_search_commits_range()` / `_search_issues_range()` | TestSearchPrsRange, TestSearchCommitsRange, TestSearchIssuesRange |
| 1.2.14 | `fetch_range()` 통합 | TestFetchRange |
