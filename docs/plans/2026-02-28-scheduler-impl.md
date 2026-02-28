# Scheduler Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** FastAPI에 APScheduler를 내장하여 daily/weekly/monthly/yearly 파이프라인을 자동 스케줄 실행하고, Web UI에서 스케줄 상태 확인 및 관리.

**Architecture:** APScheduler v3.x `AsyncIOScheduler`를 FastAPI lifespan에 통합. `schedule.toml`에서 스케줄 설정 로드. 각 계층(daily/weekly/monthly/yearly)은 독립 CronTrigger로 분리. 실행 이력은 JSON 파일로 관리. Notifier ABC로 알림 확장점 제공.

**Tech Stack:** APScheduler 3.x, FastAPI lifespan, TOML (stdlib tomllib), Alpine.js, Pico CSS

---

### Task 1: ScheduleConfig — schedule.toml 파싱

**Files:**
- Create: `src/workrecap/scheduler/__init__.py`
- Create: `src/workrecap/scheduler/config.py`
- Create: `schedule.toml` (project root)
- Test: `tests/unit/test_scheduler_config.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_scheduler_config.py
"""ScheduleConfig — schedule.toml 파싱 테스트."""

import textwrap

import pytest

from workrecap.scheduler.config import ScheduleConfig


@pytest.fixture()
def schedule_toml(tmp_path):
    p = tmp_path / "schedule.toml"
    p.write_text(
        textwrap.dedent("""\
        [scheduler]
        enabled = true
        timezone = "Asia/Seoul"

        [scheduler.daily]
        time = "02:00"
        enrich = true
        batch = false
        workers = 5

        [scheduler.weekly]
        day = "mon"
        time = "03:00"

        [scheduler.monthly]
        day = 1
        time = "04:00"

        [scheduler.yearly]
        month = 1
        day = 1
        time = "05:00"

        [scheduler.notification]
        on_failure = true
        on_success = false
        """)
    )
    return p


class TestScheduleConfig:
    def test_load_full_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.enabled is True
        assert cfg.timezone == "Asia/Seoul"

    def test_daily_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.daily.time == "02:00"
        assert cfg.daily.enrich is True
        assert cfg.daily.batch is False
        assert cfg.daily.workers == 5

    def test_weekly_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.weekly.day == "mon"
        assert cfg.weekly.time == "03:00"

    def test_monthly_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.monthly.day == 1
        assert cfg.monthly.time == "04:00"

    def test_yearly_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.yearly.month == 1
        assert cfg.yearly.day == 1
        assert cfg.yearly.time == "05:00"

    def test_notification_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.notification.on_failure is True
        assert cfg.notification.on_success is False

    def test_defaults_when_minimal(self, tmp_path):
        p = tmp_path / "schedule.toml"
        p.write_text("[scheduler]\n")
        cfg = ScheduleConfig.from_toml(p)
        assert cfg.enabled is False
        assert cfg.timezone == "Asia/Seoul"
        assert cfg.daily.time == "02:00"

    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = ScheduleConfig.from_toml(tmp_path / "nonexistent.toml")
        assert cfg.enabled is False

    def test_daily_hour_minute(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.daily.hour == 2
        assert cfg.daily.minute == 0
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'workrecap.scheduler'`

**Step 3: Write minimal implementation**

```python
# src/workrecap/scheduler/__init__.py
```

```python
# src/workrecap/scheduler/config.py
"""schedule.toml 파싱 — 스케줄 설정 모델."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DailySchedule:
    time: str = "02:00"
    enrich: bool = True
    batch: bool = False
    workers: int = 5

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


@dataclass
class WeeklySchedule:
    day: str = "mon"
    time: str = "03:00"

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


@dataclass
class MonthlySchedule:
    day: int = 1
    time: str = "04:00"

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


@dataclass
class YearlySchedule:
    month: int = 1
    day: int = 1
    time: str = "05:00"

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


@dataclass
class NotificationConfig:
    on_failure: bool = True
    on_success: bool = False


@dataclass
class ScheduleConfig:
    enabled: bool = False
    timezone: str = "Asia/Seoul"
    daily: DailySchedule = field(default_factory=DailySchedule)
    weekly: WeeklySchedule = field(default_factory=WeeklySchedule)
    monthly: MonthlySchedule = field(default_factory=MonthlySchedule)
    yearly: YearlySchedule = field(default_factory=YearlySchedule)
    notification: NotificationConfig = field(default_factory=NotificationConfig)

    @classmethod
    def from_toml(cls, path: Path) -> "ScheduleConfig":
        if not path.exists():
            return cls()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        sched = data.get("scheduler", {})
        return cls(
            enabled=sched.get("enabled", False),
            timezone=sched.get("timezone", "Asia/Seoul"),
            daily=DailySchedule(**sched.get("daily", {})),
            weekly=WeeklySchedule(**sched.get("weekly", {})),
            monthly=MonthlySchedule(**sched.get("monthly", {})),
            yearly=YearlySchedule(**sched.get("yearly", {})),
            notification=NotificationConfig(**sched.get("notification", {})),
        )
```

Create default `schedule.toml` at project root:

