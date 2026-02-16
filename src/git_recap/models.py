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


@dataclass
class Comment:
    """PR에 달린 코멘트."""

    author: str
    body: str
    created_at: str  # ISO 8601
    url: str


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


# ── Normalizer 출력 모델 ──


class ActivityKind(str, Enum):
    """활동 유형."""

    PR_AUTHORED = "pr_authored"
    PR_REVIEWED = "pr_reviewed"
    PR_COMMENTED = "pr_commented"


@dataclass
class Activity:
    """정규화된 단일 활동 레코드."""

    ts: str  # ISO 8601
    kind: ActivityKind
    repo: str  # "org/repo-name"
    pr_number: int
    title: str
    url: str  # PR HTML URL
    summary: str  # 스크립트가 생성하는 1줄 요약
    files: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    labels: list[str] = field(default_factory=list)
    evidence_urls: list[str] = field(default_factory=list)


@dataclass
class DailyStats:
    """일일 활동 통계. 스크립트가 계산, LLM에 수치 주입용."""

    date: str  # YYYY-MM-DD
    authored_count: int = 0
    reviewed_count: int = 0
    commented_count: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    repos_touched: list[str] = field(default_factory=list)
    authored_prs: list[dict] = field(default_factory=list)
    reviewed_prs: list[dict] = field(default_factory=list)


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


def activity_from_dict(d: dict) -> Activity:
    """dict → Activity 복원."""
    return Activity(
        ts=d["ts"],
        kind=ActivityKind(d["kind"]),
        repo=d["repo"],
        pr_number=d["pr_number"],
        title=d["title"],
        url=d["url"],
        summary=d["summary"],
        files=d.get("files", []),
        additions=d.get("additions", 0),
        deletions=d.get("deletions", 0),
        labels=d.get("labels", []),
        evidence_urls=d.get("evidence_urls", []),
    )


def daily_stats_from_dict(d: dict) -> DailyStats:
    """dict → DailyStats 복원."""
    return DailyStats(
        date=d["date"],
        authored_count=d.get("authored_count", 0),
        reviewed_count=d.get("reviewed_count", 0),
        commented_count=d.get("commented_count", 0),
        total_additions=d.get("total_additions", 0),
        total_deletions=d.get("total_deletions", 0),
        repos_touched=d.get("repos_touched", []),
        authored_prs=d.get("authored_prs", []),
        reviewed_prs=d.get("reviewed_prs", []),
    )
