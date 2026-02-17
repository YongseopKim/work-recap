from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from git_recap.cli.main import app
from git_recap.exceptions import FetchError, NormalizeError, SummarizeError, StepFailedError

runner = CliRunner()


# ── Mock 헬퍼 ──


def _mock_config():
    from git_recap.config import AppConfig

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
def patch_config(monkeypatch):
    """모든 CLI 테스트에서 _get_config를 mock."""
    monkeypatch.setattr("git_recap.cli.main._get_config", _mock_config)


@pytest.fixture(autouse=True)
def patch_ghes(monkeypatch):
    """GHESClient mock — context manager 지원."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("git_recap.cli.main._get_ghes_client", lambda c: mock_client)
    return mock_client


@pytest.fixture(autouse=True)
def patch_llm(monkeypatch):
    """LLMClient mock."""
    mock_llm = MagicMock()
    monkeypatch.setattr("git_recap.cli.main._get_llm_client", lambda c: mock_llm)
    return mock_llm


# ── Fetch 기본 ──


class TestFetch:
    @patch("git_recap.cli.main.FetcherService")
    def test_fetch_with_date(self, mock_cls):
        mock_cls.return_value.fetch.return_value = _fetch_result()
        result = runner.invoke(app, ["fetch", "2025-02-16"])
        assert result.exit_code == 0
        assert "Fetched" in result.output
        mock_cls.return_value.fetch.assert_called_once_with("2025-02-16", types=None)

    @patch("git_recap.cli.main.FetcherService")
    def test_fetch_default_today(self, mock_cls):
        mock_cls.return_value.fetch.return_value = _fetch_result()
        result = runner.invoke(app, ["fetch"])
        assert result.exit_code == 0
        call_args = mock_cls.return_value.fetch.call_args
        assert len(call_args[0][0]) == 10  # YYYY-MM-DD

    @patch("git_recap.cli.main.FetcherService")
    def test_fetch_error(self, mock_cls):
        mock_cls.return_value.fetch.side_effect = FetchError("GHES down")
        result = runner.invoke(app, ["fetch", "2025-02-16"])
        assert result.exit_code == 1
        assert "Error" in result.output


# ── Fetch 타입 필터 ──


class TestFetchTypeFilter:
    @patch("git_recap.cli.main.FetcherService")
    def test_type_prs(self, mock_cls):
        mock_cls.return_value.fetch.return_value = {"prs": Path("/data/prs.json")}
        result = runner.invoke(app, ["fetch", "--type", "prs", "2025-02-16"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch.assert_called_once_with("2025-02-16", types={"prs"})

    @patch("git_recap.cli.main.FetcherService")
    def test_type_commits(self, mock_cls):
        mock_cls.return_value.fetch.return_value = {"commits": Path("/data/commits.json")}
        result = runner.invoke(app, ["fetch", "--type", "commits", "2025-02-16"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch.assert_called_once_with("2025-02-16", types={"commits"})

    @patch("git_recap.cli.main.FetcherService")
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
    @patch("git_recap.cli.main.FetcherService")
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
    @patch("git_recap.cli.main.FetcherService")
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
    @patch("git_recap.cli.main.FetcherService")
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
    @patch("git_recap.cli.main.FetcherService")
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
    @patch("git_recap.cli.main.FetcherService")
    def test_no_args_no_checkpoint(self, mock_cls):
        """인자 없고 checkpoint 없으면 오늘만 fetch."""
        mock_cls.return_value.fetch.return_value = _fetch_result()
        result = runner.invoke(app, ["fetch"])
        assert result.exit_code == 0
        mock_cls.return_value.fetch.assert_called_once()

    @patch("git_recap.cli.main.date_utils")
    @patch("git_recap.cli.main._read_last_fetch_date")
    @patch("git_recap.cli.main.FetcherService")
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
        )

    @patch("git_recap.cli.main.date_utils")
    @patch("git_recap.cli.main._read_last_fetch_date")
    @patch("git_recap.cli.main.FetcherService")
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
    @patch("git_recap.cli.main.FetcherService")
    def test_output_shows_all_types(self, mock_cls):
        mock_cls.return_value.fetch.return_value = _fetch_result()
        result = runner.invoke(app, ["fetch", "2025-02-16"])
        assert result.exit_code == 0
        assert "prs" in result.output
        assert "commits" in result.output
        assert "issues" in result.output

    @patch("git_recap.cli.main.FetcherService")
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

    @patch("git_recap.cli.main.FetcherService")
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

    @patch("git_recap.cli.main.FetcherService")
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
    @patch("git_recap.cli.main.FetcherService")
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
        )

    @patch("git_recap.cli.main.FetcherService")
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
    @patch("git_recap.cli.main.NormalizerService")
    def test_normalize_with_date(self, mock_cls):
        mock_cls.return_value.normalize.return_value = (
            Path("/data/activities.jsonl"),
            Path("/data/stats.json"),
        )
        result = runner.invoke(app, ["normalize", "2025-02-16"])
        assert result.exit_code == 0
        assert "Normalized" in result.output
        mock_cls.return_value.normalize.assert_called_once_with("2025-02-16")

    @patch("git_recap.cli.main.NormalizerService")
    def test_normalize_error(self, mock_cls):
        mock_cls.return_value.normalize.side_effect = NormalizeError("no raw file")
        result = runner.invoke(app, ["normalize", "2025-02-16"])
        assert result.exit_code == 1


# ── Normalize 날짜 범위 ──


class TestNormalizeDateRange:
    @patch("git_recap.cli.main.NormalizerService")
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
        )
        assert "3 day(s)" in result.output

    @patch("git_recap.cli.main.NormalizerService")
    def test_normalize_weekly(self, mock_cls):
        mock_cls.return_value.normalize_range.return_value = [
            {"date": f"2026-02-{9 + i:02d}", "status": "success"} for i in range(7)
        ]
        result = runner.invoke(app, ["normalize", "--weekly", "2026-7"])
        assert result.exit_code == 0
        mock_cls.return_value.normalize_range.assert_called_once()

    @patch("git_recap.cli.main.NormalizerService")
    def test_normalize_monthly(self, mock_cls):
        mock_cls.return_value.normalize_range.return_value = [
            {"date": f"2026-02-{i + 1:02d}", "status": "success"} for i in range(28)
        ]
        result = runner.invoke(app, ["normalize", "--monthly", "2026-2"])
        assert result.exit_code == 0
        mock_cls.return_value.normalize_range.assert_called_once()

    @patch("git_recap.cli.main.NormalizerService")
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

    @patch("git_recap.cli.main.NormalizerService")
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
    @patch("git_recap.cli.main.SummarizerService")
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
        )
        assert "3 day(s)" in result.output

    @patch("git_recap.cli.main.SummarizerService")
    def test_summarize_daily_weekly(self, mock_cls):
        mock_cls.return_value.daily_range.return_value = [
            {"date": f"2026-02-{9 + i:02d}", "status": "success"} for i in range(7)
        ]
        result = runner.invoke(app, ["summarize", "daily", "--weekly", "2026-7"])
        assert result.exit_code == 0
        mock_cls.return_value.daily_range.assert_called_once()

    @patch("git_recap.cli.main.SummarizerService")
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
    @patch("git_recap.cli.main.SummarizerService")
    def test_summarize_daily(self, mock_cls):
        mock_cls.return_value.daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["summarize", "daily", "2025-02-16"])
        assert result.exit_code == 0
        assert "Daily summary" in result.output

    @patch("git_recap.cli.main.SummarizerService")
    def test_summarize_weekly(self, mock_cls):
        mock_cls.return_value.weekly.return_value = Path("/data/weekly.md")
        result = runner.invoke(app, ["summarize", "weekly", "2025", "7"])
        assert result.exit_code == 0
        assert "Weekly summary" in result.output
        mock_cls.return_value.weekly.assert_called_once_with(2025, 7)

    @patch("git_recap.cli.main.SummarizerService")
    def test_summarize_monthly(self, mock_cls):
        mock_cls.return_value.monthly.return_value = Path("/data/monthly.md")
        result = runner.invoke(app, ["summarize", "monthly", "2025", "2"])
        assert result.exit_code == 0
        assert "Monthly summary" in result.output
        mock_cls.return_value.monthly.assert_called_once_with(2025, 2)

    @patch("git_recap.cli.main.SummarizerService")
    def test_summarize_yearly(self, mock_cls):
        mock_cls.return_value.yearly.return_value = Path("/data/yearly.md")
        result = runner.invoke(app, ["summarize", "yearly", "2025"])
        assert result.exit_code == 0
        assert "Yearly summary" in result.output
        mock_cls.return_value.yearly.assert_called_once_with(2025)

    @patch("git_recap.cli.main.SummarizerService")
    def test_summarize_error(self, mock_cls):
        mock_cls.return_value.daily.side_effect = SummarizeError("LLM error")
        result = runner.invoke(app, ["summarize", "daily", "2025-02-16"])
        assert result.exit_code == 1


class TestRun:
    @patch("git_recap.cli.main.OrchestratorService")
    @patch("git_recap.cli.main.SummarizerService")
    @patch("git_recap.cli.main.NormalizerService")
    @patch("git_recap.cli.main.FetcherService")
    def test_run_single_date(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["run", "2025-02-16"])
        assert result.exit_code == 0
        assert "Pipeline complete" in result.output
        mock_orch.return_value.run_daily.assert_called_once_with("2025-02-16")

    @patch("git_recap.cli.main.OrchestratorService")
    @patch("git_recap.cli.main.SummarizerService")
    @patch("git_recap.cli.main.NormalizerService")
    @patch("git_recap.cli.main.FetcherService")
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
        assert "2/2 succeeded" in result.output

    @patch("git_recap.cli.main.OrchestratorService")
    @patch("git_recap.cli.main.SummarizerService")
    @patch("git_recap.cli.main.NormalizerService")
    @patch("git_recap.cli.main.FetcherService")
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
        assert "1/2 succeeded" in result.output

    @patch("git_recap.cli.main.OrchestratorService")
    @patch("git_recap.cli.main.SummarizerService")
    @patch("git_recap.cli.main.NormalizerService")
    @patch("git_recap.cli.main.FetcherService")
    def test_run_error(self, mock_fetch, mock_norm, mock_summ, mock_orch):
        mock_orch.return_value.run_daily.side_effect = StepFailedError(
            "fetch", FetchError("timeout")
        )
        result = runner.invoke(app, ["run", "2025-02-16"])
        assert result.exit_code == 1
        assert "Error" in result.output


class TestAsk:
    @patch("git_recap.cli.main.SummarizerService")
    def test_ask_question(self, mock_cls):
        mock_cls.return_value.query.return_value = "이번 달 주요 성과는..."
        result = runner.invoke(app, ["ask", "이번 달 주요 성과?"])
        assert result.exit_code == 0
        assert "이번 달 주요 성과는" in result.output

    @patch("git_recap.cli.main.SummarizerService")
    def test_ask_error(self, mock_cls):
        mock_cls.return_value.query.side_effect = SummarizeError("No context")
        result = runner.invoke(app, ["ask", "질문?"])
        assert result.exit_code == 1
        assert "Error" in result.output

    @patch("git_recap.cli.main.SummarizerService")
    def test_ask_with_months_option(self, mock_cls):
        mock_cls.return_value.query.return_value = "답변"
        result = runner.invoke(app, ["ask", "질문?", "--months", "6"])
        assert result.exit_code == 0
        mock_cls.return_value.query.assert_called_once_with("질문?", months_back=6)


# ── Checkpoint 헬퍼 테스트 ──


class TestReadCheckpointHelpers:
    def test_read_last_normalize_date_no_file(self):
        """파일 없으면 None."""
        from git_recap.cli.main import _read_last_normalize_date

        config = _mock_config()
        assert _read_last_normalize_date(config) is None

    def test_read_last_normalize_date_with_key(self, tmp_path):
        """last_normalize_date 키가 있으면 반환."""
        import json
        from git_recap.cli.main import _read_last_normalize_date

        config = _mock_config()
        config.data_dir = tmp_path / "data"
        (config.data_dir / "state").mkdir(parents=True)
        with open(config.checkpoints_path, "w") as f:
            json.dump({"last_normalize_date": "2025-02-16"}, f)
        assert _read_last_normalize_date(config) == "2025-02-16"

    def test_read_last_normalize_date_missing_key(self, tmp_path):
        """키 없으면 None."""
        import json
        from git_recap.cli.main import _read_last_normalize_date

        config = _mock_config()
        config.data_dir = tmp_path / "data"
        (config.data_dir / "state").mkdir(parents=True)
        with open(config.checkpoints_path, "w") as f:
            json.dump({"last_fetch_date": "2025-02-16"}, f)
        assert _read_last_normalize_date(config) is None

    def test_read_last_summarize_date_no_file(self):
        """파일 없으면 None."""
        from git_recap.cli.main import _read_last_summarize_date

        config = _mock_config()
        assert _read_last_summarize_date(config) is None

    def test_read_last_summarize_date_with_key(self, tmp_path):
        """last_summarize_date 키가 있으면 반환."""
        import json
        from git_recap.cli.main import _read_last_summarize_date

        config = _mock_config()
        config.data_dir = tmp_path / "data"
        (config.data_dir / "state").mkdir(parents=True)
        with open(config.checkpoints_path, "w") as f:
            json.dump({"last_summarize_date": "2025-02-16"}, f)
        assert _read_last_summarize_date(config) == "2025-02-16"

    def test_read_last_summarize_date_missing_key(self, tmp_path):
        """키 없으면 None."""
        import json
        from git_recap.cli.main import _read_last_summarize_date

        config = _mock_config()
        config.data_dir = tmp_path / "data"
        (config.data_dir / "state").mkdir(parents=True)
        with open(config.checkpoints_path, "w") as f:
            json.dump({"last_fetch_date": "2025-02-16"}, f)
        assert _read_last_summarize_date(config) is None


# ── Normalize Catch-Up 테스트 ──


class TestNormalizeCatchUp:
    @patch("git_recap.cli.main.NormalizerService")
    def test_no_args_no_checkpoint(self, mock_cls):
        """인자 없고 checkpoint 없으면 오늘만 normalize."""
        mock_cls.return_value.normalize.return_value = (
            Path("/data/activities.jsonl"),
            Path("/data/stats.json"),
        )
        result = runner.invoke(app, ["normalize"])
        assert result.exit_code == 0
        mock_cls.return_value.normalize.assert_called_once()

    @patch("git_recap.cli.main.date_utils")
    @patch("git_recap.cli.main._read_last_normalize_date")
    @patch("git_recap.cli.main.NormalizerService")
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
        )

    @patch("git_recap.cli.main.date_utils")
    @patch("git_recap.cli.main._read_last_normalize_date")
    @patch("git_recap.cli.main.NormalizerService")
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
    @patch("git_recap.cli.main.NormalizerService")
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
        )

    @patch("git_recap.cli.main.NormalizerService")
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
    @patch("git_recap.cli.main.NormalizerService")
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

    @patch("git_recap.cli.main.NormalizerService")
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
    @patch("git_recap.cli.main.SummarizerService")
    def test_no_args_no_checkpoint(self, mock_cls):
        """인자 없고 checkpoint 없으면 오늘만 summarize."""
        mock_cls.return_value.daily.return_value = Path("/data/daily.md")
        result = runner.invoke(app, ["summarize", "daily"])
        assert result.exit_code == 0
        mock_cls.return_value.daily.assert_called_once()

    @patch("git_recap.cli.main.date_utils")
    @patch("git_recap.cli.main._read_last_summarize_date")
    @patch("git_recap.cli.main.SummarizerService")
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
        )

    @patch("git_recap.cli.main.date_utils")
    @patch("git_recap.cli.main._read_last_summarize_date")
    @patch("git_recap.cli.main.SummarizerService")
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
    @patch("git_recap.cli.main.SummarizerService")
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
        )


# ── Summarize Daily Range Output 테스트 ──


class TestSummarizeDailyRangeOutput:
    @patch("git_recap.cli.main.SummarizerService")
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

    @patch("git_recap.cli.main.SummarizerService")
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
