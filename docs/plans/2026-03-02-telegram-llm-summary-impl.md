# Telegram LLM Summary Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace regex-based Telegram formatting with LLM-generated `.telegram.txt` files alongside existing `.md` summaries.

**Architecture:** SummarizerService gets a new `telegram_summary(level, target)` method that reads existing `.md`, calls LLM with `task="telegram"`, writes `.telegram.txt`. TelegramNotifier is simplified to just read `.telegram.txt` files. Scheduler jobs call `telegram_summary()` after successful pipeline runs.

**Tech Stack:** Python 3.12, Jinja2, LLMRouter (Anthropic Haiku), pytest, httpx

---

### Task 1: AppConfig — telegram path methods

**Files:**
- Modify: `src/workrecap/config.py:94-109`
- Test: `tests/unit/test_config.py`

**Step 1: Write the failing tests**

Add to `tests/unit/test_config.py` inside `TestAppConfig.test_derived_paths`:

```python
# After existing yearly_summary_path assertion (line 48):
assert config.daily_telegram_path("2025-02-16") == Path(
    "/tmp/data/summaries/2025/daily/02-16.telegram.txt"
)
assert config.weekly_telegram_path(2025, 7) == Path(
    "/tmp/data/summaries/2025/weekly/W07.telegram.txt"
)
assert config.monthly_telegram_path(2025, 2) == Path(
    "/tmp/data/summaries/2025/monthly/02.telegram.txt"
)
assert config.yearly_telegram_path(2025) == Path(
    "/tmp/data/summaries/2025/yearly.telegram.txt"
)
```

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/unit/test_config.py::TestAppConfig::test_derived_paths -v`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'daily_telegram_path'`

**Step 3: Write minimal implementation**

Add to `src/workrecap/config.py` after `yearly_summary_path` (line 109):

```python
def daily_telegram_path(self, date: str) -> Path:
    y, m, d = date.split("-")
    return self.summaries_dir / y / "daily" / f"{m}-{d}.telegram.txt"

def weekly_telegram_path(self, year: int, week: int) -> Path:
    return self.summaries_dir / str(year) / "weekly" / f"W{week:02d}.telegram.txt"

def monthly_telegram_path(self, year: int, month: int) -> Path:
    return self.summaries_dir / str(year) / "monthly" / f"{month:02d}.telegram.txt"

def yearly_telegram_path(self, year: int) -> Path:
    return self.summaries_dir / str(year) / "yearly.telegram.txt"
```

**Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_config.py::TestAppConfig::test_derived_paths -v`
Expected: PASS

**Step 5: Commit**

```
feat: add telegram path methods to AppConfig
```

---

### Task 2: Prompt template — `prompts/telegram.md`

**Files:**
- Create: `prompts/telegram.md`

**Step 1: Create the prompt template**

```markdown
당신은 소프트웨어 엔지니어의 업무 요약을 텔레그램 메시지로 변환하는 어시스턴트입니다.

아래 제공되는 마크다운 요약을 텔레그램 메시지 형식으로 변환하세요.

## 규칙
- 4000자 이내의 **평문** 출력 (마크다운 문법 금지, HTML 금지)
- 리포지토리/프로젝트별 기여도에 비례하여 글의 양을 배분
- 각 항목(활동/성과) 앞에 ✅ 붙일 것
- 커밋 단위가 아닌, **주제/토픽 단위**로 묶어서 요약
- 섹션 헤딩은 이모지로 시작:
  - 📋 개요
  - 📌 주요 활동
  - 🏆 주요 성과
  - 💻 커밋
  - 🔀 PR
  - 🎯 이슈
  - 👀 리뷰
- URL, 링크 문법([text](url)) 제거
- "활동이 없는 날입니다" 같은 마커 파일이면 "활동 없음" 한 줄 출력
- 마크다운의 구조(H1/H2/H3/HR 등)는 모두 평문 이모지 헤딩으로 대체
- 불릿 리스트의 `-`는 사용하지 말 것. 대신 ✅ 사용

## 출력 예시

📋 개요
오늘은 3개 프로젝트에서 feature 개발에 집중했다.

📌 주요 활동

🔧 my-setup (10%)
✅ macOS/Ubuntu 개발 환경 설정 파일 정리 및 초기 등록

