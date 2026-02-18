# Phase 2: NormalizerService 상세 설계

## 목적

Fetcher가 수집한 raw 데이터(`prs.json`, `commits.json`, `issues.json`)를 정규화된 Activity 목록과
일일 통계(DailyStats)로 변환한다. 이 단계에서 활동 유형 분류, 날짜 필터링,
auto_summary 생성, 수치 계산이 모두 이루어진다.

---

## 위치

`src/workrecap/services/normalizer.py`

## 의존성

- `workrecap.config.AppConfig`
- `workrecap.exceptions.NormalizeError`
- `workrecap.models` — PRRaw, CommitRaw, IssueRaw, Activity, ActivityKind, DailyStats,
  pr_raw_from_dict, commit_raw_from_dict, issue_raw_from_dict, load_json, save_json, save_jsonl

---

## 상세 구현

### 클래스 구조

```python
import logging
from pathlib import Path

from workrecap.config import AppConfig
from workrecap.exceptions import NormalizeError
from workrecap.models import (
    Activity, ActivityKind, CommitRaw, DailyStats, IssueRaw, PRRaw,
    commit_raw_from_dict, issue_raw_from_dict, load_json, pr_raw_from_dict,
    save_json, save_jsonl,
)

logger = logging.getLogger(__name__)


class NormalizerService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._username = config.username

    def normalize(self, target_date: str) -> tuple[Path, Path]:
        """
        Raw PR/Commit/Issue 데이터를 Activity 목록과 통계로 변환.

        Args:
            target_date: "YYYY-MM-DD"

        Input:
            data/raw/{Y}/{M}/{D}/prs.json (필수)
            data/raw/{Y}/{M}/{D}/commits.json (optional, 하위 호환)
            data/raw/{Y}/{M}/{D}/issues.json (optional, 하위 호환)

        Returns:
            (activities_path, stats_path)

        Raises:
            NormalizeError: prs.json 없음 또는 파싱 실패
        """
        # 1. raw 로드 — PR (필수)
        raw_path = self._config.date_raw_dir(target_date) / "prs.json"
        if not raw_path.exists():
            raise NormalizeError(f"Raw file not found: {raw_path}")

        try:
            raw_data = load_json(raw_path)
        except Exception as e:
            raise NormalizeError(f"Failed to parse {raw_path}: {e}") from e

        prs = [pr_raw_from_dict(d) for d in raw_data]

        # Commit/Issue 로드 (optional — 없으면 빈 리스트, 하위 호환)
        raw_dir = self._config.date_raw_dir(target_date)

        commits_path = raw_dir / "commits.json"
        commits: list[CommitRaw] = []
        if commits_path.exists():
            try:
                commits = [commit_raw_from_dict(d) for d in load_json(commits_path)]
            except Exception:
                logger.warning("Failed to parse %s, skipping commits", commits_path)

        issues_path = raw_dir / "issues.json"
        issues: list[IssueRaw] = []
        if issues_path.exists():
            try:
                issues = [issue_raw_from_dict(d) for d in load_json(issues_path)]
            except Exception:
                logger.warning("Failed to parse %s, skipping issues", issues_path)

        # 2. 각 소스 → Activity 변환
        pr_activities = self._convert_activities(prs, target_date)
        commit_activities = self._convert_commit_activities(commits, target_date)
        issue_activities = self._convert_issue_activities(issues, target_date)

        activities = pr_activities + commit_activities + issue_activities
        activities.sort(key=lambda a: a.ts)

        # 3. DailyStats 계산
        stats = self._compute_stats(activities, target_date)

        # 4. 저장
        out_dir = self._config.date_normalized_dir(target_date)
        activities_path = out_dir / "activities.jsonl"
        stats_path = out_dir / "stats.json"

        save_jsonl(activities, activities_path)
        save_json(stats, stats_path)

        logger.info(
            "Normalized %d activities for %s → %s",
            len(activities), target_date, out_dir,
        )
        return activities_path, stats_path
```

### Activity 변환 로직

