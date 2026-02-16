# Phase 0-4: models.py 상세 설계

## 목적

서비스 간 데이터 교환을 위한 데이터 모델을 정의한다.
모든 모델은 `dataclass`로 구현하며, JSON 직렬화/역직렬화를 지원한다.

---

## 위치

`src/git_recap/models.py`

## 의존성

- `dataclasses` (표준 라이브러리)
- `enum` (표준 라이브러리)
- `json` (표준 라이브러리)

---

## 상세 구현

### Fetcher 출력 모델

```python
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
from pathlib import Path


@dataclass
class FileChange:
    """PR에서 변경된 개별 파일."""
    filename: str
    additions: int
    deletions: int
    status: str                  # "added" | "modified" | "removed" | "renamed"


@dataclass
class Comment:
    """PR에 달린 코멘트."""
    author: str
    body: str
    created_at: str              # ISO 8601
    url: str


@dataclass
class Review:
    """PR 리뷰."""
    author: str
    state: str                   # "APPROVED" | "CHANGES_REQUESTED" | "COMMENTED"
    body: str
    submitted_at: str            # ISO 8601
    url: str


@dataclass
class PRRaw:
    """Fetcher가 수집한 PR 원시 데이터."""
    url: str                     # HTML URL
    api_url: str                 # API URL
    number: int
    title: str
    body: str
    state: str                   # "open" | "closed"
    is_merged: bool
    created_at: str              # ISO 8601
    updated_at: str              # ISO 8601
    merged_at: str | None        # ISO 8601 or None
    repo: str                    # "org/repo-name"
    labels: list[str] = field(default_factory=list)
    author: str = ""
    files: list[FileChange] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
```

### Normalizer 출력 모델

```python
class ActivityKind(str, Enum):
    """활동 유형."""
    PR_AUTHORED = "pr_authored"
    PR_REVIEWED = "pr_reviewed"
    PR_COMMENTED = "pr_commented"


@dataclass
class Activity:
    """정규화된 단일 활동 레코드."""
    ts: str                      # ISO 8601, 활동 발생 시각
    kind: ActivityKind
    repo: str                    # "org/repo-name"
    pr_number: int
    title: str
    url: str                     # PR HTML URL
    summary: str                 # 스크립트가 생성하는 1줄 요약
    files: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    labels: list[str] = field(default_factory=list)
    evidence_urls: list[str] = field(default_factory=list)


@dataclass
class DailyStats:
    """일일 활동 통계. 스크립트가 계산, LLM에 수치 주입용."""
    date: str                    # YYYY-MM-DD
    authored_count: int = 0
    reviewed_count: int = 0
    commented_count: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    repos_touched: list[str] = field(default_factory=list)
    authored_prs: list[dict] = field(default_factory=list)  # [{url, title, repo}]
    reviewed_prs: list[dict] = field(default_factory=list)  # [{url, title, repo}]
```

### 비동기 Job 모델

```python
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
    created_at: str              # ISO 8601
    updated_at: str              # ISO 8601
    result: str | None = None    # 완료 시 결과 경로
    error: str | None = None     # 실패 시 에러 메시지
```

### 직렬화 유틸리티

```python
def _serialize(obj):
    """dataclass/enum JSON 직렬화 헬퍼."""
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def save_json(data, path: Path) -> None:
    """dataclass 또는 list[dataclass]를 JSON으로 저장."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            asdict(data) if not isinstance(data, list) else [asdict(d) for d in data],
            f,
            ensure_ascii=False,
            indent=2,
            default=_serialize,
        )


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
```

### dict → dataclass 복원 팩토리

```python
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
```

---

## 설계 결정

### 왜 dataclass인가 (Pydantic 아닌)

- 서비스 간 데이터 전달 모델은 validation보다 구조 정의가 목적
- `AppConfig`만 pydantic-settings 사용 (환경변수 로딩 필요)
- dataclass는 의존성 없음, `asdict()`로 간편 직렬화

### 왜 from_dict 팩토리 함수인가

- `dataclass`는 nested 객체 역직렬화를 자동 지원하지 않음
- `PRRaw` 안의 `list[FileChange]` 등은 명시적 복원이 필요
- 팩토리 함수로 분리하여 테스트 용이성 확보

---

## 테스트 명세

### test_models.py