🚀 work-recap (20%)
✅ Telegram 알림 활성화 및 launchd 자동 실행 지원 추가
✅ .gitignore symlink 처리 수정

📊 llm-api-card-price (70%)
✅ LLM 가격 자동 수집 시스템 전체 파이프라인 구축 (config → scraper → parser → updater → notifier)
✅ rules.toml 기반 모델 필터링으로 노이즈 제거
✅ launchd/systemd 서비스 스크립트 추가

<!-- SPLIT -->

## 요약 레벨: {{ level }}
## 대상: {{ target }}
```

**Step 2: Verify template file is created correctly**

Run: `cat prompts/telegram.md | head -5`
Expected: Template starts with `당신은 소프트웨어 엔지니어의`

**Step 3: Commit**

```
feat: add telegram prompt template
```

---

### Task 3: LLM task routing — `config.toml` telegram task

**Files:**
- Modify: `.provider/config.toml`

**Step 1: Add telegram task config**

Append after `[tasks.query]` block:

```toml
[tasks.telegram]
provider = "anthropic"
model = "claude-haiku-4-5"
max_tokens = 4096
```

**Step 2: Verify config parses**

Run: `PYTHONPATH=src python -c "from workrecap.infra.provider_config import ProviderConfig; pc = ProviderConfig('.provider/config.toml'); print(pc.task_config('telegram'))"`
Expected: Output showing provider/model/max_tokens

**Step 3: Commit**

```
feat: add telegram task to LLM routing config
```

---

### Task 4: SummarizerService — `telegram_summary()` method

**Files:**
- Modify: `src/workrecap/services/summarizer.py`
- Test: `tests/unit/test_summarizer.py`

**Step 1: Write the failing tests**

Add new test class to `tests/unit/test_summarizer.py`:

```python
class TestTelegramSummary:
    """SummarizerService.telegram_summary() 테스트."""

    def test_generates_telegram_file_from_daily_md(self, test_config, mock_llm):
        from workrecap.services.summarizer import SummarizerService

        # daily .md 파일 생성
        md_path = test_config.daily_summary_path("2026-03-01")
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# Daily Summary: 2026-03-01\n\n## 개요\n내용")

        mock_llm.chat.return_value = "📋 개요\n텔레그램용 요약"

        summarizer = SummarizerService(test_config, mock_llm)
        result = summarizer.telegram_summary("daily", "2026-03-01")

        assert result == test_config.daily_telegram_path("2026-03-01")
        assert result.exists()
        assert result.read_text() == "📋 개요\n텔레그램용 요약"
        mock_llm.chat.assert_called_once()
        # task="telegram" 확인
        call_kwargs = mock_llm.chat.call_args
        assert call_kwargs.kwargs.get("task") == "telegram" or call_kwargs[2] == "telegram"

    def test_generates_telegram_file_from_weekly_md(self, test_config, mock_llm):
        from workrecap.services.summarizer import SummarizerService

        md_path = test_config.weekly_summary_path(2026, 9)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# Weekly Summary: 2026-W09\n\ncontent")

        mock_llm.chat.return_value = "📋 주간 개요\n주간 텔레그램 요약"

        summarizer = SummarizerService(test_config, mock_llm)
        result = summarizer.telegram_summary("weekly", "2026-W09")

        assert result == test_config.weekly_telegram_path(2026, 9)
        assert result.exists()

    def test_generates_telegram_file_from_monthly_md(self, test_config, mock_llm):
        from workrecap.services.summarizer import SummarizerService

        md_path = test_config.monthly_summary_path(2026, 2)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# Monthly Summary\n\ncontent")

        mock_llm.chat.return_value = "월간 텔레그램 요약"

        summarizer = SummarizerService(test_config, mock_llm)
        result = summarizer.telegram_summary("monthly", "2026-02")

        assert result == test_config.monthly_telegram_path(2026, 2)
        assert result.exists()

    def test_generates_telegram_file_from_yearly_md(self, test_config, mock_llm):
        from workrecap.services.summarizer import SummarizerService

        md_path = test_config.yearly_summary_path(2026)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# Yearly Summary\n\ncontent")

        mock_llm.chat.return_value = "연간 텔레그램 요약"

        summarizer = SummarizerService(test_config, mock_llm)
        result = summarizer.telegram_summary("yearly", "2026")

        assert result == test_config.yearly_telegram_path(2026)
        assert result.exists()

    def test_raises_when_md_not_found(self, test_config, mock_llm):
        from workrecap.services.summarizer import SummarizerService

        summarizer = SummarizerService(test_config, mock_llm)
        with pytest.raises(SummarizeError, match="Summary file not found"):
            summarizer.telegram_summary("daily", "2099-01-01")

    def test_skips_when_not_stale(self, test_config, mock_llm):
        from workrecap.services.summarizer import SummarizerService

        # .md 먼저 생성
        md_path = test_config.daily_summary_path("2026-03-01")
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# Daily\n\ncontent")

        # .telegram.txt를 .md보다 나중에 생성
        tg_path = test_config.daily_telegram_path("2026-03-01")
        tg_path.write_text("기존 텔레그램 요약")

        import time
        import os
        # .telegram.txt mtime을 .md보다 새롭게 설정
        future = time.time() + 10
        os.utime(tg_path, (future, future))

        summarizer = SummarizerService(test_config, mock_llm)
        result = summarizer.telegram_summary("daily", "2026-03-01")

        assert result == tg_path
        mock_llm.chat.assert_not_called()

    def test_regenerates_when_stale(self, test_config, mock_llm):
        from workrecap.services.summarizer import SummarizerService

        md_path = test_config.daily_summary_path("2026-03-01")
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# Daily\n\ncontent")

        tg_path = test_config.daily_telegram_path("2026-03-01")
        tg_path.write_text("old telegram")

        import time
        import os
        # .md mtime을 .telegram.txt보다 새롭게 설정
        future = time.time() + 10
        os.utime(md_path, (future, future))

        mock_llm.chat.return_value = "new telegram"

        summarizer = SummarizerService(test_config, mock_llm)
        result = summarizer.telegram_summary("daily", "2026-03-01")

        mock_llm.chat.assert_called_once()
        assert result.read_text() == "new telegram"

    def test_hard_trims_over_4096(self, test_config, mock_llm):
        from workrecap.services.summarizer import SummarizerService

        md_path = test_config.daily_summary_path("2026-03-01")
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# Daily\n\ncontent")

        mock_llm.chat.return_value = "가" * 5000

        summarizer = SummarizerService(test_config, mock_llm)
        result = summarizer.telegram_summary("daily", "2026-03-01")

        content = result.read_text()
        assert len(content) <= 4096

    def test_raises_for_invalid_level(self, test_config, mock_llm):
        from workrecap.services.summarizer import SummarizerService

        summarizer = SummarizerService(test_config, mock_llm)
        with pytest.raises(SummarizeError, match="Unknown summary level"):
            summarizer.telegram_summary("invalid", "2026-03-01")
