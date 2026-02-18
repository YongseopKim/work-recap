# Phase 0-2: config.py 상세 설계

## 목적

pydantic-settings 기반 `AppConfig` 클래스를 정의한다.
`.env` 파일 또는 환경변수에서 설정을 로드하고 validation을 수행한다.
모든 서비스는 이 config 객체를 주입받아 사용한다.

---

## 위치

`src/workrecap/config.py`

## 의존성

- `pydantic-settings` (BaseSettings)
- `pathlib.Path`

---

## 상세 구현

```python
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """애플리케이션 전체 설정. .env 파일 또는 환경변수에서 로드."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GHES 연결
    ghes_url: str                          # e.g., "https://github.example.com"
    ghes_token: str                        # Personal Access Token
    username: str                          # GHES 사용자명 (활동 수집 대상)

    # 파일 경로
    data_dir: Path = Path("data")
    prompts_dir: Path = Path("prompts")

    # LLM 설정
    llm_provider: str = "openai"           # "openai" | "anthropic"
    llm_api_key: str
    llm_model: str = "gpt-4o-mini"

    # ── 파생 경로 (helper) ──

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
```

---

## 테스트 명세

### test_config.py

```python
"""tests/unit/test_config.py"""

class TestAppConfig:
    def test_loads_from_kwargs(self):
        """환경변수 없이 직접 인자로 생성 가능."""
        config = AppConfig(
            ghes_url="https://github.example.com",
            ghes_token="token",
            username="user",
            llm_api_key="key",
        )
        assert config.ghes_url == "https://github.example.com"
        assert config.llm_model == "gpt-4o-mini"  # default

    def test_default_paths(self):
        """data_dir, prompts_dir 기본값 확인."""
        config = AppConfig(
            ghes_url="u", ghes_token="t", username="u", llm_api_key="k"
        )
        assert config.data_dir == Path("data")
        assert config.prompts_dir == Path("prompts")

    def test_derived_paths(self):
        """파생 경로 helper 메서드 정확성."""
        config = AppConfig(
            ghes_url="u", ghes_token="t", username="u",
            llm_api_key="k", data_dir=Path("/tmp/data")
        )
        assert config.raw_dir == Path("/tmp/data/raw")
        assert config.date_raw_dir("2025-02-16") == Path("/tmp/data/raw/2025/02/16")
        assert config.daily_summary_path("2025-02-16") == Path(
            "/tmp/data/summaries/2025/daily/02-16.md"
        )
        assert config.weekly_summary_path(2025, 7) == Path(
            "/tmp/data/summaries/2025/weekly/W07.md"
        )
        assert config.monthly_summary_path(2025, 2) == Path(
            "/tmp/data/summaries/2025/monthly/02.md"
        )
        assert config.yearly_summary_path(2025) == Path(
            "/tmp/data/summaries/2025/yearly.md"
        )

    def test_required_fields_missing(self):
        """필수 필드 누락 시 ValidationError."""
        with pytest.raises(ValidationError):
            AppConfig()  # ghes_url 등 필수 필드 없음

    def test_llm_provider_default(self):
        """llm_provider 기본값은 'openai'."""
        config = AppConfig(
            ghes_url="u", ghes_token="t", username="u", llm_api_key="k"
        )
        assert config.llm_provider == "openai"
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 0.2.1 | AppConfig 클래스 구현 (필수 필드 + 기본값) | test_loads_from_kwargs, test_default_paths, test_required_fields_missing |
| 0.2.2 | 파생 경로 property/method 구현 | test_derived_paths |
| 0.2.3 | llm_provider 설정 추가 | test_llm_provider_default |
