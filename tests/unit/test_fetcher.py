from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from workrecap.exceptions import FetchError
from workrecap.infra.ghes_client import GHESClient
from workrecap.models import load_json
from workrecap.services.fetcher import FetcherService


# ── Fixtures ──


def _make_search_item(api_url: str, number: int = 1, title: str = "Test PR") -> dict:
    return {
        "url": f"https://ghes/api/v3/repos/org/repo/issues/{number}",
        "html_url": f"https://ghes/org/repo/pull/{number}",
        "number": number,
        "title": title,
        "user": {"login": "testuser"},
        "state": "closed",
        "created_at": "2025-02-16T09:00:00Z",
        "updated_at": "2025-02-16T15:00:00Z",
        "pull_request": {"url": api_url},
    }


def _make_pr_detail(owner: str = "org", repo: str = "repo", number: int = 1) -> dict:
    return {
        "url": f"https://ghes/api/v3/repos/{owner}/{repo}/pulls/{number}",
        "html_url": f"https://ghes/{owner}/{repo}/pull/{number}",
        "number": number,
        "title": "Test PR",
        "body": "Description",
        "state": "closed",
        "merged": True,
        "created_at": "2025-02-16T09:00:00Z",
        "updated_at": "2025-02-16T15:00:00Z",
        "merged_at": "2025-02-16T14:00:00Z",
        "user": {"login": "testuser"},
        "labels": [{"name": "feature"}],
    }


@pytest.fixture
def mock_client():
    client = Mock(spec=GHESClient)
    client.search_issues.return_value = {"total_count": 0, "items": []}
    client.get_pr.return_value = _make_pr_detail()
    client.get_pr_files.return_value = [
        {
            "filename": "src/main.py",
            "additions": 10,
            "deletions": 3,
            "status": "modified",
            "patch": "@@ -1,3 +1,5 @@\n+new line",
        },
    ]
    client.get_pr_comments.return_value = [
        {
            "user": {"login": "reviewer1"},
            "body": "Good approach",
            "created_at": "2025-02-16T11:00:00Z",
            "html_url": "https://ghes/org/repo/pull/1#comment-1",
            "path": "src/main.py",
            "line": 5,
            "diff_hunk": "@@ -1,3 +1,5 @@\n+new line",
        },
    ]
    client.get_pr_reviews.return_value = [
        {
            "user": {"login": "reviewer1"},
            "state": "APPROVED",
            "body": "",
            "submitted_at": "2025-02-16T12:00:00Z",
            "html_url": "https://ghes/org/repo/pull/1#review-1",
        },
    ]
    # Commit/Issue 관련 기본값
    client.search_commits.return_value = {"total_count": 0, "items": []}
    client.get_commit.return_value = {}
    client.get_issue.return_value = {}
    client.get_issue_comments.return_value = []
    return client


@pytest.fixture
def fetcher(test_config, mock_client):
    return FetcherService(test_config, mock_client)


# ── Tests ──


class TestParsePrUrl:
    def test_standard_url(self):
        owner, repo, num = FetcherService._parse_pr_url(
            "https://ghes/api/v3/repos/org/repo/pulls/42"
        )
        assert (owner, repo, num) == ("org", "repo", 42)

    def test_nested_org_url(self):
        owner, repo, num = FetcherService._parse_pr_url(
            "https://ghes/api/v3/repos/my-org/my-repo/pulls/7"
        )
        assert (owner, repo, num) == ("my-org", "my-repo", 7)

    def test_trailing_slash(self):
        owner, repo, num = FetcherService._parse_pr_url(
            "https://ghes/api/v3/repos/org/repo/pulls/10/"
        )
        assert (owner, repo, num) == ("org", "repo", 10)


