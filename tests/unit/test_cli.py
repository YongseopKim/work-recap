import logging
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest
from typer.testing import CliRunner

from workrecap.cli.main import app
from workrecap.exceptions import FetchError, NormalizeError, SummarizeError, StepFailedError
from workrecap.logging_config import reset_logging
from workrecap.models import TokenUsage

runner = CliRunner()


# ── Mock 헬퍼 ──


def _mock_config():
    from workrecap.config import AppConfig

    return AppConfig(
        ghes_url="https://github.example.com",
        ghes_token="test-token",
        username="testuser",
        llm_api_key="test-key",
        data_dir=Path("/tmp/test-data"),
        prompts_dir=Path("/tmp/test-prompts"),
    )


def _fetch_result(**overrides):
    """기본 fetch 반환값 dict."""
    base = {
        "prs": Path("/data/raw/prs.json"),
        "commits": Path("/data/raw/commits.json"),
        "issues": Path("/data/raw/issues.json"),
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Reset logging config between tests to avoid idempotent guard interference."""
    reset_logging()
    yield
    reset_logging()


@pytest.fixture(autouse=True)
def patch_config(monkeypatch):
    """모든 CLI 테스트에서 _get_config를 mock."""
    monkeypatch.setattr("workrecap.cli.main._get_config", _mock_config)


@pytest.fixture(autouse=True)
def patch_ghes(monkeypatch):
    """GHESClient mock — context manager 지원."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("workrecap.cli.main._get_ghes_client", lambda c: mock_client)
    return mock_client


@pytest.fixture(autouse=True)
def patch_llm(monkeypatch):
    """LLMRouter mock."""
    mock_llm = MagicMock()
    mock_llm.usage = TokenUsage()
    mock_llm.usage_tracker = None  # fallback to .usage path in _print_usage_report
    monkeypatch.setattr("workrecap.cli.main._get_llm_router", lambda c: mock_llm)
    return mock_llm


# ── Fetch 기본 ──


class TestFetch:
    @patch("workrecap.cli.main.FetcherService")
    def test_fetch_with_date(self, mock_cls):
        mock_cls.return_value.fetch.return_value = _fetch_result()
        result = runner.invoke(app, ["fetch", "2025-02-16"])
        assert result.exit_code == 0
        assert "Fetched" in result.output
        mock_cls.return_value.fetch.assert_called_once_with("2025-02-16", types=None)

    @patch("workrecap.cli.main.FetcherService")
    def test_fetch_default_today(self, mock_cls):
        mock_cls.return_value.fetch.return_value = _fetch_result()
        result = runner.invoke(app, ["fetch"])
        assert result.exit_code == 0
        call_args = mock_cls.return_value.fetch.call_args
        assert len(call_args[0][0]) == 10  # YYYY-MM-DD

    @patch("workrecap.cli.main.FetcherService")
    def test_fetch_error(self, mock_cls):
        mock_cls.return_value.fetch.side_effect = FetchError("GHES down")
        result = runner.invoke(app, ["fetch", "2025-02-16"])
        assert result.exit_code == 1
        assert "Error" in result.output


# ── Fetch 타입 필터 ──


class TestFetchTypeFilter:
    @patch("workrecap.cli.main.FetcherService")
    def test_type_prs(self, mock_cls):
        mock_cls.return_value.fetch.return_value = {"prs": Path("/data/prs.json")}
        result = runner.invoke(app, ["fetch", "--type", "prs", "2025-02-16"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch.assert_called_once_with("2025-02-16", types={"prs"})

    @patch("workrecap.cli.main.FetcherService")
    def test_type_commits(self, mock_cls):
        mock_cls.return_value.fetch.return_value = {"commits": Path("/data/commits.json")}
        result = runner.invoke(app, ["fetch", "--type", "commits", "2025-02-16"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch.assert_called_once_with("2025-02-16", types={"commits"})

    @patch("workrecap.cli.main.FetcherService")
    def test_type_issues(self, mock_cls):
        mock_cls.return_value.fetch.return_value = {"issues": Path("/data/issues.json")}
        result = runner.invoke(app, ["fetch", "--type", "issues", "2025-02-16"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch.assert_called_once_with("2025-02-16", types={"issues"})

    def test_type_invalid(self):
        result = runner.invoke(app, ["fetch", "--type", "invalid", "2025-02-16"])
        assert result.exit_code == 1
        assert "Invalid type" in result.output


# ── Fetch 날짜 범위 ──


class TestFetchDateRange:
    @patch("workrecap.cli.main.FetcherService")
    def test_since_until(self, mock_cls):
        mock_cls.return_value.fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        result = runner.invoke(
            app,
            [
                "fetch",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-16",
            ],
        )
        assert result.exit_code == 0
        mock_cls.return_value.fetch_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-16",
            types=None,
            force=False,
            progress=ANY,
        )
        assert "3 day(s)" in result.output
        assert "3 succeeded" in result.output

    def test_since_without_until(self):
        result = runner.invoke(app, ["fetch", "--since", "2025-02-14"])
        assert result.exit_code == 1
        assert "--since" in result.output and "--until" in result.output

    def test_until_without_since(self):
        result = runner.invoke(app, ["fetch", "--until", "2025-02-16"])
        assert result.exit_code == 1
        assert "--since" in result.output and "--until" in result.output


# ── Fetch Weekly ──


class TestFetchWeekly:
    @patch("workrecap.cli.main.FetcherService")
    def test_weekly_option(self, mock_cls):
        mock_cls.return_value.fetch_range.return_value = [
            {"date": f"2026-02-{9 + i:02d}", "status": "success"} for i in range(7)
        ]
        result = runner.invoke(app, ["fetch", "--weekly", "2026-7"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch_range.assert_called_once()
        assert "7 day(s)" in result.output


# ── Fetch Monthly ──


class TestFetchMonthly:
    @patch("workrecap.cli.main.FetcherService")
    def test_monthly_option(self, mock_cls):
        mock_cls.return_value.fetch_range.return_value = [
            {"date": f"2026-02-{i + 1:02d}", "status": "success"} for i in range(28)
        ]
        result = runner.invoke(app, ["fetch", "--monthly", "2026-2"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch_range.assert_called_once()
        assert "28 day(s)" in result.output


# ── Fetch Yearly ──


class TestFetchYearly:
    @patch("workrecap.cli.main.FetcherService")
    def test_yearly_option(self, mock_cls):
        mock_cls.return_value.fetch_range.return_value = [
            {"date": f"2026-01-{i + 1:02d}", "status": "success"} for i in range(365)
        ]
        result = runner.invoke(app, ["fetch", "--yearly", "2026"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch_range.assert_called_once()
        assert "365 day(s)" in result.output


# ── Fetch Catch-up ──


class TestFetchCatchUp:
    @patch("workrecap.cli.main.FetcherService")
    def test_no_args_no_checkpoint(self, mock_cls):
        """인자 없고 checkpoint 없으면 오늘만 fetch."""
        mock_cls.return_value.fetch.return_value = _fetch_result()
        result = runner.invoke(app, ["fetch"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch.assert_called_once()

    @patch("workrecap.cli.main.date_utils")
    @patch("workrecap.cli.main._read_last_fetch_date")
    @patch("workrecap.cli.main.FetcherService")
    def test_no_args_with_checkpoint(self, mock_cls, mock_read, mock_du):
        """인자 없고 checkpoint 있으면 catch-up → fetch_range 호출."""
        mock_read.return_value = "2026-02-14"
        mock_du.catchup_range.return_value = ("2026-02-15", "2026-02-17")
        mock_du.date_range.return_value = ["2026-02-15", "2026-02-16", "2026-02-17"]
        mock_cls.return_value.fetch_range.return_value = [
            {"date": "2026-02-15", "status": "success"},
            {"date": "2026-02-16", "status": "success"},
            {"date": "2026-02-17", "status": "success"},
        ]
        result = runner.invoke(app, ["fetch"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch_range.assert_called_once_with(
            "2026-02-15",
            "2026-02-17",
            types=None,
            force=False,
            progress=ANY,
        )

    @patch("workrecap.cli.main.date_utils")
    @patch("workrecap.cli.main._read_last_fetch_date")
    @patch("workrecap.cli.main.FetcherService")
    def test_type_with_catchup(self, mock_cls, mock_read, mock_du):
        """--type + catch-up 결합 → fetch_range에 types 전달."""
        mock_read.return_value = "2026-02-15"
        mock_du.catchup_range.return_value = ("2026-02-16", "2026-02-17")
        mock_du.date_range.return_value = ["2026-02-16", "2026-02-17"]
        mock_cls.return_value.fetch_range.return_value = [
            {"date": "2026-02-16", "status": "success"},
            {"date": "2026-02-17", "status": "success"},
        ]
        result = runner.invoke(app, ["fetch", "--type", "issues"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch_range.assert_called_once_with(
            "2026-02-16",
            "2026-02-17",
            types={"issues"},
            force=False,
            progress=ANY,
        )


# ── Fetch 상호 배타 ──


class TestFetchMutualExclusion:
    def test_target_date_with_since_until(self):
        result = runner.invoke(
            app,
            [
                "fetch",
                "2025-02-16",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-16",
            ],
        )
        assert result.exit_code == 1

    def test_weekly_with_monthly(self):
        result = runner.invoke(
            app,
            [
                "fetch",
                "--weekly",
                "2026-7",
                "--monthly",
                "2026-2",
            ],
        )
        assert result.exit_code == 1


# ── Fetch 출력 ──


class TestFetchOutput:
    @patch("workrecap.cli.main.FetcherService")
    def test_output_shows_all_types(self, mock_cls):
        mock_cls.return_value.fetch.return_value = _fetch_result()
        result = runner.invoke(app, ["fetch", "2025-02-16"])
        assert result.exit_code == 0
        assert "prs" in result.output
        assert "commits" in result.output
        assert "issues" in result.output

    @patch("workrecap.cli.main.FetcherService")
    def test_output_shows_date_count(self, mock_cls):
        mock_cls.return_value.fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        result = runner.invoke(
            app,
            [
                "fetch",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-16",
            ],
        )
        assert result.exit_code == 0
        assert "3 day(s)" in result.output

    @patch("workrecap.cli.main.FetcherService")
    def test_output_shows_skipped_count(self, mock_cls):
        mock_cls.return_value.fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "skipped"},
            {"date": "2025-02-16", "status": "success"},
        ]
        result = runner.invoke(
            app,
            [
                "fetch",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-16",
            ],
        )
        assert result.exit_code == 0
        assert "2 succeeded" in result.output
        assert "1 skipped" in result.output

    @patch("workrecap.cli.main.FetcherService")
    def test_output_failed_exits_1(self, mock_cls):
        mock_cls.return_value.fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "failed", "error": "timeout"},
        ]
        result = runner.invoke(
            app,
            [
                "fetch",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-15",
            ],
        )
        assert result.exit_code == 1
        assert "1 failed" in result.output


# ── Fetch --force ──


class TestFetchForce:
    @patch("workrecap.cli.main.FetcherService")
    def test_force_flag_passed_to_fetch_range(self, mock_cls):
        mock_cls.return_value.fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
        ]
        result = runner.invoke(
            app,
            [
                "fetch",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-15",
                "--force",
            ],
        )
        assert result.exit_code == 0
        mock_cls.return_value.fetch_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-15",
            types=None,
            force=True,
            progress=ANY,
        )

    @patch("workrecap.cli.main.FetcherService")
    def test_force_short_flag(self, mock_cls):
        mock_cls.return_value.fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        result = runner.invoke(
            app,
            [
                "fetch",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-14",
                "-f",
            ],
        )
        assert result.exit_code == 0


# ── Normalize ──


class TestNormalize:
    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_with_date(self, mock_cls):
        mock_cls.return_value.normalize.return_value = (
            Path("/data/activities.jsonl"),
            Path("/data/stats.json"),
        )
        result = runner.invoke(app, ["normalize", "2025-02-16"])
        assert result.exit_code == 0
        assert "Normalized" in result.output
        mock_cls.return_value.normalize.assert_called_once_with("2025-02-16")

    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_error(self, mock_cls):
        mock_cls.return_value.normalize.side_effect = NormalizeError("no raw file")
        result = runner.invoke(app, ["normalize", "2025-02-16"])
        assert result.exit_code == 1


# ── Normalize 날짜 범위 ──


class TestNormalizeDateRange:
    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_since_until(self, mock_cls):
        mock_cls.return_value.normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        result = runner.invoke(
            app,
            ["normalize", "--since", "2025-02-14", "--until", "2025-02-16"],
        )
        assert result.exit_code == 0
        mock_cls.return_value.normalize_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-16",
            force=False,
            progress=ANY,
            max_workers=5,
        )
        assert "3 day(s)" in result.output

    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_weekly(self, mock_cls):
        mock_cls.return_value.normalize_range.return_value = [
            {"date": f"2026-02-{9 + i:02d}", "status": "success"} for i in range(7)
        ]
        result = runner.invoke(app, ["normalize", "--weekly", "2026-7"])
        assert result.exit_code == 0
        mock_cls.return_value.normalize_range.assert_called_once()

    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_monthly(self, mock_cls):
        mock_cls.return_value.normalize_range.return_value = [
            {"date": f"2026-02-{i + 1:02d}", "status": "success"} for i in range(28)
        ]
        result = runner.invoke(app, ["normalize", "--monthly", "2026-2"])
        assert result.exit_code == 0
        mock_cls.return_value.normalize_range.assert_called_once()

    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_yearly(self, mock_cls):
        mock_cls.return_value.normalize_range.return_value = [
            {"date": f"2026-01-{i + 1:02d}", "status": "success"} for i in range(365)
        ]
        result = runner.invoke(app, ["normalize", "--yearly", "2026"])
        assert result.exit_code == 0
        mock_cls.return_value.normalize_range.assert_called_once()

    def test_normalize_since_without_until(self):
        result = runner.invoke(app, ["normalize", "--since", "2025-02-14"])
        assert result.exit_code == 1
        assert "--since" in result.output and "--until" in result.output

    def test_normalize_mutual_exclusion(self):
        result = runner.invoke(
            app,
            [
                "normalize",
                "2025-02-16",
                "--weekly",
                "2026-7",
            ],
        )
        assert result.exit_code == 1

    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_output_shows_date_count(self, mock_cls):
        mock_cls.return_value.normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        result = runner.invoke(
            app,
            ["normalize", "--since", "2025-02-14", "--until", "2025-02-16"],
        )
        assert result.exit_code == 0
        assert "3 day(s)" in result.output
        assert "2025-02-14" in result.output
        assert "2025-02-15" in result.output
        assert "2025-02-16" in result.output


# ── Summarize Daily 날짜 범위 ──


class TestSummarizeDailyDateRange:
    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_daily_since_until(self, mock_cls):
        mock_cls.return_value.daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        result = runner.invoke(
            app,
            ["summarize", "daily", "--since", "2025-02-14", "--until", "2025-02-16"],
        )
        assert result.exit_code == 0
        mock_cls.return_value.daily_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-16",
            force=False,
            progress=ANY,
            max_workers=5,
        )
        assert "3 day(s)" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_daily_weekly(self, mock_cls):
        mock_cls.return_value.daily_range.return_value = [
            {"date": f"2026-02-{9 + i:02d}", "status": "success"} for i in range(7)
        ]
        result = runner.invoke(app, ["summarize", "daily", "--weekly", "2026-7"])
        assert result.exit_code == 0
        mock_cls.return_value.daily_range.assert_called_once()

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_daily_monthly(self, mock_cls):
        mock_cls.return_value.daily_range.return_value = [
            {"date": f"2026-02-{i + 1:02d}", "status": "success"} for i in range(28)
        ]
        result = runner.invoke(app, ["summarize", "daily", "--monthly", "2026-2"])
        assert result.exit_code == 0
        mock_cls.return_value.daily_range.assert_called_once()

    def test_summarize_daily_mutual_exclusion(self):
        result = runner.invoke(
            app,
            [
                "summarize",
                "daily",
                "2025-02-16",
                "--weekly",
                "2026-7",
            ],
        )
        assert result.exit_code == 1


class TestSummarize:
    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_daily(self, mock_cls):
        mock_cls.return_value.daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["summarize", "daily", "2025-02-16"])
        assert result.exit_code == 0
        assert "Daily summary" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_weekly(self, mock_cls):
        mock_cls.return_value.weekly.return_value = Path("/data/weekly.md")
        result = runner.invoke(app, ["summarize", "weekly", "2025", "7"])
        assert result.exit_code == 0
        assert "Weekly summary" in result.output
        mock_cls.return_value.weekly.assert_called_once_with(2025, 7, force=False)

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_monthly(self, mock_cls):
        mock_cls.return_value.monthly.return_value = Path("/data/monthly.md")
        result = runner.invoke(app, ["summarize", "monthly", "2025", "2"])
        assert result.exit_code == 0
        assert "Monthly summary" in result.output
        mock_cls.return_value.monthly.assert_called_once_with(2025, 2, force=False)

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_yearly(self, mock_cls):
        mock_cls.return_value.yearly.return_value = Path("/data/yearly.md")
        result = runner.invoke(app, ["summarize", "yearly", "2025"])
        assert result.exit_code == 0
        assert "Yearly summary" in result.output
        mock_cls.return_value.yearly.assert_called_once_with(2025, force=False)

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_error(self, mock_cls):
        mock_cls.return_value.daily.side_effect = SummarizeError("LLM error")
        result = runner.invoke(app, ["summarize", "daily", "2025-02-16"])
        assert result.exit_code == 1


class TestRun:
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_single_date(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["run", "2025-02-16"])
        assert result.exit_code == 0
        assert "Pipeline complete" in result.output
        mock_orch.return_value.run_daily.assert_called_once_with("2025-02-16", types=None)

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_range(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-15", "status": "success", "path": "/p1"},
            {"date": "2025-02-16", "status": "success", "path": "/p2"},
        ]
        result = runner.invoke(
            app,
            [
                "run",
                "--since",
                "2025-02-15",
                "--until",
                "2025-02-16",
            ],
        )
        assert result.exit_code == 0
        assert "2 succeeded" in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_range_partial_failure(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-15", "status": "success", "path": "/p1"},
            {"date": "2025-02-16", "status": "failed", "error": "fetch failed"},
        ]
        result = runner.invoke(
            app,
            [
                "run",
                "--since",
                "2025-02-15",
                "--until",
                "2025-02-16",
            ],
        )
        assert result.exit_code == 1
        assert "1 succeeded" in result.output
        assert "1 failed" in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_error(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        mock_orch.return_value.run_daily.side_effect = StepFailedError(
            "fetch", FetchError("timeout")
        )
        result = runner.invoke(app, ["run", "2025-02-16"])
        assert result.exit_code == 1
        assert "Error" in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_no_args_default_today(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """인자 없고 checkpoint 없으면 오늘 날짜로 run_daily 호출."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 0
        assert "Pipeline complete" in result.output
        mock_orch.return_value.run_daily.assert_called_once()

    @patch("workrecap.cli.main.date_utils")
    @patch("workrecap.cli.main._read_last_summarize_date")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_no_args_with_checkpoint(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_read, mock_du
    ):
        """인자 없고 checkpoint 있으면 catch-up → run_range 호출."""
        mock_read.return_value = "2026-02-14"
        mock_du.catchup_range.return_value = ("2026-02-15", "2026-02-17")
        mock_du.date_range.return_value = ["2026-02-15", "2026-02-16", "2026-02-17"]
        mock_orch.return_value.run_range.return_value = [
            {"date": "2026-02-15", "status": "success", "path": "/p1"},
            {"date": "2026-02-16", "status": "success", "path": "/p2"},
            {"date": "2026-02-17", "status": "success", "path": "/p3"},
        ]
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 0
        assert "3 succeeded" in result.output
        mock_orch.return_value.run_range.assert_called_once_with(
            "2026-02-15",
            "2026-02-17",
            force=False,
            types=None,
            progress=ANY,
            max_workers=5,
        )

    @patch("workrecap.cli.main.date_utils")
    @patch("workrecap.cli.main._read_last_summarize_date")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_already_up_to_date(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_read, mock_du
    ):
        """날짜 목록 비어있으면 'Already up to date.'."""
        mock_read.return_value = "2026-02-17"
        mock_du.catchup_range.return_value = ("2026-02-18", "2026-02-17")
        mock_du.date_range.return_value = []
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 0
        assert "Already up to date." in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_weekly(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        mock_orch.return_value.run_range.return_value = [
            {"date": f"2026-02-0{i}", "status": "success", "path": f"/p{i}"} for i in range(2, 9)
        ]
        result = runner.invoke(app, ["run", "--weekly", "2026-7"])
        assert result.exit_code == 0
        assert "7 succeeded" in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_monthly(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        mock_orch.return_value.run_range.return_value = [
            {"date": "2026-01-01", "status": "success", "path": "/p1"},
        ]
        result = runner.invoke(app, ["run", "--monthly", "2026-1"])
        assert result.exit_code == 0
        assert "succeeded" in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_yearly(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-01-01", "status": "success", "path": "/p1"},
        ]
        result = runner.invoke(app, ["run", "--yearly", "2025"])
        assert result.exit_code == 0
        assert "succeeded" in result.output


class TestAsk:
    @patch("workrecap.cli.main.SummarizerService")
    def test_ask_question(self, mock_cls):
        mock_cls.return_value.query.return_value = "이번 달 주요 성과는..."
        result = runner.invoke(app, ["ask", "이번 달 주요 성과?"])
        assert result.exit_code == 0
        assert "이번 달 주요 성과는" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_ask_error(self, mock_cls):
        mock_cls.return_value.query.side_effect = SummarizeError("No context")
        result = runner.invoke(app, ["ask", "질문?"])
        assert result.exit_code == 1
        assert "Error" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_ask_with_months_option(self, mock_cls):
        mock_cls.return_value.query.return_value = "답변"
        result = runner.invoke(app, ["ask", "질문?", "--months", "6"])
        assert result.exit_code == 0
        mock_cls.return_value.query.assert_called_once_with("질문?", months_back=6)


# ── Run --force 테스트 ──


class TestRunForce:
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_force_single_date(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """run --force 단일 날짜 → run_daily 호출 (force는 run_daily에 직접 전달 안 함)."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["run", "--force", "2025-02-16"])
        assert result.exit_code == 0
        assert "Pipeline complete" in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_force_with_range(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """run --force --since/--until → run_range에 force=True 전달."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-14", "status": "success", "path": "/p1"},
            {"date": "2025-02-15", "status": "success", "path": "/p2"},
        ]
        result = runner.invoke(
            app,
            [
                "run",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-15",
                "--force",
            ],
        )
        assert result.exit_code == 0
        assert "2 succeeded" in result.output
        mock_orch.return_value.run_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-15",
            force=True,
            types=None,
            progress=ANY,
            max_workers=5,
        )

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_force_short_flag_with_range(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """run -f --since/--until → run_range에 force=True 전달."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-14", "status": "success", "path": "/p1"},
        ]
        result = runner.invoke(
            app,
            [
                "run",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-14",
                "-f",
            ],
        )
        assert result.exit_code == 0
        mock_orch.return_value.run_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-14",
            force=True,
            types=None,
            progress=ANY,
            max_workers=5,
        )

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_range_with_skipped(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """skipped 날짜는 — 마크로 표시."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-14", "status": "skipped"},
            {"date": "2025-02-15", "status": "success", "path": "/p1"},
        ]
        result = runner.invoke(
            app,
            ["run", "--since", "2025-02-14", "--until", "2025-02-15"],
        )
        assert result.exit_code == 0
        assert "1 succeeded" in result.output
        assert "1 skipped" in result.output
        assert "\u2014 2025-02-14" in result.output  # — mark
        assert "\u2713 2025-02-15" in result.output  # ✓ mark


# ── Checkpoint 헬퍼 테스트 ──


class TestReadCheckpointHelpers:
    def test_read_last_normalize_date_no_file(self):
        """파일 없으면 None."""
        from workrecap.cli.main import _read_last_normalize_date

        config = _mock_config()
        assert _read_last_normalize_date(config) is None

    def test_read_last_normalize_date_with_key(self, tmp_path):
        """last_normalize_date 키가 있으면 반환."""
        import json
        from workrecap.cli.main import _read_last_normalize_date

        config = _mock_config()
        config.data_dir = tmp_path / "data"
        (config.data_dir / "state").mkdir(parents=True)
        with open(config.checkpoints_path, "w") as f:
            json.dump({"last_normalize_date": "2025-02-16"}, f)
        assert _read_last_normalize_date(config) == "2025-02-16"

    def test_read_last_normalize_date_missing_key(self, tmp_path):
        """키 없으면 None."""
        import json
        from workrecap.cli.main import _read_last_normalize_date

        config = _mock_config()
        config.data_dir = tmp_path / "data"
        (config.data_dir / "state").mkdir(parents=True)
        with open(config.checkpoints_path, "w") as f:
            json.dump({"last_fetch_date": "2025-02-16"}, f)
        assert _read_last_normalize_date(config) is None

    def test_read_last_summarize_date_no_file(self):
        """파일 없으면 None."""
        from workrecap.cli.main import _read_last_summarize_date

        config = _mock_config()
        assert _read_last_summarize_date(config) is None

    def test_read_last_summarize_date_with_key(self, tmp_path):
        """last_summarize_date 키가 있으면 반환."""
        import json
        from workrecap.cli.main import _read_last_summarize_date

        config = _mock_config()
        config.data_dir = tmp_path / "data"
        (config.data_dir / "state").mkdir(parents=True)
        with open(config.checkpoints_path, "w") as f:
            json.dump({"last_summarize_date": "2025-02-16"}, f)
        assert _read_last_summarize_date(config) == "2025-02-16"

    def test_read_last_summarize_date_missing_key(self, tmp_path):
        """키 없으면 None."""
        import json
        from workrecap.cli.main import _read_last_summarize_date

        config = _mock_config()
        config.data_dir = tmp_path / "data"
        (config.data_dir / "state").mkdir(parents=True)
        with open(config.checkpoints_path, "w") as f:
            json.dump({"last_fetch_date": "2025-02-16"}, f)
        assert _read_last_summarize_date(config) is None


# ── Normalize Catch-Up 테스트 ──


class TestNormalizeCatchUp:
    @patch("workrecap.cli.main.NormalizerService")
    def test_no_args_no_checkpoint(self, mock_cls):
        """인자 없고 checkpoint 없으면 오늘만 normalize."""
        mock_cls.return_value.normalize.return_value = (
            Path("/data/activities.jsonl"),
            Path("/data/stats.json"),
        )
        result = runner.invoke(app, ["normalize"])
        assert result.exit_code == 0
        mock_cls.return_value.normalize.assert_called_once()

    @patch("workrecap.cli.main.date_utils")
    @patch("workrecap.cli.main._read_last_normalize_date")
    @patch("workrecap.cli.main.NormalizerService")
    def test_no_args_with_checkpoint(self, mock_cls, mock_read, mock_du):
        """인자 없고 checkpoint 있으면 catch-up → normalize_range 호출."""
        mock_read.return_value = "2026-02-14"
        mock_du.catchup_range.return_value = ("2026-02-15", "2026-02-17")
        mock_du.date_range.return_value = ["2026-02-15", "2026-02-16", "2026-02-17"]
        mock_cls.return_value.normalize_range.return_value = [
            {"date": "2026-02-15", "status": "success"},
            {"date": "2026-02-16", "status": "success"},
            {"date": "2026-02-17", "status": "success"},
        ]
        result = runner.invoke(app, ["normalize"])
        assert result.exit_code == 0
        mock_cls.return_value.normalize_range.assert_called_once_with(
            "2026-02-15",
            "2026-02-17",
            force=False,
            progress=ANY,
            max_workers=5,
        )

    @patch("workrecap.cli.main.date_utils")
    @patch("workrecap.cli.main._read_last_normalize_date")
    @patch("workrecap.cli.main.NormalizerService")
    def test_already_up_to_date(self, mock_cls, mock_read, mock_du):
        """날짜 목록 비어있으면 'Already up to date.'."""
        mock_read.return_value = "2026-02-17"
        mock_du.catchup_range.return_value = ("2026-02-18", "2026-02-17")
        mock_du.date_range.return_value = []
        result = runner.invoke(app, ["normalize"])
        assert result.exit_code == 0
        assert "Already up to date" in result.output


# ── Normalize Force 테스트 ──


class TestNormalizeForce:
    @patch("workrecap.cli.main.NormalizerService")
    def test_force_flag(self, mock_cls):
        """--force → force=True 전달."""
        mock_cls.return_value.normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
        ]
        result = runner.invoke(
            app,
            ["normalize", "--since", "2025-02-14", "--until", "2025-02-15", "--force"],
        )
        assert result.exit_code == 0
        mock_cls.return_value.normalize_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-15",
            force=True,
            progress=ANY,
            max_workers=5,
        )

    @patch("workrecap.cli.main.NormalizerService")
    def test_force_short_flag(self, mock_cls):
        """-f 단축 플래그."""
        mock_cls.return_value.normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        result = runner.invoke(
            app,
            ["normalize", "--since", "2025-02-14", "--until", "2025-02-14", "-f"],
        )
        assert result.exit_code == 0


# ── Normalize Range Output 테스트 ──


class TestNormalizeRangeOutput:
    @patch("workrecap.cli.main.NormalizerService")
    def test_succeeded_skipped_failed_counts(self, mock_cls):
        """succeeded/skipped/failed 카운트 출력."""
        mock_cls.return_value.normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "skipped"},
            {"date": "2025-02-16", "status": "failed", "error": "no raw"},
        ]
        result = runner.invoke(
            app,
            ["normalize", "--since", "2025-02-14", "--until", "2025-02-16"],
        )
        assert "1 succeeded" in result.output
        assert "1 skipped" in result.output
        assert "1 failed" in result.output

    @patch("workrecap.cli.main.NormalizerService")
    def test_failed_exits_1(self, mock_cls):
        """failed 시 exit code 1."""
        mock_cls.return_value.normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "failed", "error": "no raw"},
        ]
        result = runner.invoke(
            app,
            ["normalize", "--since", "2025-02-14", "--until", "2025-02-15"],
        )
        assert result.exit_code == 1


# ── Summarize Daily Catch-Up 테스트 ──


class TestSummarizeDailyCatchUp:
    @patch("workrecap.cli.main.SummarizerService")
    def test_no_args_no_checkpoint(self, mock_cls):
        """인자 없고 checkpoint 없으면 오늘만 summarize."""
        mock_cls.return_value.daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["summarize", "daily"])
        assert result.exit_code == 0
        mock_cls.return_value.daily.assert_called_once()

    @patch("workrecap.cli.main.date_utils")
    @patch("workrecap.cli.main._read_last_summarize_date")
    @patch("workrecap.cli.main.SummarizerService")
    def test_no_args_with_checkpoint(self, mock_cls, mock_read, mock_du):
        """인자 없고 checkpoint 있으면 catch-up → daily_range 호출."""
        mock_read.return_value = "2026-02-14"
        mock_du.catchup_range.return_value = ("2026-02-15", "2026-02-17")
        mock_du.date_range.return_value = ["2026-02-15", "2026-02-16", "2026-02-17"]
        mock_cls.return_value.daily_range.return_value = [
            {"date": "2026-02-15", "status": "success"},
            {"date": "2026-02-16", "status": "success"},
            {"date": "2026-02-17", "status": "success"},
        ]
        result = runner.invoke(app, ["summarize", "daily"])
        assert result.exit_code == 0
        mock_cls.return_value.daily_range.assert_called_once_with(
            "2026-02-15",
            "2026-02-17",
            force=False,
            progress=ANY,
            max_workers=5,
        )

    @patch("workrecap.cli.main.date_utils")
    @patch("workrecap.cli.main._read_last_summarize_date")
    @patch("workrecap.cli.main.SummarizerService")
    def test_already_up_to_date(self, mock_cls, mock_read, mock_du):
        """날짜 목록 비어있으면 'Already up to date.'."""
        mock_read.return_value = "2026-02-17"
        mock_du.catchup_range.return_value = ("2026-02-18", "2026-02-17")
        mock_du.date_range.return_value = []
        result = runner.invoke(app, ["summarize", "daily"])
        assert result.exit_code == 0
        assert "Already up to date" in result.output


# ── Summarize Daily Force 테스트 ──


class TestSummarizeDailyForce:
    @patch("workrecap.cli.main.SummarizerService")
    def test_force_flag(self, mock_cls):
        """--force 전달."""
        mock_cls.return_value.daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
        ]
        result = runner.invoke(
            app,
            ["summarize", "daily", "--since", "2025-02-14", "--until", "2025-02-15", "--force"],
        )
        assert result.exit_code == 0
        mock_cls.return_value.daily_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-15",
            force=True,
            progress=ANY,
            max_workers=5,
        )


# ── Summarize Daily Range Output 테스트 ──


class TestSummarizeDailyRangeOutput:
    @patch("workrecap.cli.main.SummarizerService")
    def test_succeeded_skipped_failed_counts(self, mock_cls):
        """카운트 출력."""
        mock_cls.return_value.daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "skipped"},
            {"date": "2025-02-16", "status": "failed", "error": "no data"},
        ]
        result = runner.invoke(
            app,
            ["summarize", "daily", "--since", "2025-02-14", "--until", "2025-02-16"],
        )
        assert "1 succeeded" in result.output
        assert "1 skipped" in result.output
        assert "1 failed" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_failed_exits_1(self, mock_cls):
        """failed 시 exit code 1."""
        mock_cls.return_value.daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "failed", "error": "no data"},
        ]
        result = runner.invoke(
            app,
            ["summarize", "daily", "--since", "2025-02-14", "--until", "2025-02-15"],
        )
        assert result.exit_code == 1


# ── Summarize Weekly/Monthly/Yearly --force 테스트 ──


class TestSummarizeWeeklyForce:
    @patch("workrecap.cli.main.SummarizerService")
    def test_force_flag(self, mock_cls):
        """--force → force=True 전달."""
        mock_cls.return_value.weekly.return_value = Path("/data/weekly.md")
        result = runner.invoke(app, ["summarize", "weekly", "2025", "7", "--force"])
        assert result.exit_code == 0
        mock_cls.return_value.weekly.assert_called_once_with(2025, 7, force=True)

    @patch("workrecap.cli.main.SummarizerService")
    def test_force_short_flag(self, mock_cls):
        """-f 단축 플래그."""
        mock_cls.return_value.weekly.return_value = Path("/data/weekly.md")
        result = runner.invoke(app, ["summarize", "weekly", "2025", "7", "-f"])
        assert result.exit_code == 0
        mock_cls.return_value.weekly.assert_called_once_with(2025, 7, force=True)


class TestSummarizeMonthlyForce:
    @patch("workrecap.cli.main.SummarizerService")
    def test_force_flag(self, mock_cls):
        """--force → force=True 전달."""
        mock_cls.return_value.monthly.return_value = Path("/data/monthly.md")
        result = runner.invoke(app, ["summarize", "monthly", "2025", "2", "--force"])
        assert result.exit_code == 0
        mock_cls.return_value.monthly.assert_called_once_with(2025, 2, force=True)

    @patch("workrecap.cli.main.SummarizerService")
    def test_force_short_flag(self, mock_cls):
        """-f 단축 플래그."""
        mock_cls.return_value.monthly.return_value = Path("/data/monthly.md")
        result = runner.invoke(app, ["summarize", "monthly", "2025", "2", "-f"])
        assert result.exit_code == 0
        mock_cls.return_value.monthly.assert_called_once_with(2025, 2, force=True)


class TestSummarizeYearlyForce:
    @patch("workrecap.cli.main.SummarizerService")
    def test_force_flag(self, mock_cls):
        """--force → force=True 전달."""
        mock_cls.return_value.yearly.return_value = Path("/data/yearly.md")
        result = runner.invoke(app, ["summarize", "yearly", "2025", "--force"])
        assert result.exit_code == 0
        mock_cls.return_value.yearly.assert_called_once_with(2025, force=True)

    @patch("workrecap.cli.main.SummarizerService")
    def test_force_short_flag(self, mock_cls):
        """-f 단축 플래그."""
        mock_cls.return_value.yearly.return_value = Path("/data/yearly.md")
        result = runner.invoke(app, ["summarize", "yearly", "2025", "-f"])
        assert result.exit_code == 0
        mock_cls.return_value.yearly.assert_called_once_with(2025, force=True)


# ── Run --type 테스트 ──


class TestRunTypeFilter:
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_type_prs_single_date(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """run --type prs → run_daily에 types={\"prs\"} 전달."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["run", "--type", "prs", "2025-02-16"])
        assert result.exit_code == 0
        mock_orch.return_value.run_daily.assert_called_once_with("2025-02-16", types={"prs"})

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_type_with_range(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """run --type commits --since/--until → run_range에 types 전달."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-14", "status": "success", "path": "/p1"},
            {"date": "2025-02-15", "status": "success", "path": "/p2"},
        ]
        result = runner.invoke(
            app,
            ["run", "--type", "commits", "--since", "2025-02-14", "--until", "2025-02-15"],
        )
        assert result.exit_code == 0
        mock_orch.return_value.run_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-15",
            force=False,
            types={"commits"},
            progress=ANY,
            max_workers=5,
        )

    def test_type_invalid(self):
        """잘못된 타입 → exit code 1."""
        result = runner.invoke(app, ["run", "--type", "invalid", "2025-02-16"])
        assert result.exit_code == 1
        assert "Invalid type" in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_type_with_force(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """--type + --force 결합."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-14", "status": "success", "path": "/p1"},
        ]
        result = runner.invoke(
            app,
            [
                "run",
                "--type",
                "issues",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-14",
                "--force",
            ],
        )
        assert result.exit_code == 0
        mock_orch.return_value.run_range.assert_called_once_with(
            "2025-02-14",
            "2025-02-14",
            force=True,
            types={"issues"},
            progress=ANY,
            max_workers=5,
        )


# ── Verbose Flag 테스트 ──


class TestVerboseFlag:
    @patch("workrecap.cli.main.FetcherService")
    def test_verbose_sets_debug_level(self, mock_cls):
        """--verbose sets workrecap logger to DEBUG."""
        mock_cls.return_value.fetch.return_value = _fetch_result()
        runner.invoke(app, ["-v", "fetch", "2025-02-16"])
        root = logging.getLogger("workrecap")
        assert root.level == logging.DEBUG

    @patch("workrecap.cli.main.FetcherService")
    def test_default_sets_info_level(self, mock_cls):
        """Without --verbose, workrecap logger is INFO."""
        mock_cls.return_value.fetch.return_value = _fetch_result()
        runner.invoke(app, ["fetch", "2025-02-16"])
        root = logging.getLogger("workrecap")
        assert root.level == logging.INFO


# ── Token Usage 출력 테스트 ──


class TestTokenUsageDisplay:
    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_daily_shows_usage(self, mock_cls, patch_llm):
        """summarize daily 단일 호출 후 토큰 사용량 출력."""
        mock_cls.return_value.daily.return_value = Path("/data/daily.md")
        patch_llm.usage = TokenUsage(
            prompt_tokens=1234, completion_tokens=567, total_tokens=1801, call_count=1
        )
        result = runner.invoke(app, ["summarize", "daily", "2025-02-16"])
        assert result.exit_code == 0
        assert "Token usage:" in result.output
        assert "1,234 prompt" in result.output
        assert "567 completion" in result.output
        assert "1,801 total" in result.output
        assert "1 calls" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_daily_range_shows_usage(self, mock_cls, patch_llm):
        """summarize daily range 후 토큰 사용량 출력."""
        mock_cls.return_value.daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
        ]
        patch_llm.usage = TokenUsage(
            prompt_tokens=2000, completion_tokens=800, total_tokens=2800, call_count=2
        )
        result = runner.invoke(
            app,
            ["summarize", "daily", "--since", "2025-02-14", "--until", "2025-02-15"],
        )
        assert result.exit_code == 0
        assert "Token usage:" in result.output
        assert "2 calls" in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_single_shows_usage(self, mock_fetch, mock_norm, mock_summ, mock_orch, patch_llm):
        """run 단일 날짜 후 토큰 사용량 출력."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        patch_llm.usage = TokenUsage(
            prompt_tokens=500, completion_tokens=200, total_tokens=700, call_count=1
        )
        result = runner.invoke(app, ["run", "2025-02-16"])
        assert result.exit_code == 0
        assert "Token usage:" in result.output
        assert "500 prompt" in result.output

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_range_shows_usage(self, mock_fetch, mock_norm, mock_summ, mock_orch, patch_llm):
        """run range 후 토큰 사용량 출력."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-15", "status": "success", "path": "/p1"},
            {"date": "2025-02-16", "status": "success", "path": "/p2"},
        ]
        patch_llm.usage = TokenUsage(
            prompt_tokens=4000, completion_tokens=1500, total_tokens=5500, call_count=2
        )
        result = runner.invoke(
            app,
            ["run", "--since", "2025-02-15", "--until", "2025-02-16"],
        )
        assert result.exit_code == 0
        assert "Token usage:" in result.output
        assert "5,500 total" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_ask_shows_usage(self, mock_cls, patch_llm):
        """ask 후 토큰 사용량 출력."""
        mock_cls.return_value.query.return_value = "답변입니다"
        patch_llm.usage = TokenUsage(
            prompt_tokens=3000, completion_tokens=1000, total_tokens=4000, call_count=1
        )
        result = runner.invoke(app, ["ask", "질문?"])
        assert result.exit_code == 0
        assert "Token usage:" in result.output
        assert "4,000 total" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_no_usage_when_zero_calls(self, mock_cls, patch_llm):
        """LLM 호출이 0이면 토큰 사용량 미출력."""
        mock_cls.return_value.daily.return_value = Path("/data/daily.md")
        patch_llm.usage = TokenUsage()
        result = runner.invoke(app, ["summarize", "daily", "2025-02-16"])
        assert result.exit_code == 0
        assert "Token usage:" not in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_weekly_shows_usage(self, mock_cls, patch_llm):
        """summarize weekly 후 토큰 사용량 출력."""
        mock_cls.return_value.weekly.return_value = Path("/data/weekly.md")
        patch_llm.usage = TokenUsage(
            prompt_tokens=5000, completion_tokens=2000, total_tokens=7000, call_count=1
        )
        result = runner.invoke(app, ["summarize", "weekly", "2025", "7"])
        assert result.exit_code == 0
        assert "Token usage:" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_monthly_shows_usage(self, mock_cls, patch_llm):
        """summarize monthly 후 토큰 사용량 출력."""
        mock_cls.return_value.monthly.return_value = Path("/data/monthly.md")
        patch_llm.usage = TokenUsage(
            prompt_tokens=8000, completion_tokens=3000, total_tokens=11000, call_count=1
        )
        result = runner.invoke(app, ["summarize", "monthly", "2025", "2"])
        assert result.exit_code == 0
        assert "Token usage:" in result.output

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_yearly_shows_usage(self, mock_cls, patch_llm):
        """summarize yearly 후 토큰 사용량 출력."""
        mock_cls.return_value.yearly.return_value = Path("/data/yearly.md")
        patch_llm.usage = TokenUsage(
            prompt_tokens=10000, completion_tokens=4000, total_tokens=14000, call_count=1
        )
        result = runner.invoke(app, ["summarize", "yearly", "2025"])
        assert result.exit_code == 0
        assert "Token usage:" in result.output


# ── Enrich 기본값 + run enrich 테스트 ──


class TestEnrichDefault:
    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_enrich_default_true(self, mock_cls):
        """normalize 기본값이 --enrich (True)."""
        mock_cls.return_value.normalize.return_value = (
            Path("/data/activities.jsonl"),
            Path("/data/stats.json"),
        )
        result = runner.invoke(app, ["normalize", "2025-02-16"])
        assert result.exit_code == 0
        # LLM should be passed (enrich=True by default)
        _, kwargs = mock_cls.call_args
        assert kwargs.get("llm") is not None

    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_no_enrich(self, mock_cls):
        """--no-enrich → LLM 미전달."""
        mock_cls.return_value.normalize.return_value = (
            Path("/data/activities.jsonl"),
            Path("/data/stats.json"),
        )
        result = runner.invoke(app, ["normalize", "--no-enrich", "2025-02-16"])
        assert result.exit_code == 0
        _, kwargs = mock_cls.call_args
        assert kwargs.get("llm") is None


class TestRunEnrich:
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_default_enrich_true(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """run 기본값은 --enrich (True) → normalizer에 LLM 전달."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["run", "2025-02-16"])
        assert result.exit_code == 0
        _, kwargs = mock_norm.call_args
        assert kwargs.get("llm") is not None

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_no_enrich(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """run --no-enrich → normalizer에 LLM 미전달."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["run", "--no-enrich", "2025-02-16"])
        assert result.exit_code == 0
        _, kwargs = mock_norm.call_args
        assert kwargs.get("llm") is None


# ── --workers 옵션 테스트 ──


class TestWorkersOption:
    @patch("workrecap.cli.main.FetcherService")
    def test_fetch_workers_default(self, mock_cls):
        """Default workers=1 → no pool."""
        mock_cls.return_value.fetch.return_value = {"prs": Path("/tmp/prs.json")}
        result = runner.invoke(app, ["fetch", "2025-02-16"])
        assert result.exit_code == 0
        # FetcherService should be called without max_workers > 1
        call_kwargs = mock_cls.call_args
        assert call_kwargs.kwargs.get("max_workers", 1) == 1

    @patch("workrecap.infra.client_pool.GHESClientPool")
    @patch("workrecap.cli.main.FetcherService")
    def test_fetch_workers_creates_pool(self, mock_fetcher_cls, mock_pool_cls):
        """--workers 3 creates pool and passes it to FetcherService."""
        mock_fetcher_cls.return_value.fetch_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]
        result = runner.invoke(
            app, ["fetch", "--since", "2025-02-16", "--until", "2025-02-16", "--workers", "3"]
        )
        assert result.exit_code == 0
        mock_pool_cls.assert_called_once()
        call_kwargs = mock_fetcher_cls.call_args
        assert call_kwargs.kwargs.get("max_workers") == 3
        assert call_kwargs.kwargs.get("client_pool") is not None

    @patch("workrecap.cli.main.NormalizerService")
    def test_normalize_workers(self, mock_cls):
        """normalize --workers 5 → normalize_range(max_workers=5)."""
        mock_cls.return_value.normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        result = runner.invoke(
            app, ["normalize", "--since", "2025-02-14", "--until", "2025-02-14", "--workers", "5"]
        )
        assert result.exit_code == 0
        _, kwargs = mock_cls.return_value.normalize_range.call_args
        assert kwargs.get("max_workers") == 5

    @patch("workrecap.cli.main.SummarizerService")
    def test_summarize_daily_workers(self, mock_cls):
        """summarize daily --workers 3 → daily_range(max_workers=3)."""
        mock_cls.return_value.daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        result = runner.invoke(
            app,
            [
                "summarize",
                "daily",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-14",
                "--workers",
                "3",
            ],
        )
        assert result.exit_code == 0
        _, kwargs = mock_cls.return_value.daily_range.call_args
        assert kwargs.get("max_workers") == 3

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_workers(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """run --workers 5 → run_range(max_workers=5)."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-14", "status": "success", "path": "/p1"},
        ]
        result = runner.invoke(
            app,
            [
                "run",
                "--since",
                "2025-02-14",
                "--until",
                "2025-02-14",
                "--workers",
                "5",
            ],
        )
        assert result.exit_code == 0
        _, kwargs = mock_orch.return_value.run_range.call_args
        assert kwargs.get("max_workers") == 5

    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_workers_default_from_config(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        """run --workers 미지정 → config.max_workers 사용."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-14", "status": "success", "path": "/p1"},
        ]
        result = runner.invoke(
            app,
            ["run", "--since", "2025-02-14", "--until", "2025-02-14"],
        )
        assert result.exit_code == 0
        _, kwargs = mock_orch.return_value.run_range.call_args
        # Should use config.max_workers (default=5)
        assert kwargs.get("max_workers") == 5


# ── _weeks_in_month 헬퍼 테스트 ──


class TestWeeksInMonth:
    def test_feb_2026(self):
        """2026년 2월: ISO weeks 5-9."""
        from workrecap.cli.main import _weeks_in_month

        weeks = _weeks_in_month(2026, 2)
        # Feb 2026: Sun Feb 1 (W05) → Sat Feb 28 (W09)
        assert isinstance(weeks, list)
        assert all(isinstance(w, tuple) and len(w) == 2 for w in weeks)
        iso_weeks = [w[1] for w in weeks]
        assert iso_weeks == sorted(iso_weeks), "weeks should be in order"
        # All tuples should have year=2026
        assert all(w[0] == 2026 for w in weeks)
        # Should have 5 weeks (W05-W09)
        assert len(weeks) == 5

    def test_jan_2026(self):
        """2026년 1월: starts on Thu, ISO weeks 1-5."""
        from workrecap.cli.main import _weeks_in_month

        weeks = _weeks_in_month(2026, 1)
        # Jan 1 2026 = Thursday = W01
        # Jan 31 2026 = Saturday = W05
        assert len(weeks) == 5
        assert weeks[0] == (2026, 1)
        assert weeks[-1] == (2026, 5)

    def test_dec_iso_year_boundary(self):
        """12월 말 ISO 주가 다음 해로 넘어가는 경우."""
        from workrecap.cli.main import _weeks_in_month

        # Dec 2025: Dec 29-31 are in ISO week 1 of 2026
        weeks = _weeks_in_month(2025, 12)
        # Last entry should be (2026, 1) since Dec 29 Mon = W01 of 2026
        assert weeks[-1] == (2026, 1)

    def test_returns_unique_tuples(self):
        """중복 없이 유니크한 (year, week) 튜플만 반환."""
        from workrecap.cli.main import _weeks_in_month

        weeks = _weeks_in_month(2026, 3)
        assert len(weeks) == len(set(weeks))


# ── Run --weekly/--monthly/--yearly 계층적 summarize 테스트 ──


class TestRunHierarchicalSummarize:
    @patch("workrecap.cli.main._weeks_in_month")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_weekly_calls_summarize_weekly(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_wim
    ):
        """run --weekly → daily pipeline + summarizer.weekly() called."""
        mock_orch.return_value.run_range.return_value = [
            {"date": f"2026-02-0{i}", "status": "success", "path": f"/p{i}"} for i in range(2, 9)
        ]
        mock_summ.return_value.weekly.return_value = Path("/data/weekly.md")
        result = runner.invoke(app, ["run", "--weekly", "2026-7"])
        assert result.exit_code == 0
        mock_summ.return_value.weekly.assert_called_once_with(2026, 7, force=False)
        assert "Weekly summary" in result.output

    @patch("workrecap.cli.main._weeks_in_month")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_weekly_force_passes_force(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_wim
    ):
        """run --weekly --force → summarizer.weekly(force=True)."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2026-02-02", "status": "success", "path": "/p1"}
        ]
        mock_summ.return_value.weekly.return_value = Path("/data/weekly.md")
        result = runner.invoke(app, ["run", "--weekly", "2026-7", "--force"])
        assert result.exit_code == 0
        mock_summ.return_value.weekly.assert_called_once_with(2026, 7, force=True)

    @patch("workrecap.cli.main._weeks_in_month")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_monthly_cascades_weekly_then_monthly(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_wim
    ):
        """run --monthly → weekly summaries for each week, then monthly summary."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2026-01-01", "status": "success", "path": "/p1"},
        ]
        mock_wim.return_value = [(2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5)]
        mock_summ.return_value.weekly.return_value = Path("/data/weekly.md")
        mock_summ.return_value.monthly.return_value = Path("/data/monthly.md")
        result = runner.invoke(app, ["run", "--monthly", "2026-1"])
        assert result.exit_code == 0
        assert mock_summ.return_value.weekly.call_count == 5
        mock_summ.return_value.monthly.assert_called_once_with(2026, 1, force=False)
        assert "Monthly summary" in result.output

    @patch("workrecap.cli.main._weeks_in_month")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_monthly_weekly_error_handled(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_wim
    ):
        """run --monthly: SummarizeError from weekly doesn't crash."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2026-01-01", "status": "success", "path": "/p1"},
        ]
        mock_wim.return_value = [(2026, 1), (2026, 2)]
        mock_summ.return_value.weekly.side_effect = SummarizeError("no data")
        mock_summ.return_value.monthly.return_value = Path("/data/monthly.md")
        result = runner.invoke(app, ["run", "--monthly", "2026-1"])
        assert result.exit_code == 0
        assert "Monthly summary" in result.output

    @patch("workrecap.cli.main._weeks_in_month")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_yearly_cascades_full_hierarchy(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_wim
    ):
        """run --yearly → weekly → monthly → yearly cascade."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-01-01", "status": "success", "path": "/p1"},
        ]
        # Return 2 weeks per month for simplicity
        mock_wim.return_value = [(2025, 1), (2025, 2)]
        mock_summ.return_value.weekly.return_value = Path("/data/weekly.md")
        mock_summ.return_value.monthly.return_value = Path("/data/monthly.md")
        mock_summ.return_value.yearly.return_value = Path("/data/yearly.md")
        result = runner.invoke(app, ["run", "--yearly", "2025"])
        assert result.exit_code == 0
        # 12 months × 2 weeks = 24 weekly calls
        assert mock_summ.return_value.weekly.call_count == 24
        # 12 monthly calls
        assert mock_summ.return_value.monthly.call_count == 12
        # 1 yearly call
        mock_summ.return_value.yearly.assert_called_once_with(2025, force=False)
        assert "Yearly summary" in result.output

    @patch("workrecap.cli.main._weeks_in_month")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_yearly_handles_errors_gracefully(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_wim
    ):
        """run --yearly: SummarizeError from weekly/monthly doesn't crash."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-01-01", "status": "success", "path": "/p1"},
        ]
        mock_wim.return_value = [(2025, 1)]
        mock_summ.return_value.weekly.side_effect = SummarizeError("no data")
        mock_summ.return_value.monthly.side_effect = SummarizeError("no data")
        mock_summ.return_value.yearly.return_value = Path("/data/yearly.md")
        result = runner.invoke(app, ["run", "--yearly", "2025"])
        assert result.exit_code == 0
        assert "Yearly summary" in result.output

    @patch("workrecap.cli.main._weeks_in_month")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_weekly_skips_summarize_on_failure(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_wim
    ):
        """Daily pipeline failures → skip hierarchical summarize."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2026-02-02", "status": "failed", "error": "fetch err"},
        ]
        result = runner.invoke(app, ["run", "--weekly", "2026-7"])
        assert result.exit_code == 1
        mock_summ.return_value.weekly.assert_not_called()

    @patch("workrecap.cli.main._weeks_in_month")
    @patch("workrecap.cli.main.OrchestratorService")
    @patch("workrecap.cli.main.SummarizerService")
    @patch("workrecap.cli.main.NormalizerService")
    @patch("workrecap.cli.main.FetcherService")
    def test_run_since_until_no_hierarchical(
        self, mock_fetch, mock_norm, mock_summ, mock_orch, mock_wim
    ):
        """run --since/--until (no --weekly/--monthly/--yearly) → no hierarchical summarize."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2026-02-14", "status": "success", "path": "/p1"},
        ]
        result = runner.invoke(app, ["run", "--since", "2026-02-14", "--until", "2026-02-14"])
        assert result.exit_code == 0
        mock_summ.return_value.weekly.assert_not_called()
        mock_summ.return_value.monthly.assert_not_called()
        mock_summ.return_value.yearly.assert_not_called()


# ── Models 커맨드 테스트 ──


class TestModelsCommand:
    @patch("workrecap.cli.main.discover_models")
    def test_models_shows_results(self, mock_discover, patch_llm):
        """models 커맨드가 provider별 모델 목록을 출력."""
        from workrecap.infra.providers.base import ModelInfo

        mock_discover.return_value = [
            ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai"),
            ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini", provider="openai"),
        ]
        result = runner.invoke(app, ["models"])
        assert result.exit_code == 0
        assert "[openai]" in result.output
        assert "gpt-4o" in result.output
        assert "GPT-4o Mini" in result.output

    @patch("workrecap.cli.main.discover_models")
    def test_models_no_results(self, mock_discover, patch_llm):
        """모델이 없으면 안내 메시지 출력."""
        mock_discover.return_value = []
        result = runner.invoke(app, ["models"])
        assert result.exit_code == 0
        assert "No models discovered" in result.output


# ── _echo 래퍼 테스트 ──


class TestEcho:
    """_echo() wraps typer.echo AND logs to file-only logger."""

    @patch("workrecap.cli.main.typer")
    @patch("workrecap.cli.main._file_logger")
    def test_echo_normal_msg(self, mock_logger, mock_typer):
        """일반 메시지 → typer.echo + INFO 로그."""
        from workrecap.cli.main import _echo

        _echo("hello")
        mock_typer.echo.assert_called_once_with("hello", err=False)
        mock_logger.log.assert_called_once_with(logging.INFO, "hello")

    @patch("workrecap.cli.main.typer")
    @patch("workrecap.cli.main._file_logger")
    def test_echo_err_msg(self, mock_logger, mock_typer):
        """err=True → typer.echo(err=True) + ERROR 로그."""
        from workrecap.cli.main import _echo

        _echo("bad", err=True)
        mock_typer.echo.assert_called_once_with("bad", err=True)
        mock_logger.log.assert_called_once_with(logging.ERROR, "bad")

    @patch("workrecap.cli.main.typer")
    @patch("workrecap.cli.main._file_logger")
    def test_echo_empty_skips_log(self, mock_logger, mock_typer):
        """빈 문자열 → typer.echo는 호출, 로그는 생략."""
        from workrecap.cli.main import _echo

        _echo("")
        mock_typer.echo.assert_called_once_with("", err=False)
        mock_logger.log.assert_not_called()
