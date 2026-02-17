"""git-recap CLI — Typer 기반."""

import json
from datetime import date

import typer

from git_recap.config import AppConfig
from git_recap.exceptions import GitRecapError
from git_recap.services import date_utils
from git_recap.services.daily_state import DailyStateStore
from git_recap.services.fetcher import FetcherService
from git_recap.services.normalizer import NormalizerService
from git_recap.services.orchestrator import OrchestratorService
from git_recap.services.summarizer import SummarizerService

app = typer.Typer(help="GHES activity summarizer with LLM")
summarize_app = typer.Typer(help="Generate summaries")
app.add_typer(summarize_app, name="summarize")

VALID_TYPES = {"prs", "commits", "issues"}


def _get_config() -> AppConfig:
    return AppConfig()


def _get_ghes_client(config: AppConfig):
    from git_recap.infra.ghes_client import GHESClient

    return GHESClient(config.ghes_url, config.ghes_token)


def _get_llm_client(config: AppConfig):
    from git_recap.infra.llm_client import LLMClient

    return LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)


def _handle_error(e: GitRecapError) -> None:
    typer.echo(f"Error: {e}", err=True)
    raise typer.Exit(code=1)


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
        typer.echo(
            "Error: Only one of target_date, --since/--until, --weekly, "
            "--monthly, --yearly can be specified.",
            err=True,
        )
        raise typer.Exit(code=1)

    if (since is None) != (until is None):
        typer.echo("Error: --since and --until must be used together.", err=True)
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
    since: str = typer.Option(None, help="Range start (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Range end (YYYY-MM-DD)"),
    weekly: str = typer.Option(None, help="YEAR-WEEK, e.g. 2026-7"),
    monthly: str = typer.Option(None, help="YEAR-MONTH, e.g. 2026-2"),
    yearly: int = typer.Option(None, help="Year, e.g. 2026"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-fetch even if data exists"),
) -> None:
    """Fetch PR/Commit/Issue data from GHES."""
    # 1. --type 검증
    types: set[str] | None = None
    if type is not None:
        if type not in VALID_TYPES:
            typer.echo(f"Invalid type: {type}. Must be one of {VALID_TYPES}", err=True)
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
                typer.echo("Already up to date.")
                return
            catchup_endpoints = (s, u)
        else:
            dates = [date.today().isoformat()]

    # 3. Fetch 실행
    config = _get_config()
    try:
        with _get_ghes_client(config) as client:
            ds = DailyStateStore(config.daily_state_path)
            service = FetcherService(config, client, daily_state=ds)

            # 다중 날짜 → fetch_range (월 단위 최적화)
            range_ep = endpoints or catchup_endpoints
            if len(dates) > 1 and range_ep:
                range_results = service.fetch_range(
                    range_ep[0],
                    range_ep[1],
                    types=types,
                    force=force,
                )
                succeeded = sum(1 for r in range_results if r["status"] == "success")
                skipped = sum(1 for r in range_results if r["status"] == "skipped")
                failed = sum(1 for r in range_results if r["status"] == "failed")
                typer.echo(
                    f"Fetched {len(range_results)} day(s): "
                    f"{succeeded} succeeded, {skipped} skipped, {failed} failed"
                )
                for r in range_results:
                    mark = {"success": "+", "skipped": "=", "failed": "!"}[r["status"]]
                    typer.echo(f"  {mark} {r['date']}: {r['status']}")
                if failed > 0:
                    raise typer.Exit(code=1)
            else:
                # 단일 날짜
                result = service.fetch(dates[0], types=types)
                typer.echo("Fetched 1 day(s)")
                for type_name, path in sorted(result.items()):
                    typer.echo(f"  {dates[0]} {type_name}: {path}")
    except GitRecapError as e:
        _handle_error(e)


def _print_range_results(label: str, range_results: list[dict]) -> None:
    """Range 결과를 succeeded/skipped/failed 카운트 + 날짜별 마크로 출력."""
    succeeded = sum(1 for r in range_results if r["status"] == "success")
    skipped = sum(1 for r in range_results if r["status"] == "skipped")
    failed = sum(1 for r in range_results if r["status"] == "failed")
    typer.echo(
        f"{label} {len(range_results)} day(s): "
        f"{succeeded} succeeded, {skipped} skipped, {failed} failed"
    )
    for r in range_results:
        mark = {"success": "+", "skipped": "=", "failed": "!"}[r["status"]]
        typer.echo(f"  {mark} {r['date']}: {r['status']}")
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
) -> None:
    """Normalize raw PR data into activities and stats."""
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
                typer.echo("Already up to date.")
                return
            catchup_endpoints = (s, u)
        else:
            dates = [date.today().isoformat()]

    config = _get_config()

    try:
        ds = DailyStateStore(config.daily_state_path)
        service = NormalizerService(config, daily_state=ds)

        range_ep = endpoints or catchup_endpoints
        if range_ep:
            range_results = service.normalize_range(
                range_ep[0],
                range_ep[1],
                force=force,
            )
            _print_range_results("Normalized", range_results)
        else:
            act_path, stats_path = service.normalize(dates[0])
            typer.echo("Normalized 1 day(s)")
            typer.echo(f"  {dates[0]}: {act_path}, {stats_path}")
    except GitRecapError as e:
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
) -> None:
    """Generate daily summary."""
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
                typer.echo("Already up to date.")
                return
            catchup_endpoints = (s, u)
        else:
            dates = [date.today().isoformat()]

    config = _get_config()

    try:
        llm = _get_llm_client(config)
        ds = DailyStateStore(config.daily_state_path)
        service = SummarizerService(config, llm, daily_state=ds)

        range_ep = endpoints or catchup_endpoints
        if range_ep:
            range_results = service.daily_range(
                range_ep[0],
                range_ep[1],
                force=force,
            )
            _print_range_results("Daily summary", range_results)
        else:
            path = service.daily(dates[0])
            typer.echo(f"Daily summary → {path}")
    except GitRecapError as e:
        _handle_error(e)


