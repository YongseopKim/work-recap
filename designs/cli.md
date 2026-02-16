# Phase 5: CLI 상세 설계

## 목적

Typer 기반 CLI로 각 서비스를 개별 또는 파이프라인으로 실행한다.
에러 발생 시 stderr 출력 + exit code 1로 종료한다.

---

## 위치

`src/git_recap/cli/main.py`

## 의존성

- `typer`
- `git_recap.config.AppConfig`
- `git_recap.infra.ghes_client.GHESClient`
- `git_recap.infra.llm_client.LLMClient`
- `git_recap.services.fetcher.FetcherService`
- `git_recap.services.normalizer.NormalizerService`
- `git_recap.services.summarizer.SummarizerService`
- `git_recap.services.orchestrator.OrchestratorService`
- `git_recap.exceptions.GitRecapError`

---

## 커맨드 구조

```
git-recap fetch [DATE]                     # Fetcher만 실행
git-recap normalize [DATE]                 # Normalizer만 실행
git-recap summarize daily [DATE]           # Daily summary 생성
git-recap summarize weekly YEAR WEEK       # Weekly summary 생성
git-recap summarize monthly YEAR MONTH     # Monthly summary 생성
git-recap summarize yearly YEAR            # Yearly summary 생성
git-recap run [DATE]                       # 전체 파이프라인 (단일 날짜)
git-recap run --since SINCE --until UNTIL  # 기간 범위 backfill
git-recap ask QUESTION                     # 자유 질문
```

DATE 기본값: 오늘 날짜

---

## 상세 구현

```python
import sys
from datetime import date

import typer

from git_recap.config import AppConfig
from git_recap.exceptions import GitRecapError

app = typer.Typer(help="GHES activity summarizer with LLM")
summarize_app = typer.Typer(help="Generate summaries")
app.add_typer(summarize_app, name="summarize")


def _get_config() -> AppConfig:
    """AppConfig 로드. .env 파일에서 자동 로딩."""
    return AppConfig()


def _get_ghes_client(config: AppConfig):
    from git_recap.infra.ghes_client import GHESClient
    return GHESClient(config.ghes_url, config.ghes_token)


def _get_llm_client(config: AppConfig):
    from git_recap.infra.llm_client import LLMClient
    return LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)


def _handle_error(e: GitRecapError) -> None:
    """에러 메시지를 stderr에 출력하고 exit(1)."""
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
        from git_recap.services.fetcher import FetcherService

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
        from git_recap.services.normalizer import NormalizerService

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
        from git_recap.services.summarizer import SummarizerService

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
        from git_recap.services.summarizer import SummarizerService

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
        from git_recap.services.summarizer import SummarizerService

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
        from git_recap.services.summarizer import SummarizerService

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
        from git_recap.services.fetcher import FetcherService
        from git_recap.services.normalizer import NormalizerService
        from git_recap.services.orchestrator import OrchestratorService
        from git_recap.services.summarizer import SummarizerService

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
                status = "✓" if r["status"] == "success" else "✗"
                msg = r.get("path", r.get("error", ""))
                typer.echo(f"  {status} {r['date']}: {msg}")
            if succeeded < len(results):
                raise typer.Exit(code=1)
        else:
            target_date = target_date or date.today().isoformat()
            path = orchestrator.run_daily(target_date)
            typer.echo(f"Pipeline complete → {path}")

        ghes.close()
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
        from git_recap.services.summarizer import SummarizerService

        llm = _get_llm_client(config)
        service = SummarizerService(config, llm)
        answer = service.query(question, months_back=months)
        typer.echo(answer)
    except GitRecapError as e:
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

테스트에서는 `_get_config`, `_get_ghes_client`, `_get_llm_client`를 monkeypatch하여
실제 .env 파일이나 외부 API 없이 CLI 동작을 검증한다.

---

## 테스트 명세

### test_cli.py

`typer.testing.CliRunner`를 사용하여 CLI 커맨드를 검증한다.
서비스는 monkeypatch로 mock한다.

```python
"""tests/unit/test_cli.py"""

class TestFetch:
    def test_fetch_with_date(self, runner, mock_services):
        """git-recap fetch 2025-02-16 → FetcherService.fetch 호출."""

    def test_fetch_default_today(self, runner, mock_services):
        """날짜 미지정 시 오늘 날짜 사용."""

    def test_fetch_error(self, runner, mock_services):
        """FetchError → exit code 1 + stderr 메시지."""

class TestNormalize:
    def test_normalize_with_date(self, runner, mock_services):
        """git-recap normalize 2025-02-16 → 성공 메시지."""

class TestSummarize:
    def test_summarize_daily(self, runner, mock_services):
        """git-recap summarize daily 2025-02-16."""

    def test_summarize_weekly(self, runner, mock_services):
        """git-recap summarize weekly 2025 7."""

    def test_summarize_monthly(self, runner, mock_services):
        """git-recap summarize monthly 2025 2."""

    def test_summarize_yearly(self, runner, mock_services):
        """git-recap summarize yearly 2025."""

class TestRun:
    def test_run_single_date(self, runner, mock_services):
        """git-recap run 2025-02-16 → 파이프라인 완료 메시지."""

    def test_run_range(self, runner, mock_services):
        """git-recap run --since X --until Y → 범위 결과."""

    def test_run_error(self, runner, mock_services):
        """파이프라인 에러 → exit code 1."""

class TestAsk:
    def test_ask_question(self, runner, mock_services):
        """git-recap ask "질문" → LLM 응답 출력."""

    def test_ask_error(self, runner, mock_services):
        """context 없으면 exit code 1."""
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 5.1 | Typer app 기본 구조 + `_get_config`, `_handle_error` 헬퍼 | - |
| 5.2 | `fetch`, `normalize` 개별 커맨드 | TestFetch, TestNormalize |
| 5.3 | `summarize` 서브커맨드 (daily/weekly/monthly/yearly) | TestSummarize |
| 5.4 | `run` 커맨드 (단일 날짜 + --since/--until) | TestRun |
| 5.5 | `ask` 커맨드 | TestAsk |