```

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/unit/test_summarizer.py::TestTelegramSummary -v`
Expected: FAIL — `AttributeError: 'SummarizerService' object has no attribute 'telegram_summary'`

**Step 3: Write minimal implementation**

Add to `src/workrecap/services/summarizer.py` in `SummarizerService` class, after `query()` method:

```python
TELEGRAM_MAX_LENGTH = 4096

def telegram_summary(self, level: str, target: str) -> Path:
    """기존 .md summary를 LLM으로 텔레그램용 .telegram.txt로 변환."""
    md_path = self._resolve_md_path(level, target)
    if not md_path.exists():
        raise SummarizeError(f"Summary file not found: {md_path}")

    tg_path = self._resolve_telegram_path(level, target)

    # Staleness 체크: .telegram.txt가 .md보다 새로우면 스킵
    if tg_path.exists() and tg_path.stat().st_mtime > md_path.stat().st_mtime:
        logger.info("Telegram summary up-to-date, skipping: %s", tg_path)
        return tg_path

    md_content = md_path.read_text(encoding="utf-8")

    system_prompt, dynamic = self._render_split_prompt(
        "telegram.md", level=level, target=target
    )
    user_content = dynamic + "\n\n" + md_content

    response = self._llm.chat(
        system_prompt, user_content, task="telegram", cache_system_prompt=True
    )

    # 4096자 hard trim 안전장치
    if len(response) > TELEGRAM_MAX_LENGTH:
        response = response[: TELEGRAM_MAX_LENGTH - 10] + "\n...계속"

    tg_path.parent.mkdir(parents=True, exist_ok=True)
    tg_path.write_text(response, encoding="utf-8")
    logger.info("Generated telegram summary: %s", tg_path)
    return tg_path

def _resolve_md_path(self, level: str, target: str) -> Path:
    """level + target → .md 파일 경로."""
    if level == "daily":
        return self._config.daily_summary_path(target)
    elif level == "weekly":
        parts = target.split("-W")
        return self._config.weekly_summary_path(int(parts[0]), int(parts[1]))
    elif level == "monthly":
        parts = target.split("-")
        return self._config.monthly_summary_path(int(parts[0]), int(parts[1]))
    elif level == "yearly":
        return self._config.yearly_summary_path(int(target))
    raise SummarizeError(f"Unknown summary level: {level}")

def _resolve_telegram_path(self, level: str, target: str) -> Path:
    """level + target → .telegram.txt 파일 경로."""
    if level == "daily":
        return self._config.daily_telegram_path(target)
    elif level == "weekly":
        parts = target.split("-W")
        return self._config.weekly_telegram_path(int(parts[0]), int(parts[1]))
    elif level == "monthly":
        parts = target.split("-")
        return self._config.monthly_telegram_path(int(parts[0]), int(parts[1]))
    elif level == "yearly":
        return self._config.yearly_telegram_path(int(target))
    raise SummarizeError(f"Unknown summary level: {level}")
```

**Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_summarizer.py::TestTelegramSummary -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `PYTHONPATH=src pytest`
Expected: All tests pass

**Step 6: Commit**

```
feat: add telegram_summary() to SummarizerService
```

---

### Task 5: Simplify TelegramNotifier — read `.telegram.txt`

**Files:**
- Modify: `src/workrecap/scheduler/notifier.py`
- Modify: `tests/unit/test_scheduler_notifier.py`

**Step 1: Update tests — remove regex tests, update notifier tests**

In `tests/unit/test_scheduler_notifier.py`:

1. **Delete** entire `TestFormatForTelegram` class (lines 98-246)
2. **Delete** entire `TestTrimToFit` class (lines 249-304)
3. **Update** `TestBuildSingleMessage` — keep it but simplify `_trim_to_fit` usage
4. **Update** `TestTelegramNotifier._make_notifier` — mock `daily_telegram_path` etc.
5. **Update** `test_notify_sends_single_message` — write `.telegram.txt` instead of `.md`
6. **Add** new test: `test_read_summary_reads_telegram_txt`
7. **Add** new test: `test_read_summary_falls_back_to_none_when_missing`

Replace the notifier tests with:

```python
class TestTelegramNotifier:
    def _make_notifier(self, tmp_path):
        from workrecap.scheduler.notifier import TelegramNotifier

        config = MagicMock()
        config.daily_telegram_path.return_value = tmp_path / "daily.telegram.txt"
        config.weekly_telegram_path.return_value = tmp_path / "weekly.telegram.txt"
        config.monthly_telegram_path.return_value = tmp_path / "monthly.telegram.txt"
        config.yearly_telegram_path.return_value = tmp_path / "yearly.telegram.txt"
        return TelegramNotifier("fake-token", "12345", config)

    def test_is_notifier_subclass(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        assert issubclass(TelegramNotifier, Notifier)

    def test_format_header_success(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="2026-02-28T02:00:00",
            completed_at="2026-02-28T02:05:23",
            target="2026-02-27",
        )
        header = notifier._format_header(event)
        assert "daily" in header
        assert "2026-02-27" in header
        assert "\u2705" in header

    def test_format_header_failure(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        event = SchedulerEvent(
            job="daily",
            status="failed",
            triggered_at="2026-02-28T02:00:00",
            completed_at="2026-02-28T02:05:23",
            target="2026-02-27",
            error="FetchError: timeout",
        )
        header = notifier._format_header(event)
        assert "\u274c" in header
        assert "FetchError: timeout" in header

    def test_resolve_telegram_path_daily(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_telegram_path("daily", "2026-02-27")
        assert path == tmp_path / "daily.telegram.txt"

    def test_resolve_telegram_path_weekly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_telegram_path("weekly", "2026-W09")
        assert path == tmp_path / "weekly.telegram.txt"

    def test_resolve_telegram_path_monthly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_telegram_path("monthly", "2026-02")
        assert path == tmp_path / "monthly.telegram.txt"

    def test_resolve_telegram_path_yearly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_telegram_path("yearly", "2026")
        assert path == tmp_path / "yearly.telegram.txt"

    def test_read_summary_reads_telegram_txt(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        tg_path = tmp_path / "daily.telegram.txt"
        tg_path.write_text("📋 개요\n텔레그램 요약 내용")

        event = SchedulerEvent(
            job="daily", status="success", triggered_at="t1", target="2026-02-27"
        )
        result = notifier._read_summary(event)
        assert result == "📋 개요\n텔레그램 요약 내용"

    def test_read_summary_returns_none_when_missing(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        event = SchedulerEvent(
            job="daily", status="success", triggered_at="t1", target="2026-02-27"
        )
        result = notifier._read_summary(event)
        assert result is None

    def test_read_summary_returns_none_on_failure_event(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        event = SchedulerEvent(
            job="daily", status="failed", triggered_at="t1", target="2026-02-27",
            error="boom",
        )
        result = notifier._read_summary(event)
        assert result is None

    def test_build_message_no_body(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        result = notifier._build_message("header", None)
        assert result == "header"

    def test_build_message_with_body(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        result = notifier._build_message("header", "body content")
        assert "header" in result
        assert "body content" in result
        assert "\u2500" in result  # separator

    def test_build_message_trims_long_body(self, tmp_path):
        from workrecap.scheduler.notifier import TELEGRAM_MAX_LENGTH

        notifier = self._make_notifier(tmp_path)
        long_body = "가" * 5000
        result = notifier._build_message("header", long_body)
        assert len(result) <= TELEGRAM_MAX_LENGTH

    @patch("workrecap.scheduler.notifier.httpx.AsyncClient")
    def test_notify_sends_single_message(self, mock_client_cls, tmp_path):
        notifier = self._make_notifier(tmp_path)
        tg_path = tmp_path / "daily.telegram.txt"
        tg_path.write_text("📋 개요\nTelegram summary content")

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="2026-02-28T02:00:00",
            completed_at="2026-02-28T02:05:23",
            target="2026-02-27",
        )
        asyncio.run(notifier.notify(event))
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "sendMessage" in call_args[0][0]

    @patch("workrecap.scheduler.notifier.httpx.AsyncClient")
    def test_notify_graceful_on_http_error(self, mock_client_cls, tmp_path, caplog):
        notifier = self._make_notifier(tmp_path)
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("network error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="t1",
            target="2026-02-27",
        )
        with caplog.at_level(logging.WARNING):
            asyncio.run(notifier.notify(event))
        assert "Telegram" in caplog.text
```

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_notifier.py::TestTelegramNotifier -v`
Expected: FAIL — methods don't exist yet on simplified notifier

**Step 3: Simplify notifier implementation**

Replace `src/workrecap/scheduler/notifier.py` content. Key changes:
- Remove `_HEADING_EMOJIS`, `_ITEM_RE` module-level constants
- Remove `_format_for_telegram()` static method
- Remove `_trim_to_fit()` static method
- Rename `_resolve_summary_path()` → `_resolve_telegram_path()` — use `*_telegram_path` config methods
- Rename `_build_single_message()` → `_build_message()` — simple header+body with hard trim
- Update `_read_summary()` to read `.telegram.txt`
- Update `notify()` to not call `_format_for_telegram()`
- Remove `import re`

```python
"""알림 시스템 — Notifier ABC + LogNotifier + TelegramNotifier."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SchedulerEvent:
    job: str
    status: str  # "success" | "failed"
    triggered_at: str
    target: str
    completed_at: str | None = None
    error: str | None = None


class Notifier(ABC):
    @abstractmethod
    async def notify(self, event: SchedulerEvent) -> None: ...


class LogNotifier(Notifier):
    async def notify(self, event: SchedulerEvent) -> None:
        if event.status == "failed":
            logger.error(
                "Scheduler job '%s' failed (target=%s): %s",
                event.job,
                event.target,
                event.error,
            )
        else:
            logger.info(
                "Scheduler job '%s' %s (target=%s)",
                event.job,
                event.status,
                event.target,
            )


class CompositeNotifier(Notifier):
    """여러 Notifier를 묶어 순차 실행. 개별 실패 무시."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = notifiers

    async def notify(self, event: SchedulerEvent) -> None:
        for n in self._notifiers:
            try:
                await n.notify(event)
            except Exception:
                logger.warning(
                    "Notifier %s failed for job '%s'",
                    type(n).__name__,
                    event.job,
                    exc_info=True,
                )