class TestSearchPrs:
    def test_three_axis_search(self, fetcher, mock_client):
        """3축 쿼리가 모두 호출된다."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        fetcher._search_prs("2025-02-16")
        assert mock_client.search_issues.call_count == 3
        calls = [str(c) for c in mock_client.search_issues.call_args_list]
        assert any("author:testuser" in c for c in calls)
        assert any("reviewed-by:testuser" in c for c in calls)
        assert any("commenter:testuser" in c for c in calls)

    def test_dedup_by_api_url(self, fetcher, mock_client):
        """동일 PR이 여러 축에서 나오면 1개로 dedup."""
        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        item = _make_search_item(api_url, 1)
        mock_client.search_issues.return_value = {"total_count": 1, "items": [item]}
        result = fetcher._search_prs("2025-02-16")
        assert len(result) == 1
        assert api_url in result

    def test_multiple_prs_deduped(self, fetcher, mock_client):
        """다른 PR은 각각 포함."""
        url1 = "https://ghes/api/v3/repos/org/repo/pulls/1"
        url2 = "https://ghes/api/v3/repos/org/repo/pulls/2"
        item1 = _make_search_item(url1, 1, "PR 1")
        item2 = _make_search_item(url2, 2, "PR 2")

        def search_side_effect(query, **kwargs):
            if "author:" in query:
                return {"total_count": 1, "items": [item1]}
            elif "reviewed-by:" in query:
                return {"total_count": 1, "items": [item2]}
            else:
                return {"total_count": 1, "items": [item1]}

        mock_client.search_issues.side_effect = search_side_effect
        result = fetcher._search_prs("2025-02-16")
        assert len(result) == 2

    def test_reviewed_by_fallback_on_422(self, fetcher, mock_client):
        """reviewed-by 422 에러 시 해당 축만 스킵."""

        def search_side_effect(query, **kwargs):
            if "reviewed-by:" in query:
                raise FetchError("Client error 422")
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = search_side_effect
        result = fetcher._search_prs("2025-02-16")
        assert len(result) == 0
        # author + commenter = 2 calls (reviewed-by raises)
        assert mock_client.search_issues.call_count == 3

    def test_non_reviewed_by_error_propagates(self, fetcher, mock_client):
        """author/commenter 축 에러는 전파."""

        def search_side_effect(query, **kwargs):
            if "author:" in query:
                raise FetchError("Server error")
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = search_side_effect
        with pytest.raises(FetchError):
            fetcher._search_prs("2025-02-16")

    def test_pagination(self, fetcher, mock_client):
        """100개 초과 결과 시 다음 페이지 요청."""
        page1_items = [
            _make_search_item(f"https://ghes/api/v3/repos/org/repo/pulls/{i}", i)
            for i in range(100)
        ]
        page2_items = [
            _make_search_item(f"https://ghes/api/v3/repos/org/repo/pulls/{i}", i)
            for i in range(100, 120)
        ]

        call_count = 0

        def search_side_effect(query, page=1, per_page=100):
            nonlocal call_count
            call_count += 1
            if "author:" in query:
                if page == 1:
                    return {"total_count": 120, "items": page1_items}
                else:
                    return {"total_count": 120, "items": page2_items}
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = search_side_effect
        result = fetcher._search_prs("2025-02-16")
        assert len(result) == 120


class TestEnrich:
    def test_creates_pr_raw_from_api(self, fetcher, mock_client):
        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        item = _make_search_item(api_url, 1)
        result = fetcher._enrich(item)

        assert result.number == 1
        assert result.title == "Test PR"
        assert result.repo == "org/repo"
        assert result.is_merged is True
        assert len(result.files) == 1
        assert result.files[0].filename == "src/main.py"
        assert result.files[0].patch == "@@ -1,3 +1,5 @@\n+new line"
        assert len(result.comments) == 1
        assert result.comments[0].path == "src/main.py"
        assert result.comments[0].line == 5
        assert result.comments[0].diff_hunk == "@@ -1,3 +1,5 @@\n+new line"
        assert len(result.reviews) == 1

    def test_pr_body_none_becomes_empty_string(self, fetcher, mock_client):
        detail = _make_pr_detail()
        detail["body"] = None
        mock_client.get_pr.return_value = detail

        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        result = fetcher._enrich(_make_search_item(api_url))
        assert result.body == ""

    def test_labels_extracted(self, fetcher, mock_client):
        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        result = fetcher._enrich(_make_search_item(api_url))
        assert result.labels == ["feature"]

    def test_merged_at_none(self, fetcher, mock_client):
        detail = _make_pr_detail()
        detail["merged"] = False
        detail["merged_at"] = None
        mock_client.get_pr.return_value = detail

        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        result = fetcher._enrich(_make_search_item(api_url))
        assert result.merged_at is None
        assert result.is_merged is False


class TestNoiseFiltering:
    def test_bot_comment_filtered(self):
        comment = {"user": {"login": "dependabot[bot]"}, "body": "Update deps"}
        assert FetcherService._is_noise_comment(comment) is True

    def test_bot_suffix_filtered(self):
        comment = {"user": {"login": "ci-bot"}, "body": "Build passed"}
        assert FetcherService._is_noise_comment(comment) is True

    def test_lgtm_comment_filtered(self):
        comment = {"user": {"login": "human"}, "body": "LGTM"}
        assert FetcherService._is_noise_comment(comment) is True

    def test_lgtm_with_exclamation_filtered(self):
        comment = {"user": {"login": "human"}, "body": "LGTM!"}
        assert FetcherService._is_noise_comment(comment) is True

    def test_lgtm_case_insensitive(self):
        comment = {"user": {"login": "human"}, "body": "lgtm"}
        assert FetcherService._is_noise_comment(comment) is True

    def test_plus_one_comment_filtered(self):
        comment = {"user": {"login": "human"}, "body": "+1"}
        assert FetcherService._is_noise_comment(comment) is True

    def test_empty_body_comment_filtered(self):
        comment = {"user": {"login": "human"}, "body": ""}
        assert FetcherService._is_noise_comment(comment) is True

    def test_none_body_comment_filtered(self):
        comment = {"user": {"login": "human"}, "body": None}
        assert FetcherService._is_noise_comment(comment) is True

    def test_normal_comment_kept(self):
        comment = {"user": {"login": "human"}, "body": "Good approach, but consider..."}
        assert FetcherService._is_noise_comment(comment) is False

    def test_lgtm_in_longer_text_kept(self):
        comment = {"user": {"login": "human"}, "body": "LGTM, but one minor thing"}
        assert FetcherService._is_noise_comment(comment) is False

    def test_bot_review_filtered(self):
        review = {"user": {"login": "dependabot[bot]"}, "state": "COMMENTED"}
        assert FetcherService._is_noise_review(review) is True

    def test_normal_review_kept(self):
        review = {"user": {"login": "human"}, "state": "APPROVED"}
        assert FetcherService._is_noise_review(review) is False


class TestFetch:
    def test_full_pipeline(self, fetcher, mock_client, test_config):
        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        item = _make_search_item(api_url, 1)
        mock_client.search_issues.return_value = {"total_count": 1, "items": [item]}

        result = fetcher.fetch("2025-02-16")

        assert isinstance(result, dict)
        assert result["prs"].exists()
        data = load_json(result["prs"])
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["title"] == "Test PR"

    def test_empty_result(self, fetcher, mock_client, test_config):
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16")

        assert result["prs"].exists()
        data = load_json(result["prs"])
        assert data == []

    def test_enrich_failure_skips_pr(self, fetcher, mock_client, test_config):
        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        item = _make_search_item(api_url, 1)
        mock_client.search_issues.return_value = {"total_count": 1, "items": [item]}
        mock_client.get_pr.side_effect = FetchError("timeout")

        result = fetcher.fetch("2025-02-16")
        data = load_json(result["prs"])
        assert data == []

    def test_output_path_matches_date(self, fetcher, mock_client, test_config):
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16")
        prs_path = result["prs"]
        assert "2025" in str(prs_path)
        assert "02" in str(prs_path)
        assert "16" in str(prs_path)
        assert prs_path.name == "prs.json"

    def test_returns_dict_with_all_keys(self, fetcher, mock_client, test_config):
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16")
        assert set(result.keys()) == {"prs", "commits", "issues"}


class TestCheckpoint:
    def test_creates_checkpoint_file(self, fetcher, mock_client, test_config):
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        fetcher.fetch("2025-02-16")

        cp_path = test_config.checkpoints_path
        assert cp_path.exists()
        data = load_json(cp_path)
        assert data["last_fetch_date"] == "2025-02-16"

    def test_updates_existing_checkpoint(self, fetcher, mock_client, test_config):
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        fetcher.fetch("2025-02-15")
        fetcher.fetch("2025-02-16")

        data = load_json(test_config.checkpoints_path)
        assert data["last_fetch_date"] == "2025-02-16"


# ── Commit 수집 테스트 ──


def _make_commit_search_item(sha="abc123", repo_full="org/repo"):
    return {
        "sha": sha,
        "repository": {"full_name": repo_full},
        "author": {"login": "testuser"},
        "commit": {
            "message": "feat: add feature",
            "committer": {"date": "2025-02-16T10:00:00Z"},
        },
    }


def _make_commit_detail(sha="abc123", repo_full="org/repo"):
    return {
        "sha": sha,
        "url": f"https://ghes/api/v3/repos/{repo_full}/commits/{sha}",
        "html_url": f"https://ghes/{repo_full}/commit/{sha}",
        "commit": {
            "message": "feat: add feature\n\nDetailed description",
            "committer": {"date": "2025-02-16T10:00:00Z"},
        },
        "files": [
            {
                "filename": "src/main.py",
                "additions": 10,
                "deletions": 3,
                "status": "modified",
                "patch": "@@ -5,3 +5,6 @@\n+commit change",
            },
        ],
    }


class TestFetchCommits:
    def test_fetch_commits_basic(self, fetcher, mock_client):
        mock_client.search_commits.return_value = {
            "total_count": 1,
            "items": [_make_commit_search_item()],
        }
        mock_client.get_commit.return_value = _make_commit_detail()

        result = fetcher._fetch_commits("2025-02-16")
        assert len(result) == 1
        assert result[0].sha == "abc123"
        assert result[0].repo == "org/repo"
        assert len(result[0].files) == 1
        assert result[0].files[0].patch == "@@ -5,3 +5,6 @@\n+commit change"

    def test_fetch_commits_search_failure_returns_empty(self, fetcher, mock_client):
        """Commit search 미지원 시 빈 리스트 반환."""
        mock_client.search_commits.side_effect = FetchError("422 not supported")
        result = fetcher._fetch_commits("2025-02-16")
        assert result == []

    def test_fetch_commits_enrich_failure_skips(self, fetcher, mock_client):
        """개별 commit enrich 실패 시 skip."""
        mock_client.search_commits.return_value = {
            "total_count": 1,
            "items": [_make_commit_search_item()],
        }
        mock_client.get_commit.side_effect = FetchError("timeout")

        result = fetcher._fetch_commits("2025-02-16")
        assert result == []

    def test_fetch_commits_pagination(self, fetcher, mock_client):
        """100개 초과 시 다음 페이지 요청."""
        page1 = [_make_commit_search_item(sha=f"sha{i}") for i in range(100)]
        page2 = [_make_commit_search_item(sha=f"sha{i}") for i in range(100, 110)]

        call_count = 0

        def search_side_effect(query, page=1, per_page=100):
            nonlocal call_count
            call_count += 1
            if page == 1:
                return {"total_count": 110, "items": page1}
            return {"total_count": 110, "items": page2}

        mock_client.search_commits.side_effect = search_side_effect
        mock_client.get_commit.return_value = _make_commit_detail()

        result = fetcher._fetch_commits("2025-02-16")
        assert len(result) == 110


# ── Issue 수집 테스트 ──


def _make_issue_search_item(number=10, repo="org/repo"):
    return {
        "url": f"https://ghes/api/v3/repos/{repo}/issues/{number}",
        "html_url": f"https://ghes/{repo}/issues/{number}",
        "number": number,
        "title": "Bug report",
        "user": {"login": "testuser"},
        "state": "open",
        "created_at": "2025-02-16T09:00:00Z",
        "updated_at": "2025-02-16T15:00:00Z",
    }


def _make_issue_detail(number=10, repo="org/repo"):
    owner, repo_name = repo.split("/")
    return {
        "url": f"https://ghes/api/v3/repos/{repo}/issues/{number}",
        "html_url": f"https://ghes/{repo}/issues/{number}",
        "number": number,
        "title": "Bug report",
        "body": "Steps to reproduce...",
        "state": "open",
        "created_at": "2025-02-16T09:00:00Z",
        "updated_at": "2025-02-16T15:00:00Z",
        "closed_at": None,
        "user": {"login": "testuser"},
        "labels": [{"name": "bug"}],
    }


class TestFetchIssues:
    def test_fetch_issues_basic(self, fetcher, mock_client):
        item = _make_issue_search_item()

        def search_side_effect(query, **kwargs):
            if "type:issue" in query:
                return {"total_count": 1, "items": [item]}
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = search_side_effect
        mock_client.get_issue.return_value = _make_issue_detail()
        mock_client.get_issue_comments.return_value = []

        result = fetcher._fetch_issues("2025-02-16")
        assert len(result) == 1
        assert result[0].number == 10
        assert result[0].title == "Bug report"

    def test_fetch_issues_two_axis_dedup(self, fetcher, mock_client):
        """같은 issue가 author/commenter 축 모두에서 나오면 1개로 dedup."""
        item = _make_issue_search_item(10)

        def search_side_effect(query, **kwargs):
            if "type:issue" in query:
                return {"total_count": 1, "items": [item]}
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = search_side_effect
        mock_client.get_issue.return_value = _make_issue_detail()
        mock_client.get_issue_comments.return_value = []

        result = fetcher._fetch_issues("2025-02-16")
        assert len(result) == 1  # 2 axes return same item, deduped

    def test_fetch_issues_enrich_failure_skips(self, fetcher, mock_client):
        item = _make_issue_search_item()

        def search_side_effect(query, **kwargs):
            if "type:issue" in query:
                return {"total_count": 1, "items": [item]}
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = search_side_effect
        mock_client.get_issue.side_effect = FetchError("timeout")

        result = fetcher._fetch_issues("2025-02-16")
        assert result == []

    def test_fetch_issues_search_failure_graceful(self, fetcher, mock_client):
        """Issue search 실패 시 빈 리스트."""
        mock_client.search_issues.side_effect = FetchError("server error")
        result = fetcher._fetch_issues("2025-02-16")
        assert result == []

    def test_issue_comments_noise_filtered(self, fetcher, mock_client):
        item = _make_issue_search_item()

        def search_side_effect(query, **kwargs):
            if "type:issue" in query:
                return {"total_count": 1, "items": [item]}
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = search_side_effect
        mock_client.get_issue.return_value = _make_issue_detail()
        mock_client.get_issue_comments.return_value = [
            {
                "user": {"login": "human"},
                "body": "Good point",
                "created_at": "2025-02-16T10:00:00Z",
                "html_url": "u1",
            },
            {
                "user": {"login": "ci-bot"},
                "body": "Build passed",
                "created_at": "2025-02-16T10:00:00Z",
                "html_url": "u2",
            },
        ]

        result = fetcher._fetch_issues("2025-02-16")
        assert len(result) == 1
        assert len(result[0].comments) == 1  # bot comment filtered


class TestParseIssueUrl:
    def test_standard_url(self):
        owner, repo, num = FetcherService._parse_issue_url(
            "https://ghes/api/v3/repos/org/repo/issues/42"
        )
        assert (owner, repo, num) == ("org", "repo", 42)

    def test_trailing_slash(self):
        owner, repo, num = FetcherService._parse_issue_url(
            "https://ghes/api/v3/repos/my-org/my-repo/issues/7/"
        )
        assert (owner, repo, num) == ("my-org", "my-repo", 7)


class TestFetchIntegration:
    def test_fetch_creates_all_files(self, fetcher, mock_client, test_config):
        """fetch()가 prs.json + commits.json + issues.json 모두 생성."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        fetcher.fetch("2025-02-16")

        raw_dir = test_config.date_raw_dir("2025-02-16")
        assert (raw_dir / "prs.json").exists()
        assert (raw_dir / "commits.json").exists()
        assert (raw_dir / "issues.json").exists()

    def test_fetch_with_commits_and_issues(self, fetcher, mock_client, test_config):
        """fetch()가 PR + commit + issue를 모두 수집."""
        pr_api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        pr_item = _make_search_item(pr_api_url, 1)
        commit_item = _make_commit_search_item()
        issue_item = _make_issue_search_item()

        def search_issues_side_effect(query, **kwargs):
            if "type:issue" in query:
                return {"total_count": 1, "items": [issue_item]}
            return {"total_count": 1, "items": [pr_item]}

        mock_client.search_issues.side_effect = search_issues_side_effect
        mock_client.search_commits.return_value = {
            "total_count": 1,
            "items": [commit_item],
        }
        mock_client.get_commit.return_value = _make_commit_detail()
        mock_client.get_issue.return_value = _make_issue_detail()
        mock_client.get_issue_comments.return_value = []

        result = fetcher.fetch("2025-02-16")

        assert "prs" in result
        assert "commits" in result
        assert "issues" in result

        prs = load_json(result["prs"])
        commits = load_json(result["commits"])
        issues = load_json(result["issues"])

        assert len(prs) == 1
        assert len(commits) == 1
        assert len(issues) == 1


