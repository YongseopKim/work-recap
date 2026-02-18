# Phase 5: CLI 상세 설계

## 목적

Typer 기반 CLI로 각 서비스를 개별 또는 파이프라인으로 실행한다.
에러 발생 시 stderr 출력 + exit code 1로 종료한다.

---

## 위치

`src/workrecap/cli/main.py`

## 의존성

- `typer`
- `workrecap.config.AppConfig`
- `workrecap.infra.ghes_client.GHESClient`
- `workrecap.infra.llm_client.LLMClient`
- `workrecap.services.fetcher.FetcherService`
- `workrecap.services.normalizer.NormalizerService`
- `workrecap.services.summarizer.SummarizerService`
- `workrecap.services.orchestrator.OrchestratorService`
- `workrecap.services.date_utils`
- `workrecap.exceptions.WorkRecapError`

---

## 커맨드 구조

```
recap fetch [DATE] [--type TYPE] [--force] [--since/--until | --weekly | --monthly | --yearly]
recap normalize [DATE] [--since/--until | --weekly | --monthly | --yearly]
recap summarize daily [DATE] [--since/--until | --weekly | --monthly | --yearly]
recap summarize weekly YEAR WEEK       # Weekly summary 생성
recap summarize monthly YEAR MONTH     # Monthly summary 생성
recap summarize yearly YEAR            # Yearly summary 생성
recap run [DATE]                       # 전체 파이프라인 (단일 날짜)
recap run --since SINCE --until UNTIL  # 기간 범위 backfill
recap ask QUESTION [--months N]        # 자유 질문
```

DATE 기본값: 오늘 날짜 (fetch는 catch-up 모드 지원)

### 공통 날짜 범위 옵션

fetch, normalize, summarize daily는 아래 날짜 범위 옵션을 공유한다 (상호 배타):

| 옵션 | 형식 | 설명 |
|---|---|---|
| `[DATE]` | YYYY-MM-DD | 단일 날짜 |
| `--since/--until` | YYYY-MM-DD | 시작~종료 범위 (inclusive, 쌍으로 사용) |
| `--weekly` | YEAR-WEEK | ISO 주 번호 (월~일) |
| `--monthly` | YEAR-MONTH | 해당 월 전체 |
| `--yearly` | YEAR | 해당 연도 전체 |

fetch 전용: `--type TYPE` (prs, commits, issues), `--force` / `-f` (기존 데이터 무시 재수집), 인자 없으면 catch-up 모드

### 다중 날짜 최적화 (fetch_range)

다중 날짜 범위(`--since/--until`, `--weekly`, `--monthly`, `--yearly`, catch-up)일 때
`fetch_range()`를 사용하여 월 단위 Search API 호출로 최적화한다.
출력 형식: `Fetched N day(s): X succeeded, Y skipped, Z failed`

단일 날짜는 기존 `fetch()` 유지.

---

## 상세 구현

