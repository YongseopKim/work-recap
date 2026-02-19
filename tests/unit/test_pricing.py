"""PricingTable tests."""

from pathlib import Path

import pytest

from workrecap.infra.pricing import PricingTable

# Minimal TOML with only the models used by tests
_TEST_TOML = """\
[openai]
"gpt-5"       = { input = 1.25,  output = 10.00 }
"gpt-4o"      = { input = 2.50,  output = 10.00 }
"gpt-4o-mini" = { input = 0.15,  output = 0.60 }

[anthropic]
"claude-opus-4-6"   = { input = 5.00,  output = 25.00 }
"claude-sonnet-4-6" = { input = 3.00,  output = 15.00 }
"claude-sonnet-4-5" = { input = 3.00,  output = 15.00 }
"claude-haiku-4-5"  = { input = 1.00,  output = 5.00 }

[gemini]
"gemini-3-pro"     = { input = 2.00, output = 12.00 }
"gemini-2.0-flash" = { input = 0.10, output = 0.40 }
"""


@pytest.fixture()
def pricing_toml(tmp_path: Path) -> Path:
    """Create a temp pricing TOML for test isolation."""
    p = tmp_path / "pricing.toml"
    p.write_text(_TEST_TOML)
    return p


class TestPricingTable:
    def test_known_model_cost(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        cost = pt.estimate_cost(
            "openai", "gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=0
        )
        assert cost > 0

    def test_unknown_model_returns_zero(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        cost = pt.estimate_cost(
            "openai", "unknown-model-xyz", prompt_tokens=1000, completion_tokens=500
        )
        assert cost == 0.0

    def test_prompt_and_completion_separate_rates(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        prompt_cost = pt.estimate_cost(
            "openai", "gpt-4o", prompt_tokens=1_000_000, completion_tokens=0
        )
        completion_cost = pt.estimate_cost(
            "openai", "gpt-4o", prompt_tokens=0, completion_tokens=1_000_000
        )
        # Completion is typically more expensive
        assert completion_cost > prompt_cost

    def test_anthropic_model(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        cost = pt.estimate_cost(
            "anthropic",
            "claude-haiku-4-5-20251001",
            prompt_tokens=500_000,
            completion_tokens=100_000,
        )
        assert cost > 0

    def test_gemini_model(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        cost = pt.estimate_cost(
            "gemini", "gemini-2.0-flash", prompt_tokens=1_000_000, completion_tokens=500_000
        )
        assert cost > 0

    def test_custom_provider_returns_zero(self, pricing_toml: Path):
        """Custom/local models have no pricing."""
        pt = PricingTable(path=pricing_toml)
        cost = pt.estimate_cost(
            "custom", "llama3", prompt_tokens=1_000_000, completion_tokens=500_000
        )
        assert cost == 0.0

    def test_get_rate_returns_none_for_unknown(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        rate = pt.get_rate("openai", "nonexistent")
        assert rate is None

    def test_get_rate_returns_tuple_for_known(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        rate = pt.get_rate("openai", "gpt-4o")
        assert rate is not None
        assert len(rate) == 2  # (prompt_rate, completion_rate)

    def test_model_name_prefix_matching(self, pricing_toml: Path):
        """Models with date suffixes should match base name."""
        pt = PricingTable(path=pricing_toml)
        cost = pt.estimate_cost(
            "anthropic",
            "claude-sonnet-4-5-20250929",
            prompt_tokens=1_000_000,
            completion_tokens=0,
        )
        assert cost > 0

    def test_claude_sonnet_4_6_has_pricing(self, pricing_toml: Path):
        """claude-sonnet-4-6 must have pricing (was missing, causing $0 reports)."""
        pt = PricingTable(path=pricing_toml)
        rate = pt.get_rate("anthropic", "claude-sonnet-4-6")
        assert rate is not None
        assert rate == (3.00, 15.00)

    def test_claude_opus_4_6_has_pricing(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        rate = pt.get_rate("anthropic", "claude-opus-4-6")
        assert rate is not None
        assert rate == (5.00, 25.00)

    def test_gpt5_has_pricing(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        rate = pt.get_rate("openai", "gpt-5")
        assert rate is not None
        assert rate == (1.25, 10.00)

    def test_gemini_3_pro_has_pricing(self, pricing_toml: Path):
        pt = PricingTable(path=pricing_toml)
        rate = pt.get_rate("gemini", "gemini-3-pro")
        assert rate is not None
        assert rate == (2.00, 12.00)


class TestPricingTomlLoading:
    """Tests for loading pricing data from TOML files."""

    def test_load_from_toml(self, tmp_path: Path):
        toml_file = tmp_path / "pricing.toml"
        toml_file.write_text('[openai]\n"test-model" = { input = 1.50, output = 5.00 }\n')
        pt = PricingTable(path=toml_file)
        rate = pt.get_rate("openai", "test-model")
        assert rate == (1.50, 5.00)

    def test_missing_file_graceful(self, tmp_path: Path):
        """Missing TOML file should not raise; all costs become $0."""
        pt = PricingTable(path=tmp_path / "nonexistent.toml")
        rate = pt.get_rate("openai", "gpt-5")
        assert rate is None
        cost = pt.estimate_cost("openai", "gpt-5", prompt_tokens=1000, completion_tokens=500)
        assert cost == 0.0

    def test_invalid_toml_raises(self, tmp_path: Path):
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text("not valid [[[ toml content")
        with pytest.raises(Exception):  # tomllib.TOMLDecodeError
            PricingTable(path=toml_file)


class TestCacheAwarePricing:
    def test_estimate_cost_with_cache_read(self, pricing_toml: Path):
        """Cache read tokens get 90% discount on Anthropic input pricing."""
        pt = PricingTable(path=pricing_toml)
        # 1M total prompt, 800K from cache read
        cost = pt.estimate_cost(
            "anthropic",
            "claude-haiku-4-5",
            prompt_tokens=1_000_000,
            completion_tokens=100_000,
            cache_read_tokens=800_000,
        )
        # Without cache: (1M * 1.0 + 100K * 5.0) / 1M = $1.50
        # With cache: (200K * 1.0 + 800K * 0.1 + 100K * 5.0) / 1M = $0.78
        assert abs(cost - 0.78) < 0.01

    def test_estimate_cost_with_cache_write(self, pricing_toml: Path):
        """Cache write tokens cost 125% of input rate."""
        pt = PricingTable(path=pricing_toml)
        cost = pt.estimate_cost(
            "anthropic",
            "claude-haiku-4-5",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=1_000_000,
        )
        # cache_write: 1M * 1.0 * 1.25 / 1M = $1.25
        # non-cached prompt: (1M - 0 - 1M) = 0
        assert abs(cost - 1.25) < 0.01

    def test_estimate_cost_no_cache_unchanged(self, pricing_toml: Path):
        """No cache tokens → same as before."""
        pt = PricingTable(path=pricing_toml)
        cost_no_cache = pt.estimate_cost(
            "openai", "gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=500_000
        )
        cost_with_zeros = pt.estimate_cost(
            "openai",
            "gpt-4o-mini",
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        assert cost_no_cache == cost_with_zeros

    def test_estimate_cost_openai_cache_50_percent(self, pricing_toml: Path):
        """OpenAI cache read = 50% discount."""
        pt = PricingTable(path=pricing_toml)
        cost = pt.estimate_cost(
            "openai",
            "gpt-4o-mini",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            cache_read_tokens=800_000,
        )
        # non-cached: 200K * 0.15 / 1M = 0.03
        # cached: 800K * 0.15 * 0.5 / 1M = 0.06
        # total = 0.09
        assert abs(cost - 0.09) < 0.01