```python
    def _convert_activities(
        self, prs: list[PRRaw], target_date: str
    ) -> list[Activity]:
        """
        PR 목록에서 사용자의 활동을 추출하여 Activity 리스트 생성.

        규칙:
          - author == username → PR_AUTHORED (ts = PR created_at)
          - reviews에 username 존재 → PR_REVIEWED (ts = review submitted_at)
          - comments에 username 존재 → PR_COMMENTED (ts = comment created_at)
          - 하나의 PR에서 여러 kind가 나올 수 있음
          - authored + self-reviewed → authored만 (self-review 제외)
          - 각 activity의 ts가 target_date에 해당하지 않으면 제외 (D-4)
        """
        activities: list[Activity] = []

        for pr in prs:
            is_author = pr.author.lower() == self._username.lower()

            # PR_AUTHORED
            if is_author and self._matches_date(pr.created_at, target_date):
                activities.append(self._make_activity(
                    pr, ActivityKind.PR_AUTHORED, pr.created_at
                ))

            # PR_REVIEWED (self-review 제외)
            if not is_author:
                for review in pr.reviews:
                    if (
                        review.author.lower() == self._username.lower()
                        and self._matches_date(review.submitted_at, target_date)
                    ):
                        activities.append(self._make_activity(
                            pr, ActivityKind.PR_REVIEWED, review.submitted_at,
                            evidence_urls=[review.url],
                        ))
                        break  # PR당 1개 review activity

            # PR_COMMENTED
            user_comments = [
                c for c in pr.comments
                if c.author.lower() == self._username.lower()
                and self._matches_date(c.created_at, target_date)
            ]
            if user_comments and not (is_author and not any(
                a.kind == ActivityKind.PR_REVIEWED for a in activities
                if a.pr_number == pr.number
            )):
                # author가 자기 PR에 댓글 단 것도 포함 (디버깅/토론 기록)
                earliest = min(user_comments, key=lambda c: c.created_at)
                activities.append(self._make_activity(
                    pr, ActivityKind.PR_COMMENTED, earliest.created_at,
                    evidence_urls=[c.url for c in user_comments],
                ))

        # 시간순 정렬
        activities.sort(key=lambda a: a.ts)
        return activities
```

### Commit → Activity 변환

```python
    def _convert_commit_activities(
        self, commits: list[CommitRaw], target_date: str
    ) -> list[Activity]:
        """Commit 목록에서 COMMIT Activity를 생성."""
        activities: list[Activity] = []
        for commit in commits:
            if not self._matches_date(commit.committed_at, target_date):
                continue

            # 제목: commit message 첫 줄, 120자 truncate
            first_line = commit.message.split("\n", 1)[0]
            title = first_line[:120] + ("…" if len(first_line) > 120 else "")

            total_adds = sum(f.additions for f in commit.files)
            total_dels = sum(f.deletions for f in commit.files)
            file_names = [f.filename for f in commit.files]

            activities.append(Activity(
                ts=commit.committed_at,
                kind=ActivityKind.COMMIT,
                repo=commit.repo,
                pr_number=0,
                title=title,
                url=commit.url,
                summary=f"commit: {title} ({commit.repo}) +{total_adds}/-{total_dels}",
                sha=commit.sha,
                files=file_names,
                additions=total_adds,
                deletions=total_dels,
            ))
        return activities
```

### Issue → Activity 변환

```python
    def _convert_issue_activities(
        self, issues: list[IssueRaw], target_date: str
    ) -> list[Activity]:
        """Issue 목록에서 ISSUE_AUTHORED / ISSUE_COMMENTED Activity를 생성."""
        activities: list[Activity] = []
        for issue in issues:
            # ISSUE_AUTHORED: author가 본인이고 created_at이 target_date인 경우
            if (
                issue.author.lower() == self._username.lower()
                and self._matches_date(issue.created_at, target_date)
            ):
                activities.append(Activity(
                    ts=issue.created_at,
                    kind=ActivityKind.ISSUE_AUTHORED,
                    repo=issue.repo,
                    pr_number=issue.number,
                    title=issue.title,
                    url=issue.url,
                    summary=f"issue_authored: {issue.title} ({issue.repo})",
                    labels=issue.labels,
                ))

            # ISSUE_COMMENTED: 본인의 comment가 target_date에 존재하는 경우
            user_comments = [
                c for c in issue.comments
                if c.author.lower() == self._username.lower()
                and self._matches_date(c.created_at, target_date)
            ]
            if user_comments:
                earliest = min(user_comments, key=lambda c: c.created_at)
                activities.append(Activity(
                    ts=earliest.created_at,
                    kind=ActivityKind.ISSUE_COMMENTED,
                    repo=issue.repo,
                    pr_number=issue.number,
                    title=issue.title,
                    url=issue.url,
                    summary=f"issue_commented: {issue.title} ({issue.repo})",
                    labels=issue.labels,
                    evidence_urls=[c.url for c in user_comments],
                ))
        return activities
```