TELEGRAM_MAX_LENGTH = 4096


class TelegramNotifier(Notifier):
    """Telegram Bot API sendMessage로 스케줄 결과 전송."""

    def __init__(self, bot_token: str, chat_id: str, config) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._config = config
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    def _format_header(self, event: SchedulerEvent) -> str:
        icon = "\u2705" if event.status == "success" else "\u274c"
        status_text = "완료" if event.status == "success" else "실패"
        header = f"{icon} [{event.job}] {status_text} \u2014 {event.target}"
        if event.triggered_at and event.completed_at:
            header += f"\n\n\u23f1 {event.triggered_at} \u2192 {event.completed_at}"
        if event.error:
            header += f"\n\nError: {event.error}"
        return header

    def _resolve_telegram_path(self, job: str, target: str):
        if job == "daily":
            return self._config.daily_telegram_path(target)
        elif job == "weekly":
            parts = target.split("-W")
            return self._config.weekly_telegram_path(int(parts[0]), int(parts[1]))
        elif job == "monthly":
            parts = target.split("-")
            return self._config.monthly_telegram_path(int(parts[0]), int(parts[1]))
        elif job == "yearly":
            return self._config.yearly_telegram_path(int(target))
        return None

    def _read_summary(self, event: SchedulerEvent) -> str | None:
        if event.status != "success":
            return None
        try:
            path = self._resolve_telegram_path(event.job, event.target)
            if path and path.exists():
                return path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Failed to read telegram summary", exc_info=True)
        return None

    def _build_message(self, header: str, body: str | None) -> str:
        """헤더 + 본문을 단일 텔레그램 메시지로 조립."""
        if not body:
            return header
        separator = "\n\n" + "\u2500" * 20 + "\n"
        max_body = TELEGRAM_MAX_LENGTH - len(header) - len(separator)
        if len(body) > max_body:
            body = body[: max_body - 10] + "\n...계속"
        return header + separator + body

    async def notify(self, event: SchedulerEvent) -> None:
        summary = self._read_summary(event)
        header = self._format_header(event)
        message = self._build_message(header, summary)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    f"{self._base_url}/sendMessage",
                    json={"chat_id": self._chat_id, "text": message},
                )
        except Exception:
            logger.warning(
                "Telegram notification failed for job '%s'", event.job, exc_info=True
            )