```toml
[scheduler]
enabled = true
timezone = "Asia/Seoul"

[scheduler.daily]
time = "02:00"
enrich = true
batch = false
workers = 5

[scheduler.weekly]
day = "mon"
time = "03:00"

[scheduler.monthly]
day = 1
time = "04:00"

[scheduler.yearly]
month = 1
day = 1
time = "05:00"

[scheduler.notification]
on_failure = true
on_success = false
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_config.py -v`
Expected: All PASS

**Step 5: Commit**

```
feat(scheduler): add ScheduleConfig with schedule.toml parsing
```

---

### Task 2: SchedulerEvent + Notifier ABC + LogNotifier

**Files:**
- Create: `src/workrecap/scheduler/notifier.py`
- Test: `tests/unit/test_scheduler_notifier.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_scheduler_notifier.py
"""Notifier ABC + LogNotifier 테스트."""

import asyncio
import logging

import pytest

from workrecap.scheduler.notifier import LogNotifier, Notifier, SchedulerEvent


class TestSchedulerEvent:
    def test_event_creation(self):
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="2026-02-28T02:00:00+09:00",
            completed_at="2026-02-28T02:05:00+09:00",
            target="2026-02-27",
        )
        assert event.job == "daily"
        assert event.error is None

    def test_event_with_error(self):
        event = SchedulerEvent(
            job="daily",
            status="failed",
            triggered_at="2026-02-28T02:00:00+09:00",
            target="2026-02-27",
            error="FetchError: timeout",
        )
        assert event.status == "failed"
        assert event.error == "FetchError: timeout"


class TestLogNotifier:
    def test_is_notifier_subclass(self):
        assert issubclass(LogNotifier, Notifier)

    def test_notify_success(self, caplog):
        notifier = LogNotifier()
        event = SchedulerEvent(
            job="daily", status="success",
            triggered_at="t1", target="2026-02-27",
        )
        with caplog.at_level(logging.INFO, logger="workrecap.scheduler.notifier"):
            asyncio.get_event_loop().run_until_complete(notifier.notify(event))
        assert "daily" in caplog.text
        assert "success" in caplog.text

    def test_notify_failure(self, caplog):
        notifier = LogNotifier()
        event = SchedulerEvent(
            job="daily", status="failed",
            triggered_at="t1", target="2026-02-27",
            error="boom",
        )
        with caplog.at_level(logging.ERROR, logger="workrecap.scheduler.notifier"):
            asyncio.get_event_loop().run_until_complete(notifier.notify(event))
        assert "failed" in caplog.text
        assert "boom" in caplog.text
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_notifier.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/workrecap/scheduler/notifier.py
"""알림 시스템 — Notifier ABC + LogNotifier."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SchedulerEvent:
    job: str
    status: str  # "success" | "failed"
    triggered_at: str
    target: str  # 대상 날짜/기간 (e.g. "2026-02-27", "2026-W08")
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
                event.job, event.target, event.error,
            )
        else:
            logger.info(
                "Scheduler job '%s' %s (target=%s)",
                event.job, event.status, event.target,
            )
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_notifier.py -v`
Expected: All PASS

**Step 5: Commit**

```
feat(scheduler): add Notifier ABC, LogNotifier, SchedulerEvent
```

---

### Task 3: SchedulerHistory — 실행 이력 관리

**Files:**
- Create: `src/workrecap/scheduler/history.py`
- Test: `tests/unit/test_scheduler_history.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_scheduler_history.py
"""SchedulerHistory — 실행 이력 저장/조회 테스트."""

import json

import pytest

from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.notifier import SchedulerEvent


@pytest.fixture()
def history(tmp_path):
    return SchedulerHistory(tmp_path / "scheduler_history.json")


class TestSchedulerHistory:
    def test_empty_history(self, history):
        assert history.list() == []

    def test_record_and_list(self, history):
        event = SchedulerEvent(
            job="daily", status="success",
            triggered_at="2026-02-28T02:00:00+09:00",
            completed_at="2026-02-28T02:05:00+09:00",
            target="2026-02-27",
        )
        history.record(event)
        entries = history.list()
        assert len(entries) == 1
        assert entries[0]["job"] == "daily"
        assert entries[0]["status"] == "success"
        assert entries[0]["target"] == "2026-02-27"

    def test_record_failure(self, history):
        event = SchedulerEvent(
            job="weekly", status="failed",
            triggered_at="t1", target="2026-W08",
            error="SummarizeError: no data",
        )
        history.record(event)
        entries = history.list()
        assert entries[0]["error"] == "SummarizeError: no data"

    def test_max_entries(self, history):
        for i in range(200):
            event = SchedulerEvent(
                job="daily", status="success",
                triggered_at=f"t{i}", target=f"d{i}",
            )
            history.record(event)
        entries = history.list()
        assert len(entries) == 100  # default max

    def test_persistence(self, tmp_path):
        path = tmp_path / "history.json"
        h1 = SchedulerHistory(path)
        h1.record(SchedulerEvent(
            job="daily", status="success", triggered_at="t1", target="d1",
        ))
        h2 = SchedulerHistory(path)
        assert len(h2.list()) == 1

    def test_list_filter_by_job(self, history):
        history.record(SchedulerEvent(
            job="daily", status="success", triggered_at="t1", target="d1",
        ))
        history.record(SchedulerEvent(
            job="weekly", status="success", triggered_at="t2", target="w1",
        ))
        assert len(history.list(job="daily")) == 1
        assert len(history.list(job="weekly")) == 1
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_history.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/workrecap/scheduler/history.py
"""스케줄러 실행 이력 관리 — JSON 파일 기반."""

import json
import logging
import threading
from dataclasses import asdict
from pathlib import Path

from workrecap.scheduler.notifier import SchedulerEvent

logger = logging.getLogger(__name__)

_DEFAULT_MAX = 100


class SchedulerHistory:
    def __init__(self, path: Path, max_entries: int = _DEFAULT_MAX) -> None:
        self._path = path
        self._max = max_entries
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        with open(self._path) as f:
            return json.load(f)

    def _save(self, entries: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def record(self, event: SchedulerEvent) -> None:
        with self._lock:
            entries = self._load()
            entries.append(asdict(event))
            if len(entries) > self._max:
                entries = entries[-self._max :]
            self._save(entries)

    def list(self, job: str | None = None, limit: int | None = None) -> list[dict]:
        entries = self._load()
        if job:
            entries = [e for e in entries if e["job"] == job]
        if limit:
            entries = entries[-limit:]
        return entries
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_history.py -v`
Expected: All PASS

