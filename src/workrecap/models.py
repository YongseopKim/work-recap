"""서비스 간 데이터 교환을 위한 데이터 모델 및 직렬화 유틸리티."""

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


# ── Fetcher 출력 모델 ──


@dataclass
class FileChange:
    """PR에서 변경된 개별 파일."""

    filename: str
    additions: int
    deletions: int
    status: str  # "added" | "modified" | "removed" | "renamed"
    patch: str = ""  # unified diff text


@dataclass
class Comment:
    """PR에 달린 코멘트."""

    author: str
    body: str
    created_at: str  # ISO 8601
    url: str
    path: str = ""  # 인라인 리뷰 대상 파일
    line: int = 0  # 인라인 리뷰 대상 라인
    diff_hunk: str = ""  # 코멘트 주변 diff 컨텍스트


@dataclass
class Review:
    """PR 리뷰."""

    author: str
    state: str  # "APPROVED" | "CHANGES_REQUESTED" | "COMMENTED"
    body: str
    submitted_at: str  # ISO 8601
    url: str


@dataclass
class PRRaw:
    """Fetcher가 수집한 PR 원시 데이터."""

    url: str  # HTML URL
    api_url: str  # API URL
    number: int
    title: str
    body: str
    state: str  # "open" | "closed"
    is_merged: bool
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    merged_at: str | None  # ISO 8601 or None
    repo: str  # "org/repo-name"
    labels: list[str] = field(default_factory=list)
    author: str = ""
    files: list[FileChange] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)


@dataclass
class CommitRaw:
    """Fetcher가 수집한 Commit 원시 데이터."""

    sha: str
    url: str  # HTML URL
    api_url: str
    message: str  # full commit message
    author: str  # GitHub login
    repo: str  # "org/repo"
    committed_at: str  # ISO 8601
    files: list[FileChange] = field(default_factory=list)


@dataclass
class IssueRaw:
    """Fetcher가 수집한 Issue 원시 데이터."""

    url: str
    api_url: str
    number: int
    title: str
    body: str
    state: str  # "open" | "closed"
    created_at: str
    updated_at: str
    closed_at: str | None
    repo: str
    labels: list[str] = field(default_factory=list)
    author: str = ""
    comments: list[Comment] = field(default_factory=list)


# ── Normalizer 출력 모델 ──


class ActivityKind(str, Enum):
    """활동 유형."""

    PR_AUTHORED = "pr_authored"
    PR_REVIEWED = "pr_reviewed"
    PR_COMMENTED = "pr_commented"
    COMMIT = "commit"
    ISSUE_AUTHORED = "issue_authored"
    ISSUE_COMMENTED = "issue_commented"
    # Confluence
    CONFLUENCE_PAGE_CREATED = "confluence_page_created"
    CONFLUENCE_PAGE_EDITED = "confluence_page_edited"
    CONFLUENCE_COMMENT = "confluence_comment"
    # Jira
    JIRA_TICKET_CREATED = "jira_ticket_created"
    JIRA_TICKET_UPDATED = "jira_ticket_updated"
    JIRA_TICKET_COMMENTED = "jira_ticket_commented"


@dataclass
class Activity:
    """정규화된 단일 활동 레코드."""

    ts: str  # ISO 8601
    kind: ActivityKind
    repo: str  # "org/repo-name"
    external_id: int  # PR number, issue number, page id 등 소스별 식별자
    title: str
    url: str  # HTML URL
    summary: str  # 스크립트가 생성하는 1줄 요약
    sha: str = ""  # commit SHA (COMMIT kind만 사용)
    body: str = ""  # PR body / commit message / issue body
    review_bodies: list[str] = field(default_factory=list)  # user의 review 본문
    comment_bodies: list[str] = field(default_factory=list)  # user의 comment 본문
    files: list[str] = field(default_factory=list)
    file_patches: dict[str, str] = field(default_factory=dict)  # filename → patch
    additions: int = 0
    deletions: int = 0
    labels: list[str] = field(default_factory=list)
    evidence_urls: list[str] = field(default_factory=list)
    comment_contexts: list[dict] = field(default_factory=list)  # [{path, line, diff_hunk, body}]
    change_summary: str = ""  # LLM 생성 코드 변경 요약
    intent: str = ""  # LLM 분류: bugfix, feature, refactor, docs, chore, test, etc.
    source: str = "github"  # 데이터 소스: github, confluence, jira


