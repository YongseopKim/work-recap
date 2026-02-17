import shutil
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_recap.exceptions import SummarizeError
from git_recap.infra.llm_client import LLMClient
from git_recap.models import (
    Activity,
    ActivityKind,
    DailyStats,
    save_json,
    save_jsonl,
)
from git_recap.services.summarizer import SummarizerService

DATE = "2025-02-16"


# ── Fixtures ──


@pytest.fixture
def mock_llm():
    llm = MagicMock(spec=LLMClient)
    llm.chat.return_value = "# LLM Generated Summary\n\nMock content."
    return llm


@pytest.fixture
def prompts_dir(test_config):
    """프롬프트 디렉토리에 실제 템플릿 복사."""
    src_prompts = Path(__file__).parents[2] / "prompts"
    for f in src_prompts.glob("*.md"):
        shutil.copy(f, test_config.prompts_dir / f.name)
    return test_config.prompts_dir


@pytest.fixture
def summarizer(test_config, mock_llm, prompts_dir):
    return SummarizerService(test_config, mock_llm)


def _save_normalized(test_config, target_date=DATE):
    """테스트용 activities.jsonl + stats.json 저장."""
    activities = [
        Activity(
            ts=f"{target_date}T09:00:00Z",
            kind=ActivityKind.PR_AUTHORED,
            repo="org/repo",
            pr_number=1,
            title="Add feature",
            url="https://ghes/org/repo/pull/1",
            summary="pr_authored: Add feature (org/repo) +10/-3",
            files=["src/main.py"],
            additions=10,
            deletions=3,
        ),
    ]
    stats = DailyStats(
        date=target_date,
        authored_count=1,
        reviewed_count=0,
        commented_count=0,
        total_additions=10,
        total_deletions=3,
        repos_touched=["org/repo"],
        authored_prs=[{"url": "u", "title": "Add feature", "repo": "org/repo"}],
    )
    norm_dir = test_config.date_normalized_dir(target_date)
    save_jsonl(activities, norm_dir / "activities.jsonl")
    save_json(stats, norm_dir / "stats.json")


