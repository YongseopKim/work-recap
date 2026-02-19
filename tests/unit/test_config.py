from pathlib import Path

import pytest
from pydantic import ValidationError

from workrecap.config import AppConfig


class TestAppConfig:
    def test_loads_from_kwargs(self):
        """환경변수 없이 직접 인자로 생성 가능."""
        config = AppConfig(
            ghes_url="https://github.example.com",
            ghes_token="token",
            username="user",
        )
        assert config.ghes_url == "https://github.example.com"

    def test_default_paths(self):
        """data_dir, prompts_dir 기본값 확인."""
        config = AppConfig(ghes_url="u", ghes_token="t", username="u")
        assert config.data_dir == Path("data")
        assert config.prompts_dir == Path("prompts")

    def test_derived_paths(self):
        """파생 경로 helper 메서드 정확성."""
        config = AppConfig(
            ghes_url="u",
            ghes_token="t",
            username="u",
            data_dir=Path("/tmp/data"),
        )
        assert config.raw_dir == Path("/tmp/data/raw")
        assert config.normalized_dir == Path("/tmp/data/normalized")
        assert config.summaries_dir == Path("/tmp/data/summaries")
        assert config.state_dir == Path("/tmp/data/state")
        assert config.checkpoints_path == Path("/tmp/data/state/checkpoints.json")
        assert config.jobs_dir == Path("/tmp/data/state/jobs")
        assert config.date_raw_dir("2025-02-16") == Path("/tmp/data/raw/2025/02/16")
        assert config.date_normalized_dir("2025-02-16") == Path("/tmp/data/normalized/2025/02/16")
        assert config.daily_summary_path("2025-02-16") == Path(
            "/tmp/data/summaries/2025/daily/02-16.md"
        )
        assert config.weekly_summary_path(2025, 7) == Path("/tmp/data/summaries/2025/weekly/W07.md")
        assert config.monthly_summary_path(2025, 2) == Path(
            "/tmp/data/summaries/2025/monthly/02.md"
        )
        assert config.yearly_summary_path(2025) == Path("/tmp/data/summaries/2025/yearly.md")

    def test_required_fields_missing(self):
        """필수 필드 누락 시 ValidationError."""
        with pytest.raises(ValidationError):
            AppConfig()

    def test_max_workers_default(self):
        """max_workers 기본값은 5."""
        config = AppConfig(ghes_url="u", ghes_token="t", username="u")
        assert config.max_workers == 5

    def test_max_workers_from_kwarg(self):
        """max_workers를 직접 인자로 설정 가능."""
        config = AppConfig(ghes_url="u", ghes_token="t", username="u", max_workers=10)
        assert config.max_workers == 10

    def test_max_fetch_retries_default(self):
        """max_fetch_retries defaults to 5 — enough for transient issues without infinite loops."""
        config = AppConfig(ghes_url="u", ghes_token="t", username="u")
        assert config.max_fetch_retries == 5

    def test_max_fetch_retries_from_kwarg(self):
        """max_fetch_retries can be overridden (e.g., higher for very long runs)."""
        config = AppConfig(ghes_url="u", ghes_token="t", username="u", max_fetch_retries=10)
        assert config.max_fetch_retries == 10