```

**Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_notifier.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `PYTHONPATH=src pytest`
Expected: All tests pass

**Step 6: Commit**

```
refactor: simplify TelegramNotifier to read .telegram.txt files
```

---

### Task 6: Scheduler jobs — call `telegram_summary()` after pipeline

**Files:**
- Modify: `src/workrecap/scheduler/jobs.py`
- Modify: `tests/unit/test_scheduler_jobs.py`

**Step 1: Write the failing tests**

Update test classes in `tests/unit/test_scheduler_jobs.py`:

```python
class TestRunDailyJob:
    def test_calls_telegram_summary_on_success(self, tmp_path, history, notifier, schedule_config):
        mock_orch = MagicMock()
        mock_orch.run_daily.return_value = tmp_path / "summary.md"
        mock_summarizer = MagicMock()

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_orchestrator", return_value=mock_orch),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_daily_job(schedule_config, history, notifier))

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        mock_summarizer.telegram_summary.assert_called_once_with("daily", yesterday)

    def test_telegram_summary_failure_does_not_break_job(
        self, tmp_path, history, notifier, schedule_config
    ):
        mock_orch = MagicMock()
        mock_orch.run_daily.return_value = tmp_path / "summary.md"
        mock_summarizer = MagicMock()
        mock_summarizer.telegram_summary.side_effect = Exception("LLM down")

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_orchestrator", return_value=mock_orch),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_daily_job(schedule_config, history, notifier))

        entries = history.list()
        assert entries[0]["status"] == "success"  # job still succeeds


