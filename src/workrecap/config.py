from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """애플리케이션 전체 설정. .env 파일 또는 환경변수에서 로드."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GHES 연결
    ghes_url: str
    ghes_token: str
    username: str = Field(
        validation_alias=AliasChoices("username", "ghes_username"),
    )

    # 파일 경로
    data_dir: Path = Path("data")
    prompts_dir: Path = Path("prompts")

    # 병렬 실행
    max_workers: int = 5

    # 복원력 (Resilience)
    # Maximum retry attempts for failed dates before giving up.
    # Applies to FailedDateStore: dates failing more than this many times
    # are marked exhausted and reported to the user instead of retried.
    max_fetch_retries: int = 5

    # 멀티소스
    enabled_sources: list[str] = ["github"]

    # ── 파생 경로 ──

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def normalized_dir(self) -> Path:
        return self.data_dir / "normalized"

    @property
    def summaries_dir(self) -> Path:
        return self.data_dir / "summaries"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def checkpoints_path(self) -> Path:
        return self.state_dir / "checkpoints.json"

    @property
    def daily_state_path(self) -> Path:
        return self.state_dir / "daily_state.json"

    @property
    def jobs_dir(self) -> Path:
        return self.state_dir / "jobs"

    def date_raw_dir(self, date: str) -> Path:
        """date='2025-02-16' → data/raw/2025/02/16/"""
        y, m, d = date.split("-")
        return self.raw_dir / y / m / d

    def date_normalized_dir(self, date: str) -> Path:
        """date='2025-02-16' → data/normalized/2025/02/16/"""
        y, m, d = date.split("-")
        return self.normalized_dir / y / m / d

    def daily_summary_path(self, date: str) -> Path:
        """date='2025-02-16' → data/summaries/2025/daily/02-16.md"""
        y, m, d = date.split("-")
        return self.summaries_dir / y / "daily" / f"{m}-{d}.md"

    def weekly_summary_path(self, year: int, week: int) -> Path:
        """data/summaries/2025/weekly/W07.md"""
        return self.summaries_dir / str(year) / "weekly" / f"W{week:02d}.md"

    def monthly_summary_path(self, year: int, month: int) -> Path:
        """data/summaries/2025/monthly/02.md"""
        return self.summaries_dir / str(year) / "monthly" / f"{month:02d}.md"

    def yearly_summary_path(self, year: int) -> Path:
        """data/summaries/2025/yearly.md"""
        return self.summaries_dir / str(year) / "yearly.md"

    @property
    def provider_config_path(self) -> Path:
        return Path(".provider/config.toml")
