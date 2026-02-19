"""Summarizer batch 모드 단위 테스트."""

import json
from unittest.mock import MagicMock

import pytest

from workrecap.infra.providers.batch_mixin import BatchResult
from workrecap.models import TokenUsage
from workrecap.services.summarizer import SummarizerService


@pytest.fixture
def setup_data(tmp_path, test_config):
    """2일치 normalized 데이터 + daily 템플릿."""
    config = test_config

    # daily prompt template
    prompts_dir = config.prompts_dir
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "daily.md").write_text(
        "You are a daily summarizer.\n<!-- SPLIT -->\nDate: {{ date }}\nStats: {{ stats }}"
    )

    # 2일치 normalized 데이터
    for date_str in ["2026-01-01", "2026-01-02"]:
        norm_dir = config.date_normalized_dir(date_str)
        norm_dir.mkdir(parents=True, exist_ok=True)

        activities = [
            {
                "ts": f"{date_str}T10:00:00Z",
                "kind": "pr_authored",
                "repo": "org/repo",
                "external_id": 1,
                "title": f"PR for {date_str}",
                "url": "https://github.com/org/repo/pull/1",
                "summary": f"pr_authored: PR for {date_str}",
                "additions": 10,
                "deletions": 2,
                "files": ["main.py"],
                "source": "github",
            }
        ]
        lines = [json.dumps(a) for a in activities]
        (norm_dir / "activities.jsonl").write_text("\n".join(lines), encoding="utf-8")

        stats = {
            "date": date_str,
            "github": {
                "authored_count": 1,
                "reviewed_count": 0,
                "commented_count": 0,
                "total_additions": 10,
                "total_deletions": 2,
                "repos_touched": ["org/repo"],
                "authored_prs": [],
                "reviewed_prs": [],
                "commit_count": 0,
                "issue_authored_count": 0,
                "issue_commented_count": 0,
                "commits": [],
                "authored_issues": [],
            },
            "confluence": {},
            "jira": {},
        }
        (norm_dir / "stats.json").write_text(json.dumps(stats), encoding="utf-8")

    return config


class TestSummarizerBatchMode:
    def test_batch_false_uses_sequential(self, setup_data):
        """batch=False는 기존 per-date 호출."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "# Daily Summary\nContent here"
        summarizer = SummarizerService(setup_data, mock_llm)

        results = summarizer.daily_range("2026-01-01", "2026-01-02", force=True)
        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)
        assert mock_llm.chat.call_count == 2

    def test_batch_true_submits_batch(self, setup_data):
        """batch=True → submit_batch + wait_for_batch."""
        mock_llm = MagicMock()
        mock_llm.submit_batch.return_value = "batch-daily-123"
        mock_llm.wait_for_batch.return_value = [
            BatchResult(
                custom_id="daily-2026-01-01",
                content="# 2026-01-01 Summary\nBatch generated content",
                usage=TokenUsage(
                    prompt_tokens=200, completion_tokens=100, total_tokens=300, call_count=1
                ),
            ),
            BatchResult(
                custom_id="daily-2026-01-02",
                content="# 2026-01-02 Summary\nBatch generated content",
                usage=TokenUsage(
                    prompt_tokens=200, completion_tokens=100, total_tokens=300, call_count=1
                ),
            ),
        ]
        summarizer = SummarizerService(setup_data, mock_llm)

        results = summarizer.daily_range("2026-01-01", "2026-01-02", force=True, batch=True)
        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)

        mock_llm.submit_batch.assert_called_once()
        mock_llm.wait_for_batch.assert_called_once()
        mock_llm.chat.assert_not_called()

        # Verify summaries were saved
        for date_str in ["2026-01-01", "2026-01-02"]:
            path = setup_data.daily_summary_path(date_str)
            assert path.exists()
            content = path.read_text()
            assert "Batch generated content" in content

    def test_batch_with_error_result(self, setup_data):
        """batch 결과에 error → 해당 날짜 failed."""
        mock_llm = MagicMock()
        mock_llm.submit_batch.return_value = "batch-123"
        mock_llm.wait_for_batch.return_value = [
            BatchResult(
                custom_id="daily-2026-01-01",
                content="# Summary\nOK",
                usage=TokenUsage(call_count=1),
            ),
            BatchResult(
                custom_id="daily-2026-01-02",
                error="Context too long",
            ),
        ]
        summarizer = SummarizerService(setup_data, mock_llm)

        results = summarizer.daily_range("2026-01-01", "2026-01-02", force=True, batch=True)
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "failed"

    def test_batch_skip_logic(self, setup_data):
        """batch 모드에서도 skip 로직 동작."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "# Summary"
        summarizer = SummarizerService(setup_data, mock_llm)

        # First pass: sequential
        summarizer.daily_range("2026-01-01", "2026-01-02", force=True)

        # Second pass: batch with force=False → skip
        mock_llm.reset_mock()
        results = summarizer.daily_range("2026-01-01", "2026-01-02", force=False, batch=True)
        assert all(r["status"] == "skipped" for r in results)
        mock_llm.submit_batch.assert_not_called()

    def test_batch_empty_activities_marker(self, setup_data):
        """활동 없는 날짜는 batch에 포함하지 않고 marker 파일 생성."""
        # Overwrite one date with empty activities
        norm_dir = setup_data.date_normalized_dir("2026-01-02")
        (norm_dir / "activities.jsonl").write_text("", encoding="utf-8")

        mock_llm = MagicMock()
        mock_llm.submit_batch.return_value = "batch-123"
        mock_llm.wait_for_batch.return_value = [
            BatchResult(
                custom_id="daily-2026-01-01",
                content="# Summary for 01",
                usage=TokenUsage(call_count=1),
            ),
        ]
        summarizer = SummarizerService(setup_data, mock_llm)

        results = summarizer.daily_range("2026-01-01", "2026-01-02", force=True, batch=True)
        assert len(results) == 2
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "success"  # marker file

        # First date: batch content
        p1 = setup_data.daily_summary_path("2026-01-01")
        assert "Summary for 01" in p1.read_text()

        # Second date: marker file
        p2 = setup_data.daily_summary_path("2026-01-02")
        assert "활동이 없는 날" in p2.read_text()