class TestRunWeeklyJob:
    def test_calls_telegram_summary_on_success(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.weekly.return_value = tmp_path / "W08.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_weekly_job(schedule_config, history, notifier))

        last_week = date.today() - timedelta(weeks=1)
        iso_year, iso_week, _ = last_week.isocalendar()
        target = f"{iso_year}-W{iso_week:02d}"
        mock_summarizer.telegram_summary.assert_called_once_with("weekly", target)


class TestRunMonthlyJob:
    def test_calls_telegram_summary_on_success(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.monthly.return_value = tmp_path / "02.md"
        mock_summarizer.weekly.return_value = tmp_path / "W.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_monthly_job(schedule_config, history, notifier))

        mock_summarizer.telegram_summary.assert_called_once()
        call_args = mock_summarizer.telegram_summary.call_args[0]
        assert call_args[0] == "monthly"


class TestRunYearlyJob:
    def test_calls_telegram_summary_on_success(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.yearly.return_value = tmp_path / "yearly.md"
        mock_summarizer.weekly.return_value = tmp_path / "W.md"
        mock_summarizer.monthly.return_value = tmp_path / "M.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_yearly_job(schedule_config, history, notifier))

        mock_summarizer.telegram_summary.assert_called_once()
        call_args = mock_summarizer.telegram_summary.call_args[0]
        assert call_args[0] == "yearly"
```

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_jobs.py -v -k telegram`
Expected: FAIL — `telegram_summary` not called in current jobs

**Step 3: Modify scheduler jobs**

In `src/workrecap/scheduler/jobs.py`, update each job to call `telegram_summary()` after success:

For `run_daily_job`, after `orch.run_daily(yesterday, types=None)`:
```python
try:
    summarizer = _build_summarizer(config)
    summarizer.telegram_summary("daily", yesterday)
except Exception:
    logger.warning("Telegram summary generation failed for %s", yesterday, exc_info=True)
```

For `run_weekly_job`, after `summarizer.weekly(...)`:
```python
try:
    summarizer.telegram_summary("weekly", target)
except Exception:
    logger.warning("Telegram summary generation failed for %s", target, exc_info=True)
```

For `run_monthly_job`, after `summarizer.monthly(...)`:
```python
try:
    summarizer.telegram_summary("monthly", target)
except Exception:
    logger.warning("Telegram summary generation failed for %s", target, exc_info=True)
```

For `run_yearly_job`, after `summarizer.yearly(...)`:
```python
try:
    summarizer.telegram_summary("yearly", target)
except Exception:
    logger.warning("Telegram summary generation failed for %s", target, exc_info=True)
```

**Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_jobs.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `PYTHONPATH=src pytest`
Expected: All tests pass

**Step 6: Commit**

```
feat: scheduler jobs generate telegram summaries after pipeline
```

---

### Task 7: Final verification and docs

**Step 1: Run full test suite with lint**

Run: `PYTHONPATH=src pytest`
Run: `ruff check src/ tests/`
Run: `ruff format --check src/ tests/`

Expected: All pass

**Step 2: Update CLAUDE.md**

Add to the `notifier.py` description:
- `TelegramNotifier` reads `.telegram.txt` files (generated by SummarizerService) instead of `.md` with regex conversion
- `_resolve_telegram_path()` maps job/target to `*_telegram_path` config methods
- `_build_message()` does header+body assembly with hard 4096 char trim

Add to the `summarizer.py` description:
- `telegram_summary(level, target)`: reads `.md`, calls LLM (task="telegram"), writes `.telegram.txt`. Staleness: `.md` mtime > `.telegram.txt` mtime. 4096 char hard trim.

Add `prompts/telegram.md` to the prompts description.

Add `[tasks.telegram]` to the provider config description.

Add `daily_telegram_path`, `weekly_telegram_path`, `monthly_telegram_path`, `yearly_telegram_path` to config.py description.

**Step 3: Commit**

```
docs: update CLAUDE.md for telegram LLM summary feature
```
