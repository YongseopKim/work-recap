import pytest
from pathlib import Path
from git_recap.config import AppConfig


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """테스트용 격리된 data 디렉토리."""
    data_dir = tmp_path / "data"
    for sub in ["state/jobs", "raw", "normalized", "summaries"]:
        (data_dir / sub).mkdir(parents=True)
    return data_dir


@pytest.fixture
def test_config(tmp_data_dir: Path, tmp_path: Path) -> AppConfig:
    """테스트용 AppConfig. 실제 .env 파일 불필요."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    return AppConfig(
        ghes_url="https://github.example.com",
        ghes_token="test-token",
        username="testuser",
        data_dir=tmp_data_dir,
        prompts_dir=prompts_dir,
        llm_provider="openai",
        llm_api_key="test-key",
        llm_model="gpt-4o-mini",
    )
