import json
from unittest.mock import MagicMock

import pytest

from git_recap.exceptions import NormalizeError
from git_recap.models import (
    Activity,
    ActivityKind,
    Comment,
    CommitRaw,
    FileChange,
    IssueRaw,
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


def _comment(
    author="commenter1",
    created_at="2025-02-16T11:00:00Z",
    body="Good",
    path="",
    line=0,
    diff_hunk="",
):
    return Comment(
        author=author,
        body=body,
        created_at=created_at,
        url=f"https://ghes/org/repo/pull/1#comment-{author}",
        path=path,
        line=line,
        diff_hunk=diff_hunk,
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
        prs = [
            _make_pr(
                author="other",
                reviews=[_review(author="testuser")],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.PR_REVIEWED

    def test_commented_pr(self, normalizer):
        prs = [
            _make_pr(
                author="other",
                comments=[_comment(author="testuser")],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.PR_COMMENTED

    def test_self_review_excluded(self, normalizer):
        """자기 PR에 대한 review는 PR_REVIEWED 생성 안함."""
        prs = [
            _make_pr(
                author="testuser",
                reviews=[_review(author="testuser")],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        kinds = [a.kind for a in result]
        assert ActivityKind.PR_REVIEWED not in kinds
        assert ActivityKind.PR_AUTHORED in kinds

    def test_multiple_kinds_from_one_pr(self, normalizer):
        """한 PR에서 reviewed + commented 가능."""
        prs = [
            _make_pr(
                author="other",
                reviews=[_review(author="testuser")],
                comments=[_comment(author="testuser")],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        kinds = {a.kind for a in result}
        assert ActivityKind.PR_REVIEWED in kinds
        assert ActivityKind.PR_COMMENTED in kinds

    def test_date_filtering(self, normalizer):
        """target_date에 해당하지 않는 activity는 제외."""
        prs = [
            _make_pr(
                author="testuser",
                created_at="2025-02-15T09:00:00Z",  # 전날
            )
        ]
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
        prs = [
            _make_pr(
                author="other",
                reviews=[
                    _review(author="testuser", submitted_at="2025-02-16T10:00:00Z"),
                    _review(author="testuser", submitted_at="2025-02-16T14:00:00Z"),
                ],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        reviewed = [a for a in result if a.kind == ActivityKind.PR_REVIEWED]
        assert len(reviewed) == 1

    def test_comment_evidence_urls(self, normalizer):
        """여러 comment의 URL이 evidence_urls에 모두 포함."""
        prs = [
            _make_pr(
                author="other",
                comments=[
                    _comment(author="testuser", created_at="2025-02-16T10:00:00Z"),
                    _comment(author="testuser", created_at="2025-02-16T11:00:00Z"),
                ],
            )
        ]
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
        prs = [
            _make_pr(
                author="testuser",
                comments=[_comment(author="testuser")],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        kinds = {a.kind for a in result}
        assert ActivityKind.PR_AUTHORED in kinds
        assert ActivityKind.PR_COMMENTED in kinds

    def test_commented_pr_comment_bodies(self, normalizer):
        """PR_COMMENTED에 comment_bodies가 user의 코멘트 본문을 포함."""
        prs = [
            _make_pr(
                author="other",
                comments=[
                    _comment(
                        author="testuser",
                        created_at="2025-02-16T10:00:00Z",
                        body="This needs a fix",
                    ),
                    _comment(
                        author="testuser",
                        created_at="2025-02-16T11:00:00Z",
                        body="Actually, looks fine now",
                    ),
                ],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        commented = [a for a in result if a.kind == ActivityKind.PR_COMMENTED]
        assert len(commented) == 1
        assert commented[0].comment_bodies == ["This needs a fix", "Actually, looks fine now"]

    def test_authored_pr_body(self, normalizer):
        """PR_AUTHORED에 body == pr.body 검증."""
        prs = [_make_pr(author="testuser", body="Implements JWT-based auth")]
        result = normalizer._convert_activities(prs, DATE)
        assert result[0].kind == ActivityKind.PR_AUTHORED
        assert result[0].body == "Implements JWT-based auth"

    def test_reviewed_pr_review_bodies(self, normalizer):
        """PR_REVIEWED에 review_bodies가 reviewer의 review body를 포함."""
        prs = [
            _make_pr(
                author="other",
                reviews=[_review(author="testuser")],
            )
        ]
        # _review의 기본 body는 "" — 실제 리뷰 body 지정
        prs[0].reviews[0].body = "LGTM, nice work!"
        result = normalizer._convert_activities(prs, DATE)
        reviewed = [a for a in result if a.kind == ActivityKind.PR_REVIEWED]
        assert len(reviewed) == 1
        assert reviewed[0].review_bodies == ["LGTM, nice work!"]
        assert reviewed[0].body == "Description"  # PR body도 보존

    def test_file_patches_in_authored(self, normalizer):
        """PR_AUTHORED에 file_patches 전달."""
        prs = [
            _make_pr(
                author="testuser",
                files=[
                    FileChange("src/auth.py", 5, 2, "modified", patch="@@ +1 @@\n+new"),
                    FileChange("README.md", 1, 0, "modified"),
                ],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        assert result[0].file_patches == {"src/auth.py": "@@ +1 @@\n+new"}

    def test_comment_contexts_in_reviewed(self, normalizer):
        """PR_REVIEWED에 reviewer 인라인 코멘트가 comment_contexts로 전달."""
        prs = [
            _make_pr(
                author="other",
                reviews=[_review(author="testuser")],
                comments=[
                    _comment(
                        author="testuser",
                        path="src/auth.py",
                        line=42,
                        diff_hunk="@@ -40 @@",
                        body="Check user.verified",
                    ),
                    _comment(author="someone_else", body="General comment"),
                ],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        reviewed = [a for a in result if a.kind == ActivityKind.PR_REVIEWED]
        assert len(reviewed) == 1
        assert len(reviewed[0].comment_contexts) == 1
        assert reviewed[0].comment_contexts[0]["path"] == "src/auth.py"
        assert reviewed[0].comment_contexts[0]["line"] == 42

    def test_comment_contexts_in_commented(self, normalizer):
        """PR_COMMENTED에 인라인 코멘트가 comment_contexts로 전달."""
        prs = [
            _make_pr(
                author="other",
                comments=[
                    _comment(
                        author="testuser",
                        path="src/main.py",
                        line=10,
                        diff_hunk="@@ -8 @@",
                        body="Inline note",
                    ),
                    _comment(
                        author="testuser",
                        body="General comment",
                    ),
                ],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        commented = [a for a in result if a.kind == ActivityKind.PR_COMMENTED]
        assert len(commented) == 1
        assert len(commented[0].comment_contexts) == 1
        assert commented[0].comment_contexts[0]["path"] == "src/main.py"

    def test_comment_contexts_empty_when_no_inline(self, normalizer):
        """일반 코멘트만 있으면 comment_contexts 빈 리스트."""
        prs = [
            _make_pr(
                author="other",
                comments=[_comment(author="testuser", body="General only")],
            )
        ]
        result = normalizer._convert_activities(prs, DATE)
        commented = [a for a in result if a.kind == ActivityKind.PR_COMMENTED]
        assert len(commented) == 1
        assert commented[0].comment_contexts == []


class TestComputeStats:
    def test_counts(self):
        activities = [
            Activity(
                ts="t",
                kind=ActivityKind.PR_AUTHORED,
                repo="r",
                pr_number=1,
                title="t",
                url="u",
                summary="s",
                additions=10,
                deletions=2,
            ),
            Activity(
                ts="t",
                kind=ActivityKind.PR_AUTHORED,
                repo="r",
                pr_number=2,
                title="t",
                url="u",
                summary="s",
                additions=20,
                deletions=5,
            ),
            Activity(
                ts="t",
                kind=ActivityKind.PR_REVIEWED,
                repo="r",
                pr_number=3,
                title="t",
                url="u",
                summary="s",
                additions=50,
                deletions=10,
            ),
            Activity(
                ts="t",
                kind=ActivityKind.PR_COMMENTED,
                repo="r2",
                pr_number=4,
                title="t",
                url="u",
                summary="s",
            ),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.authored_count == 2
        assert stats.reviewed_count == 1
        assert stats.commented_count == 1

    def test_additions_only_from_authored(self):
        activities = [
            Activity(
                ts="t",
                kind=ActivityKind.PR_AUTHORED,
                repo="r",
                pr_number=1,
                title="t",
                url="u",
                summary="s",
                additions=10,
                deletions=2,
            ),
            Activity(
                ts="t",
                kind=ActivityKind.PR_REVIEWED,
                repo="r",
                pr_number=2,
                title="t",
                url="u",
                summary="s",
                additions=100,
                deletions=50,
            ),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.total_additions == 10
        assert stats.total_deletions == 2

    def test_repos_touched(self):
        activities = [
            Activity(
                ts="t",
                kind=ActivityKind.PR_AUTHORED,
                repo="org/b",
                pr_number=1,
                title="t",
                url="u",
                summary="s",
            ),
            Activity(
                ts="t",
                kind=ActivityKind.PR_REVIEWED,
                repo="org/a",
                pr_number=2,
                title="t",
                url="u",
                summary="s",
            ),
            Activity(
                ts="t",
                kind=ActivityKind.PR_COMMENTED,
                repo="org/b",
                pr_number=3,
                title="t",
                url="u",
                summary="s",
            ),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.repos_touched == ["org/a", "org/b"]

    def test_authored_prs_list(self):
        activities = [
            Activity(
                ts="t",
                kind=ActivityKind.PR_AUTHORED,
                repo="org/r",
                pr_number=1,
                title="PR1",
                url="u1",
                summary="s",
            ),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert len(stats.authored_prs) == 1
        assert stats.authored_prs[0] == {"url": "u1", "title": "PR1", "repo": "org/r"}

    def test_reviewed_prs_list(self):
        activities = [
            Activity(
                ts="t",
                kind=ActivityKind.PR_REVIEWED,
                repo="org/r",
                pr_number=2,
                title="PR2",
                url="u2",
                summary="s",
            ),
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


# ── Commit/Issue 헬퍼 ──


def _make_commit(
    sha="abc123",
    author="testuser",
    message="feat: add feature",
    repo="org/repo",
    committed_at="2025-02-16T10:00:00Z",
    files=None,
) -> CommitRaw:
    return CommitRaw(
        sha=sha,
        url=f"https://ghes/{repo}/commit/{sha}",
        api_url=f"https://ghes/api/v3/repos/{repo}/commits/{sha}",
        message=message,
        author=author,
        repo=repo,
        committed_at=committed_at,
        files=files or [FileChange("src/main.py", 10, 3, "modified")],
    )


def _make_issue(
    number=10,
    author="testuser",
    title="Bug report",
    body="Description",
    repo="org/repo",
    created_at="2025-02-16T09:00:00Z",
    updated_at="2025-02-16T15:00:00Z",
    comments=None,
    labels=None,
) -> IssueRaw:
    return IssueRaw(
        url=f"https://ghes/{repo}/issues/{number}",
        api_url=f"https://ghes/api/v3/repos/{repo}/issues/{number}",
        number=number,
        title=title,
        body=body,
        state="open",
        created_at=created_at,
        updated_at=updated_at,
        closed_at=None,
        repo=repo,
        labels=labels or [],
        author=author,
        comments=comments or [],
    )


def _save_raw_commits(test_config, commits: list[CommitRaw], date: str = DATE):
    raw_dir = test_config.date_raw_dir(date)
    save_json(commits, raw_dir / "commits.json")


def _save_raw_issues(test_config, issues: list[IssueRaw], date: str = DATE):
    raw_dir = test_config.date_raw_dir(date)
    save_json(issues, raw_dir / "issues.json")


# ── Commit 변환 테스트 ──


class TestConvertCommitActivities:
    def test_basic_conversion(self, normalizer):
        commits = [_make_commit()]
        result = normalizer._convert_commit_activities(commits, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.COMMIT
        assert result[0].sha == "abc123"
        assert result[0].pr_number == 0
        assert result[0].additions == 10
        assert result[0].deletions == 3

    def test_date_filtering(self, normalizer):
        """target_date에 해당하지 않는 commit은 제외."""
        commits = [_make_commit(committed_at="2025-02-15T23:59:59Z")]
        result = normalizer._convert_commit_activities(commits, DATE)
        assert len(result) == 0

    def test_title_is_first_line_of_message(self, normalizer):
        commits = [_make_commit(message="feat: short title\n\nLong body text")]
        result = normalizer._convert_commit_activities(commits, DATE)
        assert result[0].title == "feat: short title"

    def test_title_no_truncation(self, normalizer):
        """120자 넘는 첫 줄도 truncate 없이 전체 보존."""
        long_msg = "x" * 200
        commits = [_make_commit(message=long_msg)]
        result = normalizer._convert_commit_activities(commits, DATE)
        assert result[0].title == "x" * 200

    def test_body_is_full_message(self, normalizer):
        """body에 전체 commit message가 보존."""
        msg = "feat: add new feature\n\nDetailed description of the change"
        commits = [_make_commit(message=msg)]
        result = normalizer._convert_commit_activities(commits, DATE)
        assert result[0].body == msg

    def test_empty_commits(self, normalizer):
        result = normalizer._convert_commit_activities([], DATE)
        assert result == []

    def test_file_patches_in_commit(self, normalizer):
        """COMMIT Activity에 file_patches 전달."""
        commits = [
            _make_commit(
                files=[
                    FileChange("src/main.py", 10, 3, "modified", patch="@@ +1 @@\n+line"),
                    FileChange("docs/README.md", 1, 0, "modified"),
                ],
            )
        ]
        result = normalizer._convert_commit_activities(commits, DATE)
        assert len(result) == 1
        assert result[0].file_patches == {"src/main.py": "@@ +1 @@\n+line"}


# ── Issue 변환 테스트 ──


class TestConvertIssueActivities:
    def test_issue_authored(self, normalizer):
        issues = [_make_issue(author="testuser")]
        result = normalizer._convert_issue_activities(issues, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.ISSUE_AUTHORED
        assert result[0].title == "Bug report"

    def test_issue_commented(self, normalizer):
        issues = [
            _make_issue(
                author="other",
                comments=[_comment(author="testuser", created_at="2025-02-16T11:00:00Z")],
            )
        ]
        result = normalizer._convert_issue_activities(issues, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.ISSUE_COMMENTED

    def test_both_authored_and_commented(self, normalizer):
        """한 issue에서 authored + commented 둘 다 생성 가능."""
        issues = [
            _make_issue(
                author="testuser",
                comments=[_comment(author="testuser", created_at="2025-02-16T11:00:00Z")],
            )
        ]
        result = normalizer._convert_issue_activities(issues, DATE)
        kinds = {a.kind for a in result}
        assert ActivityKind.ISSUE_AUTHORED in kinds
        assert ActivityKind.ISSUE_COMMENTED in kinds

    def test_date_filtering_authored(self, normalizer):
        issues = [_make_issue(author="testuser", created_at="2025-02-15T09:00:00Z")]
        result = normalizer._convert_issue_activities(issues, DATE)
        assert len(result) == 0

    def test_date_filtering_commented(self, normalizer):
        issues = [
            _make_issue(
                author="other",
                comments=[_comment(author="testuser", created_at="2025-02-15T23:59:59Z")],
            )
        ]
        result = normalizer._convert_issue_activities(issues, DATE)
        assert len(result) == 0

    def test_case_insensitive_username(self, normalizer):
        issues = [_make_issue(author="TestUser")]
        result = normalizer._convert_issue_activities(issues, DATE)
        assert len(result) == 1
        assert result[0].kind == ActivityKind.ISSUE_AUTHORED

    def test_issue_authored_body(self, normalizer):
        """ISSUE_AUTHORED에 body == issue.body 검증."""
        issues = [_make_issue(author="testuser", body="Steps to reproduce the bug")]
        result = normalizer._convert_issue_activities(issues, DATE)
        authored = [a for a in result if a.kind == ActivityKind.ISSUE_AUTHORED]
        assert len(authored) == 1
        assert authored[0].body == "Steps to reproduce the bug"

    def test_issue_commented_bodies(self, normalizer):
        """ISSUE_COMMENTED에 comment_bodies가 user의 코멘트 본문을 포함."""
        issues = [
            _make_issue(
                author="other",
                comments=[
                    _comment(
                        author="testuser",
                        created_at="2025-02-16T10:00:00Z",
                        body="I can reproduce this",
                    ),
                    _comment(
                        author="testuser",
                        created_at="2025-02-16T11:00:00Z",
                        body="Found the root cause",
                    ),
                    _comment(
                        author="someone",
                        created_at="2025-02-16T12:00:00Z",
                        body="Other person comment",
                    ),
                ],
            )
        ]
        result = normalizer._convert_issue_activities(issues, DATE)
        commented = [a for a in result if a.kind == ActivityKind.ISSUE_COMMENTED]
        assert len(commented) == 1
        assert commented[0].body == "Description"  # issue body
        assert commented[0].comment_bodies == ["I can reproduce this", "Found the root cause"]

    def test_empty_issues(self, normalizer):
        result = normalizer._convert_issue_activities([], DATE)
        assert result == []


# ── _compute_stats 확장 테스트 ──


class TestComputeStatsExtended:
    def test_commit_count(self):
        activities = [
            Activity(
                ts="t",
                kind=ActivityKind.COMMIT,
                repo="r",
                pr_number=0,
                title="t",
                url="u",
                summary="s",
                sha="abc",
                additions=15,
                deletions=5,
            ),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.commit_count == 1
        assert stats.commits == [{"url": "u", "title": "t", "repo": "r", "sha": "abc"}]

    def test_additions_include_commits(self):
        """total_additions/deletions는 authored PR + commit 합산."""
        activities = [
            Activity(
                ts="t",
                kind=ActivityKind.PR_AUTHORED,
                repo="r",
                pr_number=1,
                title="t",
                url="u",
                summary="s",
                additions=10,
                deletions=2,
            ),
            Activity(
                ts="t",
                kind=ActivityKind.COMMIT,
                repo="r",
                pr_number=0,
                title="t",
                url="u",
                summary="s",
                sha="abc",
                additions=20,
                deletions=5,
            ),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.total_additions == 30
        assert stats.total_deletions == 7

    def test_issue_counts(self):
        activities = [
            Activity(
                ts="t",
                kind=ActivityKind.ISSUE_AUTHORED,
                repo="r",
                pr_number=10,
                title="t",
                url="u",
                summary="s",
            ),
            Activity(
                ts="t",
                kind=ActivityKind.ISSUE_COMMENTED,
                repo="r",
                pr_number=10,
                title="t",
                url="u2",
                summary="s",
            ),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.issue_authored_count == 1
        assert stats.issue_commented_count == 1
        assert stats.authored_issues == [{"url": "u", "title": "t", "repo": "r"}]

    def test_repos_touched_all_types(self):
        """모든 활동 유형에서 repos_touched 수집."""
        activities = [
            Activity(
                ts="t",
                kind=ActivityKind.PR_AUTHORED,
                repo="org/a",
                pr_number=1,
                title="t",
                url="u",
                summary="s",
            ),
            Activity(
                ts="t",
                kind=ActivityKind.COMMIT,
                repo="org/b",
                pr_number=0,
                title="t",
                url="u",
                summary="s",
                sha="abc",
            ),
            Activity(
                ts="t",
                kind=ActivityKind.ISSUE_AUTHORED,
                repo="org/c",
                pr_number=10,
                title="t",
                url="u",
                summary="s",
            ),
        ]
        stats = NormalizerService._compute_stats(activities, DATE)
        assert stats.repos_touched == ["org/a", "org/b", "org/c"]


# ── normalize() 통합 테스트 확장 ──


class TestNormalizeWithCommitsAndIssues:
    def test_with_commits(self, normalizer, test_config):
        _save_raw(test_config, [_make_pr(author="testuser")])
        _save_raw_commits(test_config, [_make_commit()])

        act_path, stats_path = normalizer.normalize(DATE)
        activities = load_jsonl(act_path)
        stats = load_json(stats_path)

        kinds = {a["kind"] for a in activities}
        assert "pr_authored" in kinds
        assert "commit" in kinds
        assert stats["commit_count"] == 1

    def test_with_issues(self, normalizer, test_config):
        _save_raw(test_config, [])
        _save_raw_issues(test_config, [_make_issue(author="testuser")])

        act_path, stats_path = normalizer.normalize(DATE)
        activities = load_jsonl(act_path)
        stats = load_json(stats_path)

        assert any(a["kind"] == "issue_authored" for a in activities)
        assert stats["issue_authored_count"] == 1

    def test_without_commits_issues_backward_compat(self, normalizer, test_config):
        """commits.json/issues.json 없어도 정상 동작 (하위 호환)."""
        _save_raw(test_config, [_make_pr(author="testuser")])
        # commits.json, issues.json 없음

        act_path, stats_path = normalizer.normalize(DATE)
        activities = load_jsonl(act_path)
        stats = load_json(stats_path)

        assert len(activities) == 1
        assert stats["commit_count"] == 0
        assert stats["issue_authored_count"] == 0

    def test_sorted_by_timestamp(self, normalizer, test_config):
        """모든 활동이 시간순 정렬."""
        _save_raw(test_config, [_make_pr(author="testuser", created_at="2025-02-16T15:00:00Z")])
        _save_raw_commits(test_config, [_make_commit(committed_at="2025-02-16T09:00:00Z")])
        _save_raw_issues(
            test_config,
            [
                _make_issue(
                    author="testuser",
                    created_at="2025-02-16T12:00:00Z",
                )
            ],
        )

        act_path, _ = normalizer.normalize(DATE)
        activities = load_jsonl(act_path)

        timestamps = [a["ts"] for a in activities]
        assert timestamps == sorted(timestamps)


# ── _is_date_normalized 테스트 ──


class TestIsDateNormalized:
    def test_all_files_exist(self, normalizer, test_config):
        """activities.jsonl + stats.json 모두 존재 → True."""
        norm_dir = test_config.date_normalized_dir(DATE)
        norm_dir.mkdir(parents=True, exist_ok=True)
        (norm_dir / "activities.jsonl").write_text("")
        (norm_dir / "stats.json").write_text("{}")
        assert normalizer._is_date_normalized(DATE) is True

    def test_missing_activities(self, normalizer, test_config):
        """stats.json만 존재 → False."""
        norm_dir = test_config.date_normalized_dir(DATE)
        norm_dir.mkdir(parents=True, exist_ok=True)
        (norm_dir / "stats.json").write_text("{}")
        assert normalizer._is_date_normalized(DATE) is False

    def test_missing_stats(self, normalizer, test_config):
        """activities.jsonl만 존재 → False."""
        norm_dir = test_config.date_normalized_dir(DATE)
        norm_dir.mkdir(parents=True, exist_ok=True)
        (norm_dir / "activities.jsonl").write_text("")
        assert normalizer._is_date_normalized(DATE) is False

    def test_dir_not_exist(self, normalizer):
        """디렉토리 없음 → False."""
        assert normalizer._is_date_normalized("2099-01-01") is False


# ── Normalize Checkpoint 테스트 ──


class TestNormalizeCheckpoint:
    def test_creates_checkpoint_file(self, normalizer, test_config):
        """normalize 후 checkpoints.json에 last_normalize_date."""
        _save_raw(test_config, [_make_pr(author="testuser")])
        normalizer.normalize(DATE)

        cp = load_json(test_config.checkpoints_path)
        assert cp["last_normalize_date"] == DATE

    def test_updates_existing_checkpoint(self, normalizer, test_config):
        """두 번 normalize → 마지막 날짜로 갱신."""
        date2 = "2025-02-17"
        _save_raw(test_config, [_make_pr(author="testuser")])
        _save_raw(
            test_config,
            [
                _make_pr(
                    author="testuser",
                    created_at="2025-02-17T09:00:00Z",
                    updated_at="2025-02-17T15:00:00Z",
                )
            ],
            date=date2,
        )
        normalizer.normalize(DATE)
        normalizer.normalize(date2)

        cp = load_json(test_config.checkpoints_path)
        assert cp["last_normalize_date"] == date2

    def test_preserves_other_keys(self, normalizer, test_config):
        """last_fetch_date 보존 확인."""
        import json

        cp_path = test_config.checkpoints_path
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cp_path, "w") as f:
            json.dump({"last_fetch_date": "2025-02-16"}, f)

        _save_raw(test_config, [_make_pr(author="testuser")])
        normalizer.normalize(DATE)

        cp = load_json(cp_path)
        assert cp["last_fetch_date"] == "2025-02-16"
        assert cp["last_normalize_date"] == DATE


# ── normalize_range 테스트 ──


class TestNormalizeRange:
    def _prepare_raw(self, test_config, dates):
        """여러 날짜의 raw 데이터 생성."""
        for d in dates:
            _save_raw(
                test_config,
                [
                    _make_pr(
                        author="testuser", created_at=f"{d}T09:00:00Z", updated_at=f"{d}T15:00:00Z"
                    )
                ],
                date=d,
            )

    def test_basic_range(self, normalizer, test_config):
        """3일 range → 3개 success."""
        dates = ["2025-02-14", "2025-02-15", "2025-02-16"]
        self._prepare_raw(test_config, dates)
        results = normalizer.normalize_range("2025-02-14", "2025-02-16")
        assert len(results) == 3
        assert all(r["status"] == "success" for r in results)

    def test_skip_existing(self, normalizer, test_config):
        """중간 날짜 pre-create → skipped."""
        dates = ["2025-02-14", "2025-02-15", "2025-02-16"]
        self._prepare_raw(test_config, dates)
        # Pre-create normalized for middle date
        norm_dir = test_config.date_normalized_dir("2025-02-15")
        norm_dir.mkdir(parents=True, exist_ok=True)
        (norm_dir / "activities.jsonl").write_text("")
        (norm_dir / "stats.json").write_text("{}")

        results = normalizer.normalize_range("2025-02-14", "2025-02-16")
        statuses = {r["date"]: r["status"] for r in results}
        assert statuses["2025-02-15"] == "skipped"
        assert statuses["2025-02-14"] == "success"
        assert statuses["2025-02-16"] == "success"

    def test_force_override(self, normalizer, test_config):
        """force=True → skip 없이 전부 success."""
        dates = ["2025-02-14", "2025-02-15", "2025-02-16"]
        self._prepare_raw(test_config, dates)
        # Pre-create normalized for middle date
        norm_dir = test_config.date_normalized_dir("2025-02-15")
        norm_dir.mkdir(parents=True, exist_ok=True)
        (norm_dir / "activities.jsonl").write_text("")
        (norm_dir / "stats.json").write_text("{}")

        results = normalizer.normalize_range("2025-02-14", "2025-02-16", force=True)
        assert all(r["status"] == "success" for r in results)

    def test_failure_resilience(self, normalizer, test_config):
        """중간 날짜 raw 없음 → failed, 나머지 success."""
        _save_raw(
            test_config,
            [
                _make_pr(
                    author="testuser",
                    created_at="2025-02-14T09:00:00Z",
                    updated_at="2025-02-14T15:00:00Z",
                )
            ],
            date="2025-02-14",
        )
        # 2025-02-15: no raw data
        _save_raw(
            test_config,
            [
                _make_pr(
                    author="testuser",
                    created_at="2025-02-16T09:00:00Z",
                    updated_at="2025-02-16T15:00:00Z",
                )
            ],
            date="2025-02-16",
        )

        results = normalizer.normalize_range("2025-02-14", "2025-02-16")
        statuses = {r["date"]: r["status"] for r in results}
        assert statuses["2025-02-14"] == "success"
        assert statuses["2025-02-15"] == "failed"
        assert statuses["2025-02-16"] == "success"

    def test_checkpoint_per_date(self, normalizer, test_config):
        """last_normalize_date == 마지막 성공 날짜."""
        dates = ["2025-02-14", "2025-02-15", "2025-02-16"]
        self._prepare_raw(test_config, dates)
        normalizer.normalize_range("2025-02-14", "2025-02-16")

        cp = load_json(test_config.checkpoints_path)
        assert cp["last_normalize_date"] == "2025-02-16"

    def test_returns_list_of_dicts(self, normalizer, test_config):
        """반환 형식 검증."""
        self._prepare_raw(test_config, ["2025-02-16"])
        results = normalizer.normalize_range("2025-02-16", "2025-02-16")
        assert isinstance(results, list)
        assert len(results) == 1
        assert "date" in results[0]
        assert "status" in results[0]


# ── DailyStateStore cascade 테스트 ──


class TestNormalizerDailyStateIntegration:
    """DailyStateStore cascade 동작 테스트."""

    def test_cascade_reprocess_when_fetch_newer(self, test_config):
        """fetch_ts > normalize_ts 이면 normalize_range가 재처리."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        # is_normalize_stale returns True → cascade reprocess
        mock_ds.is_normalize_stale.return_value = True
        normalizer = NormalizerService(test_config, daily_state=mock_ds)

        # raw 데이터 준비
        _save_raw(
            test_config,
            [
                _make_pr(
                    author="testuser",
                    created_at=f"{DATE}T09:00:00Z",
                    updated_at=f"{DATE}T15:00:00Z",
                )
            ],
        )

        results = normalizer.normalize_range(DATE, DATE)
        assert len(results) == 1
        assert results[0]["status"] == "success"
        # stale check 호출 검증
        mock_ds.is_normalize_stale.assert_called_with(DATE)

    def test_skip_when_normalize_fresh(self, test_config):
        """normalize_ts >= fetch_ts 이면 skip."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        mock_ds.is_normalize_stale.return_value = False  # fresh → skip
        normalizer = NormalizerService(test_config, daily_state=mock_ds)

        # raw 데이터 준비 (normalize가 호출되지 않으므로 실제 필요 없지만)
        _save_raw(test_config, [_make_pr(author="testuser")])

        results = normalizer.normalize_range(DATE, DATE)
        assert len(results) == 1
        assert results[0]["status"] == "skipped"

    def test_set_timestamp_called_after_normalize(self, test_config):
        """normalize 성공 후 daily_state.set_timestamp("normalize") 호출."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        normalizer = NormalizerService(test_config, daily_state=mock_ds)

        _save_raw(test_config, [_make_pr(author="testuser")])
        normalizer.normalize(DATE)

        mock_ds.set_timestamp.assert_called_once_with("normalize", DATE)


# ── LLM Enrichment 테스트 ──


class TestLLMEnrichment:
    def _make_activities(self):
        return [
            Activity(
                ts=f"{DATE}T09:00:00Z",
                kind=ActivityKind.PR_AUTHORED,
                repo="org/repo",
                pr_number=1,
                title="Add auth feature",
                url="https://ghes/org/repo/pull/1",
                summary="pr_authored: Add auth feature",
                files=["src/auth.py"],
                additions=10,
                deletions=3,
            ),
        ]

    def test_llm_sets_change_summary_and_intent(self, test_config):
        """LLM 주입 시 change_summary/intent 설정."""
        import shutil
        from pathlib import Path

        src_prompts = Path(__file__).parents[2] / "prompts"
        for f in src_prompts.glob("*.md"):
            shutil.copy(f, test_config.prompts_dir / f.name)

        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps(
            [{"index": 0, "change_summary": "인증 기능 추가", "intent": "feature"}]
        )

        normalizer = NormalizerService(test_config, llm=mock_llm)
        activities = self._make_activities()
        normalizer._enrich_activities(activities)

        assert activities[0].change_summary == "인증 기능 추가"
        assert activities[0].intent == "feature"
        mock_llm.chat.assert_called_once()

    def test_llm_failure_graceful_degradation(self, test_config):
        """LLM 실패 시 빈 필드로 계속."""
        import shutil
        from pathlib import Path

        src_prompts = Path(__file__).parents[2] / "prompts"
        for f in src_prompts.glob("*.md"):
            shutil.copy(f, test_config.prompts_dir / f.name)

        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("API error")

        normalizer = NormalizerService(test_config, llm=mock_llm)
        activities = self._make_activities()
        normalizer._enrich_activities(activities)

        assert activities[0].change_summary == ""
        assert activities[0].intent == ""

    def test_no_llm_leaves_fields_empty(self, test_config):
        """LLM 미주입 시 빈 필드."""
        normalizer = NormalizerService(test_config)
        activities = self._make_activities()
        normalizer._enrich_activities(activities)

        assert activities[0].change_summary == ""
        assert activities[0].intent == ""

    def test_empty_activities_no_llm_call(self, test_config):
        """빈 activities 시 LLM 미호출."""
        mock_llm = MagicMock()
        normalizer = NormalizerService(test_config, llm=mock_llm)
        normalizer._enrich_activities([])
        mock_llm.chat.assert_not_called()

    def test_enrichment_in_normalize_pipeline(self, test_config):
        """normalize() 호출 시 enrichment가 activities에 반영."""
        import shutil
        from pathlib import Path

        src_prompts = Path(__file__).parents[2] / "prompts"
        for f in src_prompts.glob("*.md"):
            shutil.copy(f, test_config.prompts_dir / f.name)

        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps(
            [{"index": 0, "change_summary": "기능 추가", "intent": "feature"}]
        )

        _save_raw(test_config, [_make_pr(author="testuser")])
        normalizer = NormalizerService(test_config, llm=mock_llm)
        act_path, _ = normalizer.normalize(DATE)

        activities = load_jsonl(act_path)
        assert activities[0]["change_summary"] == "기능 추가"
        assert activities[0]["intent"] == "feature"


class TestActivityNewFields:
    def test_activity_default_values(self):
        """Activity의 change_summary/intent 기본값은 빈 문자열."""
        act = Activity(
            ts="t",
            kind=ActivityKind.PR_AUTHORED,
            repo="r",
            pr_number=1,
            title="t",
            url="u",
            summary="s",
        )
        assert act.change_summary == ""
        assert act.intent == ""

    def test_activity_from_dict_with_new_fields(self):
        """activity_from_dict가 change_summary/intent를 복원."""
        from git_recap.models import activity_from_dict

        d = {
            "ts": "t",
            "kind": "pr_authored",
            "repo": "r",
            "pr_number": 1,
            "title": "t",
            "url": "u",
            "summary": "s",
            "change_summary": "인증 기능 추가",
            "intent": "feature",
        }
        act = activity_from_dict(d)
        assert act.change_summary == "인증 기능 추가"
        assert act.intent == "feature"

    def test_activity_from_dict_without_new_fields(self):
        """change_summary/intent 없는 dict도 역호환."""
        from git_recap.models import activity_from_dict

        d = {
            "ts": "t",
            "kind": "pr_authored",
            "repo": "r",
            "pr_number": 1,
            "title": "t",
            "url": "u",
            "summary": "s",
        }
        act = activity_from_dict(d)
        assert act.change_summary == ""
        assert act.intent == ""
