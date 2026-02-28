"""SchedulerService -- APScheduler AsyncIOScheduler 래퍼."""

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
                hour=weekly.hour,
                minute=weekly.minute,
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
                hour=monthly.hour,
                minute=monthly.minute,
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
                month=yearly.month,
                day=yearly.day,
                hour=yearly.hour,
                minute=yearly.minute,
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
            result.append(
                {
                    "id": job.id,
                    "next_run": next_run.isoformat() if next_run else None,
                }
            )
        return result
