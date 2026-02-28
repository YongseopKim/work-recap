# Telegram Notifier Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 스케줄러 실행 결과(daily/weekly/monthly/yearly)를 Telegram Bot API로 전송하는 TelegramNotifier 구현

**Architecture:** 기존 Notifier ABC를 구현하는 TelegramNotifier를 추가하고, CompositeNotifier로 LogNotifier와 함께 묶어 사용. httpx.AsyncClient로 Telegram Bot API sendMessage 호출. AppConfig에서 summary 파일 경로를 유도하여 성공 시 요약 전체를 메시지에 포함.

**Tech Stack:** httpx (기존 dependency), Telegram Bot API (sendMessage), tomllib (schedule.toml 파싱)

---

### Task 1: TelegramConfig 데이터클래스 + ScheduleConfig 확장

`schedule.toml`의 `[scheduler.telegram]` 섹션을 파싱하는 `TelegramConfig`를 추가하고, `ScheduleConfig`에 연결한다.

**Files:**
- Modify: `src/workrecap/scheduler/config.py`
- Test: `tests/unit/test_scheduler_config.py`

**Step 1: Write the failing test**

`tests/unit/test_scheduler_config.py` 끝에 추가:

```python
class TestTelegramConfig:
    def test_default_telegram_config(self):
        config = ScheduleConfig()
        assert config.telegram.enabled is False

    def test_from_toml_with_telegram(self, tmp_path):
        toml_file = tmp_path / "schedule.toml"
        toml_file.write_text(
            '[scheduler]\nenabled = true\n\n[scheduler.telegram]\nenabled = true\n'
        )
        config = ScheduleConfig.from_toml(toml_file)
        assert config.telegram.enabled is True

    def test_from_toml_without_telegram(self, tmp_path):
        toml_file = tmp_path / "schedule.toml"
        toml_file.write_text("[scheduler]\nenabled = true\n")
        config = ScheduleConfig.from_toml(toml_file)
        assert config.telegram.enabled is False
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_config.py::TestTelegramConfig -v`
Expected: FAIL with AttributeError ('ScheduleConfig' has no attribute 'telegram')

**Step 3: Write minimal implementation**

In `src/workrecap/scheduler/config.py`:

1. Add after `NotificationConfig`:

```python
@dataclass
class TelegramConfig:
    enabled: bool = False
```

2. Add field to `ScheduleConfig`:

```python
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
```

3. In `from_toml`, add telegram parsing in the return statement:

```python
            telegram=TelegramConfig(**sched.get("telegram", {})),
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_config.py -v`
Expected: ALL PASS

**Step 5: Commit**

```
git commit -m "feat(scheduler): add TelegramConfig to ScheduleConfig"
```

---

### Task 2: AppConfig — telegram_bot_token, telegram_chat_id

`.env`의 Telegram 자격 증명을 AppConfig에 추가한다.

**Files:**
- Modify: `src/workrecap/config.py`
- Test: `tests/unit/test_config.py` (기존 테스트가 깨지지 않는지 확인)

**Step 1: Write the failing test**

`tests/unit/test_config.py` 끝에 추가 (없으면 새로 만듦):

```python
class TestAppConfigTelegram:
    def test_default_telegram_fields(self, test_config):
        assert test_config.telegram_bot_token == ""
        assert test_config.telegram_chat_id == ""
```

Note: `test_config` fixture는 `conftest.py`에 이미 존재. `.env.test`에 TELEGRAM 변수가 없으므로 기본값 ""이 사용됨.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_config.py::TestAppConfigTelegram -v`
Expected: FAIL with AttributeError

**Step 3: Write minimal implementation**

In `src/workrecap/config.py`, add after `tei_url` field (~line 48):

```python
    # Telegram 알림
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_config.py tests/unit/test_config.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `PYTHONPATH=src pytest`
Expected: ALL PASS (기존 테스트에 영향 없음 — `extra="ignore"` 설정)

