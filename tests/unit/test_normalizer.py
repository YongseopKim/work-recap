from pathlib import Path

import pytest

from git_recap.exceptions import NormalizeError
from git_recap.models import (
    Activity,
    ActivityKind,
    Comment,
    DailyStats,
    FileChange,
    PRRaw,
    load_json,
    load_jsonl,
    save_json,
)
from git_recap.services.normalizer import NormalizerService


# ── 헬퍼 ──

DATE = "2025-02-16"


def _make_pr(
    number=1,
    author="testuser",
    title="Test PR",
    body="Description",
    repo="org/repo",
    created_at="2025-02-16T09:00:00Z",
    updated_at="2025-02-16T15:00:00Z",
    files=None,
    comments=None,
    reviews=None,
    labels=None,
) -> PRRaw:
    return PRRaw(
        url=f"https://ghes/{repo}/pull/{number}",
        api_url=f"https://ghes/api/v3/repos/{repo}/pulls/{number}",
        number=number,
        title=title,
        body=body,
        state="closed",
        is_merged=True,
        created_at=created_at,
        updated_at=updated_at,
        merged_at=updated_at,
        repo=repo,
        labels=labels or [],
        author=author,
        files=files or [FileChange("src/main.py", 10, 3, "modified")],
        comments=comments or [],
        reviews=reviews or [],
    )


def _review(author="reviewer1", submitted_at="2025-02-16T12:00:00Z"):
    from git_recap.models import Review
    return Review(
        author=author,
        state="APPROVED",
        body="",
        submitted_at=submitted_at,
        url=f"https://ghes/org/repo/pull/1#review-{author}",
    )


def _comment(author="commenter1", created_at="2025-02-16T11:00:00Z", body="Good"):
    return Comment(
        author=author,
        body=body,
        created_at=created_at,
        url=f"https://ghes/org/repo/pull/1#comment-{author}",
    )


@pytest.fixture
def normalizer(test_config):
    return NormalizerService(test_config)


def _save_raw(test_config, prs: list[PRRaw], date: str = DATE):
    """테스트용 raw prs.json 저장."""
    raw_dir = test_config.date_raw_dir(date)
    save_json(prs, raw_dir / "prs.json")


# ── Tests ──


class TestMatchesDate:
    def test_matching(self):
        assert NormalizerService._matches_date("2025-02-16T09:00:00Z", "2025-02-16") is True

    def test_not_matching(self):
        assert NormalizerService._matches_date("2025-02-15T23:59:59Z", "2025-02-16") is False

    def test_exact_boundary(self):
        assert NormalizerService._matches_date("2025-02-16T00:00:00Z", "2025-02-16") is True


class TestAutoSummary:
    def test_with_body(self):
        pr = _make_pr(body="Has description")
        result = NormalizerService._auto_summary(pr, ActivityKind.PR_AUTHORED, 10, 3)
        assert result == "pr_authored: Test PR (org/repo) +10/-3"

    def test_without_body_file_dirs(self):
        pr = _make_pr(
            body="",
            files=[
                FileChange("src/main.py", 5, 1, "modified"),
                FileChange("tests/test_main.py", 10, 0, "added"),
            ],
        )
        result = NormalizerService._auto_summary(pr, ActivityKind.PR_AUTHORED, 15, 1)
        assert "[src, tests]" in result
        assert "2개 파일 변경" in result
        assert "+15/-1" in result

    def test_without_body_many_dirs(self):
        pr = _make_pr(
            body="",
            files=[
                FileChange("a/1.py", 1, 0, "added"),
                FileChange("b/2.py", 1, 0, "added"),
                FileChange("c/3.py", 1, 0, "added"),
                FileChange("d/4.py", 1, 0, "added"),
            ],
        )
        result = NormalizerService._auto_summary(pr, ActivityKind.PR_AUTHORED, 4, 0)
        assert "외" in result

    def test_without_body_root_files(self):
        pr = _make_pr(
            body="",
            files=[FileChange("README.md", 1, 0, "modified")],
        )
        result = NormalizerService._auto_summary(pr, ActivityKind.PR_AUTHORED, 1, 0)
        assert "README.md" in result

    def test_whitespace_only_body(self):
        pr = _make_pr(body="   \n  ")
        result = NormalizerService._auto_summary(pr, ActivityKind.PR_AUTHORED, 10, 3)
        # whitespace-only body → fallback
        assert "파일 변경" in result