```python
"""work-recap CLI — Typer 기반."""

import json
from datetime import date
from pathlib import Path

import typer

from workrecap.config import AppConfig
from workrecap.exceptions import WorkRecapError
from workrecap.services import date_utils
from workrecap.services.fetcher import FetcherService
from workrecap.services.normalizer import NormalizerService
from workrecap.services.orchestrator import OrchestratorService
from workrecap.services.summarizer import SummarizerService

app = typer.Typer(help="GHES activity summarizer with LLM")
summarize_app = typer.Typer(help="Generate summaries")
app.add_typer(summarize_app, name="summarize")

VALID_TYPES = {"prs", "commits", "issues"}


def _get_config() -> AppConfig:
    return AppConfig()


def _get_ghes_client(config: AppConfig):
    from workrecap.infra.ghes_client import GHESClient
    return GHESClient(config.ghes_url, config.ghes_token)


def _get_llm_client(config: AppConfig):
    from workrecap.infra.llm_client import LLMClient
    return LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)


def _handle_error(e: WorkRecapError) -> None:
    """에러 메시지를 stderr에 출력하고 exit(1)."""
    typer.echo(f"Error: {e}", err=True)
    raise typer.Exit(code=1)


def _read_last_fetch_date(config: AppConfig) -> str | None:
    """checkpoint 파일에서 마지막 fetch 날짜를 읽는다."""
    cp_path = config.checkpoints_path
    if not cp_path.exists():
        return None
    with open(cp_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("last_fetch_date")


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
    """공통 날짜 범위 헬퍼. 상호 배타 검증 + 날짜 리스트 반환. 인자 모두 None이면 None."""
    range_opts = sum([
        target_date is not None,
        since is not None or until is not None,
        weekly is not None,
        monthly is not None,
        yearly is not None,
    ])
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


# ── 개별 서비스 커맨드 ──

@app.command()
def fetch(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today or catch-up"
    ),
    type: str = typer.Option(
        None, "--type", "-t", help="prs, commits, or issues"
    ),
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
            service = FetcherService(config, client)

            # 다중 날짜 → fetch_range (월 단위 최적화)
            range_ep = endpoints or catchup_endpoints
            if len(dates) > 1 and range_ep:
                range_results = service.fetch_range(
                    range_ep[0], range_ep[1], types=types, force=force,
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
    except WorkRecapError as e:
        _handle_error(e)


@app.command()
def normalize(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today"
    ),
    since: str = typer.Option(None, help="Range start (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Range end (YYYY-MM-DD)"),
    weekly: str = typer.Option(None, help="YEAR-WEEK, e.g. 2026-7"),
    monthly: str = typer.Option(None, help="YEAR-MONTH, e.g. 2026-2"),
    yearly: int = typer.Option(None, help="Year, e.g. 2026"),
) -> None:
    """Normalize raw PR data into activities and stats."""
    dates = _resolve_dates(target_date, since, until, weekly, monthly, yearly)
    if dates is None:
        dates = [date.today().isoformat()]

    config = _get_config()

    try:
        service = NormalizerService(config)
        results: list[tuple[str, Path, Path]] = []
        for d in dates:
            act_path, stats_path = service.normalize(d)
            results.append((d, act_path, stats_path))

        typer.echo(f"Normalized {len(dates)} day(s)")
        for d, act_path, stats_path in results:
            typer.echo(f"  {d}: {act_path}, {stats_path}")
    except WorkRecapError as e:
        _handle_error(e)


# ── Summarize 서브커맨드 ──

@summarize_app.command("daily")
def summarize_daily(
    target_date: str = typer.Argument(
        default=None, help="Target date (YYYY-MM-DD). Default: today"
    ),
    since: str = typer.Option(None, help="Range start (YYYY-MM-DD)"),
    until: str = typer.Option(None, help="Range end (YYYY-MM-DD)"),
    weekly: str = typer.Option(None, help="YEAR-WEEK, e.g. 2026-7"),
    monthly: str = typer.Option(None, help="YEAR-MONTH, e.g. 2026-2"),
    yearly: int = typer.Option(None, help="Year, e.g. 2026"),
) -> None:
    """Generate daily summary."""
    dates = _resolve_dates(target_date, since, until, weekly, monthly, yearly)
    if dates is None:
        dates = [date.today().isoformat()]

    config = _get_config()

    try:
        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        results: list[tuple[str, Path]] = []
        for d in dates:
            path = service.daily(d)
            results.append((d, path))

        if len(dates) > 1:
            typer.echo(f"Daily summary {len(dates)} day(s)")
            for d, path in results:
                typer.echo(f"  {d}: {path}")
        else:
            typer.echo(f"Daily summary → {results[0][1]}")
    except WorkRecapError as e:
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
    except WorkRecapError as e:
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
    except WorkRecapError as e:
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
    except WorkRecapError as e:
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
    except WorkRecapError as e:
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
    except WorkRecapError as e:
        _handle_error(e)
```

---

## 의존성 초기화 패턴

각 커맨드에서 서비스를 직접 생성한다 (DI 컨테이너 없음).
이유: CLI는 진입점이며 간단한 wiring만 필요.

```
커맨드 호출
  → AppConfig() 로드
  → 필요한 client/service 생성
  → 서비스 메서드 호출
  → 결과 출력 또는 에러 처리
```

서비스 import는 모듈 레벨에서 수행한다 (`@patch`가 동작하려면 모듈 레벨 이름이 필요).
GHESClient, LLMClient만 `_get_*_client()` 헬퍼 내에서 로컬 import — 이들은 mock 대상이 헬퍼 함수 자체이므로 문제없음.

테스트에서는 `_get_config`, `_get_ghes_client`, `_get_llm_client`를 monkeypatch하여
실제 .env 파일이나 외부 API 없이 CLI 동작을 검증한다.

---

## 테스트 명세

### test_cli.py

`typer.testing.CliRunner`를 사용하여 CLI 커맨드를 검증한다.
서비스는 `@patch` + monkeypatch로 mock한다.

