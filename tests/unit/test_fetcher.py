from pathlib import Path
from unittest.mock import Mock

import pytest

from git_recap.config import AppConfig
from git_recap.exceptions import FetchError
from git_recap.infra.ghes_client import GHESClient
from git_recap.models import load_json
from git_recap.services.fetcher import FetcherService


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
        {"filename": "src/main.py", "additions": 10, "deletions": 3, "status": "modified"},
    ]
    client.get_pr_comments.return_value = [
        {
            "user": {"login": "reviewer1"},
            "body": "Good approach",
            "created_at": "2025-02-16T11:00:00Z",
            "html_url": "https://ghes/org/repo/pull/1#comment-1",
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
        mock_client.search_issues.return_value = {
            "total_count": 1, "items": [item]
        }
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
            _make_search_item(
                f"https://ghes/api/v3/repos/org/repo/pulls/{i}", i
            )
            for i in range(100)
        ]
        page2_items = [
            _make_search_item(
                f"https://ghes/api/v3/repos/org/repo/pulls/{i}", i
            )
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
        assert len(result.comments) == 1
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
        mock_client.search_issues.return_value = {
            "total_count": 1, "items": [item]
        }

        result_path = fetcher.fetch("2025-02-16")

        assert result_path.exists()
        data = load_json(result_path)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["title"] == "Test PR"

    def test_empty_result(self, fetcher, mock_client, test_config):
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result_path = fetcher.fetch("2025-02-16")

        assert result_path.exists()
        data = load_json(result_path)
        assert data == []

    def test_enrich_failure_skips_pr(self, fetcher, mock_client, test_config):
        api_url = "https://ghes/api/v3/repos/org/repo/pulls/1"
        item = _make_search_item(api_url, 1)
        mock_client.search_issues.return_value = {
            "total_count": 1, "items": [item]
        }
        mock_client.get_pr.side_effect = FetchError("timeout")

        result_path = fetcher.fetch("2025-02-16")
        data = load_json(result_path)
        assert data == []

    def test_output_path_matches_date(self, fetcher, mock_client, test_config):
        mock_client.search_issues.return_value = {"total_count": 0, "items": []}
        result_path = fetcher.fetch("2025-02-16")
        assert "2025" in str(result_path)
        assert "02" in str(result_path)
        assert "16" in str(result_path)
        assert result_path.name == "prs.json"


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