@dataclass
class GitHubStats:
    """GitHub 활동 통계."""

    authored_count: int = 0
    reviewed_count: int = 0
    commented_count: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    repos_touched: list[str] = field(default_factory=list)
    authored_prs: list[dict] = field(default_factory=list)
    reviewed_prs: list[dict] = field(default_factory=list)
    commit_count: int = 0
    issue_authored_count: int = 0
    issue_commented_count: int = 0
    commits: list[dict] = field(default_factory=list)  # [{url, title, repo, sha}]
    authored_issues: list[dict] = field(default_factory=list)  # [{url, title, repo}]


@dataclass
class ConfluenceStats:
    """Confluence 활동 통계 (스텁)."""

    pages_created: int = 0
    pages_edited: int = 0
    comments_added: int = 0


@dataclass
class JiraStats:
    """Jira 활동 통계 (스텁)."""

    tickets_created: int = 0
    tickets_updated: int = 0
    tickets_commented: int = 0


@dataclass
class DailyStats:
    """일일 활동 통계. 소스별 중첩 구조."""

    date: str  # YYYY-MM-DD
    github: GitHubStats = field(default_factory=GitHubStats)
    confluence: ConfluenceStats = field(default_factory=ConfluenceStats)
    jira: JiraStats = field(default_factory=JiraStats)


# ── 비동기 Job 모델 ──


class JobStatus(str, Enum):
    """async job 상태."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    """비동기 작업 상태 추적."""

    job_id: str
    status: JobStatus
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    result: str | None = None
    error: str | None = None


# ── LLM 토큰 사용량 ──


@dataclass
class TokenUsage:
    """LLM API 토큰 사용량 추적."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            call_count=self.call_count + other.call_count,
        )


@dataclass
class ModelUsage:
    """Per-model LLM usage tracking with cost estimation."""

    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0
    estimated_cost_usd: float = 0.0


# ── 직렬화 유틸리티 ──


def _serialize(obj):
    """dataclass/enum JSON 직렬화 헬퍼."""
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def save_json(data, path: Path) -> None:
    """dataclass 또는 list[dataclass]를 JSON으로 저장."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(data) if not isinstance(data, list) else [asdict(d) for d in data]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_serialize)


def save_jsonl(items: list, path: Path) -> None:
    """list[dataclass]를 JSONL로 저장."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            line = json.dumps(asdict(item), ensure_ascii=False, default=_serialize)
            f.write(line + "\n")


