# Telegram Notifier Design — 스케줄 결과 Telegram 알림

**Date:** 2026-02-28
**Status:** Approved

## 요구사항

- 스케줄 실행 결과(daily/weekly/monthly/yearly)를 Telegram으로 수신
- 성공/실패 모두 전송 (on_failure/on_success 설정 무관, 항상 전송)
- 성공 시 생성된 summary markdown 전체를 메시지에 포함
- 메시지 수신만 (봇 명령어/대화 기능 불필요)

## 접근 방식: httpx 직접 호출

기존 프로젝트 dependency인 httpx로 Telegram Bot API `sendMessage` 직접 호출.
새 라이브러리 추가 없음.

## 모듈 구조

```
src/workrecap/scheduler/
├── notifier.py          ← 기존: SchedulerEvent, Notifier ABC, LogNotifier
│                          신규: CompositeNotifier, TelegramNotifier
```

## CompositeNotifier

여러 Notifier를 묶어 순차 실행. 하나가 실패해도 나머지 계속 실행.

```python
class CompositeNotifier(Notifier):
    def __init__(self, notifiers: list[Notifier]):
        self._notifiers = notifiers

    async def notify(self, event):
        for n in self._notifiers:
            try:
                await n.notify(event)
            except Exception:
                pass  # log warning, continue
```

## TelegramNotifier

httpx.AsyncClient로 `POST https://api.telegram.org/bot{token}/sendMessage` 호출.

생성자 파라미터: `bot_token`, `chat_id`, `config` (AppConfig, summary 파일 경로 유도용).

### 메시지 포맷

성공 시:
```
✅ [daily] 완료 — 2026-02-27

⏱ 02:00:00 → 02:05:23 (5m 23s)

─────────────────
[summary markdown 전체]
```

실패 시:
```
❌ [daily] 실패 — 2026-02-27

⏱ 02:00:00 → 02:05:23 (5m 23s)

Error: Connection timeout to GHES API
```

### Summary 파일 경로 유도

`event.job`과 `event.target`으로 파일 경로 결정:
- daily (`target="2026-02-27"`): `data/summaries/2026/daily/02-27.md`
- weekly (`target="2026-W09"`): `data/summaries/2026/weekly/W09.md`
- monthly (`target="2026-02"`): `data/summaries/2026/monthly/02.md`
- yearly (`target="2026"`): `data/summaries/2026/yearly.md`

파일 없거나 읽기 실패 시 → summary 없이 상태만 전송.

### Telegram 4096자 제한 처리

- 헤더 + summary 합산 4096자 초과 시 → 헤더를 첫 메시지, summary를 후속 메시지들로 분할
- summary 자체가 4096자 초과 시 → 4096자 단위로 순차 전송

### 전송 실패 처리

httpx 에러/타임아웃 → 로그 경고만 남기고 무시 (graceful degradation).
스케줄러 job 자체에 영향 없음.

## 설정

### `.env`

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789
```

### `AppConfig` (config.py)

```python
telegram_bot_token: str = ""
telegram_chat_id: str = ""
```

### `schedule.toml`

```toml
[scheduler.telegram]
enabled = true
```

### `ScheduleConfig` (scheduler/config.py)

```python
@dataclass
class TelegramConfig:
    enabled: bool = False
```

## FastAPI lifespan 통합

Notifier 조립:

```python
notifiers = [LogNotifier()]
if telegram_config.enabled and config.telegram_bot_token:
    notifiers.append(TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id, config))
notifier = CompositeNotifier(notifiers) if len(notifiers) > 1 else notifiers[0]
```

- `telegram.enabled = true`이지만 token 비어있으면 → 로그 경고 + Telegram 스킵
- 기존 LogNotifier 동작은 항상 유지

## 의존성

없음 (httpx 기존 사용).