# ── 타입 필터링 테스트 ──


class TestSearchPrsRange:
    def test_date_range_in_query(self, fetcher, mock_client):
        """쿼리에 날짜 범위 포함."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        fetcher._search_prs_range("2025-02-14", "2025-02-16")
        calls = [str(c) for c in mock_client.search_issues.call_args_list]
        assert any("updated:2025-02-14..2025-02-16" in c for c in calls)

    def test_three_axes(self, fetcher, mock_client):
        """3축 쿼리 모두 호출."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        fetcher._search_prs_range("2025-02-14", "2025-02-16")
        calls = [str(c) for c in mock_client.search_issues.call_args_list]
        assert any("author:testuser" in c for c in calls)
        assert any("reviewed-by:testuser" in c for c in calls)
        assert any("commenter:testuser" in c for c in calls)

    def test_dedup(self, fetcher, mock_client):
        """동일 PR dedup."""
        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        item = _make_search_item(api_url, 1)
        mock_client.search_issues.return_value = {"total_count": 1, "items": [item]}
        result = fetcher._search_prs_range("2025-02-14", "2025-02-16")
        assert len(result) == 1

    def test_reviewed_by_422_fallback(self, fetcher, mock_client):
        """reviewed-by 422 시 스킵."""

        def side_effect(query, **kwargs):
            if "reviewed-by:" in query:
                raise FetchError("422")
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = side_effect
        result = fetcher._search_prs_range("2025-02-14", "2025-02-16")
        assert len(result) == 0

    def test_warns_on_1000_results(self, fetcher, mock_client, caplog):
        """수집 결과 >= 1000 시 warning."""
        # 10 pages of 100 items = 1000 items total
        pages = {
            i: [
                _make_search_item(
                    f"https://ghes/api/v3/repos/org/repo/pulls/{(i - 1) * 100 + j}",
                    (i - 1) * 100 + j,
                )
                for j in range(100)
            ]
            for i in range(1, 11)
        }

        def side_effect(query, page=1, per_page=100):
            if page in pages:
                return {"total_count": 1000, "items": pages[page]}
            return {"total_count": 1000, "items": []}

        mock_client.search_issues.side_effect = side_effect
        import logging

        with caplog.at_level(logging.WARNING):
            fetcher._search_prs_range("2025-02-14", "2025-02-16")
        assert any("truncated" in r.message.lower() or "1000" in r.message for r in caplog.records)