```python
"""tests/unit/test_models.py"""

class TestFileChange:
    def test_creation(self):
        fc = FileChange(filename="src/main.py", additions=10, deletions=3, status="modified")
        assert fc.filename == "src/main.py"
        assert fc.additions == 10

class TestPRRaw:
    def test_creation_minimal(self):
        """필수 필드만으로 생성, 리스트 필드는 빈 리스트."""
        pr = PRRaw(
            url="https://github.example.com/org/repo/pull/1",
            api_url="https://github.example.com/api/v3/repos/org/repo/pulls/1",
            number=1,
            title="Add feature",
            body="Description",
            state="open",
            is_merged=False,
            created_at="2025-02-16T10:00:00Z",
            updated_at="2025-02-16T12:00:00Z",
            merged_at=None,
            repo="org/repo",
        )
        assert pr.files == []
        assert pr.comments == []
        assert pr.reviews == []
        assert pr.labels == []

    def test_roundtrip_json(self, tmp_path):
        """PRRaw → JSON → dict → PRRaw 왕복 변환."""
        pr = _make_sample_pr_raw()
        path = tmp_path / "pr.json"
        save_json([pr], path)
        loaded = load_json(path)
        restored = pr_raw_from_dict(loaded[0])
        assert restored.title == pr.title
        assert len(restored.files) == len(pr.files)
        assert restored.files[0].filename == pr.files[0].filename

class TestActivity:
    def test_creation(self):
        act = Activity(
            ts="2025-02-16T10:00:00Z",
            kind=ActivityKind.PR_AUTHORED,
            repo="org/repo",
            pr_number=1,
            title="Add feature",
            url="https://github.example.com/org/repo/pull/1",
            summary="pr_authored: Add feature (org/repo) +10/-3",
        )
        assert act.kind == ActivityKind.PR_AUTHORED
        assert act.kind.value == "pr_authored"

    def test_roundtrip_jsonl(self, tmp_path):
        """Activity → JSONL → dict → Activity 왕복 변환."""
        activities = [_make_sample_activity()]
        path = tmp_path / "activities.jsonl"
        save_jsonl(activities, path)
        loaded = load_jsonl(path)
        restored = activity_from_dict(loaded[0])
        assert restored.kind == activities[0].kind
        assert restored.pr_number == activities[0].pr_number

class TestDailyStats:
    def test_creation_defaults(self):
        stats = DailyStats(date="2025-02-16")
        assert stats.authored_count == 0
        assert stats.repos_touched == []

    def test_roundtrip_json(self, tmp_path):
        stats = DailyStats(
            date="2025-02-16",
            authored_count=3,
            reviewed_count=2,
            commented_count=1,
            total_additions=150,
            total_deletions=30,
            repos_touched=["org/repo-a", "org/repo-b"],
            authored_prs=[{"url": "u", "title": "t", "repo": "r"}],
            reviewed_prs=[],
        )
        path = tmp_path / "stats.json"
        save_json(stats, path)
        loaded = load_json(path)
        restored = daily_stats_from_dict(loaded)
        assert restored.authored_count == 3
        assert restored.repos_touched == ["org/repo-a", "org/repo-b"]

class TestActivityKind:
    def test_enum_values(self):
        assert ActivityKind.PR_AUTHORED.value == "pr_authored"
        assert ActivityKind.PR_REVIEWED.value == "pr_reviewed"
        assert ActivityKind.PR_COMMENTED.value == "pr_commented"

    def test_from_string(self):
        assert ActivityKind("pr_authored") == ActivityKind.PR_AUTHORED

class TestJobStatus:
    def test_enum_values(self):
        assert JobStatus.ACCEPTED.value == "accepted"
        assert JobStatus.RUNNING.value == "running"
        assert JobStatus.COMPLETED.value == "completed"
        assert JobStatus.FAILED.value == "failed"

class TestSerializationUtils:
    def test_save_json_creates_parent_dirs(self, tmp_path):
        """부모 디렉토리가 없으면 자동 생성."""
        path = tmp_path / "a" / "b" / "data.json"
        stats = DailyStats(date="2025-02-16")
        save_json(stats, path)
        assert path.exists()

    def test_save_jsonl_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "a" / "b" / "data.jsonl"
        save_jsonl([], path)
        assert path.exists()

    def test_load_jsonl_skips_empty_lines(self, tmp_path):
        path = tmp_path / "data.jsonl"
        path.write_text('{"a": 1}\n\n{"b": 2}\n')
        result = load_jsonl(path)
        assert len(result) == 2


# ── 테스트 헬퍼 ──

def _make_sample_pr_raw() -> PRRaw:
    return PRRaw(
        url="https://github.example.com/org/repo/pull/1",
        api_url="https://github.example.com/api/v3/repos/org/repo/pulls/1",
        number=1,
        title="Add user authentication",
        body="Implements JWT-based auth",
        state="closed",
        is_merged=True,
        created_at="2025-02-16T09:00:00Z",
        updated_at="2025-02-16T15:00:00Z",
        merged_at="2025-02-16T14:00:00Z",
        repo="org/repo",
        labels=["feature", "auth"],
        author="testuser",
        files=[
            FileChange("src/auth.py", 50, 0, "added"),
            FileChange("tests/test_auth.py", 30, 0, "added"),
        ],
        comments=[
            Comment("reviewer1", "Looks good!", "2025-02-16T11:00:00Z",
                    "https://github.example.com/org/repo/pull/1#comment-1"),
        ],
        reviews=[
            Review("reviewer1", "APPROVED", "", "2025-02-16T12:00:00Z",
                   "https://github.example.com/org/repo/pull/1#review-1"),
        ],
    )


def _make_sample_activity() -> Activity:
    return Activity(
        ts="2025-02-16T09:00:00Z",
        kind=ActivityKind.PR_AUTHORED,
        repo="org/repo",
        pr_number=1,
        title="Add user authentication",
        url="https://github.example.com/org/repo/pull/1",
        summary="pr_authored: Add user authentication (org/repo) +80/-0",
        files=["src/auth.py", "tests/test_auth.py"],
        additions=80,
        deletions=0,
        labels=["feature", "auth"],
        evidence_urls=[],
    )
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 0.4.1 | FileChange, Comment, Review dataclass 구현 | TestFileChange |
| 0.4.2 | PRRaw dataclass 구현 | TestPRRaw (creation + roundtrip) |
| 0.4.3 | ActivityKind enum + Activity dataclass 구현 | TestActivityKind, TestActivity |
| 0.4.4 | DailyStats dataclass 구현 | TestDailyStats |
| 0.4.5 | JobStatus enum + Job dataclass 구현 | TestJobStatus |
| 0.4.6 | 직렬화 유틸 (save_json, save_jsonl, load_json, load_jsonl) | TestSerializationUtils |
| 0.4.7 | 복원 팩토리 (pr_raw_from_dict, activity_from_dict, daily_stats_from_dict) | roundtrip 테스트들 |