### Activity 생성 헬퍼

```python
    def _make_activity(
        self,
        pr: PRRaw,
        kind: ActivityKind,
        ts: str,
        evidence_urls: list[str] | None = None,
    ) -> Activity:
        """PRRaw에서 Activity 객체 생성."""
        total_adds = sum(f.additions for f in pr.files)
        total_dels = sum(f.deletions for f in pr.files)
        file_names = [f.filename for f in pr.files]

        return Activity(
            ts=ts,
            kind=kind,
            repo=pr.repo,
            pr_number=pr.number,
            title=pr.title,
            url=pr.url,
            summary=self._auto_summary(pr, kind, total_adds, total_dels),
            files=file_names,
            additions=total_adds,
            deletions=total_dels,
            labels=pr.labels,
            evidence_urls=evidence_urls or [],
        )
```

### Auto Summary 생성

```python
    @staticmethod
    def _auto_summary(
        pr: PRRaw, kind: ActivityKind, adds: int, dels: int
    ) -> str:
        """
        1줄 자동 요약 생성.

        body가 있으면: "{kind.value}: {title} ({repo}) +{adds}/-{dels}"
        body가 없으면: 파일 경로 기반 디렉토리 추론 (D-2)
        """
        if pr.body and pr.body.strip():
            return f"{kind.value}: {pr.title} ({pr.repo}) +{adds}/-{dels}"

        # body가 없으면 파일 경로에서 주요 디렉토리 추출
        dirs = set()
        for f in pr.files:
            parts = f.filename.split("/")
            if len(parts) > 1:
                dirs.add(parts[0])
            else:
                dirs.add(f.filename)

        dir_hint = ", ".join(sorted(dirs)[:3])
        if len(dirs) > 3:
            dir_hint += " 외"

        return (
            f"{kind.value}: [{dir_hint}] "
            f"{len(pr.files)}개 파일 변경 ({pr.repo}) +{adds}/-{dels}"
        )
```

### 날짜 필터링

```python
    @staticmethod
    def _matches_date(iso_timestamp: str, target_date: str) -> bool:
        """
        ISO 8601 타임스탬프가 target_date(YYYY-MM-DD)에 해당하는지 확인.
        타임스탬프의 날짜 부분만 비교 (timezone 무시, UTC 가정).
        """
        return iso_timestamp[:10] == target_date
```

### DailyStats 계산

```python
    @staticmethod
    def _compute_stats(
        activities: list[Activity], target_date: str
    ) -> DailyStats:
        """activities에서 수치 통계를 계산."""
        authored = [a for a in activities if a.kind == ActivityKind.PR_AUTHORED]
        reviewed = [a for a in activities if a.kind == ActivityKind.PR_REVIEWED]
        commented = [a for a in activities if a.kind == ActivityKind.PR_COMMENTED]
        commits = [a for a in activities if a.kind == ActivityKind.COMMIT]
        issue_authored = [a for a in activities if a.kind == ActivityKind.ISSUE_AUTHORED]
        issue_commented = [a for a in activities if a.kind == ActivityKind.ISSUE_COMMENTED]

        # authored PR + commit의 additions/deletions 합산
        total_adds = (
            sum(a.additions for a in authored)
            + sum(a.additions for a in commits)
        )
        total_dels = (
            sum(a.deletions for a in authored)
            + sum(a.deletions for a in commits)
        )

        repos = sorted(set(a.repo for a in activities))

        return DailyStats(
            date=target_date,
            authored_count=len(authored),
            reviewed_count=len(reviewed),
            commented_count=len(commented),
            total_additions=total_adds,
            total_deletions=total_dels,
            repos_touched=repos,
            authored_prs=[
                {"url": a.url, "title": a.title, "repo": a.repo}
                for a in authored
            ],
            reviewed_prs=[
                {"url": a.url, "title": a.title, "repo": a.repo}
                for a in reviewed
            ],
            commit_count=len(commits),
            issue_authored_count=len(issue_authored),
            issue_commented_count=len(issue_commented),
            commits=[
                {"url": a.url, "title": a.title, "repo": a.repo, "sha": a.sha}
                for a in commits
            ],
            authored_issues=[
                {"url": a.url, "title": a.title, "repo": a.repo}
                for a in issue_authored
            ],
        )
```