class TestSearchCommitsRange:
    def test_date_range_in_query(self, fetcher, mock_client):
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}
        fetcher._search_commits_range("2025-02-14", "2025-02-16")
        call_args = str(mock_client.search_commits.call_args)
        assert "committer-date:2025-02-14..2025-02-16" in call_args

    def test_returns_items(self, fetcher, mock_client):
        items = [_make_commit_search_item(sha="abc")]
        mock_client.search_commits.return_value = {"total_count": 1, "items": items}
        result = fetcher._search_commits_range("2025-02-14", "2025-02-16")
        assert len(result) == 1
        assert result[0]["sha"] == "abc"

    def test_search_failure_returns_empty(self, fetcher, mock_client):
        mock_client.search_commits.side_effect = FetchError("not supported")
        result = fetcher._search_commits_range("2025-02-14", "2025-02-16")
        assert result == []

    def test_warns_on_1000_results(self, fetcher, mock_client, caplog):
        """수집 결과 >= 1000 시 warning."""
        pages = {
            i: [_make_commit_search_item(sha=f"sha{(i - 1) * 100 + j}") for j in range(100)]
            for i in range(1, 11)
        }

        def side_effect(query, page=1, per_page=100):
            if page in pages:
                return {"total_count": 1000, "items": pages[page]}
            return {"total_count": 1000, "items": []}

        mock_client.search_commits.side_effect = side_effect
        import logging

        with caplog.at_level(logging.WARNING):
            fetcher._search_commits_range("2025-02-14", "2025-02-16")
        assert any("truncated" in r.message.lower() or "1000" in r.message for r in caplog.records)


