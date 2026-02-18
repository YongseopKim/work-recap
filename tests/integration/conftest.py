"""Integration test fixtures — real .env credentials + isolated tmp data directory."""

import os
from pathlib import Path

import pytest

from workrecap.config import AppConfig
from workrecap.infra.ghes_client import GHESClient
from workrecap.infra.llm_client import LLMClient

# ── .env 존재 여부 확인 ──

_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
HAS_ENV = _env_path.exists()


@pytest.fixture(autouse=True)
def _use_real_env(monkeypatch):
    """Root conftest의 _use_test_env를 재override하여 real .env 사용."""
    monkeypatch.setattr(AppConfig, "model_config", {**AppConfig.model_config, "env_file": ".env"})


@pytest.fixture(scope="class")
def shared_tmp_dir(tmp_path_factory):
    """Class-scope 임시 디렉토리. 같은 클래스 내 테스트가 데이터를 공유."""
    return tmp_path_factory.mktemp("integration")


@pytest.fixture(scope="class")
def test_date():
    """테스트 대상 날짜. INTEGRATION_TEST_DATE 환경변수 또는 3일 전."""
    from datetime import date, timedelta

    env_date = os.environ.get("INTEGRATION_TEST_DATE")
    if env_date:
        return env_date
    return (date.today() - timedelta(days=3)).isoformat()


@pytest.fixture(scope="class")
def real_config(shared_tmp_dir):
    """Real .env 자격증명 + tmp data_dir. 실제 data/ 오염 없음."""
    # 직접 model_config override (class scope에서는 monkeypatch 사용 불가)
    original = AppConfig.model_config.copy()
    AppConfig.model_config = {**original, "env_file": ".env"}

    try:
        data_dir = shared_tmp_dir / "data"
        for sub in ["state/jobs", "raw", "normalized", "summaries"]:
            (data_dir / sub).mkdir(parents=True)

        prompts_dir = Path(__file__).resolve().parent.parent.parent / "prompts"

        config = AppConfig(data_dir=data_dir, prompts_dir=prompts_dir)

        required = [config.ghes_url, config.ghes_token, config.username, config.llm_api_key]
        if not all(required):
            pytest.skip("Required .env keys missing (ghes_url, ghes_token, username, llm_api_key)")

        yield config
    finally:
        AppConfig.model_config = original


@pytest.fixture(scope="class")
def ghes_client(real_config):
    """Real GHES HTTP client."""
    client = GHESClient(real_config.ghes_url, real_config.ghes_token)
    yield client
    client.close()


@pytest.fixture(scope="class")
def llm_client(real_config):
    """Real LLM client."""
    return LLMClient(
        provider=real_config.llm_provider,
        api_key=real_config.llm_api_key,
        model=real_config.llm_model,
    )