---

## 설계 결정 상세

### 활동 분류 규칙

| 조건 | kind | timestamp |
|---|---|---|
| `pr.author == username` | PR_AUTHORED | `pr.created_at` |
| `review.author == username` AND `pr.author != username` | PR_REVIEWED | `review.submitted_at` |
| `comment.author == username` (PR) | PR_COMMENTED | 가장 이른 comment의 `created_at` |
| commit의 `committed_at` | COMMIT | `committed_at` |
| `issue.author == username` | ISSUE_AUTHORED | `issue.created_at` |
| `comment.author == username` (Issue) | ISSUE_COMMENTED | 가장 이른 comment의 `created_at` |

- **self-review 제외**: 자기가 만든 PR에 대한 review는 PR_AUTHORED에 이미 포함
- **PR당 1개 review activity**: 여러 review를 남겨도 하나의 PR_REVIEWED로 집계
- **PR당 1개 comment activity**: 여러 comment를 남겨도 하나의 PR_COMMENTED, evidence_urls에 모든 URL 포함
- **Commit title**: message 첫 줄에서 120자 truncate
- **Issue activity**: 한 Issue에서 ISSUE_AUTHORED + ISSUE_COMMENTED 둘 다 가능

### 날짜 필터링 (D-4)

- Fetcher는 `updated:{date}` 쿼리로 후보 PR을 가져옴
- Normalizer에서 각 activity의 실제 timestamp를 확인
- `ts[:10] != target_date`인 activity는 제외
- 예: PR이 2/15에 생성되고 2/16에 review를 받으면, 2/16 normalize 시 review만 포함

### DailyStats의 additions/deletions

- authored PR + commit의 line count 합산
- reviewed/commented PR, issue의 변경량은 남의 코드이므로 "내가 쓴 코드량"에 포함하지 않음

---

## 테스트 명세

### test_normalizer.py

