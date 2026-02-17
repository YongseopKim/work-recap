import pytest

from git_recap.models import (
    Activity,
    ActivityKind,
    Comment,
    CommitRaw,
    DailyStats,
    FileChange,
    IssueRaw,
    Job,
    JobStatus,
    PRRaw,
    Review,
    activity_from_dict,
    commit_raw_from_dict,
    daily_stats_from_dict,
    issue_raw_from_dict,
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
        assert ActivityKind.COMMIT.value == "commit"
        assert ActivityKind.ISSUE_AUTHORED.value == "issue_authored"
        assert ActivityKind.ISSUE_COMMENTED.value == "issue_commented"

    def test_from_string(self):
        assert ActivityKind("pr_authored") == ActivityKind.PR_AUTHORED
        assert ActivityKind("pr_reviewed") == ActivityKind.PR_REVIEWED
        assert ActivityKind("pr_commented") == ActivityKind.PR_COMMENTED
        assert ActivityKind("commit") == ActivityKind.COMMIT
        assert ActivityKind("issue_authored") == ActivityKind.ISSUE_AUTHORED
        assert ActivityKind("issue_commented") == ActivityKind.ISSUE_COMMENTED


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

    def test_roundtrip_with_text_fields(self, tmp_path):
        """body, review_bodies, comment_bodies 포함 왕복 변환."""
        act = Activity(
            ts="2025-02-16T09:00:00Z",
            kind=ActivityKind.PR_REVIEWED,
            repo="org/repo",
            pr_number=1,
            title="Add feature",
            url="u",
            summary="s",
            body="PR description text",
            review_bodies=["LGTM", "Nice work!"],
            comment_bodies=["Needs fix here"],
        )
        path = tmp_path / "act.jsonl"
        save_jsonl([act], path)
        loaded = load_jsonl(path)
        restored = activity_from_dict(loaded[0])
        assert restored.body == "PR description text"
        assert restored.review_bodies == ["LGTM", "Nice work!"]
        assert restored.comment_bodies == ["Needs fix here"]

    def test_backward_compat_no_text_fields(self):
        """옛 데이터(body/review_bodies/comment_bodies 없음)로 from_dict 호출 시 정상 동작."""
        d = {
            "ts": "t", "kind": "pr_authored", "repo": "r",
            "pr_number": 1, "title": "t", "url": "u", "summary": "s",
        }
        act = activity_from_dict(d)
        assert act.body == ""
        assert act.review_bodies == []
        assert act.comment_bodies == []


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


# ── CommitRaw 테스트 ──


def _make_sample_commit_raw() -> CommitRaw:
    return CommitRaw(
        sha="abc123def456",
        url="https://github.example.com/org/repo/commit/abc123def456",
        api_url="https://github.example.com/api/v3/repos/org/repo/commits/abc123def456",
        message="feat: add new feature\n\nDetailed description",
        author="testuser",
        repo="org/repo",
        committed_at="2025-02-16T10:00:00Z",
        files=[
            FileChange("src/feature.py", 20, 5, "modified"),
            FileChange("tests/test_feature.py", 15, 0, "added"),
        ],
    )


class TestCommitRaw:
    def test_creation_minimal(self):
        commit = CommitRaw(
            sha="abc123",
            url="https://example.com/commit/abc123",
            api_url="https://example.com/api/commits/abc123",
            message="fix bug",
            author="user",
            repo="org/repo",
            committed_at="2025-02-16T10:00:00Z",
        )
        assert commit.files == []
        assert commit.sha == "abc123"

    def test_roundtrip_json(self, tmp_path):
        """CommitRaw → JSON → dict → CommitRaw 왕복 변환."""
        commit = _make_sample_commit_raw()
        path = tmp_path / "commits.json"
        save_json([commit], path)
        loaded = load_json(path)
        restored = commit_raw_from_dict(loaded[0])
        assert restored.sha == commit.sha
        assert restored.message == commit.message
        assert restored.author == commit.author
        assert restored.repo == commit.repo
        assert restored.committed_at == commit.committed_at
        assert len(restored.files) == 2
        assert restored.files[0].filename == "src/feature.py"
        assert restored.files[0].additions == 20


# ── IssueRaw 테스트 ──


def _make_sample_issue_raw() -> IssueRaw:
    return IssueRaw(
        url="https://github.example.com/org/repo/issues/10",
        api_url="https://github.example.com/api/v3/repos/org/repo/issues/10",
        number=10,
        title="Bug: login fails",
        body="Steps to reproduce...",
        state="open",
        created_at="2025-02-16T09:00:00Z",
        updated_at="2025-02-16T15:00:00Z",
        closed_at=None,
        repo="org/repo",
        labels=["bug", "priority-high"],
        author="testuser",
        comments=[
            Comment("other", "I can reproduce", "2025-02-16T10:00:00Z",
                    "https://github.example.com/org/repo/issues/10#comment-1"),
        ],
    )


class TestIssueRaw:
    def test_creation_minimal(self):
        issue = IssueRaw(
            url="https://example.com/issues/1",
            api_url="https://example.com/api/issues/1",
            number=1,
            title="Test issue",
            body="",
            state="open",
            created_at="2025-02-16T10:00:00Z",
            updated_at="2025-02-16T10:00:00Z",
            closed_at=None,
            repo="org/repo",
        )
        assert issue.labels == []
        assert issue.author == ""
        assert issue.comments == []

    def test_roundtrip_json(self, tmp_path):
        """IssueRaw → JSON → dict → IssueRaw 왕복 변환."""
        issue = _make_sample_issue_raw()
        path = tmp_path / "issues.json"
        save_json([issue], path)
        loaded = load_json(path)
        restored = issue_raw_from_dict(loaded[0])
        assert restored.number == issue.number
        assert restored.title == issue.title
        assert restored.state == issue.state
        assert restored.closed_at is None
        assert restored.labels == ["bug", "priority-high"]
        assert restored.author == "testuser"
        assert len(restored.comments) == 1
        assert restored.comments[0].author == "other"

    def test_closed_issue_roundtrip(self, tmp_path):
        issue = IssueRaw(
            url="u", api_url="a", number=2, title="t", body="b",
            state="closed", created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-02T00:00:00Z",
            closed_at="2025-01-02T00:00:00Z", repo="org/repo",
        )
        path = tmp_path / "issue.json"
        save_json([issue], path)
        loaded = load_json(path)
        restored = issue_raw_from_dict(loaded[0])
        assert restored.closed_at == "2025-01-02T00:00:00Z"


class TestActivityWithSha:
    def test_sha_default_empty(self):
        act = Activity(
            ts="2025-02-16T10:00:00Z",
            kind=ActivityKind.PR_AUTHORED,
            repo="org/repo",
            pr_number=1,
            title="PR",
            url="u",
            summary="s",
        )
        assert act.sha == ""

    def test_sha_roundtrip(self, tmp_path):
        act = Activity(
            ts="2025-02-16T10:00:00Z",
            kind=ActivityKind.COMMIT,
            repo="org/repo",
            pr_number=0,
            title="feat: add",
            url="u",
            summary="s",
            sha="abc123",
        )
        path = tmp_path / "act.jsonl"
        save_jsonl([act], path)
        loaded = load_jsonl(path)
        restored = activity_from_dict(loaded[0])
        assert restored.sha == "abc123"
        assert restored.kind == ActivityKind.COMMIT

    def test_backward_compat_no_sha(self):
        """옛 데이터(sha 없음)로 from_dict 호출 시 정상 동작."""
        d = {
            "ts": "t", "kind": "pr_authored", "repo": "r",
            "pr_number": 1, "title": "t", "url": "u", "summary": "s",
        }
        act = activity_from_dict(d)
        assert act.sha == ""


class TestDailyStatsExtended:
    def test_new_fields_defaults(self):
        stats = DailyStats(date="2025-02-16")
        assert stats.commit_count == 0
        assert stats.issue_authored_count == 0
        assert stats.issue_commented_count == 0
        assert stats.commits == []
        assert stats.authored_issues == []

    def test_new_fields_roundtrip(self, tmp_path):
        stats = DailyStats(
            date="2025-02-16",
            commit_count=3,
            issue_authored_count=1,
            issue_commented_count=2,
            commits=[{"url": "u", "title": "t", "repo": "r", "sha": "abc"}],
            authored_issues=[{"url": "u", "title": "t", "repo": "r"}],
        )
        path = tmp_path / "stats.json"
        save_json(stats, path)
        loaded = load_json(path)
        restored = daily_stats_from_dict(loaded)
        assert restored.commit_count == 3
        assert restored.issue_authored_count == 1
        assert restored.issue_commented_count == 2
        assert restored.commits == [{"url": "u", "title": "t", "repo": "r", "sha": "abc"}]
        assert restored.authored_issues == [{"url": "u", "title": "t", "repo": "r"}]

    def test_backward_compat_no_new_fields(self):
        """옛 데이터(새 필드 없음)로 from_dict 호출 시 정상 동작."""
        d = {
            "date": "2025-02-16",
            "authored_count": 1,
            "reviewed_count": 0,
            "commented_count": 0,
        }
        stats = daily_stats_from_dict(d)
        assert stats.commit_count == 0
        assert stats.issue_authored_count == 0
        assert stats.issue_commented_count == 0
        assert stats.commits == []
        assert stats.authored_issues == []
