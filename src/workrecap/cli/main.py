"""work-recap CLI — Typer 기반."""

import calendar
import json
import logging
from datetime import date
from pathlib import Path

import typer

from workrecap.config import AppConfig
from workrecap.exceptions import WorkRecapError, SummarizeError
from workrecap.infra.model_discovery import discover_models
from workrecap.logging_config import setup_file_logging, setup_logging
from workrecap.services import date_utils
from workrecap.services.daily_state import DailyStateStore
from workrecap.services.failed_dates import FailedDateStore
from workrecap.services.fetcher import FetcherService
from workrecap.services.normalizer import NormalizerService
from workrecap.services.orchestrator import OrchestratorService
from workrecap.services.summarizer import SummarizerService

logger = logging.getLogger(__name__)
_file_logger = logging.getLogger("workrecap.cli.output")

app = typer.Typer(help="GHES activity summarizer with LLM")
summarize_app = typer.Typer(help="Generate summaries")
storage_app = typer.Typer(help="Manage database and vector storage")
app.add_typer(summarize_app, name="summarize")
app.add_typer(storage_app, name="storage")


def _echo(msg: str = "", err: bool = False) -> None:
    """Echo to terminal AND log to file."""
    typer.echo(msg, err=err)
    if msg:
        level = logging.ERROR if err else logging.INFO
        _file_logger.log(level, msg)


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """GHES activity summarizer with LLM."""
    level = logging.DEBUG if verbose else logging.INFO
    setup_logging(level)
    setup_file_logging(Path(".log"))


VALID_TYPES = {"prs", "commits", "issues"}

# 소스별 valid types
SOURCE_TYPES: dict[str, set[str]] = {
    "github": {"prs", "commits", "issues"},
}


def _get_config() -> AppConfig:
    return AppConfig()


def _get_ghes_client(config: AppConfig):
    from workrecap.infra.ghes_client import GHESClient

    return GHESClient(config.ghes_url, config.ghes_token)


def _get_llm_router(config: AppConfig):
    from workrecap.infra.llm_router import LLMRouter
    from workrecap.infra.provider_config import ProviderConfig
    from workrecap.infra.usage_tracker import UsageTracker
    from workrecap.infra.pricing import PricingTable

    pc = ProviderConfig(config.provider_config_path)
    tracker = UsageTracker(pricing=PricingTable())
    return LLMRouter(pc, usage_tracker=tracker)


def _handle_error(e: WorkRecapError) -> None:
    _echo(f"Error: {e}", err=True)
    raise typer.Exit(code=1)


def _progress(msg: str) -> None:
    """진행 상황 콜백."""
    _echo(msg)


def _print_usage_report(llm) -> None:
    """LLM usage report 출력 (per-model breakdown + cost)."""
    tracker = getattr(llm, "usage_tracker", None)
    if tracker:
        report = tracker.format_report()
        if "No LLM usage" not in report:
            _echo(report)
    else:
        u = llm.usage
        if u.call_count > 0:
            _echo(
                f"Token usage: {u.prompt_tokens:,} prompt + {u.completion_tokens:,} completion"
                f" = {u.total_tokens:,} total ({u.call_count} calls)"
            )