**Step 5: Commit**

```
feat(scheduler): add SchedulerHistory for execution tracking
```

---

### Task 4: Scheduler jobs — 파이프라인 실행 함수

**Files:**
- Create: `src/workrecap/scheduler/jobs.py`
- Test: `tests/unit/test_scheduler_jobs.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_scheduler_jobs.py
"""스케줄러 job 함수 테스트 — daily, weekly, monthly, yearly."""

import asyncio
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from workrecap.scheduler.config import ScheduleConfig, DailySchedule
from workrecap.scheduler.jobs import run_daily_job, run_weekly_job, run_monthly_job, run_yearly_job
from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.notifier import LogNotifier


@pytest.fixture()
def history(tmp_path):
    return SchedulerHistory(tmp_path / "history.json")


@pytest.fixture()
def notifier():
    return LogNotifier()


@pytest.fixture()
def schedule_config():
    return ScheduleConfig(enabled=True)


class TestRunDailyJob:
    def test_runs_yesterday_pipeline(self, tmp_path, history, notifier, schedule_config):
        mock_orch = MagicMock()
        mock_orch.run_daily.return_value = tmp_path / "summary.md"

        with patch("workrecap.scheduler.jobs._build_orchestrator", return_value=mock_orch):
            asyncio.get_event_loop().run_until_complete(
                run_daily_job(schedule_config, history, notifier)
            )

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        mock_orch.run_daily.assert_called_once_with(yesterday, types=None)
        entries = history.list()
        assert len(entries) == 1
        assert entries[0]["status"] == "success"
        assert entries[0]["target"] == yesterday

    def test_records_failure(self, history, notifier, schedule_config):
        mock_orch = MagicMock()
        mock_orch.run_daily.side_effect = Exception("boom")

        with patch("workrecap.scheduler.jobs._build_orchestrator", return_value=mock_orch):
            asyncio.get_event_loop().run_until_complete(
                run_daily_job(schedule_config, history, notifier)
            )

        entries = history.list()
        assert entries[0]["status"] == "failed"
        assert "boom" in entries[0]["error"]


class TestRunWeeklyJob:
    def test_runs_last_week_summary(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.weekly.return_value = tmp_path / "W08.md"

        with patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer):
            asyncio.get_event_loop().run_until_complete(
                run_weekly_job(schedule_config, history, notifier)
            )

        mock_summarizer.weekly.assert_called_once()
        entries = history.list()
        assert entries[0]["status"] == "success"
        assert entries[0]["job"] == "weekly"


class TestRunMonthlyJob:
    def test_runs_last_month_summary(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.monthly.return_value = tmp_path / "02.md"
        mock_summarizer.weekly.return_value = tmp_path / "W.md"

        with patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer):
            asyncio.get_event_loop().run_until_complete(
                run_monthly_job(schedule_config, history, notifier)
            )

        mock_summarizer.monthly.assert_called_once()
        entries = history.list()
        assert entries[0]["status"] == "success"
        assert entries[0]["job"] == "monthly"


class TestRunYearlyJob:
    def test_runs_last_year_summary(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.yearly.return_value = tmp_path / "yearly.md"
        mock_summarizer.weekly.return_value = tmp_path / "W.md"
        mock_summarizer.monthly.return_value = tmp_path / "M.md"

        with patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer):
            asyncio.get_event_loop().run_until_complete(
                run_yearly_job(schedule_config, history, notifier)
            )

        mock_summarizer.yearly.assert_called_once()
        entries = history.list()
        assert entries[0]["status"] == "success"
        assert entries[0]["job"] == "yearly"
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/workrecap/scheduler/jobs.py
"""스케줄러 job 함수 — daily, weekly, monthly, yearly 파이프라인 실행."""

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
    """Build full pipeline orchestrator (fetch→normalize→summarize)."""
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
        config, daily_state=ds, llm=llm if daily.enrich else None,
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
    """해당 월에 걸치는 모든 ISO (year, week) 튜플."""
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
    """매일 실행: 전날 데이터에 대해 fetch→normalize→summarize."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    triggered_at = _now_iso()
    try:
        config = AppConfig()
        orch = _build_orchestrator(config, schedule_config)
        orch.run_daily(yesterday, types=None)
        event = SchedulerEvent(
            job="daily", status="success",
            triggered_at=triggered_at, completed_at=_now_iso(),
            target=yesterday,
        )
    except Exception as e:
        logger.exception("Scheduler daily job failed for %s", yesterday)
        event = SchedulerEvent(
            job="daily", status="failed",
            triggered_at=triggered_at, completed_at=_now_iso(),
            target=yesterday, error=str(e),
        )
    history.record(event)
    await notifier.notify(event)


async def run_weekly_job(
    schedule_config: ScheduleConfig,
    history: SchedulerHistory,
    notifier: Notifier,
) -> None:
    """매주 실행: 지난주 weekly summary 생성."""
    last_week = date.today() - timedelta(weeks=1)
    iso_year, iso_week, _ = last_week.isocalendar()
    target = f"{iso_year}-W{iso_week:02d}"
    triggered_at = _now_iso()
    try:
        config = AppConfig()
        summarizer = _build_summarizer(config)
        summarizer.weekly(iso_year, iso_week, force=False)
        event = SchedulerEvent(
            job="weekly", status="success",
            triggered_at=triggered_at, completed_at=_now_iso(),
            target=target,
        )
    except Exception as e:
        logger.exception("Scheduler weekly job failed for %s", target)
        event = SchedulerEvent(
            job="weekly", status="failed",
            triggered_at=triggered_at, completed_at=_now_iso(),
            target=target, error=str(e),
        )
    history.record(event)
    await notifier.notify(event)


async def run_monthly_job(
    schedule_config: ScheduleConfig,
    history: SchedulerHistory,
    notifier: Notifier,
) -> None:
    """매월 실행: 지난달 weekly→monthly cascade summary 생성."""
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
        # Generate weekly summaries for the month first
        for wy, ww in _weeks_in_month(last_year, last_month):
            try:
                summarizer.weekly(wy, ww, force=False)
            except SummarizeError:
                pass
        summarizer.monthly(last_year, last_month, force=False)
        event = SchedulerEvent(
            job="monthly", status="success",
            triggered_at=triggered_at, completed_at=_now_iso(),
            target=target,
        )
    except Exception as e:
        logger.exception("Scheduler monthly job failed for %s", target)
        event = SchedulerEvent(
            job="monthly", status="failed",
            triggered_at=triggered_at, completed_at=_now_iso(),
            target=target, error=str(e),
        )
    history.record(event)
    await notifier.notify(event)


async def run_yearly_job(
    schedule_config: ScheduleConfig,
    history: SchedulerHistory,
    notifier: Notifier,
) -> None:
    """매년 실행: 작년 weekly→monthly→yearly cascade summary 생성."""
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
            job="yearly", status="success",
            triggered_at=triggered_at, completed_at=_now_iso(),
            target=target,
        )
    except Exception as e:
        logger.exception("Scheduler yearly job failed for %s", target)
        event = SchedulerEvent(
            job="yearly", status="failed",
            triggered_at=triggered_at, completed_at=_now_iso(),
            target=target, error=str(e),
        )
    history.record(event)
    await notifier.notify(event)
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_jobs.py -v`
Expected: All PASS

