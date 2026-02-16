"""git-recap CLI — Typer 기반."""

from datetime import date
from pathlib import Path

import typer

from git_recap.config import AppConfig
from git_recap.exceptions import GitRecapError
from git_recap.services.fetcher import FetcherService
from git_recap.services.normalizer import NormalizerService
from git_recap.services.orchestrator import OrchestratorService
from git_recap.services.summarizer import SummarizerService

app = typer.Typer(help="GHES activity summarizer with LLM")
summarize_app = typer.Typer(help="Generate summaries")
app.add_typer(summarize_app, name="summarize")


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


# ── 개별 서비스 커맨드 ──


@app.command()
def fetch(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today"
    ),
) -> None:
    """Fetch PR data from GHES for a specific date."""
    target_date = target_date or date.today().isoformat()
    config = _get_config()

    try:
        with _get_ghes_client(config) as client:
            service = FetcherService(config, client)
            path = service.fetch(target_date)
        typer.echo(f"Fetched → {path}")
    except GitRecapError as e:
        _handle_error(e)


@app.command()
def normalize(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today"
    ),
) -> None:
    """Normalize raw PR data into activities and stats."""
    target_date = target_date or date.today().isoformat()
    config = _get_config()

    try:
        service = NormalizerService(config)
        act_path, stats_path = service.normalize(target_date)
        typer.echo(f"Normalized → {act_path}, {stats_path}")
    except GitRecapError as e:
        _handle_error(e)


# ── Summarize 서브커맨드 ──


@summarize_app.command("daily")
def summarize_daily(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today"
    ),
) -> None:
    """Generate daily summary."""
    target_date = target_date or date.today().isoformat()
    config = _get_config()

    try:
        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        path = service.daily(target_date)
        typer.echo(f"Daily summary → {path}")
    except GitRecapError as e:
        _handle_error(e)


@summarize_app.command("weekly")
def summarize_weekly(
    year: int = typer.Argument(help="Year"),
    week: int = typer.Argument(help="ISO week number"),
) -> None:
    """Generate weekly summary."""
    config = _get_config()

    try:
        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        path = service.weekly(year, week)
        typer.echo(f"Weekly summary → {path}")
    except GitRecapError as e:
        _handle_error(e)


@summarize_app.command("monthly")
def summarize_monthly(
    year: int = typer.Argument(help="Year"),
    month: int = typer.Argument(help="Month (1-12)"),
) -> None:
    """Generate monthly summary."""
    config = _get_config()

    try:
        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        path = service.monthly(year, month)
        typer.echo(f"Monthly summary → {path}")
    except GitRecapError as e:
        _handle_error(e)


@summarize_app.command("yearly")
def summarize_yearly(
    year: int = typer.Argument(help="Year"),
) -> None:
    """Generate yearly summary."""
    config = _get_config()

    try:
        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        path = service.yearly(year)
        typer.echo(f"Yearly summary → {path}")
    except GitRecapError as e:
        _handle_error(e)


# ── 파이프라인 ──


@app.command()
def run(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today"
    ),
    since: str = typer.Option(None, help="Range start (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Range end (YYYY-MM-DD)"),
) -> None:
    """Run full pipeline (fetch → normalize → summarize)."""
    config = _get_config()

    try:
        ghes = _get_ghes_client(config)
        llm = _get_llm_client(config)
        fetcher = FetcherService(config, ghes)
        normalizer = NormalizerService(config)
        summarizer = SummarizerService(config, llm)
        orchestrator = OrchestratorService(fetcher, normalizer, summarizer)

        if since and until:
            results = orchestrator.run_range(since, until)
            succeeded = sum(1 for r in results if r["status"] == "success")
            typer.echo(f"Range complete: {succeeded}/{len(results)} succeeded")
            for r in results:
                status_mark = "✓" if r["status"] == "success" else "✗"
                msg = r.get("path", r.get("error", ""))
                typer.echo(f"  {status_mark} {r['date']}: {msg}")
            ghes.close()
            if succeeded < len(results):
                raise typer.Exit(code=1)
        else:
            target_date = target_date or date.today().isoformat()
            path = orchestrator.run_daily(target_date)
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
