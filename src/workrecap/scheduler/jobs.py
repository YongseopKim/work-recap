"""스케줄러 job 함수 -- daily, weekly, monthly, yearly 파이프라인 실행."""

import calendar
import logging
from datetime import date, datetime, timedelta, timezone

from workrecap.config import AppConfig
from workrecap.exceptions import SummarizeError
from workrecap.scheduler.config import ScheduleConfig
from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.notifier import Notifier, SchedulerEvent

logger = logging.getLogger(__name__)


def _build_orchestrator(config: AppConfig, schedule_config: ScheduleConfig):
    """Build full pipeline orchestrator (fetch->normalize->summarize)."""
    from workrecap.infra.ghes_client import GHESClient
    from workrecap.infra.llm_router import LLMRouter
    from workrecap.infra.pricing import PricingTable
    from workrecap.infra.provider_config import ProviderConfig
    from workrecap.infra.usage_tracker import UsageTracker
    from workrecap.services.daily_state import DailyStateStore
    from workrecap.services.fetch_progress import FetchProgressStore
    from workrecap.services.fetcher import FetcherService
    from workrecap.services.normalizer import NormalizerService
    from workrecap.services.orchestrator import OrchestratorService
    from workrecap.services.summarizer import SummarizerService

    ghes = GHESClient(config.ghes_url, config.ghes_token, search_interval=2.0)
    pc = ProviderConfig(config.provider_config_path)
    tracker = UsageTracker(pricing=PricingTable())
    llm = LLMRouter(pc, usage_tracker=tracker)
    ds = DailyStateStore(config.daily_state_path)
    ps = FetchProgressStore(config.state_dir / "fetch_progress")

    daily = schedule_config.daily
    fetcher = FetcherService(config, ghes, daily_state=ds, progress_store=ps)
    normalizer = NormalizerService(
        config,
        daily_state=ds,
        llm=llm if daily.enrich else None,
    )
    summarizer = SummarizerService(config, llm, daily_state=ds)
    return OrchestratorService(fetcher, normalizer, summarizer, config=config)


def _build_summarizer(config: AppConfig):
    """Build summarizer only (for weekly/monthly/yearly)."""
    from workrecap.infra.llm_router import LLMRouter
    from workrecap.infra.pricing import PricingTable
    from workrecap.infra.provider_config import ProviderConfig
    from workrecap.infra.usage_tracker import UsageTracker
    from workrecap.services.daily_state import DailyStateStore
    from workrecap.services.summarizer import SummarizerService

    pc = ProviderConfig(config.provider_config_path)
    tracker = UsageTracker(pricing=PricingTable())
    llm = LLMRouter(pc, usage_tracker=tracker)
    ds = DailyStateStore(config.daily_state_path)
    return SummarizerService(config, llm, daily_state=ds)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _weeks_in_month(year: int, month: int) -> list[tuple[int, int]]:
    """Return all ISO (year, week) tuples that overlap with the given month."""
    seen: set[tuple[int, int]] = set()
    result: list[tuple[int, int]] = []
    num_days = calendar.monthrange(year, month)[1]
    for day in range(1, num_days + 1):
        iso_y, iso_w, _ = date(year, month, day).isocalendar()
        key = (iso_y, iso_w)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


async def run_daily_job(
    schedule_config: ScheduleConfig,
    history: SchedulerHistory,
    notifier: Notifier,
) -> None:
    """Run daily: fetch->normalize->summarize for yesterday's data."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    triggered_at = _now_iso()
    try:
        config = AppConfig()
        orch = _build_orchestrator(config, schedule_config)
        orch.run_daily(yesterday, types=None)
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at=triggered_at,
            completed_at=_now_iso(),
            target=yesterday,
        )
    except Exception as e:
        logger.exception("Scheduler daily job failed for %s", yesterday)
        event = SchedulerEvent(
            job="daily",
            status="failed",
            triggered_at=triggered_at,
            completed_at=_now_iso(),
            target=yesterday,
            error=str(e),
        )
    history.record(event)
    await notifier.notify(event)


async def run_weekly_job(
    schedule_config: ScheduleConfig,
    history: SchedulerHistory,
    notifier: Notifier,
) -> None:
    """Run weekly: generate last week's weekly summary."""
    last_week = date.today() - timedelta(weeks=1)
    iso_year, iso_week, _ = last_week.isocalendar()
    target = f"{iso_year}-W{iso_week:02d}"
    triggered_at = _now_iso()
    try:
        config = AppConfig()
        summarizer = _build_summarizer(config)
        summarizer.weekly(iso_year, iso_week, force=False)
        event = SchedulerEvent(
            job="weekly",
            status="success",
            triggered_at=triggered_at,
            completed_at=_now_iso(),
            target=target,
        )
    except Exception as e:
        logger.exception("Scheduler weekly job failed for %s", target)
        event = SchedulerEvent(
            job="weekly",
            status="failed",
            triggered_at=triggered_at,
            completed_at=_now_iso(),
            target=target,
            error=str(e),
        )
    history.record(event)
    await notifier.notify(event)


async def run_monthly_job(
    schedule_config: ScheduleConfig,
    history: SchedulerHistory,
    notifier: Notifier,
) -> None:
    """Run monthly: cascade weekly->monthly summary for last month."""
    today = date.today()
    if today.month == 1:
        last_year, last_month = today.year - 1, 12
    else:
        last_year, last_month = today.year, today.month - 1
    target = f"{last_year}-{last_month:02d}"
    triggered_at = _now_iso()
    try:
        config = AppConfig()
        summarizer = _build_summarizer(config)
        for wy, ww in _weeks_in_month(last_year, last_month):
            try:
                summarizer.weekly(wy, ww, force=False)
            except SummarizeError:
                pass
        summarizer.monthly(last_year, last_month, force=False)
        event = SchedulerEvent(
            job="monthly",
            status="success",
            triggered_at=triggered_at,
            completed_at=_now_iso(),
            target=target,
        )
    except Exception as e:
        logger.exception("Scheduler monthly job failed for %s", target)
        event = SchedulerEvent(
            job="monthly",
            status="failed",
            triggered_at=triggered_at,
            completed_at=_now_iso(),
            target=target,
            error=str(e),
        )
    history.record(event)
    await notifier.notify(event)


async def run_yearly_job(
    schedule_config: ScheduleConfig,
    history: SchedulerHistory,
    notifier: Notifier,
) -> None:
    """Run yearly: cascade weekly->monthly->yearly summary for last year."""
    last_year = date.today().year - 1
    target = str(last_year)
    triggered_at = _now_iso()
    try:
        config = AppConfig()
        summarizer = _build_summarizer(config)
        for mo in range(1, 13):
            for wy, ww in _weeks_in_month(last_year, mo):
                try:
                    summarizer.weekly(wy, ww, force=False)
                except SummarizeError:
                    pass
            try:
                summarizer.monthly(last_year, mo, force=False)
            except SummarizeError:
                pass
        summarizer.yearly(last_year, force=False)
        event = SchedulerEvent(
            job="yearly",
            status="success",
            triggered_at=triggered_at,
            completed_at=_now_iso(),
            target=target,
        )
    except Exception as e:
        logger.exception("Scheduler yearly job failed for %s", target)
        event = SchedulerEvent(
            job="yearly",
            status="failed",
            triggered_at=triggered_at,
            completed_at=_now_iso(),
            target=target,
            error=str(e),
        )
    history.record(event)
    await notifier.notify(event)