**Step 5: Commit**

```
feat(scheduler): add job functions for daily/weekly/monthly/yearly
```

---

### Task 5: SchedulerService — APScheduler 래퍼

**Files:**
- Create: `src/workrecap/scheduler/core.py`
- Modify: `pyproject.toml` (add `apscheduler` dependency)
- Test: `tests/unit/test_scheduler_core.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_scheduler_core.py
"""SchedulerService — APScheduler 래퍼 테스트."""

import textwrap

import pytest

from workrecap.scheduler.config import ScheduleConfig
from workrecap.scheduler.core import SchedulerService
from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.notifier import LogNotifier


@pytest.fixture()
def schedule_config():
    return ScheduleConfig(enabled=True, timezone="Asia/Seoul")


@pytest.fixture()
def history(tmp_path):
    return SchedulerHistory(tmp_path / "history.json")


@pytest.fixture()
def service(schedule_config, history):
    return SchedulerService(schedule_config, history, LogNotifier())


class TestSchedulerService:
    def test_create(self, service):
        assert service is not None

    def test_start_registers_jobs(self, service):
        service.start()
        try:
            jobs = service.get_jobs()
            job_ids = [j["id"] for j in jobs]
            assert "daily" in job_ids
            assert "weekly" in job_ids
            assert "monthly" in job_ids
            assert "yearly" in job_ids
        finally:
            service.shutdown()

    def test_status_running(self, service):
        service.start()
        try:
            status = service.status()
            assert status["state"] == "running"
            assert len(status["jobs"]) == 4
        finally:
            service.shutdown()

    def test_status_stopped(self, service):
        status = service.status()
        assert status["state"] == "stopped"

    def test_pause_resume(self, service):
        service.start()
        try:
            service.pause()
            assert service.status()["state"] == "paused"
            service.resume()
            assert service.status()["state"] == "running"
        finally:
            service.shutdown()

    def test_disabled_config_no_start(self):
        cfg = ScheduleConfig(enabled=False)
        svc = SchedulerService(cfg, SchedulerHistory(None), LogNotifier())
        svc.start()
        assert svc.status()["state"] == "disabled"

    def test_get_jobs_includes_next_run(self, service):
        service.start()
        try:
            jobs = service.get_jobs()
            for j in jobs:
                assert "next_run" in j
        finally:
            service.shutdown()
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_core.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Add dependency + write implementation**

Add to `pyproject.toml` dependencies:
```
"apscheduler>=3.10,<4.0",
```

```python
# src/workrecap/scheduler/core.py
"""SchedulerService — APScheduler AsyncIOScheduler 래퍼."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from workrecap.scheduler.config import ScheduleConfig
from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.jobs import (
    run_daily_job,
    run_monthly_job,
    run_weekly_job,
    run_yearly_job,
)
from workrecap.scheduler.notifier import Notifier

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(
        self,
        config: ScheduleConfig,
        history: SchedulerHistory,
        notifier: Notifier,
    ) -> None:
        self._config = config
        self._history = history
        self._notifier = notifier
        self._scheduler: AsyncIOScheduler | None = None
        self._paused = False

    def start(self) -> None:
        if not self._config.enabled:
            logger.info("Scheduler disabled in config")
            return

        tz = self._config.timezone
        self._scheduler = AsyncIOScheduler(timezone=tz)

        daily = self._config.daily
        self._scheduler.add_job(
            run_daily_job,
            CronTrigger(hour=daily.hour, minute=daily.minute, timezone=tz),
            id="daily",
            args=[self._config, self._history, self._notifier],
            replace_existing=True,
        )

        weekly = self._config.weekly
        self._scheduler.add_job(
            run_weekly_job,
            CronTrigger(
                day_of_week=weekly.day,
                hour=weekly.hour, minute=weekly.minute,
                timezone=tz,
            ),
            id="weekly",
            args=[self._config, self._history, self._notifier],
            replace_existing=True,
        )

        monthly = self._config.monthly
        self._scheduler.add_job(
            run_monthly_job,
            CronTrigger(
                day=monthly.day,
                hour=monthly.hour, minute=monthly.minute,
                timezone=tz,
            ),
            id="monthly",
            args=[self._config, self._history, self._notifier],
            replace_existing=True,
        )

        yearly = self._config.yearly
        self._scheduler.add_job(
            run_yearly_job,
            CronTrigger(
                month=yearly.month, day=yearly.day,
                hour=yearly.hour, minute=yearly.minute,
                timezone=tz,
            ),
            id="yearly",
            args=[self._config, self._history, self._notifier],
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info("Scheduler started (tz=%s)", tz)

    def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._paused = False

    def pause(self) -> None:
        if self._scheduler:
            self._scheduler.pause()
            self._paused = True

    def resume(self) -> None:
        if self._scheduler:
            self._scheduler.resume()
            self._paused = False

    def status(self) -> dict:
        if not self._config.enabled:
            return {"state": "disabled", "jobs": []}
        if self._scheduler is None:
            return {"state": "stopped", "jobs": []}
        if self._paused:
            return {"state": "paused", "jobs": self.get_jobs()}
        return {"state": "running", "jobs": self.get_jobs()}

    def get_jobs(self) -> list[dict]:
        if not self._scheduler:
            return []
        result = []
        for job in self._scheduler.get_jobs():
            next_run = job.next_run_time
            result.append({
                "id": job.id,
                "next_run": next_run.isoformat() if next_run else None,
            })
        return result
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_scheduler_core.py -v`
Expected: All PASS

**Step 5: Commit**

```
feat(scheduler): add SchedulerService with APScheduler integration
```

---

### Task 6: FastAPI lifespan 통합

**Files:**
- Modify: `src/workrecap/api/app.py`
- Modify: `src/workrecap/config.py` (add `schedule_config_path` property)
- Test: `tests/unit/test_api.py` (add scheduler lifespan test)

**Step 1: Write the failing test**

Add to `tests/unit/test_api.py`:

```python
class TestSchedulerLifespan:
    def test_scheduler_available_in_app_state(self, client):
        """lifespan에서 scheduler가 app.state에 등록되는지 확인."""
        # 스케줄러 status 엔드포인트로 간접 확인
        resp = client.get("/api/scheduler/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "state" in data
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_api.py::TestSchedulerLifespan -v`
Expected: FAIL — 404 (route not yet added)

**Step 3: Write implementation**

Add to `src/workrecap/config.py`:
```python
    @property
    def schedule_config_path(self) -> Path:
        return Path("schedule.toml")
```

Modify `src/workrecap/api/app.py`:

```python
"""FastAPI 앱 팩토리 + CORS + exception handler + 정적 파일 서빙."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from workrecap.api.deps import get_config
from workrecap.api.routes import (
    fetch,
    normalize,
    pipeline,
    query,
    scheduler as scheduler_routes,
    summaries_available,
    summarize_pipeline,
    summary,
)
from workrecap.exceptions import WorkRecapError
from workrecap.logging_config import setup_logging

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from workrecap.scheduler.config import ScheduleConfig
    from workrecap.scheduler.core import SchedulerService
    from workrecap.scheduler.history import SchedulerHistory
    from workrecap.scheduler.notifier import LogNotifier

    config = get_config()
    schedule_config = ScheduleConfig.from_toml(config.schedule_config_path)
    history = SchedulerHistory(config.state_dir / "scheduler_history.json")
    notifier = LogNotifier()
    scheduler = SchedulerService(schedule_config, history, notifier)
    scheduler.start()
    app.state.scheduler = scheduler
    app.state.scheduler_history = history
    yield
    scheduler.shutdown()


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="work-recap", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
    app.include_router(fetch.router, prefix="/api/pipeline/fetch", tags=["fetch"])
    app.include_router(normalize.router, prefix="/api/pipeline/normalize", tags=["normalize"])
    app.include_router(
        summarize_pipeline.router,
        prefix="/api/pipeline/summarize",
        tags=["summarize"],
    )
    app.include_router(summary.router, prefix="/api/summary", tags=["summary"])
    app.include_router(summaries_available.router, prefix="/api/summaries", tags=["summaries"])
    app.include_router(query.router, prefix="/api", tags=["query"])
    app.include_router(scheduler_routes.router, prefix="/api/scheduler", tags=["scheduler"])

    @app.exception_handler(WorkRecapError)
    async def handle_workrecap_error(request: Request, exc: WorkRecapError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )

    # 정적 파일 서빙 (API 라우터 뒤에 마운트)
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_api.py::TestSchedulerLifespan -v`
Expected: PASS (depends on Task 7 routes)

**Step 5: Commit**

```
feat(api): integrate scheduler into FastAPI lifespan
```

---

### Task 7: Scheduler API routes

**Files:**
- Create: `src/workrecap/api/routes/scheduler.py`
- Test: `tests/unit/test_api_scheduler.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_api_scheduler.py
"""Scheduler API 엔드포인트 테스트."""

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from workrecap.api.app import create_app
from workrecap.api.deps import get_config
from workrecap.config import AppConfig


