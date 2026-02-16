import pytest

from git_recap.models import (
    Activity,
    ActivityKind,
    Comment,
    DailyStats,
    FileChange,
    Job,
    JobStatus,
    PRRaw,
    Review,
    activity_from_dict,
    daily_stats_from_dict,
    load_json,
    load_jsonl,
    pr_raw_from_dict,
    save_json,
    save_jsonl,
)


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
            Comment(
                "reviewer1",
                "Looks good!",
                "2025-02-16T11:00:00Z",
                "https://github.example.com/org/repo/pull/1#comment-1",
            ),
        ],
        reviews=[
            Review(
                "reviewer1",
                "APPROVED",
                "",
                "2025-02-16T12:00:00Z",
                "https://github.example.com/org/repo/pull/1#review-1",
            ),
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


# ── 테스트 ──


class TestFileChange:
    def test_creation(self):
        fc = FileChange(filename="src/main.py", additions=10, deletions=3, status="modified")
        assert fc.filename == "src/main.py"
        assert fc.additions == 10
        assert fc.deletions == 3
        assert fc.status == "modified"


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
        assert pr.author == ""

    def test_roundtrip_json(self, tmp_path):
        """PRRaw → JSON → dict → PRRaw 왕복 변환."""
        pr = _make_sample_pr_raw()
        path = tmp_path / "pr.json"
        save_json([pr], path)
        loaded = load_json(path)
        restored = pr_raw_from_dict(loaded[0])
        assert restored.title == pr.title
        assert restored.number == pr.number
        assert restored.is_merged == pr.is_merged
        assert restored.merged_at == pr.merged_at
        assert len(restored.files) == len(pr.files)
        assert restored.files[0].filename == pr.files[0].filename
        assert restored.files[0].additions == pr.files[0].additions
        assert len(restored.comments) == 1
        assert restored.comments[0].author == "reviewer1"
        assert len(restored.reviews) == 1
        assert restored.reviews[0].state == "APPROVED"
        assert restored.labels == ["feature", "auth"]


class TestActivityKind:
    def test_enum_values(self):
        assert ActivityKind.PR_AUTHORED.value == "pr_authored"
        assert ActivityKind.PR_REVIEWED.value == "pr_reviewed"
        assert ActivityKind.PR_COMMENTED.value == "pr_commented"

    def test_from_string(self):
        assert ActivityKind("pr_authored") == ActivityKind.PR_AUTHORED
        assert ActivityKind("pr_reviewed") == ActivityKind.PR_REVIEWED
        assert ActivityKind("pr_commented") == ActivityKind.PR_COMMENTED


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
        assert act.files == []
        assert act.additions == 0

    def test_roundtrip_jsonl(self, tmp_path):
        """Activity → JSONL → dict → Activity 왕복 변환."""
        activities = [_make_sample_activity()]
        path = tmp_path / "activities.jsonl"
        save_jsonl(activities, path)
        loaded = load_jsonl(path)
        assert len(loaded) == 1
        restored = activity_from_dict(loaded[0])
        assert restored.kind == activities[0].kind
        assert restored.pr_number == activities[0].pr_number
        assert restored.files == activities[0].files
        assert restored.additions == activities[0].additions
        assert restored.labels == activities[0].labels


class TestDailyStats:
    def test_creation_defaults(self):
        stats = DailyStats(date="2025-02-16")
        assert stats.authored_count == 0
        assert stats.reviewed_count == 0
        assert stats.commented_count == 0
        assert stats.total_additions == 0
        assert stats.total_deletions == 0
        assert stats.repos_touched == []
        assert stats.authored_prs == []
        assert stats.reviewed_prs == []

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
        assert restored.reviewed_count == 2
        assert restored.commented_count == 1
        assert restored.total_additions == 150
        assert restored.total_deletions == 30
        assert restored.repos_touched == ["org/repo-a", "org/repo-b"]
        assert restored.authored_prs == [{"url": "u", "title": "t", "repo": "r"}]


class TestJobStatus:
    def test_enum_values(self):
        assert JobStatus.ACCEPTED.value == "accepted"
        assert JobStatus.RUNNING.value == "running"
        assert JobStatus.COMPLETED.value == "completed"
        assert JobStatus.FAILED.value == "failed"

    def test_job_creation(self):
        job = Job(
            job_id="abc-123",
            status=JobStatus.ACCEPTED,
            created_at="2025-02-16T10:00:00Z",
            updated_at="2025-02-16T10:00:00Z",
        )
        assert job.result is None
        assert job.error is None


class TestSerializationUtils:
    def test_save_json_creates_parent_dirs(self, tmp_path):
        """부모 디렉토리가 없으면 자동 생성."""
        path = tmp_path / "a" / "b" / "data.json"
        stats = DailyStats(date="2025-02-16")
        save_json(stats, path)
        assert path.exists()
        loaded = load_json(path)
        assert loaded["date"] == "2025-02-16"

    def test_save_jsonl_creates_parent_dirs(self, tmp_path):
        """부모 디렉토리가 없으면 자동 생성."""
        path = tmp_path / "a" / "b" / "data.jsonl"
        save_jsonl([], path)
        assert path.exists()

    def test_load_jsonl_skips_empty_lines(self, tmp_path):
        """빈 라인은 무시."""
        path = tmp_path / "data.jsonl"
        path.write_text('{"a": 1}\n\n{"b": 2}\n')
        result = load_jsonl(path)
        assert len(result) == 2
        assert result[0] == {"a": 1}
        assert result[1] == {"b": 2}

    def test_save_json_single_dataclass(self, tmp_path):
        """단일 dataclass도 저장 가능."""
        path = tmp_path / "single.json"
        stats = DailyStats(date="2025-02-16", authored_count=5)
        save_json(stats, path)
        loaded = load_json(path)
        assert loaded["authored_count"] == 5

    def test_save_json_list_of_dataclass(self, tmp_path):
        """list[dataclass]도 저장 가능."""
        path = tmp_path / "list.json"
        prs = [_make_sample_pr_raw()]
        save_json(prs, path)
        loaded = load_json(path)
        assert isinstance(loaded, list)
        assert len(loaded) == 1
        assert loaded[0]["title"] == "Add user authentication"