@summarize_app.command("weekly")
def summarize_weekly(
    year: int = typer.Argument(help="Year"),
    week: int = typer.Argument(help="ISO week number"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-generate even if exists"),
) -> None:
    """Generate weekly summary."""
    config = _get_config()

    try:
        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        path = service.weekly(year, week, force=force)
        typer.echo(f"Weekly summary → {path}")
    except GitRecapError as e:
        _handle_error(e)


@summarize_app.command("monthly")
def summarize_monthly(
    year: int = typer.Argument(help="Year"),
    month: int = typer.Argument(help="Month (1-12)"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-generate even if exists"),
) -> None:
    """Generate monthly summary."""
    config = _get_config()

    try:
        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        path = service.monthly(year, month, force=force)
        typer.echo(f"Monthly summary → {path}")
    except GitRecapError as e:
        _handle_error(e)


@summarize_app.command("yearly")
def summarize_yearly(
    year: int = typer.Argument(help="Year"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-generate even if exists"),
) -> None:
    """Generate yearly summary."""
    config = _get_config()

    try:
        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        path = service.yearly(year, force=force)
        typer.echo(f"Yearly summary → {path}")
    except GitRecapError as e:
        _handle_error(e)


# ── 파이프라인 ──


@app.command()
def run(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today or catch-up"
    ),
    type: str = typer.Option(None, "--type", "-t", help="prs, commits, or issues"),
    since: str = typer.Option(None, help="Range start (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Range end (YYYY-MM-DD)"),
    weekly: str = typer.Option(None, help="YEAR-WEEK, e.g. 2026-7"),
    monthly: str = typer.Option(None, help="YEAR-MONTH, e.g. 2026-2"),
    yearly: int = typer.Option(None, help="Year, e.g. 2026"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-run even if data exists"),
) -> None:
    """Run full pipeline (fetch → normalize → summarize)."""
    # --type 검증
    types: set[str] | None = None
    if type is not None:
        if type not in VALID_TYPES:
            typer.echo(f"Invalid type: {type}. Must be one of {VALID_TYPES}", err=True)
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
                typer.echo("Already up to date.")
                return
            catchup_endpoints = (s, u)
        else:
            dates = [date.today().isoformat()]

    config = _get_config()

    try:
        ghes = _get_ghes_client(config)
        llm = _get_llm_client(config)
        ds = DailyStateStore(config.daily_state_path)
        fetcher = FetcherService(config, ghes, daily_state=ds)
        normalizer = NormalizerService(config, daily_state=ds)
        summarizer = SummarizerService(config, llm, daily_state=ds)
        orchestrator = OrchestratorService(fetcher, normalizer, summarizer, config=config)

        range_ep = endpoints or catchup_endpoints
        if range_ep:
            results = orchestrator.run_range(range_ep[0], range_ep[1], force=force, types=types)
            succeeded = sum(1 for r in results if r["status"] == "success")
            skipped = sum(1 for r in results if r["status"] == "skipped")
            failed = sum(1 for r in results if r["status"] == "failed")
            typer.echo(f"Range complete: {succeeded} succeeded, {skipped} skipped, {failed} failed")
            for r in results:
                mark = {"success": "✓", "skipped": "—", "failed": "✗"}.get(r["status"], "?")
                msg = r.get("path", r.get("error", ""))
                typer.echo(f"  {mark} {r['date']}: {msg}")
            ghes.close()
            if failed > 0:
                raise typer.Exit(code=1)
        else:
            path = orchestrator.run_daily(dates[0], types=types)
            ghes.close()
            typer.echo(f"Pipeline complete → {path}")
    except GitRecapError as e:
        _handle_error(e)


# ── 자유 질문 ──


@app.command()
def ask(
    question: str = typer.Argument(help="Question to ask"),
    months: int = typer.Option(3, help="Months of context to use"),
) -> None:
    """Ask a question based on recent summaries."""
    config = _get_config()

    try:
        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        answer = service.query(question, months_back=months)
        typer.echo(answer)
    except GitRecapError as e:
        _handle_error(e)
