"""ProviderConfig TOML-only parsing tests."""

import textwrap
from pathlib import Path

import pytest

from workrecap.infra.provider_config import ProviderConfig


# ── TOML Parsing ──


class TestTOMLParsing:
    def _write_toml(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / ".provider" / "config.toml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
        return p

    def test_full_config(self, tmp_path):
        path = self._write_toml(
            tmp_path,
            """\
            [strategy]
            mode = "adaptive"

            [providers.openai]
            api_key = "sk-openai"

            [providers.anthropic]
            api_key = "sk-ant"

            [providers.gemini]
            api_key = "AIza"

            [providers.custom]
            api_key = ""
            base_url = "http://localhost:11434/v1"

            [tasks.enrich]
            provider = "anthropic"
            model = "claude-haiku-4-5-20251001"
            escalation_model = "claude-sonnet-4-5-20250929"

            [tasks.daily]
            provider = "openai"
            model = "gpt-4o-mini"

            [tasks.weekly]
            provider = "openai"
            model = "gpt-4o-mini"

            [tasks.monthly]
            provider = "anthropic"
            model = "claude-sonnet-4-5-20250929"

            [tasks.yearly]
            provider = "anthropic"
            model = "claude-sonnet-4-5-20250929"

            [tasks.query]
            provider = "openai"
            model = "gpt-4o"
            """,
        )
        pc = ProviderConfig(config_path=path)

        assert pc.strategy_mode == "adaptive"

        enrich = pc.get_task_config("enrich")
        assert enrich.provider == "anthropic"
        assert enrich.model == "claude-haiku-4-5-20251001"
        assert enrich.escalation_model == "claude-sonnet-4-5-20250929"

        daily = pc.get_task_config("daily")
        assert daily.provider == "openai"
        assert daily.model == "gpt-4o-mini"
        assert daily.escalation_model is None

        ant = pc.get_provider_entry("anthropic")
        assert ant.api_key == "sk-ant"

        custom = pc.get_provider_entry("custom")
        assert custom.base_url == "http://localhost:11434/v1"

    def test_minimal_config_uses_default_task(self, tmp_path):
        """[tasks.default]만 있으면 모든 task에 적용."""
        path = self._write_toml(
            tmp_path,
            """\
            [providers.openai]
            api_key = "sk-test"

            [tasks.default]
            provider = "openai"
            model = "gpt-4o-mini"
            """,
        )
        pc = ProviderConfig(config_path=path)
        for task in ("enrich", "daily", "weekly", "monthly", "yearly", "query"):
            tc = pc.get_task_config(task)
            assert tc.provider == "openai"
            assert tc.model == "gpt-4o-mini"

    def test_missing_provider_in_validation(self, tmp_path):
        """task에서 참조하는 provider가 [providers]에 없으면 validation 에러."""
        path = self._write_toml(
            tmp_path,
            """\
            [providers.openai]
            api_key = "sk-test"

            [tasks.enrich]
            provider = "anthropic"
            model = "claude-haiku-4-5-20251001"
            """,
        )
        pc = ProviderConfig(config_path=path)
        errors = pc.validate()
        assert any("anthropic" in e for e in errors)

    def test_missing_api_key_in_validation(self, tmp_path):
        """provider에 api_key가 빈 문자열이면 validation 경고 (custom 제외)."""
        path = self._write_toml(
            tmp_path,
            """\
            [providers.openai]
            api_key = ""

            [tasks.enrich]
            provider = "openai"
            model = "gpt-4o-mini"
            """,
        )
        pc = ProviderConfig(config_path=path)
        errors = pc.validate()
        assert any("api_key" in e for e in errors)

    def test_strategy_defaults_to_fixed(self, tmp_path):
        """[strategy] 섹션 없으면 'fixed' 기본값."""
        path = self._write_toml(
            tmp_path,
            """\
            [providers.openai]
            api_key = "sk-test"

            [tasks.default]
            provider = "openai"
            model = "gpt-4o-mini"
            """,
        )
        pc = ProviderConfig(config_path=path)
        assert pc.strategy_mode == "fixed"

    def test_nonexistent_path_raises(self, tmp_path):
        """존재하지 않는 config_path는 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            ProviderConfig(config_path=tmp_path / "missing.toml")

    def test_unknown_task_falls_back_to_default(self, tmp_path):
        """알 수 없는 task 이름은 default config으로 fallback."""
        path = self._write_toml(
            tmp_path,
            """\
            [providers.openai]
            api_key = "sk-test"

            [tasks.default]
            provider = "openai"
            model = "gpt-4o-mini"
            """,
        )
        pc = ProviderConfig(config_path=path)
        tc = pc.get_task_config("unknown_task")
        assert tc.provider == "openai"
        assert tc.model == "gpt-4o-mini"


# ── AppConfig integration ──


class TestAppConfigIntegration:
    def test_provider_config_path_property(self, test_config):
        """AppConfig.provider_config_path returns .provider/config.toml."""
        assert test_config.provider_config_path == Path(".provider/config.toml")