def load_json(path: Path) -> dict | list:
    """JSON 파일 로드."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> list[dict]:
    """JSONL 파일 로드. 각 라인을 dict로 반환."""
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


# ── dict → dataclass 복원 팩토리 ──


def pr_raw_from_dict(d: dict) -> PRRaw:
    """dict → PRRaw 복원."""
    return PRRaw(
        url=d["url"],
        api_url=d["api_url"],
        number=d["number"],
        title=d["title"],
        body=d["body"],
        state=d["state"],
        is_merged=d["is_merged"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        merged_at=d.get("merged_at"),
        repo=d["repo"],
        labels=d.get("labels", []),
        author=d.get("author", ""),
        files=[FileChange(**f) for f in d.get("files", [])],
        comments=[Comment(**c) for c in d.get("comments", [])],
        reviews=[Review(**r) for r in d.get("reviews", [])],
    )


def commit_raw_from_dict(d: dict) -> CommitRaw:
    """dict → CommitRaw 복원."""
    return CommitRaw(
        sha=d["sha"],
        url=d["url"],
        api_url=d["api_url"],
        message=d["message"],
        author=d["author"],
        repo=d["repo"],
        committed_at=d["committed_at"],
        files=[FileChange(**f) for f in d.get("files", [])],
    )


def issue_raw_from_dict(d: dict) -> IssueRaw:
    """dict → IssueRaw 복원."""
    return IssueRaw(
        url=d["url"],
        api_url=d["api_url"],
        number=d["number"],
        title=d["title"],
        body=d["body"],
        state=d["state"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        closed_at=d.get("closed_at"),
        repo=d["repo"],
        labels=d.get("labels", []),
        author=d.get("author", ""),
        comments=[Comment(**c) for c in d.get("comments", [])],
    )


def activity_from_dict(d: dict) -> Activity:
    """dict → Activity 복원."""
    return Activity(
        ts=d["ts"],
        kind=ActivityKind(d["kind"]),
        repo=d["repo"],
        external_id=d.get("external_id", d.get("pr_number", 0)),
        title=d["title"],
        url=d["url"],
        summary=d["summary"],
        sha=d.get("sha", ""),
        body=d.get("body", ""),
        review_bodies=d.get("review_bodies", []),
        comment_bodies=d.get("comment_bodies", []),
        files=d.get("files", []),
        file_patches=d.get("file_patches", {}),
        additions=d.get("additions", 0),
        deletions=d.get("deletions", 0),
        labels=d.get("labels", []),
        evidence_urls=d.get("evidence_urls", []),
        comment_contexts=d.get("comment_contexts", []),
        change_summary=d.get("change_summary", ""),
        intent=d.get("intent", ""),
        source=d.get("source", "github"),
    )


def github_stats_from_dict(d: dict) -> GitHubStats:
    """dict → GitHubStats 복원."""
    return GitHubStats(
        authored_count=d.get("authored_count", 0),
        reviewed_count=d.get("reviewed_count", 0),
        commented_count=d.get("commented_count", 0),
        total_additions=d.get("total_additions", 0),
        total_deletions=d.get("total_deletions", 0),
        repos_touched=d.get("repos_touched", []),
        authored_prs=d.get("authored_prs", []),
        reviewed_prs=d.get("reviewed_prs", []),
        commit_count=d.get("commit_count", 0),
        issue_authored_count=d.get("issue_authored_count", 0),
        issue_commented_count=d.get("issue_commented_count", 0),
        commits=d.get("commits", []),
        authored_issues=d.get("authored_issues", []),
    )


def confluence_stats_from_dict(d: dict) -> ConfluenceStats:
    """dict → ConfluenceStats 복원."""
    return ConfluenceStats(
        pages_created=d.get("pages_created", 0),
        pages_edited=d.get("pages_edited", 0),
        comments_added=d.get("comments_added", 0),
    )


def jira_stats_from_dict(d: dict) -> JiraStats:
    """dict → JiraStats 복원."""
    return JiraStats(
        tickets_created=d.get("tickets_created", 0),
        tickets_updated=d.get("tickets_updated", 0),
        tickets_commented=d.get("tickets_commented", 0),
    )


def daily_stats_from_dict(d: dict) -> DailyStats:
    """dict → DailyStats 복원. flat(기존) 및 nested(신규) 포맷 모두 지원."""
    if "github" in d:
        # 신규 중첩 포맷
        return DailyStats(
            date=d["date"],
            github=github_stats_from_dict(d["github"]),
            confluence=confluence_stats_from_dict(d.get("confluence", {})),
            jira=jira_stats_from_dict(d.get("jira", {})),
        )
    else:
        # 기존 flat 포맷 → GitHubStats로 마이그레이션
        return DailyStats(
            date=d["date"],
            github=github_stats_from_dict(d),
        )