class TestConvertActivities:
    def test_authored_pr(self, normalizer):
        prs = [_make_pr(author="testuser")]
        result = normalizer._convert_activities(prs, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.PR_AUTHORED
        assert result[0].ts == "2025-02-16T09:00:00Z"

    def test_reviewed_pr(self, normalizer):
        prs = [_make_pr(
            author="other",
            reviews=[_review(author="testuser")],
        )]
        result = normalizer._convert_activities(prs, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.PR_REVIEWED

    def test_commented_pr(self, normalizer):
        prs = [_make_pr(
            author="other",
            comments=[_comment(author="testuser")],
        )]
        result = normalizer._convert_activities(prs, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.PR_COMMENTED

    def test_self_review_excluded(self, normalizer):
        """자기 PR에 대한 review는 PR_REVIEWED 생성 안함."""
        prs = [_make_pr(
            author="testuser",
            reviews=[_review(author="testuser")],
        )]
        result = normalizer._convert_activities(prs, DATE)
        kinds = [a.kind for a in result]
        assert ActivityKind.PR_REVIEWED not in kinds
        assert ActivityKind.PR_AUTHORED in kinds

    def test_multiple_kinds_from_one_pr(self, normalizer):
        """한 PR에서 reviewed + commented 가능."""
        prs = [_make_pr(
            author="other",
            reviews=[_review(author="testuser")],
            comments=[_comment(author="testuser")],
        )]
        result = normalizer._convert_activities(prs, DATE)
        kinds = {a.kind for a in result}
        assert ActivityKind.PR_REVIEWED in kinds
        assert ActivityKind.PR_COMMENTED in kinds

    def test_date_filtering(self, normalizer):
        """target_date에 해당하지 않는 activity는 제외."""
        prs = [_make_pr(
            author="testuser",
            created_at="2025-02-15T09:00:00Z",  # 전날
        )]
        result = normalizer._convert_activities(prs, DATE)
        assert len(result) == 0

    def test_sorted_by_timestamp(self, normalizer):
        prs = [
            _make_pr(number=1, author="testuser", created_at="2025-02-16T15:00:00Z"),
            _make_pr(number=2, author="testuser", created_at="2025-02-16T09:00:00Z"),
        ]
        result = normalizer._convert_activities(prs, DATE)
        assert result[0].ts < result[1].ts

    def test_empty_prs(self, normalizer):
        result = normalizer._convert_activities([], DATE)
        assert result == []

    def test_one_review_per_pr(self, normalizer):
        """같은 PR에 여러 review를 남겨도 1개 activity."""
        prs = [_make_pr(
            author="other",
            reviews=[
                _review(author="testuser", submitted_at="2025-02-16T10:00:00Z"),
                _review(author="testuser", submitted_at="2025-02-16T14:00:00Z"),
            ],
        )]
        result = normalizer._convert_activities(prs, DATE)
        reviewed = [a for a in result if a.kind == ActivityKind.PR_REVIEWED]
        assert len(reviewed) == 1

    def test_comment_evidence_urls(self, normalizer):
        """여러 comment의 URL이 evidence_urls에 모두 포함."""
        prs = [_make_pr(
            author="other",
            comments=[
                _comment(author="testuser", created_at="2025-02-16T10:00:00Z"),
                _comment(author="testuser", created_at="2025-02-16T11:00:00Z"),
            ],
        )]
        result = normalizer._convert_activities(prs, DATE)
        commented = [a for a in result if a.kind == ActivityKind.PR_COMMENTED]
        assert len(commented) == 1
        assert len(commented[0].evidence_urls) == 2

    def test_case_insensitive_username(self, normalizer):
        """username 비교는 대소문자 무시."""
        prs = [_make_pr(author="TestUser")]
        result = normalizer._convert_activities(prs, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.PR_AUTHORED

    def test_author_commenting_own_pr(self, normalizer):
        """author가 자기 PR에 댓글도 PR_COMMENTED 생성."""
        prs = [_make_pr(
            author="testuser",
            comments=[_comment(author="testuser")],
        )]
        result = normalizer._convert_activities(prs, DATE)
        kinds = {a.kind for a in result}
        assert ActivityKind.PR_AUTHORED in kinds
        assert ActivityKind.PR_COMMENTED in kinds


class TestComputeStats:
    def test_counts(self):
        activities = [
            Activity(ts="t", kind=ActivityKind.PR_AUTHORED, repo="r", pr_number=1,
                     title="t", url="u", summary="s", additions=10, deletions=2),
            Activity(ts="t", kind=ActivityKind.PR_AUTHORED, repo="r", pr_number=2,
                     title="t", url="u", summary="s", additions=20, deletions=5),
            Activity(ts="t", kind=ActivityKind.PR_REVIEWED, repo="r", pr_number=3,
                     title="t", url="u", summary="s", additions=50, deletions=10),
            Activity(ts="t", kind=ActivityKind.PR_COMMENTED, repo="r2", pr_number=4,
                     title="t", url="u", summary="s"),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.authored_count == 2
        assert stats.reviewed_count == 1
        assert stats.commented_count == 1

    def test_additions_only_from_authored(self):
        activities = [
            Activity(ts="t", kind=ActivityKind.PR_AUTHORED, repo="r", pr_number=1,
                     title="t", url="u", summary="s", additions=10, deletions=2),
            Activity(ts="t", kind=ActivityKind.PR_REVIEWED, repo="r", pr_number=2,
                     title="t", url="u", summary="s", additions=100, deletions=50),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.total_additions == 10
        assert stats.total_deletions == 2

    def test_repos_touched(self):
        activities = [
            Activity(ts="t", kind=ActivityKind.PR_AUTHORED, repo="org/b", pr_number=1,
                     title="t", url="u", summary="s"),
            Activity(ts="t", kind=ActivityKind.PR_REVIEWED, repo="org/a", pr_number=2,
                     title="t", url="u", summary="s"),
            Activity(ts="t", kind=ActivityKind.PR_COMMENTED, repo="org/b", pr_number=3,
                     title="t", url="u", summary="s"),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.repos_touched == ["org/a", "org/b"]

    def test_authored_prs_list(self):
        activities = [
            Activity(ts="t", kind=ActivityKind.PR_AUTHORED, repo="org/r", pr_number=1,
                     title="PR1", url="u1", summary="s"),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert len(stats.authored_prs) == 1
        assert stats.authored_prs[0] == {"url": "u1", "title": "PR1", "repo": "org/r"}

    def test_reviewed_prs_list(self):
        activities = [
            Activity(ts="t", kind=ActivityKind.PR_REVIEWED, repo="org/r", pr_number=2,
                     title="PR2", url="u2", summary="s"),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert len(stats.reviewed_prs) == 1
        assert stats.reviewed_prs[0] == {"url": "u2", "title": "PR2", "repo": "org/r"}

    def test_empty_activities(self):
        stats = NormalizerService._compute_stats([], DATE)
        assert stats.authored_count == 0
        assert stats.reviewed_count == 0
        assert stats.commented_count == 0
        assert stats.total_additions == 0
        assert stats.total_deletions == 0
        assert stats.repos_touched == []


class TestNormalize:
    def test_full_pipeline(self, normalizer, test_config):
        prs = [
            _make_pr(number=1, author="testuser"),
            _make_pr(number=2, author="other", reviews=[_review(author="testuser")]),
        ]
        _save_raw(test_config, prs)

        act_path, stats_path = normalizer.normalize(DATE)

        assert act_path.exists()
        assert stats_path.exists()

        activities = load_jsonl(act_path)
        assert len(activities) == 2

        stats = load_json(stats_path)
        assert stats["authored_count"] == 1
        assert stats["reviewed_count"] == 1
        assert stats["date"] == DATE

    def test_raw_file_not_found(self, normalizer):
        with pytest.raises(NormalizeError, match="Raw file not found"):
            normalizer.normalize("2099-01-01")

    def test_invalid_json(self, normalizer, test_config):
        raw_dir = test_config.date_raw_dir(DATE)
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "prs.json").write_text("not json!!!")

        with pytest.raises(NormalizeError, match="Failed to parse"):
            normalizer.normalize(DATE)

    def test_idempotent(self, normalizer, test_config):
        prs = [_make_pr(author="testuser")]
        _save_raw(test_config, prs)

        act1, stats1 = normalizer.normalize(DATE)
        data1 = load_jsonl(act1)

        act2, stats2 = normalizer.normalize(DATE)
        data2 = load_jsonl(act2)

        assert data1 == data2

    def test_empty_prs(self, normalizer, test_config):
        _save_raw(test_config, [])

        act_path, stats_path = normalizer.normalize(DATE)
        activities = load_jsonl(act_path)
        stats = load_json(stats_path)

        assert activities == []
        assert stats["authored_count"] == 0


# load_jsonl import
from git_recap.models import load_jsonl