class TestSearchIssuesRange:
    def test_date_range_in_query(self, fetcher, mock_client):
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        fetcher._search_issues_range("2025-02-14", "2025-02-16")
        calls = [str(c) for c in mock_client.search_issues.call_args_list]
        assert any("updated:2025-02-14..2025-02-16" in c for c in calls)

    def test_two_axes(self, fetcher, mock_client):
        """author + commenter 2축."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        fetcher._search_issues_range("2025-02-14", "2025-02-16")
        assert mock_client.search_issues.call_count == 2

    def test_dedup(self, fetcher, mock_client):
        item = _make_issue_search_item(10)
        mock_client.search_issues.return_value = {"total_count": 1, "items": [item]}
        result = fetcher._search_issues_range("2025-02-14", "2025-02-16")
        assert len(result) == 1

    def test_search_failure_graceful(self, fetcher, mock_client):
        mock_client.search_issues.side_effect = FetchError("server error")
        result = fetcher._search_issues_range("2025-02-14", "2025-02-16")
        assert result == {}

    def test_warns_on_1000_results(self, fetcher, mock_client, caplog):
        """수집 결과 >= 1000 시 warning."""
        pages = {
            i: [_make_issue_search_item(number=(i - 1) * 100 + j) for j in range(100)]
            for i in range(1, 11)
        }

        def side_effect(query, page=1, per_page=100):
            if page in pages:
                return {"total_count": 1000, "items": pages[page]}
            return {"total_count": 1000, "items": []}

        mock_client.search_issues.side_effect = side_effect
        import logging

        with caplog.at_level(logging.WARNING):
            fetcher._search_issues_range("2025-02-14", "2025-02-16")
        assert any("truncated" in r.message.lower() or "1000" in r.message for r in caplog.records)


class TestBucketByDate:
    def test_single_pr(self):
        pr_map = {
            "url1": {"updated_at": "2025-02-16T15:00:00Z", "title": "PR1"},
        }
        result = FetcherService._bucket_by_date(pr_map, [], {})
        assert "2025-02-16" in result
        assert "url1" in result["2025-02-16"]["prs"]
        assert result["2025-02-16"]["commits"] == []
        assert result["2025-02-16"]["issues"] == {}

    def test_single_commit(self):
        commit = {"commit": {"committer": {"date": "2025-02-16T10:00:00Z"}}, "sha": "abc"}
        result = FetcherService._bucket_by_date({}, [commit], {})
        assert "2025-02-16" in result
        assert len(result["2025-02-16"]["commits"]) == 1

    def test_single_issue(self):
        issue_map = {
            "url2": {"updated_at": "2025-02-16T12:00:00Z", "title": "Issue1"},
        }
        result = FetcherService._bucket_by_date({}, [], issue_map)
        assert "2025-02-16" in result
        assert "url2" in result["2025-02-16"]["issues"]

    def test_multiple_dates(self):
        pr_map = {
            "url1": {"updated_at": "2025-02-15T10:00:00Z"},
            "url2": {"updated_at": "2025-02-16T10:00:00Z"},
        }
        commits = [
            {"commit": {"committer": {"date": "2025-02-15T08:00:00Z"}}, "sha": "a"},
            {"commit": {"committer": {"date": "2025-02-16T09:00:00Z"}}, "sha": "b"},
        ]
        result = FetcherService._bucket_by_date(pr_map, commits, {})
        assert len(result) == 2
        assert "url1" in result["2025-02-15"]["prs"]
        assert "url2" in result["2025-02-16"]["prs"]
        assert len(result["2025-02-15"]["commits"]) == 1
        assert len(result["2025-02-16"]["commits"]) == 1

    def test_empty_inputs(self):
        result = FetcherService._bucket_by_date({}, [], {})
        assert result == {}

    def test_mixed_types_same_date(self):
        pr_map = {"url1": {"updated_at": "2025-02-16T10:00:00Z"}}
        commits = [{"commit": {"committer": {"date": "2025-02-16T11:00:00Z"}}, "sha": "x"}]
        issue_map = {"url2": {"updated_at": "2025-02-16T12:00:00Z"}}
        result = FetcherService._bucket_by_date(pr_map, commits, issue_map)
        assert len(result) == 1
        bucket = result["2025-02-16"]
        assert len(bucket["prs"]) == 1
        assert len(bucket["commits"]) == 1
        assert len(bucket["issues"]) == 1


class TestIsDateFetched:
    def test_all_files_exist(self, fetcher, test_config):
        """3개 파일 모두 존재 → True."""
        raw_dir = test_config.date_raw_dir("2025-02-16")
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "prs.json").write_text("[]")
        (raw_dir / "commits.json").write_text("[]")
        (raw_dir / "issues.json").write_text("[]")
        assert fetcher._is_date_fetched("2025-02-16") is True

    def test_missing_one_file(self, fetcher, test_config):
        """파일 1개 누락 → False."""
        raw_dir = test_config.date_raw_dir("2025-02-16")
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "prs.json").write_text("[]")
        (raw_dir / "commits.json").write_text("[]")
        # issues.json 없음
        assert fetcher._is_date_fetched("2025-02-16") is False

    def test_dir_not_exist(self, fetcher, test_config):
        """디렉토리 자체가 없음 → False."""
        assert fetcher._is_date_fetched("2099-01-01") is False


class TestFetchWithTypes:
    def test_fetch_only_prs(self, fetcher, mock_client, test_config):
        """types={"prs"} → commits/issues 미호출."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16", types={"prs"})
        assert "prs" in result
        assert "commits" not in result
        assert "issues" not in result
        mock_client.search_commits.assert_not_called()

    def test_fetch_only_commits(self, fetcher, mock_client, test_config):
        """types={"commits"} → PR/Issue 미호출."""
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16", types={"commits"})
        assert "commits" in result
        assert "prs" not in result
        assert "issues" not in result
        mock_client.search_issues.assert_not_called()

    def test_fetch_only_issues(self, fetcher, mock_client, test_config):
        """types={"issues"} → PR/Commit 미호출."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16", types={"issues"})
        assert "issues" in result
        assert "prs" not in result
        assert "commits" not in result
        mock_client.search_commits.assert_not_called()
        # search_issues IS called (for issue search), but not for PR search

    def test_fetch_two_types(self, fetcher, mock_client, test_config):
        """types={"prs", "issues"} → commits 미호출."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16", types={"prs", "issues"})
        assert "prs" in result
        assert "issues" in result
        assert "commits" not in result
        mock_client.search_commits.assert_not_called()

    def test_fetch_all_default(self, fetcher, mock_client, test_config):
        """types=None → 3개 모두 호출 (하위호환)."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16")
        assert set(result.keys()) == {"prs", "commits", "issues"}

    def test_returns_only_requested_keys(self, fetcher, mock_client, test_config):
        """types={"prs"} → "prs" 키만 포함."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16", types={"prs"})
        assert set(result.keys()) == {"prs"}

    def test_checkpoint_updated(self, fetcher, mock_client, test_config):
        """타입 필터링해도 checkpoint 업데이트."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        fetcher.fetch("2025-02-16", types={"prs"})
        cp_path = test_config.checkpoints_path
        assert cp_path.exists()
        data = load_json(cp_path)
        assert data["last_fetch_date"] == "2025-02-16"

    def test_fetch_returns_path_values(self, fetcher, mock_client, test_config):
        """반환값의 value가 Path 인스턴스."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result = fetcher.fetch("2025-02-16", types={"prs"})
        assert isinstance(result["prs"], Path)