@pytest.fixture()
def test_config(tmp_path):
    # Create schedule.toml for the test
    toml = tmp_path / "schedule.toml"
    toml.write_text(textwrap.dedent("""\
    [scheduler]
    enabled = true
    timezone = "Asia/Seoul"

    [scheduler.daily]
    time = "02:00"
    """))
    return AppConfig(
        ghes_url="https://github.example.com",
        ghes_token="test-token",
        username="testuser",
        data_dir=tmp_path / "data",
        prompts_dir=tmp_path / "prompts",
    )


@pytest.fixture()
def client(test_config, tmp_path):
    app = create_app()
    app.dependency_overrides[get_config] = lambda: test_config
    return TestClient(app)


class TestSchedulerStatus:
    def test_get_status(self, client):
        resp = client.get("/api/scheduler/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "state" in data
        assert "jobs" in data

    def test_pause_and_resume(self, client):
        resp = client.put("/api/scheduler/pause")
        assert resp.status_code == 200
        resp = client.get("/api/scheduler/status")
        assert resp.json()["state"] == "paused"

        resp = client.put("/api/scheduler/resume")
        assert resp.status_code == 200
        resp = client.get("/api/scheduler/status")
        assert resp.json()["state"] == "running"


class TestSchedulerHistory:
    def test_get_empty_history(self, client):
        resp = client.get("/api/scheduler/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_history_with_job_filter(self, client):
        resp = client.get("/api/scheduler/history?job=daily")
        assert resp.status_code == 200


class TestSchedulerTrigger:
    @patch("workrecap.api.routes.scheduler.run_daily_job", new_callable=AsyncMock)
    def test_trigger_daily(self, mock_job, client):
        resp = client.post("/api/scheduler/trigger/daily")
        assert resp.status_code == 202
        assert resp.json()["triggered"] == "daily"

    def test_trigger_invalid_job(self, client):
        resp = client.post("/api/scheduler/trigger/invalid")
        assert resp.status_code == 404
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_api_scheduler.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# src/workrecap/api/routes/scheduler.py
"""Scheduler 엔드포인트 — status, history, trigger, pause, resume."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.jobs import (
    run_daily_job,
    run_monthly_job,
    run_weekly_job,
    run_yearly_job,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_JOB_FUNCS = {
    "daily": run_daily_job,
    "weekly": run_weekly_job,
    "monthly": run_monthly_job,
    "yearly": run_yearly_job,
}


def _get_scheduler(request: Request):
    return request.app.state.scheduler


def _get_history(request: Request) -> SchedulerHistory:
    return request.app.state.scheduler_history


@router.get("/status")
def get_status(request: Request):
    scheduler = _get_scheduler(request)
    return scheduler.status()


@router.get("/history")
def get_history(
    request: Request,
    job: str | None = Query(default=None),
    limit: int | None = Query(default=None),
):
    history = _get_history(request)
    return history.list(job=job, limit=limit)


@router.post("/trigger/{job_name}", status_code=202)
async def trigger_job(job_name: str, request: Request):
    if job_name not in _JOB_FUNCS:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_name}")
    scheduler = _get_scheduler(request)
    history = _get_history(request)

    job_func = _JOB_FUNCS[job_name]
    asyncio.create_task(
        job_func(scheduler._config, history, scheduler._notifier)
    )
    return {"triggered": job_name}


@router.put("/pause")
def pause_scheduler(request: Request):
    scheduler = _get_scheduler(request)
    scheduler.pause()
    return {"state": "paused"}


@router.put("/resume")
def resume_scheduler(request: Request):
    scheduler = _get_scheduler(request)
    scheduler.resume()
    return {"state": "running"}
```

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_api_scheduler.py -v`
Expected: All PASS

**Step 5: Commit**

```
feat(api): add scheduler routes (status, history, trigger, pause, resume)
```

---

### Task 8: Frontend — Scheduler 탭 추가

**Files:**
- Create: `frontend/js/scheduler.js`
- Modify: `frontend/js/app.js` (register scheduler component)
- Modify: `frontend/index.html` (add Scheduler tab + section)
- Modify: `frontend/style.css` (scheduler styles)

**Step 1: Write the scheduler component**

```javascript
// frontend/js/scheduler.js
import { api } from "./api.js";

export function schedulerComponent() {
  return {
    state: "loading",
    jobs: [],
    history: [],
    triggerBusy: false,
    triggerResult: "",

    async init() {
      await this.refresh();
    },

    async refresh() {
      try {
        const [statusResp, historyResp] = await Promise.all([
          api("GET", "/scheduler/status"),
          api("GET", "/scheduler/history?limit=20"),
        ]);
        const status = await statusResp.json();
        const historyData = await historyResp.json();
        this.state = status.state;
        this.jobs = status.jobs;
        this.history = historyData.reverse();
      } catch (e) {
        this.state = "error";
      }
    },

    async togglePause() {
      const action = this.state === "paused" ? "resume" : "pause";
      await api("PUT", `/scheduler/${action}`);
      await this.refresh();
    },

    async triggerJob(jobName) {
      this.triggerBusy = true;
      this.triggerResult = "";
      try {
        await api("POST", `/scheduler/trigger/${jobName}`);
        this.triggerResult = `${jobName} triggered`;
        setTimeout(() => this.refresh(), 2000);
      } catch (e) {
        this.triggerResult = `Error: ${e.message}`;
      } finally {
        this.triggerBusy = false;
      }
    },

    formatTime(iso) {
      if (!iso) return "-";
      return new Date(iso).toLocaleString("ko-KR", {
        month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      });
    },
  };
}
```

**Step 2: Register in app.js**

Add import and registration:
```javascript
import { schedulerComponent } from "./scheduler.js";
// In alpine:init:
Alpine.data("scheduler", schedulerComponent);
```

**Step 3: Add tab and section to index.html**

Add to nav (after Ask):
```html
<li>
  <a href="#"
     :class="$store.app.tab === 'scheduler' && 'active'"
     @click.prevent="$store.app.tab = 'scheduler'">Scheduler</a>
</li>
```

Add section before `</main>`:
```html
<!-- ===== Scheduler Tab ===== -->
<section x-show="$store.app.tab === 'scheduler'"
         x-data="scheduler"
         x-cloak>

  <h2>Scheduler</h2>

  <!-- Status -->
  <article>
    <div class="scheduler-header">
      <h3>Status</h3>
      <span class="scheduler-state" :class="'state-' + state" x-text="state"></span>
      <button class="outline" style="margin-left:auto"
              @click="togglePause()"
              x-show="state === 'running' || state === 'paused'"
              x-text="state === 'paused' ? 'Resume' : 'Pause'">
      </button>
      <button class="outline" @click="refresh()">Refresh</button>
    </div>
  </article>

  <!-- Scheduled Jobs -->
  <article x-show="jobs.length">
    <h3>Scheduled Jobs</h3>
    <table>
      <thead><tr><th>Job</th><th>Next Run</th><th></th></tr></thead>
      <tbody>
        <template x-for="job in jobs" :key="job.id">
          <tr>
            <td x-text="job.id"></td>
            <td x-text="formatTime(job.next_run)"></td>
            <td>
              <button class="outline" style="padding:0.25rem 0.5rem;font-size:0.85rem"
                      @click="triggerJob(job.id)"
                      :disabled="triggerBusy">Run Now</button>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <small x-show="triggerResult" x-text="triggerResult" style="color:var(--pico-primary)"></small>
  </article>

  <!-- History -->
  <article>
    <h3>Recent History</h3>
    <template x-if="history.length === 0">
      <p>No executions yet.</p>
    </template>
    <table x-show="history.length">
      <thead><tr><th>Job</th><th>Target</th><th>Status</th><th>Time</th><th>Error</th></tr></thead>
      <tbody>
        <template x-for="(h, i) in history" :key="i">
          <tr>
            <td x-text="h.job"></td>
            <td x-text="h.target"></td>
            <td>
              <span class="history-badge" :class="'badge-' + h.status" x-text="h.status"></span>
            </td>
            <td x-text="formatTime(h.triggered_at)"></td>
            <td x-text="h.error || '-'" style="max-width:200px;overflow:hidden;text-overflow:ellipsis"></td>
          </tr>
        </template>
      </tbody>
    </table>
  </article>
</section>
```

**Step 4: Add styles to style.css**

```css
/* Scheduler */
.scheduler-header { display: flex; align-items: center; gap: 1rem; }
.scheduler-state { font-weight: bold; text-transform: uppercase; font-size: 0.9rem; }
.state-running { color: var(--pico-color-green-500, #22c55e); }
.state-paused { color: var(--pico-color-yellow-500, #eab308); }
.state-disabled { color: var(--pico-muted-color); }
.state-stopped { color: var(--pico-del-color, #e53e3e); }
.state-error { color: var(--pico-del-color, #e53e3e); }
.history-badge { padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.8rem; }
.badge-success { background: var(--pico-color-green-100, #dcfce7); color: var(--pico-color-green-700, #15803d); }
.badge-failed { background: var(--pico-del-color, #fecaca); color: #991b1b; }
[data-theme="dark"] .badge-success { background: #14532d; color: #86efac; }
[data-theme="dark"] .badge-failed { background: #7f1d1d; color: #fca5a5; }
```

**Step 5: Manually test in browser**

1. Start server: `uvicorn workrecap.api.app:app --reload`
2. Open http://localhost:8000
3. Click "Scheduler" tab
4. Verify: status displays, jobs listed, history table shown

**Step 6: Commit**

```
feat(frontend): add Scheduler tab with status, jobs, and history
```

---

### Task 9: Update pyproject.toml + schedule.toml + CLAUDE.md

**Files:**
- Modify: `pyproject.toml` (add `apscheduler` dep — may already be done in Task 5)
- Verify: `schedule.toml` exists at project root
- Modify: `CLAUDE.md` (document scheduler module)

**Step 1: Verify dependency is added**

Check `pyproject.toml` includes `"apscheduler>=3.10,<4.0"` in `[project.dependencies]`.

**Step 2: Update CLAUDE.md**

Add to Architecture / Key modules section:

```markdown
- `src/workrecap/scheduler/config.py` — `ScheduleConfig`: parses `schedule.toml` (project root). Dataclass hierarchy: `DailySchedule`, `WeeklySchedule`, `MonthlySchedule`, `YearlySchedule`, `NotificationConfig`. `from_toml(path)` class method, defaults when file missing. `hour`/`minute` properties parse `"HH:MM"` time strings.
- `src/workrecap/scheduler/core.py` — `SchedulerService`: APScheduler `AsyncIOScheduler` wrapper. `start()` registers CronTrigger jobs (daily/weekly/monthly/yearly). `shutdown()`, `pause()`, `resume()`. `status()` returns `{"state": "running"|"paused"|"stopped"|"disabled", "jobs": [...]}`. `get_jobs()` returns job id + next_run_time. FastAPI lifespan manages lifecycle.
- `src/workrecap/scheduler/jobs.py` — Async job functions: `run_daily_job` (yesterday fetch→normalize→summarize), `run_weekly_job` (last week weekly summary), `run_monthly_job` (last month weekly→monthly cascade), `run_yearly_job` (last year weekly→monthly→yearly cascade). Each records to `SchedulerHistory` and notifies via `Notifier`.
- `src/workrecap/scheduler/history.py` — `SchedulerHistory`: JSON file (`data/state/scheduler_history.json`). Thread-safe. `record(event)`, `list(job?, limit?)`. Max 100 entries (FIFO).
- `src/workrecap/scheduler/notifier.py` — `Notifier` ABC + `LogNotifier`. `SchedulerEvent` dataclass (job, status, triggered_at, target, error). Extensible for Telegram, system notifications.
- `schedule.toml` — Scheduler configuration: `[scheduler]` enabled/timezone, `[scheduler.daily]` time/enrich/batch/workers, `[scheduler.weekly]` day/time, `[scheduler.monthly]` day/time, `[scheduler.yearly]` month/day/time, `[scheduler.notification]` on_failure/on_success.
```

Add to API routes:
```markdown
- `src/workrecap/api/routes/scheduler.py` — Scheduler endpoints. `GET /api/scheduler/status`, `GET /api/scheduler/history`, `POST /api/scheduler/trigger/{job}`, `PUT /api/scheduler/pause`, `PUT /api/scheduler/resume`.
```

**Step 3: Commit**

```
docs: update CLAUDE.md with scheduler module documentation
```

---

### Task 10: Full test suite pass + lint

**Step 1: Run full test suite**

Run: `PYTHONPATH=src pytest -v`
Expected: All tests pass (including ~1026 existing + new scheduler tests)

**Step 2: Run lint**

Run: `ruff check src/ tests/`
Run: `ruff format --check src/ tests/`
Expected: No errors

**Step 3: Fix any issues found**

**Step 4: Final commit if fixes needed**

```
fix: resolve lint/test issues from scheduler integration
```
