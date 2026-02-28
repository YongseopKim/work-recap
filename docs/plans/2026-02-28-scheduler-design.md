# Scheduler Design — APScheduler 내장 자동 스케줄링

**Date:** 2026-02-28
**Status:** Approved

## 요구사항

- macOS + Ubuntu 크로스 플랫폼
- FastAPI 서버(항상 실행 중)에 스케줄러 내장
- 전체 계층 자동화: daily → weekly → monthly → yearly
- 새벽 시간대 자동 처리
- 확장 가능한 알림 구조 (향후 시스템 알림, Telegram Bot)
- 로그 + Web UI 모니터링

## 접근 방식: APScheduler 내장

FastAPI 프로세스에 APScheduler(v3.x, AsyncIOScheduler)를 내장.
uvicorn의 이벤트 루프를 공유하며 비동기로 스케줄 실행.

## 모듈 구조

```
src/workrecap/
├── scheduler/
│   ├── __init__.py
│   ├── core.py          ← SchedulerService (APScheduler 래퍼)
│   ├── jobs.py           ← 스케줄 작업 정의 (daily, weekly, monthly, yearly)
│   ├── config.py         ← ScheduleConfig (스케줄 설정 모델)
│   ├── history.py        ← SchedulerHistory (실행 이력 관리)
│   └── notifier.py       ← Notifier ABC + LogNotifier (확장점)
```

## 스케줄 설정

`schedule.toml`:

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

## 계층 실행 로직

| 시간 | 트리거 | 작업 |
|------|--------|------|
| 매일 02:00 | `CronTrigger(hour=2)` | `run_daily(yesterday)` — fetch→normalize→summarize |
| 매주 월 03:00 | `CronTrigger(day_of_week='mon', hour=3)` | `summarize weekly(last_week)` |
| 매월 1일 04:00 | `CronTrigger(day=1, hour=4)` | `summarize monthly(last_month)` |
| 매년 1/1 05:00 | `CronTrigger(month=1, day=1, hour=5)` | `summarize yearly(last_year)` |

각 계층은 독립 스케줄로 분리. daily 실패가 weekly에 영향 없음.
시간 간격으로 리소스 충돌 방지 (daily 완료 후 weekly 실행).

## API 엔드포인트

```
GET  /api/scheduler/status        ← 스케줄러 상태 (running/paused, 등록된 jobs)
GET  /api/scheduler/history       ← 최근 실행 이력 (성공/실패/시간)
POST /api/scheduler/trigger/{job} ← 수동 트리거 (daily/weekly/monthly/yearly)
PUT  /api/scheduler/pause         ← 스케줄러 일시정지
PUT  /api/scheduler/resume        ← 스케줄러 재개
```

## 실행 이력

`data/state/scheduler_history.json`:

```json
[
  {
    "job": "daily",
    "triggered_at": "2026-02-28T02:00:00+09:00",
    "completed_at": "2026-02-28T02:05:23+09:00",
    "status": "success",
    "target": "2026-02-27",
    "error": null
  }
]
```

## 알림 시스템

```python
class Notifier(ABC):
    @abstractmethod
    async def notify(self, event: SchedulerEvent) -> None: ...

class LogNotifier(Notifier):       # 1차 구현
    ...

# 향후 확장:
# class TelegramNotifier(Notifier): ...
# class SystemNotifier(Notifier): ...
```

`SchedulerEvent`: job 이름, 상태, 시간, 에러 메시지 등 데이터클래스.

## 에러 처리

1. 개별 job 실패는 다른 job에 영향 없음
2. 실패한 daily는 FailedDateStore 연동으로 다음 실행 시 자동 재시도
3. `on_failure=true`일 때 Notifier 호출
4. 성공/실패 모두 scheduler_history.json에 기록

## Web UI

기존 3탭(Pipeline, Summaries, Ask)에 4번째 탭 "Scheduler" 추가:
- 스케줄러 상태 (on/off 토글)
- 다음 실행 예정 시간 표시
- 최근 실행 이력 (성공/실패 뱃지)
- 수동 트리거 버튼

## FastAPI 통합

FastAPI lifespan에서 SchedulerService start/shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = SchedulerService(schedule_config)
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown()
```

## 의존성

- `apscheduler>=3.10,<4.0` (v3.x 안정 버전)