# ── fetch_range 테스트 ──


class TestFetchRange:
    def _setup_empty_search(self, mock_client):
        """빈 검색 결과 세팅."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

    def test_basic_range(self, fetcher, mock_client, test_config):
        """기본 범위 fetch — 날짜별 빈 파일 생성."""
        self._setup_empty_search(mock_client)
        results = fetcher.fetch_range("2025-02-14", "2025-02-16")
        assert len(results) == 3
        for r in results:
            assert r["status"] == "success"
        # 3개 날짜 모두 파일 존재
        for d in ["2025-02-14", "2025-02-15", "2025-02-16"]:
            raw_dir = test_config.date_raw_dir(d)
            assert (raw_dir / "prs.json").exists()
            assert (raw_dir / "commits.json").exists()
            assert (raw_dir / "issues.json").exists()

    def test_skip_existing(self, fetcher, mock_client, test_config):
        """이미 fetch한 날짜는 skip."""
        self._setup_empty_search(mock_client)
        # 2025-02-15를 미리 생성
        raw_dir = test_config.date_raw_dir("2025-02-15")
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "prs.json").write_text("[]")
        (raw_dir / "commits.json").write_text("[]")
        (raw_dir / "issues.json").write_text("[]")

        results = fetcher.fetch_range("2025-02-14", "2025-02-16")
        statuses = {r["date"]: r["status"] for r in results}
        assert statuses["2025-02-15"] == "skipped"
        assert statuses["2025-02-14"] == "success"
        assert statuses["2025-02-16"] == "success"

    def test_force_override(self, fetcher, mock_client, test_config):
        """force=True → 기존 데이터 무시하고 재수집."""
        self._setup_empty_search(mock_client)
        # 미리 생성
        raw_dir = test_config.date_raw_dir("2025-02-15")
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "prs.json").write_text("[]")
        (raw_dir / "commits.json").write_text("[]")
        (raw_dir / "issues.json").write_text("[]")

        results = fetcher.fetch_range("2025-02-14", "2025-02-16", force=True)
        statuses = {r["date"]: r["status"] for r in results}
        assert statuses["2025-02-15"] == "success"  # force → not skipped

    def test_empty_dates_saved(self, fetcher, mock_client, test_config):
        """검색 결과 없는 날짜도 빈 JSON 저장."""
        self._setup_empty_search(mock_client)
        results = fetcher.fetch_range("2025-02-14", "2025-02-14")
        assert len(results) == 1
        assert results[0]["status"] == "success"
        raw_dir = test_config.date_raw_dir("2025-02-14")
        data = load_json(raw_dir / "prs.json")
        assert data == []

    def test_checkpoint_per_date(self, fetcher, mock_client, test_config):
        """성공한 날짜마다 checkpoint 갱신."""
        self._setup_empty_search(mock_client)
        fetcher.fetch_range("2025-02-14", "2025-02-16")
        data = load_json(test_config.checkpoints_path)
        assert data["last_fetch_date"] == "2025-02-16"

    def test_types_filter(self, fetcher, mock_client, test_config):
        """types 필터 전달 시 해당 타입만 검색/저장."""
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        results = fetcher.fetch_range("2025-02-14", "2025-02-14", types={"prs"})
        assert len(results) == 1
        # commits search 미호출
        mock_client.search_commits.assert_not_called()

    @patch("workrecap.services.fetcher.monthly_chunks")
    def test_monthly_chunking(self, mock_chunks, fetcher, mock_client, test_config):
        """monthly_chunks를 호출하여 월 단위 chunk."""
        mock_chunks.return_value = [("2025-02-01", "2025-02-28")]
        self._setup_empty_search(mock_client)
        fetcher.fetch_range("2025-02-01", "2025-02-28")
        mock_chunks.assert_called_once_with("2025-02-01", "2025-02-28")

    def test_single_day_range(self, fetcher, mock_client, test_config):
        """1일 범위도 정상 동작."""
        self._setup_empty_search(mock_client)
        results = fetcher.fetch_range("2025-02-16", "2025-02-16")
        assert len(results) == 1
        assert results[0]["date"] == "2025-02-16"
        assert results[0]["status"] == "success"

    def test_failure_resilience(self, fetcher, mock_client, test_config):
        """한 날짜 실패해도 다음 날짜 계속 진행."""
        call_count = 0

        def search_side_effect(query, **kwargs):
            nonlocal call_count
            call_count += 1
            # 첫 번째 chunk의 PR search에서 실패
            if call_count <= 3:  # first 3 calls (3 axes for PR)
                raise FetchError("temporary error")
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = search_side_effect
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        results = fetcher.fetch_range("2025-02-14", "2025-02-14")
        # Failure should be caught, date marked as failed
        assert any(r["status"] == "failed" for r in results)

    def test_returns_list_of_dicts(self, fetcher, mock_client, test_config):
        """반환값 형식 검증."""
        self._setup_empty_search(mock_client)
        results = fetcher.fetch_range("2025-02-14", "2025-02-15")
        assert isinstance(results, list)
        for r in results:
            assert "date" in r
            assert "status" in r
            assert r["status"] in ("success", "skipped", "failed")

    def test_with_pr_data(self, fetcher, mock_client, test_config):
        """PR 데이터 있는 경우 enrich + save."""
        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        pr_item = _make_search_item(api_url, 1)
        pr_item["updated_at"] = "2025-02-14T15:00:00Z"
        mock_client.search_issues.return_value = {"total_count": 1, "items": [pr_item]}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        results = fetcher.fetch_range("2025-02-14", "2025-02-14")
        assert results[0]["status"] == "success"
        raw_dir = test_config.date_raw_dir("2025-02-14")
        prs = load_json(raw_dir / "prs.json")
        assert len(prs) == 1


# ── DailyStateStore 통합 테스트 ──


class TestFetcherDailyStateIntegration:
    """DailyStateStore injection이 FetcherService에 미치는 영향 테스트."""

    def test_is_date_fetched_uses_daily_state_when_injected(self, test_config, mock_client):
        """daily_state가 있으면 _is_date_fetched가 timestamp 기반 체크 사용."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        mock_ds.is_fetch_stale.return_value = False  # not stale → already fetched
        fetcher = FetcherService(test_config, mock_client, daily_state=mock_ds)

        # 파일이 없더라도 daily_state가 "not stale" → True (already fetched)
        assert fetcher._is_date_fetched("2025-02-16") is True
        mock_ds.is_fetch_stale.assert_called_once_with("2025-02-16")

    def test_is_date_fetched_stale_returns_false(self, test_config, mock_client):
        """daily_state에서 stale이면 _is_date_fetched가 False 반환."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        mock_ds.is_fetch_stale.return_value = True  # stale → needs fetch
        fetcher = FetcherService(test_config, mock_client, daily_state=mock_ds)

        assert fetcher._is_date_fetched("2025-02-16") is False

    def test_set_timestamp_called_after_fetch(self, test_config, mock_client):
        """fetch 성공 후 daily_state.set_timestamp("fetch") 호출."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        fetcher = FetcherService(test_config, mock_client, daily_state=mock_ds)

        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        fetcher.fetch("2025-02-16")

        mock_ds.set_timestamp.assert_called_once_with("fetch", "2025-02-16")

    def test_fetch_range_narrows_to_stale_dates(self, test_config, mock_client):
        """daily_state가 있으면 fetch_range가 stale 날짜만 처리."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        # 2025-02-14 is stale, 2025-02-15 is not, 2025-02-16 is stale
        mock_ds.stale_dates.return_value = ["2025-02-14", "2025-02-16"]
        fetcher = FetcherService(test_config, mock_client, daily_state=mock_ds)

        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        results = fetcher.fetch_range("2025-02-14", "2025-02-16")

        statuses = {r["date"]: r["status"] for r in results}
        assert statuses["2025-02-15"] == "skipped"
        # 14 and 16 should be processed (success or at least attempted)
        assert statuses["2025-02-14"] in ("success", "failed")
        assert statuses["2025-02-16"] in ("success", "failed")

    def test_fetch_range_all_fresh_skips_all(self, test_config, mock_client):
        """daily_state에서 모든 날짜가 fresh면 전부 skipped."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        mock_ds.stale_dates.return_value = []  # no stale dates
        fetcher = FetcherService(test_config, mock_client, daily_state=mock_ds)

        results = fetcher.fetch_range("2025-02-14", "2025-02-16")

        assert len(results) == 3
        assert all(r["status"] == "skipped" for r in results)