```python
"""tests/unit/test_cli.py"""

class TestFetch:
    def test_fetch_with_date(self):
        """recap fetch 2025-02-16 → FetcherService.fetch 호출."""
    def test_fetch_default_today(self):
        """날짜 미지정 + checkpoint 없음 → 오늘 날짜 사용."""
    def test_fetch_error(self):
        """FetchError → exit code 1 + stderr 메시지."""

class TestFetchTypeFilter:
    def test_type_prs(self):
        """--type prs → types={"prs"} 전달."""
    def test_type_commits(self):
        """--type commits → types={"commits"} 전달."""
    def test_type_issues(self):
        """--type issues → types={"issues"} 전달."""
    def test_type_invalid(self):
        """--type invalid → exit code 1."""

class TestFetchDateRange:
    def test_since_until(self):
        """--since/--until → 3일, fetch 3회 호출."""
    def test_since_without_until(self):
        """--since만 → exit 1."""
    def test_until_without_since(self):
        """--until만 → exit 1."""

class TestFetchWeekly:
    def test_weekly_option(self):
        """--weekly 2026-7 → 7일 호출."""

class TestFetchMonthly:
    def test_monthly_option(self):
        """--monthly 2026-2 → 28일 호출."""

class TestFetchYearly:
    def test_yearly_option(self):
        """--yearly 2026 → 365일 호출."""

class TestFetchCatchUp:
    def test_no_args_no_checkpoint(self):
        """인자 없고 checkpoint 없으면 오늘만 fetch."""
    def test_no_args_with_checkpoint(self):
        """인자 없고 checkpoint 있으면 catch-up (이후 날짜들)."""
    def test_type_with_catchup(self):
        """--type + catch-up 결합."""

class TestFetchMutualExclusion:
    def test_target_date_with_since_until(self):
        """target_date + --since/--until → exit 1."""
    def test_weekly_with_monthly(self):
        """--weekly + --monthly → exit 1."""

class TestFetchOutput:
    def test_output_shows_all_types(self):
        """단일 날짜 출력에 prs, commits, issues 표시."""
    def test_output_shows_date_count(self):
        """범위 출력에 날짜 수 표시."""

class TestNormalize:
    def test_normalize_with_date(self):
        """recap normalize 2025-02-16 → 성공 메시지."""
    def test_normalize_error(self):
        """NormalizeError → exit code 1."""

class TestNormalizeDateRange:
    def test_normalize_since_until(self):
        """--since/--until → 3일, normalize 3회 호출."""
    def test_normalize_weekly(self):
        """--weekly → 7일 호출."""
    def test_normalize_monthly(self):
        """--monthly → 28일 호출."""
    def test_normalize_yearly(self):
        """--yearly → 365일 호출."""
    def test_normalize_since_without_until(self):
        """--since만 → exit 1."""
    def test_normalize_mutual_exclusion(self):
        """target_date + --weekly → exit 1."""
    def test_normalize_output_shows_date_count(self):
        """출력에 날짜 수와 각 날짜 표시."""

class TestSummarizeDailyDateRange:
    def test_summarize_daily_since_until(self):
        """--since/--until → 3일, daily 3회 호출."""
    def test_summarize_daily_weekly(self):
        """--weekly → 7일 호출."""
    def test_summarize_daily_monthly(self):
        """--monthly → 28일 호출."""
    def test_summarize_daily_mutual_exclusion(self):
        """target_date + --weekly → exit 1."""

class TestSummarize:
    def test_summarize_daily(self):
        """recap summarize daily 2025-02-16 → 단일 날짜 출력."""
    def test_summarize_weekly(self):
        """recap summarize weekly 2025 7."""
    def test_summarize_monthly(self):
        """recap summarize monthly 2025 2."""
    def test_summarize_yearly(self):
        """recap summarize yearly 2025."""
    def test_summarize_error(self):
        """SummarizeError → exit code 1."""

class TestRun:
    def test_run_single_date(self):
        """recap run 2025-02-16 → 파이프라인 완료 메시지."""
    def test_run_range(self):
        """recap run --since X --until Y → 범위 결과."""
    def test_run_range_partial_failure(self):
        """일부 날짜 실패 → exit code 1 + 결과 표시."""
    def test_run_error(self):
        """파이프라인 에러 → exit code 1."""

class TestAsk:
    def test_ask_question(self):
        """recap ask "질문" → LLM 응답 출력."""
    def test_ask_error(self):
        """context 없으면 exit code 1."""
    def test_ask_with_months_option(self):
        """--months 옵션 전달."""
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 5.1 | Typer app 기본 구조 + `_get_config`, `_handle_error` 헬퍼 | - |
| 5.2 | `fetch` 커맨드 (단일 날짜) | TestFetch |
| 5.3 | `fetch` --type 옵션 | TestFetchTypeFilter |
| 5.4 | `fetch` 날짜 범위 (--since/--until, --weekly, --monthly, --yearly) | TestFetchDateRange, Weekly, Monthly, Yearly |
| 5.5 | `fetch` catch-up 모드 (checkpoint 기반) | TestFetchCatchUp |
| 5.6 | `fetch` 상호 배타 검증 | TestFetchMutualExclusion |
| 5.7 | `_resolve_dates()` 공통 헬퍼 추출 | (fetch/normalize/summarize daily에서 공유) |
| 5.8 | `normalize` 커맨드 + 날짜 범위 | TestNormalize, TestNormalizeDateRange |
| 5.9 | `summarize` 서브커맨드 (daily + 날짜 범위, weekly, monthly, yearly) | TestSummarize, TestSummarizeDailyDateRange |
| 5.10 | `run` 커맨드 (단일 날짜 + --since/--until) | TestRun |
| 5.11 | `ask` 커맨드 | TestAsk |
