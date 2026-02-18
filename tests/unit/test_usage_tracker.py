"""UsageTracker tests."""

from concurrent.futures import ThreadPoolExecutor

from workrecap.infra.usage_tracker import UsageTracker
from workrecap.infra.pricing import PricingTable
from workrecap.models import ModelUsage, TokenUsage


class TestUsageTracker:
    def test_initial_state_empty(self):
        tracker = UsageTracker()
        assert tracker.total_usage == TokenUsage()
        assert tracker.model_usages == {}

    def test_record_single_call(self):
        tracker = UsageTracker()
        usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, call_count=1)
        tracker.record("openai", "gpt-4o-mini", usage)

        mu = tracker.model_usages["openai/gpt-4o-mini"]
        assert mu.prompt_tokens == 100
        assert mu.completion_tokens == 50
        assert mu.total_tokens == 150
        assert mu.call_count == 1

    def test_record_accumulates(self):
        tracker = UsageTracker()
        u1 = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, call_count=1)
        u2 = TokenUsage(prompt_tokens=200, completion_tokens=80, total_tokens=280, call_count=1)
        tracker.record("openai", "gpt-4o-mini", u1)
        tracker.record("openai", "gpt-4o-mini", u2)

        mu = tracker.model_usages["openai/gpt-4o-mini"]
        assert mu.prompt_tokens == 300
        assert mu.completion_tokens == 130
        assert mu.total_tokens == 430
        assert mu.call_count == 2

    def test_multiple_models(self):
        tracker = UsageTracker()
        tracker.record("openai", "gpt-4o-mini", TokenUsage(100, 50, 150, 1))
        tracker.record("anthropic", "claude-haiku", TokenUsage(80, 40, 120, 1))

        assert len(tracker.model_usages) == 2
        assert "openai/gpt-4o-mini" in tracker.model_usages
        assert "anthropic/claude-haiku" in tracker.model_usages

    def test_total_usage_aggregates_all_models(self):
        tracker = UsageTracker()
        tracker.record("openai", "gpt-4o-mini", TokenUsage(100, 50, 150, 1))
        tracker.record("anthropic", "claude-haiku", TokenUsage(80, 40, 120, 1))

        total = tracker.total_usage
        assert total.prompt_tokens == 180
        assert total.completion_tokens == 90
        assert total.total_tokens == 270
        assert total.call_count == 2

    def test_thread_safety(self):
        tracker = UsageTracker()

        def record_call(i):
            tracker.record("openai", "gpt-4o-mini", TokenUsage(100, 50, 150, 1))

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(record_call, range(100)))

        mu = tracker.model_usages["openai/gpt-4o-mini"]
        assert mu.call_count == 100
        assert mu.prompt_tokens == 10_000
        assert mu.total_tokens == 15_000


class TestUsageTrackerWithPricing:
    def test_record_with_pricing(self):
        pricing = PricingTable()
        tracker = UsageTracker(pricing=pricing)
        tracker.record("openai", "gpt-4o-mini", TokenUsage(1_000_000, 500_000, 1_500_000, 1))

        mu = tracker.model_usages["openai/gpt-4o-mini"]
        assert mu.estimated_cost_usd > 0

    def test_unknown_model_zero_cost(self):
        pricing = PricingTable()
        tracker = UsageTracker(pricing=pricing)
        tracker.record("custom", "llama3", TokenUsage(1000, 500, 1500, 1))

        mu = tracker.model_usages["custom/llama3"]
        assert mu.estimated_cost_usd == 0.0


class TestFormatReport:
    def test_empty_report(self):
        tracker = UsageTracker()
        report = tracker.format_report()
        assert "No LLM usage" in report

    def test_single_model_report(self):
        tracker = UsageTracker()
        tracker.record("openai", "gpt-4o-mini", TokenUsage(100, 50, 150, 1))
        report = tracker.format_report()
        assert "openai" in report
        assert "gpt-4o-mini" in report
        assert "1 call" in report

    def test_multi_model_report_has_total(self):
        tracker = UsageTracker()
        tracker.record("openai", "gpt-4o-mini", TokenUsage(100, 50, 150, 1))
        tracker.record("anthropic", "claude-haiku", TokenUsage(80, 40, 120, 1))
        report = tracker.format_report()
        assert "Total" in report
        assert "2 calls" in report


class TestModelUsageDataclass:
    def test_defaults(self):
        mu = ModelUsage(provider="openai", model="gpt-4o")
        assert mu.prompt_tokens == 0
        assert mu.estimated_cost_usd == 0.0