def _read_last_fetch_date(config: AppConfig) -> str | None:
    cp_path = config.checkpoints_path
    if not cp_path.exists():
        return None
    with open(cp_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("last_fetch_date")


def _read_last_normalize_date(config: AppConfig) -> str | None:
    cp_path = config.checkpoints_path
    if not cp_path.exists():
        return None
    with open(cp_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("last_normalize_date")


def _read_last_summarize_date(config: AppConfig) -> str | None:
    cp_path = config.checkpoints_path
    if not cp_path.exists():
        return None
    with open(cp_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("last_summarize_date")


def _parse_weekly(value: str) -> tuple[int, int]:
    parts = value.split("-")
    return int(parts[0]), int(parts[1])


def _parse_monthly(value: str) -> tuple[int, int]:
    parts = value.split("-")
    return int(parts[0]), int(parts[1])


def _weeks_in_month(year: int, month: int) -> list[tuple[int, int]]:
    """해당 월에 걸치는 모든 ISO (year, week) 튜플을 순서대로 반환."""
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


def _resolve_dates(
    target_date: str | None,
    since: str | None,
    until: str | None,
    weekly: str | None,
    monthly: str | None,
    yearly: int | None,
) -> list[str] | None:
    """상호 배타 검증 + 날짜 리스트 반환. 인자 모두 None이면 None."""
    range_opts = sum(
        [
            target_date is not None,
            since is not None or until is not None,
            weekly is not None,
            monthly is not None,
            yearly is not None,
        ]
    )
    if range_opts > 1:
        _echo(
            "Error: Only one of target_date, --since/--until, --weekly, "
            "--monthly, --yearly can be specified.",
            err=True,
        )
        raise typer.Exit(code=1)

    if (since is None) != (until is None):
        _echo("Error: --since and --until must be used together.", err=True)
        raise typer.Exit(code=1)

    if since and until:
        return date_utils.date_range(since, until)
    elif weekly:
        year, week = _parse_weekly(weekly)
        s, u = date_utils.weekly_range(year, week)
        return date_utils.date_range(s, u)
    elif monthly:
        year, month = _parse_monthly(monthly)
        s, u = date_utils.monthly_range(year, month)
        return date_utils.date_range(s, u)
    elif yearly is not None:
        s, u = date_utils.yearly_range(yearly)
        return date_utils.date_range(s, u)
    elif target_date:
        return [target_date]
    else:
        return None


def _resolve_range_endpoints(
    target_date: str | None,
    since: str | None,
    until: str | None,
    weekly: str | None,
    monthly: str | None,
    yearly: int | None,
) -> tuple[str, str] | None:
    """날짜 범위의 (since, until) 엔드포인트 반환. 범위가 아니면 None."""
    if since and until:
        return since, until
    elif weekly:
        year, week = _parse_weekly(weekly)
        return date_utils.weekly_range(year, week)
    elif monthly:
        year, month = _parse_monthly(monthly)
        return date_utils.monthly_range(year, month)
    elif yearly is not None:
        return date_utils.yearly_range(yearly)
    return None


# ── 개별 서비스 커맨드 ──


@app.command()
def fetch(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today or catch-up"
    ),
    type: str = typer.Option(None, "--type", "-t", help="prs, commits, or issues"),
    source: str = typer.Option(None, "--source", "-s", help="Data source (default: all enabled)"),
    since: str = typer.Option(None, help="Range start (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Range end (YYYY-MM-DD)"),
    weekly: str = typer.Option(None, help="YEAR-WEEK, e.g. 2026-7"),
    monthly: str = typer.Option(None, help="YEAR-MONTH, e.g. 2026-2"),
    yearly: int = typer.Option(None, help="Year, e.g. 2026"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-fetch even if data exists"),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel workers (default: 1)"),
) -> None:
    """Fetch PR/Commit/Issue data from GHES."""
    logger.info("Command: fetch date=%s types=%s force=%s", target_date, type, force)
    # 1. --type 검증
    types: set[str] | None = None
    if type is not None:
        if type not in VALID_TYPES:
            _echo(f"Invalid type: {type}. Must be one of {VALID_TYPES}", err=True)
            raise typer.Exit(code=1)
        types = {type}

    # 2. 날짜 범위 결정
    dates = _resolve_dates(target_date, since, until, weekly, monthly, yearly)
    endpoints = _resolve_range_endpoints(target_date, since, until, weekly, monthly, yearly)

    # catch-up 모드
    catchup_endpoints: tuple[str, str] | None = None
    if dates is None:
        config = _get_config()
        last = _read_last_fetch_date(config)
        if last:
            s, u = date_utils.catchup_range(last)
            dates = date_utils.date_range(s, u)
            if not dates:
                _echo("Already up to date.")
                return
            catchup_endpoints = (s, u)
        else:
            dates = [date.today().isoformat()]

    # 3. Fetch 실행
    config = _get_config()
    pool = None
    try:
        with _get_ghes_client(config) as client:
            from workrecap.services.fetch_progress import FetchProgressStore

            ds = DailyStateStore(config.daily_state_path)
            progress_store = FetchProgressStore(config.state_dir / "fetch_progress")
            failed_store = FailedDateStore(
                config.state_dir / "failed_dates.json",
                max_retries=config.max_fetch_retries,
            )
            fetch_kwargs: dict = {
                "daily_state": ds,
                "progress_store": progress_store,
                "failed_date_store": failed_store,
            }
            if workers > 1:
                from workrecap.infra.client_pool import GHESClientPool

                pool = GHESClientPool(config.ghes_url, config.ghes_token, size=workers)
                fetch_kwargs["max_workers"] = workers
                fetch_kwargs["client_pool"] = pool
            service = FetcherService(config, client, **fetch_kwargs)

            # 다중 날짜 → fetch_range (월 단위 최적화)
            range_ep = endpoints or catchup_endpoints
            if len(dates) > 1 and range_ep:
                range_results = service.fetch_range(
                    range_ep[0],
                    range_ep[1],
                    types=types,
                    force=force,
                    progress=_progress,
                )
                succeeded = sum(1 for r in range_results if r["status"] == "success")
                skipped = sum(1 for r in range_results if r["status"] == "skipped")
                failed = sum(1 for r in range_results if r["status"] == "failed")
                _echo(
                    f"Fetched {len(range_results)} day(s): "
                    f"{succeeded} succeeded, {skipped} skipped, {failed} failed"
                )
                for r in range_results:
                    mark = {"success": "+", "skipped": "=", "failed": "!"}[r["status"]]
                    _echo(f"  {mark} {r['date']}: {r['status']}")
                # Report exhausted dates (max retries reached)
                exhausted = failed_store.exhausted_dates()
                if exhausted:
                    _echo(
                        f"  {len(exhausted)} date(s) exhausted "
                        f"(max {config.max_fetch_retries} retries reached)"
                    )
                if failed > 0:
                    raise typer.Exit(code=1)
            else:
                # 단일 날짜
                result = service.fetch(dates[0], types=types)
                _echo("Fetched 1 day(s)")
                for type_name, path in sorted(result.items()):
                    _echo(f"  {dates[0]} {type_name}: {path}")
    except WorkRecapError as e:
        _handle_error(e)
    finally:
        if pool is not None:
            pool.close()


def _print_range_results(label: str, range_results: list[dict]) -> None:
    """Range 결과를 succeeded/skipped/failed 카운트 + 날짜별 마크로 출력."""
    succeeded = sum(1 for r in range_results if r["status"] == "success")
    skipped = sum(1 for r in range_results if r["status"] == "skipped")
    failed = sum(1 for r in range_results if r["status"] == "failed")
    _echo(
        f"{label} {len(range_results)} day(s): "
        f"{succeeded} succeeded, {skipped} skipped, {failed} failed"
    )
    for r in range_results:
        mark = {"success": "+", "skipped": "=", "failed": "!"}[r["status"]]
        _echo(f"  {mark} {r['date']}: {r['status']}")
    if failed > 0:
        raise typer.Exit(code=1)


@app.command()
def normalize(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today or catch-up"
    ),
    since: str = typer.Option(None, help="Range start (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Range end (YYYY-MM-DD)"),
    weekly: str = typer.Option(None, help="YEAR-WEEK, e.g. 2026-7"),
    monthly: str = typer.Option(None, help="YEAR-MONTH, e.g. 2026-2"),
    yearly: int = typer.Option(None, help="Year, e.g. 2026"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-normalize even if data exists"),
    enrich: bool = typer.Option(
        True, "--enrich/--no-enrich", help="Enrich activities with LLM (change_summary, intent)"
    ),
    workers: int = typer.Option(None, "--workers", "-w", help="Parallel workers (default: config)"),
    batch: bool = typer.Option(False, "--batch/--no-batch", help="Use batch API for LLM calls"),
) -> None:
    """Normalize raw PR data into activities and stats."""
    logger.info(
        "Command: normalize date=%s force=%s enrich=%s batch=%s",
        target_date,
        force,
        enrich,
        batch,
    )
    dates = _resolve_dates(target_date, since, until, weekly, monthly, yearly)
    endpoints = _resolve_range_endpoints(target_date, since, until, weekly, monthly, yearly)

    # catch-up 모드
    catchup_endpoints: tuple[str, str] | None = None
    if dates is None:
        config = _get_config()
        last = _read_last_normalize_date(config)
        if last:
            s, u = date_utils.catchup_range(last)
            dates = date_utils.date_range(s, u)
            if not dates:
                _echo("Already up to date.")
                return
            catchup_endpoints = (s, u)
        else:
            dates = [date.today().isoformat()]

    config = _get_config()
    max_workers = workers if workers is not None else config.max_workers

    try:
        ds = DailyStateStore(config.daily_state_path)
        llm = _get_llm_router(config) if enrich else None
        service = NormalizerService(config, daily_state=ds, llm=llm)

        range_ep = endpoints or catchup_endpoints
        if range_ep:
            range_results = service.normalize_range(
                range_ep[0],
                range_ep[1],
                force=force,
                progress=_progress,
                max_workers=max_workers,
                batch=batch,
            )
            _print_range_results("Normalized", range_results)
        else:
            act_path, stats_path, _, _ = service.normalize(dates[0])
            _echo("Normalized 1 day(s)")
            _echo(f"  {dates[0]}: {act_path}, {stats_path}")
        if llm:
            _print_usage_report(llm)
    except WorkRecapError as e:
        _handle_error(e)


# ── Summarize 서브커맨드 ──


@summarize_app.command("daily")
def summarize_daily(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today or catch-up"
    ),
    since: str = typer.Option(None, help="Range start (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Range end (YYYY-MM-DD)"),
    weekly: str = typer.Option(None, help="YEAR-WEEK, e.g. 2026-7"),
    monthly: str = typer.Option(None, help="YEAR-MONTH, e.g. 2026-2"),
    yearly: int = typer.Option(None, help="Year, e.g. 2026"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-summarize even if data exists"),
    workers: int = typer.Option(None, "--workers", "-w", help="Parallel workers (default: config)"),
    batch: bool = typer.Option(False, "--batch/--no-batch", help="Use batch API for LLM calls"),
) -> None:
    """Generate daily summary."""
    logger.info("Command: summarize daily date=%s force=%s batch=%s", target_date, force, batch)
    dates = _resolve_dates(target_date, since, until, weekly, monthly, yearly)
    endpoints = _resolve_range_endpoints(target_date, since, until, weekly, monthly, yearly)

    # catch-up 모드
    catchup_endpoints: tuple[str, str] | None = None
    if dates is None:
        config = _get_config()
        last = _read_last_summarize_date(config)
        if last:
            s, u = date_utils.catchup_range(last)
            dates = date_utils.date_range(s, u)
            if not dates:
                _echo("Already up to date.")
                return
            catchup_endpoints = (s, u)
        else:
            dates = [date.today().isoformat()]

    config = _get_config()
    max_workers = workers if workers is not None else config.max_workers

    try:
        llm = _get_llm_router(config)
        ds = DailyStateStore(config.daily_state_path)
        service = SummarizerService(config, llm, daily_state=ds)

        range_ep = endpoints or catchup_endpoints
        if range_ep:
            range_results = service.daily_range(
                range_ep[0],
                range_ep[1],
                force=force,
                progress=_progress,
                max_workers=max_workers,
                batch=batch,
            )
            _print_range_results("Daily summary", range_results)
        else:
            path = service.daily(dates[0])
            _echo(f"Daily summary → {path}")
        _print_usage_report(llm)
    except WorkRecapError as e:
        _handle_error(e)


@summarize_app.command("weekly")
def summarize_weekly(
    year: int = typer.Argument(help="Year"),
    week: int = typer.Argument(help="ISO week number"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-generate even if exists"),
) -> None:
    """Generate weekly summary."""
    logger.info("Command: summarize weekly year=%d week=%d force=%s", year, week, force)
    config = _get_config()

    try:
        llm = _get_llm_router(config)
        service = SummarizerService(config, llm)
        path = service.weekly(year, week, force=force)
        _echo(f"Weekly summary → {path}")
        _print_usage_report(llm)
    except WorkRecapError as e:
        _handle_error(e)


@summarize_app.command("monthly")
def summarize_monthly(
    year: int = typer.Argument(help="Year"),
    month: int = typer.Argument(help="Month (1-12)"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-generate even if exists"),
) -> None:
    """Generate monthly summary."""
    logger.info("Command: summarize monthly year=%d month=%d force=%s", year, month, force)
    config = _get_config()

    try:
        llm = _get_llm_router(config)
        service = SummarizerService(config, llm)
        path = service.monthly(year, month, force=force)
        _echo(f"Monthly summary → {path}")
        _print_usage_report(llm)
    except WorkRecapError as e:
        _handle_error(e)


@summarize_app.command("yearly")
def summarize_yearly(
    year: int = typer.Argument(help="Year"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-generate even if exists"),
) -> None:
    """Generate yearly summary."""
    logger.info("Command: summarize yearly year=%d force=%s", year, force)
    config = _get_config()

    try:
        llm = _get_llm_router(config)
        service = SummarizerService(config, llm)
        path = service.yearly(year, force=force)
        _echo(f"Yearly summary → {path}")
        _print_usage_report(llm)
    except WorkRecapError as e:
        _handle_error(e)


# ── 파이프라인 ──


@app.command()
def run(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today or catch-up"
    ),
    type: str = typer.Option(None, "--type", "-t", help="prs, commits, or issues"),
    source: str = typer.Option(None, "--source", "-s", help="Data source (default: all enabled)"),
    since: str = typer.Option(None, help="Range start (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Range end (YYYY-MM-DD)"),
    weekly: str = typer.Option(None, help="YEAR-WEEK, e.g. 2026-7"),
    monthly: str = typer.Option(None, help="YEAR-MONTH, e.g. 2026-2"),
    yearly: int = typer.Option(None, help="Year, e.g. 2026"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-run even if data exists"),
    enrich: bool = typer.Option(
        True, "--enrich/--no-enrich", help="Enrich activities with LLM (change_summary, intent)"
    ),
    workers: int = typer.Option(None, "--workers", "-w", help="Parallel workers (default: config)"),
    batch: bool = typer.Option(False, "--batch/--no-batch", help="Use batch API for LLM calls"),
) -> None:
    """Run full pipeline (fetch → normalize → summarize)."""
    logger.info(
        "Command: run date=%s types=%s force=%s enrich=%s batch=%s",
        target_date,
        type,
        force,
        enrich,
        batch,
    )
    # --type 검증
    types: set[str] | None = None
    if type is not None:
        if type not in VALID_TYPES:
            _echo(f"Invalid type: {type}. Must be one of {VALID_TYPES}", err=True)
            raise typer.Exit(code=1)
        types = {type}

    dates = _resolve_dates(target_date, since, until, weekly, monthly, yearly)
    endpoints = _resolve_range_endpoints(target_date, since, until, weekly, monthly, yearly)

    # catch-up 모드
    catchup_endpoints: tuple[str, str] | None = None
    if dates is None:
        config = _get_config()
        last = _read_last_summarize_date(config)
        if last:
            s, u = date_utils.catchup_range(last)
            dates = date_utils.date_range(s, u)
            if not dates:
                _echo("Already up to date.")
                return
            catchup_endpoints = (s, u)
        else:
            dates = [date.today().isoformat()]

    config = _get_config()
    max_workers = workers if workers is not None else config.max_workers
    pool = None
    storage = None

    try:
        from workrecap.services.fetch_progress import FetchProgressStore

        ghes = _get_ghes_client(config)
        llm = _get_llm_router(config)
        ds = DailyStateStore(config.daily_state_path)
        progress_store = FetchProgressStore(config.state_dir / "fetch_progress")
        failed_store = FailedDateStore(
            config.state_dir / "failed_dates.json",
            max_retries=config.max_fetch_retries,
        )

        fetch_kwargs: dict = {
            "daily_state": ds,
            "progress_store": progress_store,
            "failed_date_store": failed_store,
        }
        if max_workers > 1:
            from workrecap.infra.client_pool import GHESClientPool

            pool = GHESClientPool(config.ghes_url, config.ghes_token, size=max_workers)
            fetch_kwargs["max_workers"] = max_workers
            fetch_kwargs["client_pool"] = pool

        fetcher = FetcherService(config, ghes, **fetch_kwargs)
        normalizer = NormalizerService(config, daily_state=ds, llm=llm if enrich else None)
        summarizer = SummarizerService(config, llm, daily_state=ds)

        # Storage (graceful degradation — 실패해도 파이프라인 계속)
        storage = None
        try:
            storage = _get_storage_service(config)
        except Exception as e:
            logger.warning("Storage init failed, continuing without DB: %s", e)

        orchestrator = OrchestratorService(
            fetcher, normalizer, summarizer, config=config, storage=storage
        )

        range_ep = endpoints or catchup_endpoints
        if range_ep:
            results = orchestrator.run_range(
                range_ep[0],
                range_ep[1],
                force=force,
                types=types,
                progress=_progress,
                max_workers=max_workers,
                batch=batch,
            )
            succeeded = sum(1 for r in results if r["status"] == "success")
            skipped = sum(1 for r in results if r["status"] == "skipped")
            failed = sum(1 for r in results if r["status"] == "failed")
            _echo(f"Range complete: {succeeded} succeeded, {skipped} skipped, {failed} failed")
            for r in results:
                mark = {"success": "\u2713", "skipped": "\u2014", "failed": "\u2717"}.get(
                    r["status"], "?"
                )
                msg = r.get("path", r.get("error", ""))
                _echo(f"  {mark} {r['date']}: {msg}")
            ghes.close()

            # Hierarchical summarization after daily pipeline
            if failed == 0:
                if weekly:
                    yr, wk = _parse_weekly(weekly)
                    path = summarizer.weekly(yr, wk, force=force)
                    _echo(f"Weekly summary → {path}")
                elif monthly:
                    yr, mo = _parse_monthly(monthly)
                    for wy, ww in _weeks_in_month(yr, mo):
                        try:
                            summarizer.weekly(wy, ww, force=force)
                        except SummarizeError:
                            pass
                    path = summarizer.monthly(yr, mo, force=force)
                    _echo(f"Monthly summary → {path}")
                elif yearly is not None:
                    for mo in range(1, 13):
                        for wy, ww in _weeks_in_month(yearly, mo):
                            try:
                                summarizer.weekly(wy, ww, force=force)
                            except SummarizeError:
                                pass
                        try:
                            summarizer.monthly(yearly, mo, force=force)
                        except SummarizeError:
                            pass
                    path = summarizer.yearly(yearly, force=force)
                    _echo(f"Yearly summary → {path}")

            # Report exhausted dates (max retries reached)
            exhausted = failed_store.exhausted_dates()
            if exhausted:
                _echo(
                    f"  {len(exhausted)} date(s) exhausted "
                    f"(max {config.max_fetch_retries} retries reached)"
                )

            _print_usage_report(llm)
            if failed > 0:
                raise typer.Exit(code=1)
        else:
            path = orchestrator.run_daily(dates[0], types=types)
            ghes.close()
            _echo(f"Pipeline complete → {path}")
            _print_usage_report(llm)

    except WorkRecapError as e:
        _handle_error(e)
    finally:
        if pool is not None:
            pool.close()
        if storage is not None:
            try:
                storage.close_sync()
            except Exception:
                pass


# ── 자유 질문 ──


@app.command()
def ask(
    question: str = typer.Argument(help="Question to ask"),
    months: int = typer.Option(3, help="Months of context to use"),
) -> None:
    """Ask a question based on recent summaries."""
    logger.info("Command: ask months=%d", months)
    config = _get_config()

    try:
        llm = _get_llm_router(config)
        service = SummarizerService(config, llm)
        answer = service.query(question, months_back=months)
        _echo(answer)
        _print_usage_report(llm)
    except WorkRecapError as e:
        _handle_error(e)


# ── 모델 탐색 ──


@app.command()
def models() -> None:
    """List available models from configured providers."""
    config = _get_config()
    router = _get_llm_router(config)

    # Create providers for all configured entries
    providers = {}
    for name in router._config.providers:
        try:
            providers[name] = router._get_provider(name)
        except Exception:
            pass

    model_list = discover_models(providers)
    if not model_list:
        _echo("No models discovered. Check provider configuration.")
        return

    current_provider = ""
    for m in model_list:
        if m.provider != current_provider:
            current_provider = m.provider
            _echo(f"\n[{current_provider}]")
        _echo(f"  {m.id:40s}  {m.name}")


# ── Storage 관리 ──


def _get_storage_service(config: AppConfig):
    from workrecap.infra.postgres_client import PostgresClient
    from workrecap.infra.vector_client import VectorDBClient
    from workrecap.infra.embedding_client import EmbeddingClient
    from workrecap.services.storage import StorageService

    pg = PostgresClient(config)
    vdb = VectorDBClient(config)
    emb = EmbeddingClient(config)
    return StorageService(pg, vdb, emb)


@storage_app.command("init-db")
def storage_init_db() -> None:
    """Initialize PostgreSQL tables."""
    config = _get_config()
    storage = _get_storage_service(config)
    storage.init_db_sync()
    storage.close_sync()
    _echo("Database initialized.")


@storage_app.command("sync")
def storage_sync(
    since: str = typer.Option(None, help="Start date (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="End date (YYYY-MM-DD)"),
) -> None:
    """Sync existing file data to PostgreSQL and ChromaDB."""
    from workrecap.models import load_json, load_jsonl

    config = _get_config()
    storage = _get_storage_service(config)
    storage.init_db_sync()

    _echo("Starting sync from files to database...")

    # 1. Activities & Stats
    norm_root = config.normalized_dir
    if norm_root.exists():
        for year_dir in sorted(norm_root.iterdir()):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for month_dir in sorted(year_dir.iterdir()):
                if not month_dir.is_dir() or not month_dir.name.isdigit():
                    continue
                for day_dir in sorted(month_dir.iterdir()):
                    if not day_dir.is_dir() or not day_dir.name.isdigit():
                        continue

                    date_str = f"{year_dir.name}-{month_dir.name}-{day_dir.name}"
                    if since and date_str < since:
                        continue
                    if until and date_str > until:
                        continue

                    _echo(f"  Syncing activities {date_str}...")
                    try:
                        acts = load_jsonl(day_dir / "activities.jsonl")
                        stats = load_json(day_dir / "stats.json")
                        storage.save_activities_sync(date_str, acts, stats)
                    except Exception as e:
                        _echo(f"  Failed {date_str}: {e}", err=True)

    # 2. Summaries
    summ_root = config.summaries_dir
    if summ_root.exists():
        for year_dir in sorted(summ_root.iterdir()):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue

            # Daily
            daily_dir = year_dir / "daily"
            if daily_dir.exists():
                for f in sorted(daily_dir.glob("*.md")):
                    date_key = f"{year_dir.name}-{f.stem}"
                    if since and date_key < since:
                        continue
                    if until and date_key > until:
                        continue
                    _echo(f"  Syncing daily summary {date_key}...")
                    content = f.read_text(encoding="utf-8")
                    storage.save_summary_sync("daily", date_key, content)

            # Weekly
            weekly_dir = year_dir / "weekly"
            if weekly_dir.exists():
                for f in sorted(weekly_dir.glob("*.md")):
                    date_key = f"{year_dir.name}-{f.stem}"
                    _echo(f"  Syncing weekly summary {date_key}...")
                    content = f.read_text(encoding="utf-8")
                    storage.save_summary_sync("weekly", date_key, content)

            # Monthly
            monthly_dir = year_dir / "monthly"
            if monthly_dir.exists():
                for f in sorted(monthly_dir.glob("*.md")):
                    date_key = f"{year_dir.name}-{f.stem}"
                    _echo(f"  Syncing monthly summary {date_key}...")
                    content = f.read_text(encoding="utf-8")
                    storage.save_summary_sync("monthly", date_key, content)

            # Yearly
            yearly_file = year_dir / "yearly.md"
            if yearly_file.exists():
                _echo(f"  Syncing yearly summary {year_dir.name}...")
                content = yearly_file.read_text(encoding="utf-8")
                storage.save_summary_sync("yearly", year_dir.name, content)

    storage.close_sync()
    _echo("Sync complete.")


@storage_app.command("search")
def storage_search(
    query: str = typer.Argument(help="Search query"),
    n: int = typer.Option(5, "--results", "-n", help="Number of results"),
) -> None:
    """Search summaries using semantic search."""
    config = _get_config()
    storage = _get_storage_service(config)
    results = storage.search_summaries_sync(query, n_results=n)

    if not results:
        _echo("No results found.")
    else:
        for i, res in enumerate(results, 1):
            dist = res.get("distance")
            dist_str = f" (Distance: {dist:.4f})" if dist is not None else ""
            _echo(f"\n[{i}] {res['id']}{dist_str}")
            _echo("-" * 40)
            content = res["content"]
            if len(content) > 300:
                content = content[:300] + "..."
            _echo(content)

    storage.close_sync()