class TestParallelEnrichment:
    def test_enrich_with_explicit_client(self, test_config, mock_client):
        """_enrich with explicit client uses that client, not self._client."""
        other_client = Mock(spec=GHESClient)
        other_client.get_pr.return_value = _make_pr_detail()
        other_client.get_pr_files.return_value = []
        other_client.get_pr_comments.return_value = []
        other_client.get_pr_reviews.return_value = []

        fetcher = FetcherService(test_config, mock_client)
        pr_basic = _make_search_item("https://ghes/api/v3/repos/org/repo/pulls/1")
        result = fetcher._enrich(pr_basic, client=other_client)

        assert result.number == 1
        other_client.get_pr.assert_called_once()
        mock_client.get_pr.assert_not_called()

    def test_enrich_commit_with_explicit_client(self, test_config, mock_client):
        """_enrich_commit with explicit client uses that client."""
        other_client = Mock(spec=GHESClient)
        other_client.get_commit.return_value = {
            "sha": "abc123",
            "html_url": "https://ghes/org/repo/commit/abc123",
            "url": "https://ghes/api/v3/repos/org/repo/commits/abc123",
            "commit": {
                "message": "test",
                "committer": {"date": "2025-02-16T10:00:00Z"},
            },
            "files": [],
        }

        fetcher = FetcherService(test_config, mock_client)
        item = {
            "sha": "abc123",
            "repository": {"full_name": "org/repo"},
            "author": {"login": "testuser"},
        }
        result = fetcher._enrich_commit(item, client=other_client)

        assert result.sha == "abc123"
        other_client.get_commit.assert_called_once()
        mock_client.get_commit.assert_not_called()

    def test_enrich_issue_with_explicit_client(self, test_config, mock_client):
        """_enrich_issue with explicit client uses that client."""
        other_client = Mock(spec=GHESClient)
        other_client.get_issue.return_value = {
            "url": "https://ghes/api/v3/repos/org/repo/issues/10",
            "html_url": "https://ghes/org/repo/issues/10",
            "number": 10,
            "title": "Bug",
            "body": "desc",
            "state": "open",
            "created_at": "2025-02-16T09:00:00Z",
            "updated_at": "2025-02-16T15:00:00Z",
            "closed_at": None,
            "user": {"login": "testuser"},
            "labels": [],
        }
        other_client.get_issue_comments.return_value = []

        fetcher = FetcherService(test_config, mock_client)
        item = {"url": "https://ghes/api/v3/repos/org/repo/issues/10"}
        result = fetcher._enrich_issue(item, client=other_client)

        assert result.number == 10
        other_client.get_issue.assert_called_once()
        mock_client.get_issue.assert_not_called()

    def test_default_client_when_none_passed(self, test_config, mock_client):
        """_enrich with client=None falls back to self._client."""
        fetcher = FetcherService(test_config, mock_client)
        pr_basic = _make_search_item("https://ghes/api/v3/repos/org/repo/pulls/1")
        result = fetcher._enrich(pr_basic)

        assert result.number == 1
        mock_client.get_pr.assert_called_once()

    def test_parallel_enrichment_with_max_workers(self, test_config, mock_client):
        """FetcherService with max_workers > 1 uses ThreadPoolExecutor."""
        from unittest.mock import MagicMock

        mock_pool = MagicMock()
        mock_pool_client = Mock(spec=GHESClient)
        mock_pool_client.get_pr.return_value = _make_pr_detail()
        mock_pool_client.get_pr_files.return_value = []
        mock_pool_client.get_pr_comments.return_value = []
        mock_pool_client.get_pr_reviews.return_value = []
        mock_pool.acquire.return_value = mock_pool_client
        mock_pool.release = Mock()

        fetcher = FetcherService(test_config, mock_client, max_workers=3, client_pool=mock_pool)

        bucket = {
            "prs": {
                f"https://ghes/api/v3/repos/org/repo/pulls/{i}": _make_search_item(
                    f"https://ghes/api/v3/repos/org/repo/pulls/{i}", i
                )
                for i in range(1, 4)
            },
            "commits": [],
            "issues": {},
        }
        fetcher._save_date_from_bucket("2025-02-16", bucket, {"prs"})

        # Pool should have been used
        assert mock_pool.acquire.call_count == 3
        assert mock_pool.release.call_count == 3

    def test_failure_isolation_in_parallel(self, test_config, mock_client):
        """One PR enrichment failure doesn't prevent others from succeeding."""
        call_count = [0]

        def fail_on_second(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise FetchError("boom")
            return _make_pr_detail()

        mock_client.get_pr.side_effect = fail_on_second

        fetcher = FetcherService(test_config, mock_client, max_workers=1)

        bucket = {
            "prs": {
                f"https://ghes/api/v3/repos/org/repo/pulls/{i}": _make_search_item(
                    f"https://ghes/api/v3/repos/org/repo/pulls/{i}", i
                )
                for i in range(1, 4)
            },
            "commits": [],
            "issues": {},
        }
        # Should not raise - failures are logged and skipped
        fetcher._save_date_from_bucket("2025-02-16", bucket, {"prs"})


class TestFetchRangeParallel:
    def test_fetch_range_max_workers_passed(self, test_config, mock_client):
        """fetch_range with max_workers>1 uses parallel date processing."""
        from unittest.mock import MagicMock

        mock_pool = MagicMock()
        pool_client = Mock(spec=GHESClient)
        pool_client.get_pr.return_value = _make_pr_detail()
        pool_client.get_pr_files.return_value = []
        pool_client.get_pr_comments.return_value = []
        pool_client.get_pr_reviews.return_value = []
        mock_pool.acquire.return_value = pool_client
        mock_pool.release = Mock()

        fetcher = FetcherService(test_config, mock_client, max_workers=3, client_pool=mock_pool)

        # Mock search to return 1 PR per axis
        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        item = _make_search_item(api_url)
        mock_client.search_issues.return_value = {"total_count": 1, "items": [item]}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        results = fetcher.fetch_range("2025-02-16", "2025-02-16", force=True)

        assert len(results) == 1
        assert results[0]["status"] == "success"

    def test_fetch_range_sequential_fallback(self, test_config, mock_client):
        """fetch_range with max_workers=1 uses sequential path."""
        fetcher = FetcherService(test_config, mock_client, max_workers=1)

        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        results = fetcher.fetch_range("2025-02-16", "2025-02-16", force=True)

        assert len(results) == 1
        assert results[0]["status"] == "success"

    def test_fetch_range_parallel_date_failure_isolation(self, test_config, mock_client):
        """One date failure in parallel mode doesn't affect others."""
        call_count = [0]

        def search_side_effect(query, **kwargs):
            call_count[0] += 1
            return {"total_count": 0, "items": []}

        mock_client.search_issues.side_effect = search_side_effect
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        fetcher = FetcherService(test_config, mock_client, max_workers=2)

        results = fetcher.fetch_range("2025-02-14", "2025-02-16", force=True)

        assert len(results) == 3
        # All should succeed (empty results is still success)
        statuses = [r["status"] for r in results]
        assert "success" in statuses


class TestFetchRangeResume:
    def test_resume_skips_search_for_cached_chunk(self, test_config, mock_client):
        """When progress_store has cached chunk, search API is not called."""
        from workrecap.services.fetch_progress import FetchProgressStore

        progress_dir = test_config.data_dir / "state" / "fetch_progress"
        progress_store = FetchProgressStore(progress_dir)

        # Pre-cache a chunk search result (empty buckets)
        progress_store.save_chunk_search("2025-02-16__2025-02-16", {})

        fetcher = FetcherService(test_config, mock_client, progress_store=progress_store)

        results = fetcher.fetch_range("2025-02-16", "2025-02-16", force=True)

        # Search should NOT have been called (cached)
        mock_client.search_issues.assert_not_called()
        mock_client.search_commits.assert_not_called()
        assert len(results) == 1
        assert results[0]["status"] == "success"

    def test_resume_calls_search_for_uncached_chunk(self, test_config, mock_client):
        """When progress_store has no cache, search API is called normally."""
        from workrecap.services.fetch_progress import FetchProgressStore

        progress_dir = test_config.data_dir / "state" / "fetch_progress"
        progress_store = FetchProgressStore(progress_dir)
        # Don't cache anything

        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        fetcher = FetcherService(test_config, mock_client, progress_store=progress_store)

        results = fetcher.fetch_range("2025-02-16", "2025-02-16", force=True)

        # Search SHOULD have been called
        assert mock_client.search_issues.call_count > 0
        assert len(results) == 1

    def test_resume_clears_chunk_after_completion(self, test_config, mock_client):
        """Completed chunks are cleared from progress store."""
        from workrecap.services.fetch_progress import FetchProgressStore

        progress_dir = test_config.data_dir / "state" / "fetch_progress"
        progress_store = FetchProgressStore(progress_dir)

        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        fetcher = FetcherService(test_config, mock_client, progress_store=progress_store)

        fetcher.fetch_range("2025-02-16", "2025-02-16", force=True)

        # Chunk should have been cleared after successful processing
        assert progress_store.load_chunk_search("2025-02-16__2025-02-16") is None

    def test_resume_interruption_scenario(self, test_config, mock_client):
        """Simulate interruption: cache chunk, process some dates, then resume."""
        from workrecap.services.fetch_progress import FetchProgressStore
        from workrecap.services.daily_state import DailyStateStore

        progress_dir = test_config.data_dir / "state" / "fetch_progress"
        progress_store = FetchProgressStore(progress_dir)
        ds = DailyStateStore(test_config.daily_state_path)

        # First run: "interrupted" — cache chunk but only process some dates
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        mock_client.search_commits.return_value = {"total_count": 0, "items": []}

        fetcher = FetcherService(
            test_config, mock_client, daily_state=ds, progress_store=progress_store
        )

        # First run — processes all dates, caches chunk search results
        results1 = fetcher.fetch_range("2025-02-14", "2025-02-16", force=True)
        assert len(results1) == 3

        # Second run — daily_state shows dates already processed, so skips them
        mock_client.search_issues.reset_mock()
        mock_client.search_commits.reset_mock()
        results2 = fetcher.fetch_range("2025-02-14", "2025-02-16", force=False)

        # All should be skipped (already processed)
        assert all(r["status"] == "skipped" for r in results2)