def _save_daily_summary(test_config, target_date, content="# Daily\nContent"):
    path = test_config.daily_summary_path(target_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _save_weekly_summary(test_config, year, week, content="# Weekly\nContent"):
    path = test_config.weekly_summary_path(year, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _save_monthly_summary(test_config, year, month, content="# Monthly\nContent"):
    path = test_config.monthly_summary_path(year, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── Tests ──


class TestRenderPrompt:
    def test_renders_template_with_vars(self, summarizer, test_config):
        result = summarizer._render_prompt(
            "daily.md",
            date="2025-02-16",
            stats={
                "authored_count": 3,
                "reviewed_count": 1,
                "commented_count": 0,
                "total_additions": 100,
                "total_deletions": 20,
                "repos_touched": ["org/a", "org/b"],
            },
        )
        assert "2025-02-16" in result
        assert "3" in result  # authored_count
        assert "org/a" in result

    def test_template_not_found(self, summarizer):
        with pytest.raises(SummarizeError, match="Prompt template not found"):
            summarizer._render_prompt("nonexistent.md")


class TestFormatActivities:
    def test_formats_activities(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "Add feature",
                "repo": "org/repo",
                "additions": 10,
                "deletions": 3,
                "url": "https://ghes/pull/1",
                "files": ["src/main.py", "tests/test.py"],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "pr_authored" in result
        assert "Add feature" in result
        assert "+10/-3" in result
        assert "src/main.py" in result

    def test_empty_activities(self):
        result = SummarizerService._format_activities([])
        assert result == "(활동 없음)"

    def test_truncates_files(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "Big change",
                "repo": "org/repo",
                "additions": 100,
                "deletions": 50,
                "url": "https://ghes/pull/1",
                "files": [f"file{i}.py" for i in range(8)],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "외 3개" in result

    def test_no_files(self):
        activities = [
            {
                "kind": "pr_commented",
                "title": "Review",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "https://ghes/pull/1",
                "files": [],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Files:" not in result


class TestDaily:
    def test_generates_daily_summary(self, summarizer, mock_llm, test_config):
        _save_normalized(test_config)
        path = summarizer.daily(DATE)

        assert path.exists()
        assert path.read_text(encoding="utf-8") == "# LLM Generated Summary\n\nMock content."
        mock_llm.chat.assert_called_once()

    def test_activities_not_found(self, summarizer):
        with pytest.raises(SummarizeError, match="Activities file not found"):
            summarizer.daily("2099-01-01")

    def test_stats_not_found(self, summarizer, test_config):
        norm_dir = test_config.date_normalized_dir(DATE)
        save_jsonl([], norm_dir / "activities.jsonl")

        with pytest.raises(SummarizeError, match="Stats file not found"):
            summarizer.daily(DATE)

    def test_llm_receives_stats_in_prompt(self, summarizer, mock_llm, test_config):
        _save_normalized(test_config)
        summarizer.daily(DATE)

        system_prompt = mock_llm.chat.call_args[0][0]
        assert "2025-02-16" in system_prompt
        assert "1" in system_prompt  # authored_count


class TestWeekly:
    def test_generates_weekly_summary(self, summarizer, mock_llm, test_config):
        # 2025-W07: Mon=2025-02-10 ~ Sun=2025-02-16
        _save_daily_summary(test_config, "2025-02-10", "# Mon content")
        _save_daily_summary(test_config, "2025-02-14", "# Fri content")

        path = summarizer.weekly(2025, 7)
        assert path.exists()
        mock_llm.chat.assert_called_once()
        user_content = mock_llm.chat.call_args[0][1]
        assert "Mon content" in user_content
        assert "Fri content" in user_content

    def test_no_daily_found(self, summarizer):
        with pytest.raises(SummarizeError, match="No daily summaries found"):
            summarizer.weekly(2099, 1)


class TestCollectDailyForWeek:
    def test_iso_week_calculation(self, summarizer, test_config):
        # 2025-W07: Mon 2/10 ~ Sun 2/16
        for d in range(10, 17):
            _save_daily_summary(test_config, f"2025-02-{d:02d}", f"Day {d}")

        contents = summarizer._collect_daily_for_week(2025, 7)
        assert len(contents) == 7

    def test_partial_week(self, summarizer, test_config):
        # 월~수만 존재
        _save_daily_summary(test_config, "2025-02-10")
        _save_daily_summary(test_config, "2025-02-11")
        _save_daily_summary(test_config, "2025-02-12")

        contents = summarizer._collect_daily_for_week(2025, 7)
        assert len(contents) == 3


class TestMonthly:
    def test_generates_monthly_summary(self, summarizer, mock_llm, test_config):
        _save_weekly_summary(test_config, 2025, 5)
        _save_weekly_summary(test_config, 2025, 6)
        _save_weekly_summary(test_config, 2025, 7)
        _save_weekly_summary(test_config, 2025, 8)

        path = summarizer.monthly(2025, 2)
        assert path.exists()
        mock_llm.chat.assert_called_once()

    def test_no_weekly_found(self, summarizer):
        with pytest.raises(SummarizeError, match="No weekly summaries found"):
            summarizer.monthly(2099, 1)


class TestCollectWeeklyForMonth:
    def test_february_2025(self, summarizer, test_config):
        # Feb 2025: weeks 5, 6, 7, 8, 9
        for w in range(5, 10):
            _save_weekly_summary(test_config, 2025, w)

        contents = summarizer._collect_weekly_for_month(2025, 2)
        assert len(contents) >= 4  # at least W5~W8

    def test_no_files(self, summarizer, test_config):
        contents = summarizer._collect_weekly_for_month(2025, 2)
        assert contents == []


class TestYearly:
    def test_generates_yearly_summary(self, summarizer, mock_llm, test_config):
        _save_monthly_summary(test_config, 2025, 1)
        _save_monthly_summary(test_config, 2025, 2)

        path = summarizer.yearly(2025)
        assert path.exists()
        mock_llm.chat.assert_called_once()
        user_content = mock_llm.chat.call_args[0][1]
        assert "Monthly" in user_content

    def test_no_monthly_found(self, summarizer):
        with pytest.raises(SummarizeError, match="No monthly summaries found"):
            summarizer.yearly(2099)


class TestQuery:
    def test_query_with_context(self, summarizer, mock_llm, test_config):
        today = date.today()
        _save_monthly_summary(test_config, today.year, today.month, "# This month")

        result = summarizer.query("이번 달 주요 성과?")
        assert result == "# LLM Generated Summary\n\nMock content."
        mock_llm.chat.assert_called_once()
        user_content = mock_llm.chat.call_args[0][1]
        assert "이번 달 주요 성과?" in user_content
        assert "This month" in user_content

    def test_no_context(self, summarizer):
        with pytest.raises(SummarizeError, match="No summary data available"):
            summarizer.query("질문?")


# ── _is_date_summarized 테스트 ──


class TestIsDateSummarized:
    def test_summary_exists(self, summarizer, test_config):
        """daily summary 파일 존재 → True."""
        _save_daily_summary(test_config, DATE)
        assert summarizer._is_date_summarized(DATE) is True

    def test_summary_not_exists(self, summarizer):
        """daily summary 파일 없음 → False."""
        assert summarizer._is_date_summarized("2099-01-01") is False


# ── Summarize Checkpoint 테스트 ──


class TestSummarizeCheckpoint:
    def test_creates_checkpoint_file(self, summarizer, test_config):
        """daily() 후 checkpoints.json에 last_summarize_date."""
        _save_normalized(test_config)
        summarizer.daily(DATE)

        from git_recap.models import load_json

        cp = load_json(test_config.checkpoints_path)
        assert cp["last_summarize_date"] == DATE

    def test_updates_existing_checkpoint(self, summarizer, test_config):
        """두 번 daily() → 마지막 날짜로 갱신."""
        date2 = "2025-02-17"
        _save_normalized(test_config, DATE)
        _save_normalized(test_config, date2)
        summarizer.daily(DATE)
        summarizer.daily(date2)

        from git_recap.models import load_json

        cp = load_json(test_config.checkpoints_path)
        assert cp["last_summarize_date"] == date2

    def test_preserves_other_keys(self, summarizer, test_config):
        """last_fetch_date 보존 확인."""
        import json

        cp_path = test_config.checkpoints_path
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cp_path, "w") as f:
            json.dump({"last_fetch_date": "2025-02-16"}, f)

        _save_normalized(test_config)
        summarizer.daily(DATE)

        from git_recap.models import load_json

        cp = load_json(cp_path)
        assert cp["last_fetch_date"] == "2025-02-16"
        assert cp["last_summarize_date"] == DATE


# ── daily_range 테스트 ──


class TestDailyRange:
    def test_basic_range(self, summarizer, test_config):
        """3일 range → 3개 success."""
        for d in ["2025-02-14", "2025-02-15", "2025-02-16"]:
            _save_normalized(test_config, d)
        results = summarizer.daily_range("2025-02-14", "2025-02-16")
        assert len(results) == 3
        assert all(r["status"] == "success" for r in results)

    def test_skip_existing(self, summarizer, test_config):
        """중간 날짜 pre-create → skipped."""
        for d in ["2025-02-14", "2025-02-15", "2025-02-16"]:
            _save_normalized(test_config, d)
        # Pre-create summary for middle date
        _save_daily_summary(test_config, "2025-02-15")

        results = summarizer.daily_range("2025-02-14", "2025-02-16")
        statuses = {r["date"]: r["status"] for r in results}
        assert statuses["2025-02-15"] == "skipped"
        assert statuses["2025-02-14"] == "success"
        assert statuses["2025-02-16"] == "success"

    def test_force_override(self, summarizer, test_config):
        """force=True → skip 없이 전부 success."""
        for d in ["2025-02-14", "2025-02-15", "2025-02-16"]:
            _save_normalized(test_config, d)
        _save_daily_summary(test_config, "2025-02-15")

        results = summarizer.daily_range("2025-02-14", "2025-02-16", force=True)
        assert all(r["status"] == "success" for r in results)

    def test_failure_resilience(self, summarizer, test_config):
        """중간 날짜 normalized 없음 → failed, 나머지 success."""
        _save_normalized(test_config, "2025-02-14")
        # 2025-02-15: no normalized data
        _save_normalized(test_config, "2025-02-16")

        results = summarizer.daily_range("2025-02-14", "2025-02-16")
        statuses = {r["date"]: r["status"] for r in results}
        assert statuses["2025-02-14"] == "success"
        assert statuses["2025-02-15"] == "failed"
        assert statuses["2025-02-16"] == "success"

    def test_checkpoint_per_date(self, summarizer, test_config):
        """last_summarize_date == 마지막 성공 날짜."""
        for d in ["2025-02-14", "2025-02-15", "2025-02-16"]:
            _save_normalized(test_config, d)
        summarizer.daily_range("2025-02-14", "2025-02-16")

        from git_recap.models import load_json

        cp = load_json(test_config.checkpoints_path)
        assert cp["last_summarize_date"] == "2025-02-16"

    def test_returns_list_of_dicts(self, summarizer, test_config):
        """반환 형식 검증."""
        _save_normalized(test_config, DATE)
        results = summarizer.daily_range(DATE, DATE)
        assert isinstance(results, list)
        assert len(results) == 1
        assert "date" in results[0]
        assert "status" in results[0]
