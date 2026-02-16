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


# ── Tests ──


class TestFetch:
    @patch("git_recap.cli.main.FetcherService")
    def test_fetch_with_date(self, mock_cls):
        mock_cls.return_value.fetch.return_value = Path("/data/raw/prs.json")
        result = runner.invoke(app, ["fetch", "2025-02-16"])
        assert result.exit_code == 0
        assert "Fetched" in result.output
        mock_cls.return_value.fetch.assert_called_once_with("2025-02-16")

    @patch("git_recap.cli.main.FetcherService")
    def test_fetch_default_today(self, mock_cls):
        mock_cls.return_value.fetch.return_value = Path("/data/raw/prs.json")
        result = runner.invoke(app, ["fetch"])
        assert result.exit_code == 0
        # 오늘 날짜로 호출됨
        call_args = mock_cls.return_value.fetch.call_args[0][0]
        assert len(call_args) == 10  # YYYY-MM-DD

    @patch("git_recap.cli.main.FetcherService")
    def test_fetch_error(self, mock_cls):
        mock_cls.return_value.fetch.side_effect = FetchError("GHES down")
        result = runner.invoke(app, ["fetch", "2025-02-16"])
        assert result.exit_code == 1
        assert "Error" in result.output


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
        result = runner.invoke(app, [
            "run", "--since", "2025-02-15", "--until", "2025-02-16",
        ])
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
        result = runner.invoke(app, [
            "run", "--since", "2025-02-15", "--until", "2025-02-16",
        ])
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