```python
"""tests/unit/test_normalizer.py"""

# ── 샘플 데이터 팩토리 ──

def _make_pr(
    number=1, author="testuser", title="Test PR",
    body="Description", created_at="2025-02-16T09:00:00Z",
    updated_at="2025-02-16T15:00:00Z",
    files=None, comments=None, reviews=None, labels=None,
) -> PRRaw: ...


class TestConvertActivities:
    def test_authored_pr(self):
        """author == username → PR_AUTHORED activity 생성."""

    def test_reviewed_pr(self):
        """다른 사람의 PR에 review → PR_REVIEWED activity 생성."""

    def test_commented_pr(self):
        """다른 사람의 PR에 comment → PR_COMMENTED activity 생성."""

    def test_self_review_excluded(self):
        """자기 PR에 대한 review는 제외."""

    def test_multiple_kinds_from_one_pr(self):
        """한 PR에서 reviewed + commented 둘 다 생성 가능."""

    def test_date_filtering(self):
        """target_date에 해당하지 않는 activity는 제외."""

    def test_sorted_by_timestamp(self):
        """결과가 시간순으로 정렬."""

    def test_empty_prs(self):
        """빈 PR 목록 → 빈 activity 목록."""

    def test_one_review_per_pr(self):
        """같은 PR에 여러 review를 남겨도 1개 activity."""

    def test_comment_evidence_urls(self):
        """여러 comment의 URL이 evidence_urls에 모두 포함."""


class TestAutoSummary:
    def test_with_body(self):
        """body 있으면 기본 포맷."""

    def test_without_body_file_dirs(self):
        """body 없으면 파일 디렉토리 기반 요약."""

    def test_without_body_many_dirs(self):
        """3개 초과 디렉토리 시 '외' 표시."""

    def test_without_body_root_files(self):
        """루트 레벨 파일은 파일명 자체."""


class TestMatchesDate:
    def test_matching(self):
        assert NormalizerService._matches_date("2025-02-16T09:00:00Z", "2025-02-16") is True

    def test_not_matching(self):
        assert NormalizerService._matches_date("2025-02-15T23:59:59Z", "2025-02-16") is False


class TestConvertCommitActivities:
    def test_commit_activity(self):
        """CommitRaw → COMMIT Activity 생성."""

    def test_commit_title_truncate(self):
        """120자 초과 commit message → truncate + '…'."""

    def test_commit_date_filtering(self):
        """target_date에 해당하지 않는 commit은 제외."""

    def test_commit_sha_preserved(self):
        """Activity.sha에 commit SHA가 보존."""


class TestConvertIssueActivities:
    def test_issue_authored(self):
        """author == username → ISSUE_AUTHORED Activity 생성."""

    def test_issue_commented(self):
        """사용자 comment → ISSUE_COMMENTED Activity 생성."""

    def test_issue_both_kinds(self):
        """한 Issue에서 authored + commented 둘 다 생성 가능."""

    def test_issue_date_filtering(self):
        """target_date에 해당하지 않는 activity는 제외."""


class TestComputeStats:
    def test_counts(self):
        """authored/reviewed/commented/commit/issue count 정확."""

    def test_additions_from_authored_and_commits(self):
        """additions/deletions는 authored PR + commit 합산."""

    def test_repos_touched(self):
        """활동한 repo 목록 (중복 제거, 정렬)."""

    def test_authored_prs_list(self):
        """authored_prs에 url, title, repo 포함."""

    def test_reviewed_prs_list(self):
        """reviewed_prs에 url, title, repo 포함."""

    def test_commits_list(self):
        """commits에 url, title, repo, sha 포함."""

    def test_authored_issues_list(self):
        """authored_issues에 url, title, repo 포함."""

    def test_empty_activities(self):
        """빈 activities → 모든 수치 0."""


class TestNormalize:
    def test_full_pipeline(self, test_config):
        """prs.json + commits.json + issues.json → activities.jsonl + stats.json 생성."""

    def test_raw_file_not_found(self, test_config):
        """raw 파일 없으면 NormalizeError."""

    def test_invalid_json(self, test_config):
        """잘못된 JSON이면 NormalizeError."""

    def test_idempotent(self, test_config):
        """같은 입력으로 두 번 실행해도 동일 결과."""

    def test_missing_commits_json(self, test_config):
        """commits.json 없어도 정상 동작 (하위 호환)."""

    def test_missing_issues_json(self, test_config):
        """issues.json 없어도 정상 동작 (하위 호환)."""

    def test_invalid_commits_json(self, test_config):
        """잘못된 commits.json → 경고 로그 + commit 스킵."""
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 2.1 | `_matches_date()` 구현 | TestMatchesDate |
| 2.2 | `_auto_summary()` 구현 (body 유무에 따른 분기) | TestAutoSummary |
| 2.3 | `_make_activity()` + `_convert_activities()` 구현 (kind 분류, 날짜 필터링, self-review 제외) | TestConvertActivities |
| 2.4 | `_compute_stats()` 구현 (수치 계산, authored만 line count) | TestComputeStats |
| 2.5 | `normalize()` 통합 (로드 → 변환 → 저장) | TestNormalize |
| 2.6 | `_convert_commit_activities()` 구현 (COMMIT kind, title truncate) | TestConvertCommitActivities |
| 2.7 | `_convert_issue_activities()` 구현 (ISSUE_AUTHORED + ISSUE_COMMENTED) | TestConvertIssueActivities |
| 2.8 | `_compute_stats()` 확장 (commit/issue 카운터 + 리스트) | TestComputeStats |
| 2.9 | `normalize()` commits.json/issues.json optional 로딩 (하위 호환) | TestNormalize |
