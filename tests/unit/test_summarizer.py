import logging
import shutil
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workrecap.exceptions import SummarizeError
from workrecap.infra.llm_router import LLMRouter
from workrecap.models import (
    Activity,
    ActivityKind,
    DailyStats,
    GitHubStats,
    save_json,
    save_jsonl,
)
from workrecap.services.summarizer import SummarizerService

DATE = "2025-02-16"


# ── Fixtures ──


@pytest.fixture
def mock_llm():
    llm = MagicMock(spec=LLMRouter)
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
            external_id=1,
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
        github=GitHubStats(
            authored_count=1,
            reviewed_count=0,
            commented_count=0,
            total_additions=10,
            total_deletions=3,
            repos_touched=["org/repo"],
            authored_prs=[{"url": "u", "title": "Add feature", "repo": "org/repo"}],
        ),
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
                "github": {
                    "authored_count": 3,
                    "reviewed_count": 1,
                    "commented_count": 0,
                    "total_additions": 100,
                    "total_deletions": 20,
                    "repos_touched": ["org/a", "org/b"],
                },
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
                "files": [f"file{i}.py" for i in range(13)],
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

    def test_body_included(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "Add feature",
                "repo": "org/repo",
                "additions": 10,
                "deletions": 3,
                "url": "https://ghes/pull/1",
                "files": [],
                "body": "Implements JWT-based auth for the login flow",
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Body: Implements JWT-based auth" in result

    def test_review_bodies_included(self):
        activities = [
            {
                "kind": "pr_reviewed",
                "title": "Add feature",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "https://ghes/pull/1",
                "files": [],
                "review_bodies": ["LGTM, nice work!", "One minor nit"],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Reviews: LGTM, nice work! | One minor nit" in result

    def test_comment_bodies_included(self):
        activities = [
            {
                "kind": "pr_commented",
                "title": "Add feature",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "https://ghes/pull/1",
                "files": [],
                "comment_bodies": ["Looks good", "Fixed in next commit"],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Comments: Looks good | Fixed in next commit" in result

    def test_body_truncated_at_1000(self):
        long_body = "x" * 1200
        activities = [
            {
                "kind": "pr_authored",
                "title": "Big PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "https://ghes/pull/1",
                "files": [],
                "body": long_body,
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Body: " + "x" * 1000 + "..." in result
        assert "x" * 1001 not in result

    def test_review_body_truncated_at_500(self):
        long_review = "r" * 600
        activities = [
            {
                "kind": "pr_reviewed",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "https://ghes/pull/1",
                "files": [],
                "review_bodies": [long_review],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Reviews: " + "r" * 500 + "..." in result
        assert "r" * 501 not in result

    def test_comment_body_truncated_at_500(self):
        long_comment = "c" * 600
        activities = [
            {
                "kind": "pr_commented",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "https://ghes/pull/1",
                "files": [],
                "comment_bodies": [long_comment],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Comments: " + "c" * 500 + "..." in result
        assert "c" * 501 not in result

    def test_patches_section_rendered(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "PR",
                "repo": "org/repo",
                "additions": 5,
                "deletions": 2,
                "url": "https://ghes/pull/1",
                "files": ["src/auth.py"],
                "file_patches": {"src/auth.py": "@@ -40,3 +40,5 @@\n+  if user is None:"},
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Patches:" in result
        assert "--- src/auth.py ---" in result
        assert "+  if user is None:" in result

    def test_patches_section_truncated_per_file(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "u",
                "files": ["big.py"],
                "file_patches": {"big.py": "x" * 1200},
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Patches:" in result
        assert "x" * 1000 in result
        assert "x" * 1001 not in result

    def test_patches_section_not_shown_when_empty(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "u",
                "files": [],
                "file_patches": {},
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Patches:" not in result

    def test_inline_comments_section_rendered(self):
        activities = [
            {
                "kind": "pr_reviewed",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "u",
                "files": [],
                "comment_contexts": [
                    {
                        "path": "src/auth.py",
                        "line": 42,
                        "diff_hunk": "@@ -40,3 +40,5 @@",
                        "body": "Consider checking user.verified",
                    },
                ],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Inline comments:" in result
        assert "at src/auth.py:42" in result
        assert "Consider checking user.verified" in result

    def test_inline_comments_hunk_truncated_from_end(self):
        long_hunk = "h" * 500
        activities = [
            {
                "kind": "pr_reviewed",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "u",
                "files": [],
                "comment_contexts": [
                    {"path": "f.py", "line": 1, "diff_hunk": long_hunk, "body": "note"},
                ],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Inline comments:" in result
        # hunk truncated to last 300 chars
        assert "h" * 300 in result
        assert "h" * 301 not in result

    def test_inline_comments_not_shown_when_empty(self):
        activities = [
            {
                "kind": "pr_reviewed",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "u",
                "files": [],
                "comment_contexts": [],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Inline comments:" not in result

    def test_empty_body_not_shown(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "https://ghes/pull/1",
                "files": [],
                "body": "",
                "review_bodies": [],
                "comment_bodies": [],
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Body:" not in result
        assert "Reviews:" not in result
        assert "Comments:" not in result

    def test_intent_included(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "Fix login",
                "repo": "org/repo",
                "additions": 5,
                "deletions": 2,
                "url": "https://ghes/pull/1",
                "files": [],
                "intent": "bugfix",
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Intent: bugfix" in result

    def test_change_summary_included(self):
        activities = [
            {
                "kind": "commit",
                "title": "Update deps",
                "repo": "org/repo",
                "additions": 3,
                "deletions": 1,
                "url": "https://ghes/commit/abc",
                "files": [],
                "change_summary": "의존성 라이브러리를 최신 버전으로 업데이트",
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Change Summary: 의존성 라이브러리를 최신 버전으로 업데이트" in result

    def test_empty_intent_not_shown(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "u",
                "files": [],
                "intent": "",
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Intent:" not in result

    def test_empty_change_summary_not_shown(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "PR",
                "repo": "org/repo",
                "additions": 0,
                "deletions": 0,
                "url": "u",
                "files": [],
                "change_summary": "",
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Change Summary:" not in result

    def test_intent_and_change_summary_order(self):
        activities = [
            {
                "kind": "pr_authored",
                "title": "Add auth",
                "repo": "org/repo",
                "additions": 50,
                "deletions": 10,
                "url": "https://ghes/pull/5",
                "files": ["src/auth.py"],
                "intent": "feature",
                "change_summary": "JWT 기반 인증 로직 추가",
            },
        ]
        result = SummarizerService._format_activities(activities)
        intent_pos = result.index("Intent: feature")
        summary_pos = result.index("Change Summary: JWT")
        assert intent_pos < summary_pos

    def test_commit_with_enriched_fields(self):
        activities = [
            {
                "kind": "commit",
                "title": "fix: null pointer in parser",
                "repo": "org/repo",
                "additions": 12,
                "deletions": 3,
                "url": "https://ghes/commit/abc123",
                "files": ["src/parser.py"],
                "body": "Fixes #42",
                "intent": "bugfix",
                "change_summary": "파서에서 발생하던 NullPointer 예외를 수정",
            },
        ]
        result = SummarizerService._format_activities(activities)
        assert "Intent: bugfix" in result
        assert "Change Summary: 파서에서 발생하던 NullPointer 예외를 수정" in result
        assert "Body: Fixes #42" in result
        assert "Files: src/parser.py" in result


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
        user_content = mock_llm.chat.call_args[0][1]
        # Static instructions in system prompt (cacheable)
        assert "일일 업무 리포트" in system_prompt
        # Dynamic stats (date, counts) in user content
        assert "2025-02-16" in user_content
        assert "1" in user_content  # authored_count


class TestDailyEmptyActivities:
    def test_empty_activities_writes_marker(self, summarizer, mock_llm, test_config):
        """빈 activities → 마커 파일 작성, LLM 미호출."""
        norm_dir = test_config.date_normalized_dir(DATE)
        save_jsonl([], norm_dir / "activities.jsonl")
        save_json(
            DailyStats(date=DATE),
            norm_dir / "stats.json",
        )

        path = summarizer.daily(DATE)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "No activity on this day" in content
        assert DATE in content
        mock_llm.chat.assert_not_called()

    def test_empty_activities_updates_checkpoint(self, summarizer, test_config):
        """빈 activities도 checkpoint 정상 업데이트."""
        norm_dir = test_config.date_normalized_dir(DATE)
        save_jsonl([], norm_dir / "activities.jsonl")
        save_json(DailyStats(date=DATE), norm_dir / "stats.json")

        summarizer.daily(DATE)

        from workrecap.models import load_json as _load_json

        cp = _load_json(test_config.checkpoints_path)
        assert cp["last_summarize_date"] == DATE

    def test_empty_activities_logs_llm_skip(self, summarizer, mock_llm, test_config, caplog):
        """빈 activities → LLM skip 사유 로깅."""
        norm_dir = test_config.date_normalized_dir(DATE)
        save_jsonl([], norm_dir / "activities.jsonl")
        save_json(DailyStats(date=DATE), norm_dir / "stats.json")

        with caplog.at_level(logging.INFO, logger="workrecap.services.summarizer"):
            summarizer.daily(DATE)

        assert any("skipping LLM" in r.message for r in caplog.records)

    def test_empty_in_daily_range_is_success(self, summarizer, mock_llm, test_config):
        """daily_range에서 빈 날짜는 'success' 상태."""
        norm_dir = test_config.date_normalized_dir(DATE)
        save_jsonl([], norm_dir / "activities.jsonl")
        save_json(DailyStats(date=DATE), norm_dir / "stats.json")

        results = summarizer.daily_range(DATE, DATE, force=True)
        assert len(results) == 1
        assert results[0]["status"] == "success"
        mock_llm.chat.assert_not_called()


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

    def test_skip_if_exists(self, summarizer, mock_llm, test_config):
        """이미 존재하면 LLM 호출 없이 skip."""
        _save_daily_summary(test_config, "2025-02-10", "# Mon content")
        _save_weekly_summary(test_config, 2025, 7, "# Existing weekly")

        path = summarizer.weekly(2025, 7)
        assert path.exists()
        mock_llm.chat.assert_not_called()

    def test_force_regenerates(self, summarizer, mock_llm, test_config):
        """force=True → 기존 파일 있어도 재생성."""
        _save_daily_summary(test_config, "2025-02-10", "# Mon content")
        _save_weekly_summary(test_config, 2025, 7, "# Old weekly")

        path = summarizer.weekly(2025, 7, force=True)
        assert path.exists()
        mock_llm.chat.assert_called_once()
        assert path.read_text(encoding="utf-8") == "# LLM Generated Summary\n\nMock content."


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

    def test_skip_if_exists(self, summarizer, mock_llm, test_config):
        """이미 존재하면 LLM 호출 없이 skip."""
        _save_weekly_summary(test_config, 2025, 5)
        _save_monthly_summary(test_config, 2025, 2, "# Existing monthly")

        path = summarizer.monthly(2025, 2)
        assert path.exists()
        mock_llm.chat.assert_not_called()

    def test_force_regenerates(self, summarizer, mock_llm, test_config):
        """force=True → 기존 파일 있어도 재생성."""
        _save_weekly_summary(test_config, 2025, 5)
        _save_monthly_summary(test_config, 2025, 2, "# Old monthly")

        path = summarizer.monthly(2025, 2, force=True)
        assert path.exists()
        mock_llm.chat.assert_called_once()
        assert path.read_text(encoding="utf-8") == "# LLM Generated Summary\n\nMock content."


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

    def test_skip_if_exists(self, summarizer, mock_llm, test_config):
        """이미 존재하면 LLM 호출 없이 skip."""
        _save_monthly_summary(test_config, 2025, 1)
        # Pre-create yearly summary
        yearly_path = test_config.yearly_summary_path(2025)
        yearly_path.parent.mkdir(parents=True, exist_ok=True)
        yearly_path.write_text("# Existing yearly", encoding="utf-8")

        path = summarizer.yearly(2025)
        assert path.exists()
        mock_llm.chat.assert_not_called()

    def test_force_regenerates(self, summarizer, mock_llm, test_config):
        """force=True → 기존 파일 있어도 재생성."""
        _save_monthly_summary(test_config, 2025, 1)
        yearly_path = test_config.yearly_summary_path(2025)
        yearly_path.parent.mkdir(parents=True, exist_ok=True)
        yearly_path.write_text("# Old yearly", encoding="utf-8")

        path = summarizer.yearly(2025, force=True)
        assert path.exists()
        mock_llm.chat.assert_called_once()
        assert path.read_text(encoding="utf-8") == "# LLM Generated Summary\n\nMock content."


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


class TestCacheSystemPrompt:
    """All chat() calls should pass cache_system_prompt=True."""

    def test_daily_passes_cache_system_prompt(self, summarizer, mock_llm, test_config):
        _save_normalized(test_config)
        summarizer.daily(DATE)
        assert mock_llm.chat.call_args.kwargs.get("cache_system_prompt") is True

    def test_weekly_passes_cache_system_prompt(self, summarizer, mock_llm, test_config):
        _save_daily_summary(test_config, "2025-02-10", "# Mon content")
        summarizer.weekly(2025, 7)
        assert mock_llm.chat.call_args.kwargs.get("cache_system_prompt") is True

    def test_monthly_passes_cache_system_prompt(self, summarizer, mock_llm, test_config):
        _save_weekly_summary(test_config, 2025, 5)
        summarizer.monthly(2025, 2)
        assert mock_llm.chat.call_args.kwargs.get("cache_system_prompt") is True

    def test_yearly_passes_cache_system_prompt(self, summarizer, mock_llm, test_config):
        _save_monthly_summary(test_config, 2025, 1)
        summarizer.yearly(2025)
        assert mock_llm.chat.call_args.kwargs.get("cache_system_prompt") is True

    def test_daily_batch_passes_cache_system_prompt(self, summarizer, mock_llm, test_config):
        _save_normalized(test_config)
        summarizer._daily_range_batch([DATE], force=True, progress=None)
        batch_requests = mock_llm.submit_batch.call_args[0][0]
        assert batch_requests[0]["cache_system_prompt"] is True


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

        from workrecap.models import load_json

        cp = load_json(test_config.checkpoints_path)
        assert cp["last_summarize_date"] == DATE

    def test_updates_existing_checkpoint(self, summarizer, test_config):
        """두 번 daily() → 마지막 날짜로 갱신."""
        date2 = "2025-02-17"
        _save_normalized(test_config, DATE)
        _save_normalized(test_config, date2)
        summarizer.daily(DATE)
        summarizer.daily(date2)

        from workrecap.models import load_json

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

        from workrecap.models import load_json

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

        from workrecap.models import load_json

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


# ── DailyStateStore cascade 테스트 ──


class TestSummarizerDailyStateIntegration:
    """DailyStateStore cascade 동작 테스트."""

    def test_cascade_reprocess_when_normalize_newer(self, test_config, mock_llm, prompts_dir):
        """normalize_ts > summarize_ts 이면 daily_range가 재처리."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        mock_ds.is_summarize_stale.return_value = True  # cascade trigger
        summarizer = SummarizerService(test_config, mock_llm, daily_state=mock_ds)

        _save_normalized(test_config, DATE)

        results = summarizer.daily_range(DATE, DATE)
        assert len(results) == 1
        assert results[0]["status"] == "success"
        mock_ds.is_summarize_stale.assert_called_with(DATE)

    def test_skip_when_summarize_fresh(self, test_config, mock_llm, prompts_dir):
        """summarize_ts >= normalize_ts 이면 skip."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        mock_ds.is_summarize_stale.return_value = False  # fresh → skip
        summarizer = SummarizerService(test_config, mock_llm, daily_state=mock_ds)

        _save_normalized(test_config, DATE)

        results = summarizer.daily_range(DATE, DATE)
        assert len(results) == 1
        assert results[0]["status"] == "skipped"

    def test_set_timestamp_called_after_daily(self, test_config, mock_llm, prompts_dir):
        """daily() 성공 후 daily_state.set_timestamp("summarize") 호출."""
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        summarizer = SummarizerService(test_config, mock_llm, daily_state=mock_ds)

        _save_normalized(test_config, DATE)
        summarizer.daily(DATE)

        mock_ds.set_timestamp.assert_called_once_with("summarize", DATE)


# ── _is_stale 테스트 ──


class TestIsStale:
    def test_output_not_exists(self, tmp_path):
        """output 파일이 없으면 항상 stale."""
        output = tmp_path / "out.md"
        inp = tmp_path / "inp.md"
        inp.write_text("data")
        assert SummarizerService._is_stale(output, [inp]) is True

    def test_output_newer_than_inputs(self, tmp_path):
        """output이 input보다 새로우면 not stale."""
        import os
        import time

        inp = tmp_path / "inp.md"
        inp.write_text("data")
        # Set input mtime to the past
        past = time.time() - 100
        os.utime(inp, (past, past))

        output = tmp_path / "out.md"
        output.write_text("summary")
        # output has current mtime, which is newer than inp

        assert SummarizerService._is_stale(output, [inp]) is False

    def test_input_newer_than_output(self, tmp_path):
        """input이 output보다 새로우면 stale."""
        import os
        import time

        output = tmp_path / "out.md"
        output.write_text("summary")
        # Set output mtime to the past
        past = time.time() - 100
        os.utime(output, (past, past))

        inp = tmp_path / "inp.md"
        inp.write_text("newer data")
        # inp has current mtime, which is newer than output

        assert SummarizerService._is_stale(output, [inp]) is True

    def test_no_inputs_exist(self, tmp_path):
        """input 파일이 하나도 없으면 not stale (재생성 불가)."""
        output = tmp_path / "out.md"
        output.write_text("summary")
        assert SummarizerService._is_stale(output, []) is False

    def test_mixed_inputs(self, tmp_path):
        """input 중 하나만 output보다 새로워도 stale."""
        import os
        import time

        output = tmp_path / "out.md"
        output.write_text("summary")

        old_inp = tmp_path / "old.md"
        old_inp.write_text("old")
        past = time.time() - 100
        os.utime(old_inp, (past, past))
        os.utime(output, (past + 50, past + 50))

        new_inp = tmp_path / "new.md"
        new_inp.write_text("new")
        # new_inp has current mtime, newer than output

        assert SummarizerService._is_stale(output, [old_inp, new_inp]) is True


# ── Weekly/Monthly/Yearly cascade staleness 테스트 ──


class TestWeeklyCascadeStaleness:
    def test_regenerate_when_daily_newer(self, summarizer, mock_llm, test_config):
        """daily mtime > weekly mtime → LLM 호출하여 재생성."""
        import os
        import time

        # Create daily summaries first
        _save_daily_summary(test_config, "2025-02-10", "# Mon")
        _save_daily_summary(test_config, "2025-02-14", "# Fri")

        # Create weekly summary with old mtime
        _save_weekly_summary(test_config, 2025, 7, "# Old weekly")
        weekly_path = test_config.weekly_summary_path(2025, 7)
        past = time.time() - 200
        os.utime(weekly_path, (past, past))

        # Touch daily to be newer than weekly
        daily_path = test_config.daily_summary_path("2025-02-10")
        now = time.time()
        os.utime(daily_path, (now, now))

        path = summarizer.weekly(2025, 7)
        assert path.exists()
        mock_llm.chat.assert_called_once()
        assert path.read_text(encoding="utf-8") == "# LLM Generated Summary\n\nMock content."


class TestMonthlyCascadeStaleness:
    def test_regenerate_when_weekly_newer(self, summarizer, mock_llm, test_config):
        """weekly mtime > monthly mtime → LLM 호출하여 재생성."""
        import os
        import time

        # Create weekly summaries
        _save_weekly_summary(test_config, 2025, 5)
        _save_weekly_summary(test_config, 2025, 6)

        # Create monthly summary with old mtime
        _save_monthly_summary(test_config, 2025, 2, "# Old monthly")
        monthly_path = test_config.monthly_summary_path(2025, 2)
        past = time.time() - 200
        os.utime(monthly_path, (past, past))

        # Touch weekly to be newer
        weekly_path = test_config.weekly_summary_path(2025, 5)
        now = time.time()
        os.utime(weekly_path, (now, now))

        path = summarizer.monthly(2025, 2)
        assert path.exists()
        mock_llm.chat.assert_called_once()
        assert path.read_text(encoding="utf-8") == "# LLM Generated Summary\n\nMock content."


class TestYearlyCascadeStaleness:
    def test_regenerate_when_monthly_newer(self, summarizer, mock_llm, test_config):
        """monthly mtime > yearly mtime → LLM 호출하여 재생성."""
        import os
        import time

        # Create monthly summaries
        _save_monthly_summary(test_config, 2025, 1)
        _save_monthly_summary(test_config, 2025, 2)

        # Create yearly summary with old mtime
        yearly_path = test_config.yearly_summary_path(2025)
        yearly_path.parent.mkdir(parents=True, exist_ok=True)
        yearly_path.write_text("# Old yearly", encoding="utf-8")
        past = time.time() - 200
        os.utime(yearly_path, (past, past))

        # Touch monthly to be newer
        monthly_path = test_config.monthly_summary_path(2025, 1)
        now = time.time()
        os.utime(monthly_path, (now, now))

        path = summarizer.yearly(2025)
        assert path.exists()
        mock_llm.chat.assert_called_once()
        assert path.read_text(encoding="utf-8") == "# LLM Generated Summary\n\nMock content."


class TestCascadeStaleness:
    def test_daily_change_cascades_through_all_levels(self, summarizer, mock_llm, test_config):
        """daily 재생성 → weekly stale → monthly stale → yearly stale 순차 확인."""
        import os
        import time

        base_time = time.time() - 500

        # 1. Create daily summaries (old)
        _save_daily_summary(test_config, "2025-02-10", "# Mon")
        _save_daily_summary(test_config, "2025-02-14", "# Fri")
        for d in ["2025-02-10", "2025-02-14"]:
            os.utime(test_config.daily_summary_path(d), (base_time, base_time))

        # 2. Create weekly summary (slightly newer than daily)
        _save_weekly_summary(test_config, 2025, 7, "# Weekly")
        os.utime(
            test_config.weekly_summary_path(2025, 7),
            (base_time + 100, base_time + 100),
        )

        # 3. Create monthly summary (slightly newer than weekly)
        _save_monthly_summary(test_config, 2025, 2, "# Monthly")
        os.utime(
            test_config.monthly_summary_path(2025, 2),
            (base_time + 200, base_time + 200),
        )

        # 4. Create yearly summary (slightly newer than monthly)
        yearly_path = test_config.yearly_summary_path(2025)
        yearly_path.parent.mkdir(parents=True, exist_ok=True)
        yearly_path.write_text("# Yearly", encoding="utf-8")
        os.utime(yearly_path, (base_time + 300, base_time + 300))

        # All fresh — no regeneration needed
        assert not SummarizerService._is_stale(
            test_config.weekly_summary_path(2025, 7),
            [test_config.daily_summary_path("2025-02-10")],
        )

        # 5. Simulate daily re-generation (touch daily to be newest)
        future = time.time()
        daily_path = test_config.daily_summary_path("2025-02-10")
        os.utime(daily_path, (future, future))

        # Weekly is now stale (daily newer)
        assert SummarizerService._is_stale(
            test_config.weekly_summary_path(2025, 7),
            [test_config.daily_summary_path("2025-02-10")],
        )

        # 6. Regenerate weekly → weekly mtime updates
        summarizer.weekly(2025, 7)
        assert mock_llm.chat.call_count == 1

        # Monthly is now stale (weekly just regenerated, newer than monthly)
        assert SummarizerService._is_stale(
            test_config.monthly_summary_path(2025, 2),
            [test_config.weekly_summary_path(2025, 7)],
        )

        # 7. Regenerate monthly → monthly mtime updates
        summarizer.monthly(2025, 2)
        assert mock_llm.chat.call_count == 2

        # Yearly is now stale (monthly just regenerated)
        assert SummarizerService._is_stale(
            yearly_path,
            [test_config.monthly_summary_path(2025, 2)],
        )

        # 8. Regenerate yearly
        summarizer.yearly(2025)
        assert mock_llm.chat.call_count == 3


# ── Parallel daily_range 테스트 ──


class TestParallelDailyRange:
    def test_parallel_basic_range(self, test_config, mock_llm, prompts_dir):
        """parallel daily_range produces same results as sequential."""
        dates = ["2025-02-14", "2025-02-15", "2025-02-16"]
        for d in dates:
            _save_normalized(test_config, d)
        summarizer = SummarizerService(test_config, mock_llm)
        results = summarizer.daily_range("2025-02-14", "2025-02-16", max_workers=3)
        assert len(results) == 3
        assert all(r["status"] == "success" for r in results)

    def test_parallel_failure_resilience(self, test_config, mock_llm, prompts_dir):
        """parallel: middle date fails, others succeed."""
        _save_normalized(test_config, "2025-02-14")
        # 2025-02-15: no normalized data
        _save_normalized(test_config, "2025-02-16")

        summarizer = SummarizerService(test_config, mock_llm)
        results = summarizer.daily_range("2025-02-14", "2025-02-16", max_workers=3)
        statuses = {r["date"]: r["status"] for r in results}
        assert statuses["2025-02-14"] == "success"
        assert statuses["2025-02-15"] == "failed"
        assert statuses["2025-02-16"] == "success"

    def test_parallel_checkpoint_is_max_date(self, test_config, mock_llm, prompts_dir):
        """parallel: checkpoint should be max successful date."""
        dates = ["2025-02-14", "2025-02-15", "2025-02-16"]
        for d in dates:
            _save_normalized(test_config, d)
        summarizer = SummarizerService(test_config, mock_llm)
        summarizer.daily_range("2025-02-14", "2025-02-16", max_workers=3)

        from workrecap.models import load_json

        cp = load_json(test_config.checkpoints_path)
        assert cp["last_summarize_date"] == "2025-02-16"

    def test_sequential_when_max_workers_1(self, test_config, mock_llm, prompts_dir):
        """max_workers=1 uses sequential execution."""
        dates = ["2025-02-14", "2025-02-15"]
        for d in dates:
            _save_normalized(test_config, d)
        summarizer = SummarizerService(test_config, mock_llm)
        results = summarizer.daily_range("2025-02-14", "2025-02-15", max_workers=1)
        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)

    def test_parallel_results_in_date_order(self, test_config, mock_llm, prompts_dir):
        """parallel: results returned in date order."""
        dates = ["2025-02-14", "2025-02-15", "2025-02-16"]
        for d in dates:
            _save_normalized(test_config, d)
        summarizer = SummarizerService(test_config, mock_llm)
        results = summarizer.daily_range("2025-02-14", "2025-02-16", max_workers=3)
        result_dates = [r["date"] for r in results]
        assert result_dates == dates
