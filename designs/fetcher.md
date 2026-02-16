# Phase 1-2~4: FetcherService 상세 설계

## 목적

GHES Search API를 통해 특정 사용자의 특정 날짜 PR 활동을 수집하고,
PR별 상세 정보(files, comments, reviews)를 enrich하여 `data/raw/` 에 저장한다.

---

## 위치

`src/git_recap/services/fetcher.py`

## 의존성

- `git_recap.config.AppConfig`
- `git_recap.infra.ghes_client.GHESClient`
- `git_recap.models.PRRaw, FileChange, Comment, Review, save_json, load_json`
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
    Comment, FileChange, PRRaw, Review, save_json, load_json,
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
        지정 날짜의 PR 활동을 수집하여 파일로 저장.

        Args:
            target_date: "YYYY-MM-DD"

        Returns:
            저장된 파일 경로 (data/raw/{Y}/{M}/{D}/prs.json)
        """
        # 1. 3축 검색 + dedup
        pr_urls_map = self._search_prs(target_date)

        # 2. PR별 enrich
        prs: list[PRRaw] = []
        for pr_api_url, pr_basic in pr_urls_map.items():
            try:
                enriched = self._enrich(pr_basic)
                prs.append(enriched)
            except FetchError:
                logger.warning("Failed to enrich PR %s, skipping", pr_api_url)

        # 3. 저장
        output_path = self._save(target_date, prs)

        # 4. checkpoint 갱신
        self._update_checkpoint(target_date)

        logger.info("Fetched %d PRs for %s → %s", len(prs), target_date, output_path)
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

### 파일 저장 + Checkpoint

```python
    def _save(self, target_date: str, prs: list[PRRaw]) -> Path:
        """PRRaw 목록을 JSON 파일로 저장."""
        output_dir = self._config.date_raw_dir(target_date)
        output_path = output_dir / "prs.json"
        save_json(prs, output_path)
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


class TestFetch:
    def test_full_pipeline(self, fetcher, mock_client, tmp_data_dir):
        """fetch() 호출 시 prs.json 생성 + checkpoint 갱신."""

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