**Step 6: Commit**

```
git commit -m "feat(config): add telegram_bot_token and telegram_chat_id fields"
```

---

### Task 3: CompositeNotifier

여러 Notifier를 묶어 순차 실행하는 CompositeNotifier. 개별 notifier 실패 시 로그 경고만 남기고 나머지 계속 실행.

**Files:**
- Modify: `src/workrecap/scheduler/notifier.py`
- Test: `tests/unit/test_scheduler_notifier.py`

**Step 1: Write the failing test**

`tests/unit/test_scheduler_notifier.py` 끝에 추가:

```python
from unittest.mock import AsyncMock, patch


class TestCompositeNotifier:
    def test_is_notifier_subclass(self):
        from workrecap.scheduler.notifier import CompositeNotifier
        assert issubclass(CompositeNotifier, Notifier)

    def test_calls_all_notifiers(self):
        from workrecap.scheduler.notifier import CompositeNotifier

        n1 = AsyncMock(spec=Notifier)
        n2 = AsyncMock(spec=Notifier)
        composite = CompositeNotifier([n1, n2])
        event = SchedulerEvent(
            job="daily", status="success", triggered_at="t1", target="2026-02-27"
        )
        asyncio.run(composite.notify(event))
        n1.notify.assert_awaited_once_with(event)
        n2.notify.assert_awaited_once_with(event)

    def test_continues_on_failure(self, caplog):
        from workrecap.scheduler.notifier import CompositeNotifier

        n1 = AsyncMock(spec=Notifier)
        n1.notify.side_effect = RuntimeError("boom")
        n2 = AsyncMock(spec=Notifier)
        composite = CompositeNotifier([n1, n2])
        event = SchedulerEvent(
            job="daily", status="failed", triggered_at="t1", target="2026-02-27"
        )
        with caplog.at_level(logging.WARNING):
            asyncio.run(composite.notify(event))
        n2.notify.assert_awaited_once_with(event)
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_notifier.py::TestCompositeNotifier -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

In `src/workrecap/scheduler/notifier.py`, add after `LogNotifier`:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_notifier.py -v`
Expected: ALL PASS

**Step 5: Commit**

```
git commit -m "feat(scheduler): add CompositeNotifier for multi-notifier support"
```

---

### Task 4: TelegramNotifier — 메시지 포맷 + 전송

httpx.AsyncClient로 Telegram Bot API sendMessage를 호출하는 TelegramNotifier.
성공 시 summary 파일을 읽어 메시지에 포함. 4096자 제한 처리.

**Files:**
- Modify: `src/workrecap/scheduler/notifier.py`
- Test: `tests/unit/test_scheduler_notifier.py`

**Step 1: Write the failing tests**

`tests/unit/test_scheduler_notifier.py`에 추가:

```python
from pathlib import Path
from unittest.mock import MagicMock


class TestTelegramNotifier:
    def _make_notifier(self, tmp_path):
        from workrecap.scheduler.notifier import TelegramNotifier

        config = MagicMock()
        config.daily_summary_path.return_value = tmp_path / "daily.md"
        config.weekly_summary_path.return_value = tmp_path / "weekly.md"
        config.monthly_summary_path.return_value = tmp_path / "monthly.md"
        config.yearly_summary_path.return_value = tmp_path / "yearly.md"
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
        assert "\u2705" in header  # ✅

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
        assert "\u274c" in header  # ❌
        assert "FetchError: timeout" in header

    def test_resolve_summary_path_daily(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_summary_path("daily", "2026-02-27")
        assert path == tmp_path / "daily.md"

    def test_resolve_summary_path_weekly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_summary_path("weekly", "2026-W09")
        assert path == tmp_path / "weekly.md"

    def test_resolve_summary_path_monthly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_summary_path("monthly", "2026-02")
        assert path == tmp_path / "monthly.md"

    def test_resolve_summary_path_yearly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_summary_path("yearly", "2026")
        assert path == tmp_path / "yearly.md"

    def test_split_messages_short(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        msgs = notifier._split_messages("header", "short body")
        assert len(msgs) == 1
        assert "header" in msgs[0]
        assert "short body" in msgs[0]

    def test_split_messages_long(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        long_body = "x" * 5000
        msgs = notifier._split_messages("header", long_body)
        assert len(msgs) >= 2
        assert "header" in msgs[0]

    @patch("workrecap.scheduler.notifier.httpx.AsyncClient")
    def test_notify_sends_message(self, mock_client_cls, tmp_path):
        notifier = self._make_notifier(tmp_path)
        # Write summary file
        summary_path = tmp_path / "daily.md"
        summary_path.write_text("# Daily Summary\nSome content")

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
        mock_client.post.assert_called()
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

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_notifier.py::TestTelegramNotifier -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

In `src/workrecap/scheduler/notifier.py`, add import and class:

```python
import httpx

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

    def _resolve_summary_path(self, job: str, target: str):
        if job == "daily":
            return self._config.daily_summary_path(target)
        elif job == "weekly":
            # target="2026-W09" → year=2026, week=9
            parts = target.split("-W")
            return self._config.weekly_summary_path(int(parts[0]), int(parts[1]))
        elif job == "monthly":
            # target="2026-02" → year=2026, month=2
            parts = target.split("-")
            return self._config.monthly_summary_path(int(parts[0]), int(parts[1]))
        elif job == "yearly":
            # target="2026"
            return self._config.yearly_summary_path(int(target))
        return None

    def _read_summary(self, event: SchedulerEvent) -> str | None:
        if event.status != "success":
            return None
        try:
            path = self._resolve_summary_path(event.job, event.target)
            if path and path.exists():
                return path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Failed to read summary for Telegram", exc_info=True)
        return None

    def _split_messages(self, header: str, body: str | None) -> list[str]:
        if not body:
            return [header]
        separator = "\n\n" + "\u2500" * 20 + "\n"
        full = header + separator + body
        if len(full) <= TELEGRAM_MAX_LENGTH:
            return [full]
        # Header as first message, split body into chunks
        messages = [header]
        while body:
            chunk = body[:TELEGRAM_MAX_LENGTH]
            messages.append(chunk)
            body = body[TELEGRAM_MAX_LENGTH:]
        return messages

    async def notify(self, event: SchedulerEvent) -> None:
        summary = self._read_summary(event)
        header = self._format_header(event)
        messages = self._split_messages(header, summary)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for msg in messages:
                    await client.post(
                        f"{self._base_url}/sendMessage",
                        json={"chat_id": self._chat_id, "text": msg},
                    )
        except Exception:
            logger.warning("Telegram notification failed for job '%s'", event.job, exc_info=True)
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_notifier.py -v`
Expected: ALL PASS

**Step 5: Commit**

```
git commit -m "feat(scheduler): add TelegramNotifier with summary content"
```

---

### Task 5: FastAPI lifespan 통합 — CompositeNotifier 조립

lifespan에서 TelegramConfig를 확인하고 TelegramNotifier를 CompositeNotifier로 조립.

**Files:**
- Modify: `src/workrecap/api/app.py`
- Test: `tests/unit/test_api_scheduler.py`

**Step 1: Write the failing test**

`tests/unit/test_api_scheduler.py`에 추가:

```python
class TestSchedulerLifespanTelegram:
    def test_lifespan_creates_composite_notifier_when_telegram_enabled(self):
        """Telegram enabled + token 설정 → CompositeNotifier 사용 확인."""
        from unittest.mock import patch, MagicMock
        from workrecap.scheduler.notifier import CompositeNotifier

        mock_config = MagicMock()
        mock_config.schedule_config_path = Path("/nonexistent")
        mock_config.state_dir = Path("/tmp/test_state")
        mock_config.telegram_bot_token = "fake-token"
        mock_config.telegram_chat_id = "12345"

        with patch("workrecap.api.app.get_config", return_value=mock_config):
            with patch("workrecap.api.app.ScheduleConfig.from_toml") as mock_from_toml:
                sched_config = MagicMock()
                sched_config.telegram.enabled = True
                sched_config.enabled = False  # scheduler disabled so start() is no-op
                mock_from_toml.return_value = sched_config

                from starlette.testclient import TestClient
                from workrecap.api.app import create_app

                app = create_app()
                with TestClient(app):
                    scheduler = app.state.scheduler
                    assert isinstance(scheduler._notifier, CompositeNotifier)
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_api_scheduler.py::TestSchedulerLifespanTelegram -v`
Expected: FAIL (notifier is LogNotifier, not CompositeNotifier)

**Step 3: Write minimal implementation**

Modify `src/workrecap/api/app.py` lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    try:
        from workrecap.scheduler.config import ScheduleConfig
        from workrecap.scheduler.core import SchedulerService
        from workrecap.scheduler.history import SchedulerHistory
        from workrecap.scheduler.notifier import CompositeNotifier, LogNotifier, TelegramNotifier

        config = get_config()
        schedule_config = ScheduleConfig.from_toml(config.schedule_config_path)
        history = SchedulerHistory(config.state_dir / "scheduler_history.json")

        notifiers: list = [LogNotifier()]
        if schedule_config.telegram.enabled:
            if config.telegram_bot_token:
                notifiers.append(
                    TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id, config)
                )
            else:
                logger.warning("Telegram enabled but TELEGRAM_BOT_TOKEN is empty — skipping")
        notifier = CompositeNotifier(notifiers) if len(notifiers) > 1 else notifiers[0]

        scheduler = SchedulerService(schedule_config, history, notifier)
        scheduler.start()
        app.state.scheduler = scheduler
        app.state.scheduler_history = history
    except Exception:
        logger.warning("Scheduler init failed — running without scheduler", exc_info=True)
        from workrecap.scheduler.config import ScheduleConfig
        from workrecap.scheduler.core import SchedulerService
        from workrecap.scheduler.history import SchedulerHistory
        from workrecap.scheduler.notifier import LogNotifier

        fallback_config = ScheduleConfig()
        fallback_history = SchedulerHistory(Path("/dev/null"))
        scheduler = SchedulerService(fallback_config, fallback_history, LogNotifier())
        app.state.scheduler = scheduler
        app.state.scheduler_history = fallback_history
    yield
    if scheduler is not None:
        scheduler.shutdown()
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_api_scheduler.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `PYTHONPATH=src pytest`
Expected: ALL PASS

**Step 6: Commit**

```
git commit -m "feat(api): integrate TelegramNotifier via CompositeNotifier in lifespan"
```

---

### Task 6: schedule.toml 업데이트 + CLAUDE.md 문서 업데이트

`schedule.toml`에 telegram 섹션 추가. `CLAUDE.md`에 Telegram 관련 설정/모듈 문서 반영.

**Files:**
- Modify: `schedule.toml`
- Modify: `CLAUDE.md`

**Step 1: Update schedule.toml**

끝에 추가:

```toml

[scheduler.telegram]
enabled = false
```

**Step 2: Update CLAUDE.md**

관련 섹션에 TelegramNotifier, CompositeNotifier, AppConfig 필드, schedule.toml telegram 섹션 설명 추가.

**Step 3: Run full test suite**

Run: `PYTHONPATH=src pytest`
Expected: ALL PASS (config 변경 없으므로)

**Step 4: Run lint**

Run: `ruff check src/ tests/`
Run: `ruff format --check src/ tests/`
Expected: PASS

**Step 5: Commit**

```
git commit -m "docs: update schedule.toml and CLAUDE.md for Telegram notifier"
```
