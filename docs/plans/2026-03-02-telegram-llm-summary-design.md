# Telegram LLM Summary Design

**Date:** 2026-03-02
**Status:** Approved

## Problem

기존 regex 기반 markdown→plaintext 변환은 가독성이 낮고, 커밋 단위 나열이라 요약이 부족하다. 리포별 기여도 비례 배분과 토픽 기반 요약이 필요.

## Solution: B안 — Summarizer에서 .telegram.txt 생성

기존 `.md` summary를 LLM 입력으로 넣어 텔레그램 최적화 평문 요약을 `.telegram.txt`로 생성. Notifier는 파일만 읽음.

## File Layout

```
data/summaries/{YYYY}/daily/{MM-DD}.telegram.txt
data/summaries/{YYYY}/weekly/W{NN}.telegram.txt
data/summaries/{YYYY}/monthly/{MM}.telegram.txt
data/summaries/{YYYY}/yearly.telegram.txt
```

## Components

### 1. AppConfig — 4개 path 메서드 추가

- `daily_telegram_path(date)` → `summaries/{YYYY}/daily/{MM-DD}.telegram.txt`
- `weekly_telegram_path(year, week)` → `summaries/{YYYY}/weekly/W{NN}.telegram.txt`
- `monthly_telegram_path(year, month)` → `summaries/{YYYY}/monthly/{MM}.telegram.txt`
- `yearly_telegram_path(year)` → `summaries/{YYYY}/yearly.telegram.txt`

### 2. Prompt Template — `prompts/telegram.md`

단일 공용 템플릿 (daily/weekly/monthly/yearly 공용):

- 4000자 이내 평문 (마크다운/HTML 금지)
- 리포별 기여도 비례 글 양 배분
- 각 항목 앞에 `✅` 마커
- 커밋 단위가 아닌 토픽 단위 묶음 요약
- 헤딩 이모지: 📋 개요, 📌 주요 활동, 🏆 주요 성과, 💻 커밋, 🔀 PR, 🎯 이슈, 👀 리뷰
- URL/링크 문법 제거
- `<!-- SPLIT -->` 분할: static instructions → system prompt, dynamic (level/target) → user content

### 3. SummarizerService — `telegram_summary()` 메서드

```python
def telegram_summary(self, level: str, target: str) -> Path:
```

- `.md` 읽기 → LLM 호출 (task="telegram") → `.telegram.txt` 저장
- 4096자 hard trim 안전장치
- Staleness: `.md` mtime > `.telegram.txt` mtime → 재생성
- 헬퍼: `_resolve_summary_path(level, target)`, `_resolve_telegram_path(level, target)`

### 4. LLM Task Routing

`.provider/config.toml`에 `[tasks.telegram]` 추가 — 경량 모델(haiku) 사용으로 비용 최소화.

### 5. Scheduler Jobs

각 job 함수에서 파이프라인 성공 후 `telegram_summary()` 호출. 실패 시 graceful — `.telegram.txt` 미생성이어도 `.md`는 보존.

### 6. TelegramNotifier 간소화

- `_format_for_telegram()`, `_trim_to_fit()`, `_HEADING_EMOJIS`, `_ITEM_RE` 제거
- `_read_summary()` → `.telegram.txt` 파일 읽기로 변경
- `_build_single_message()` 유지 (header + body 조합)

### 7. Staleness

`.telegram.txt`는 `.md`에 의존. `.md` mtime > `.telegram.txt` mtime → stale.

## Testing

- `test_config.py`: 4개 path 메서드
- `test_summarizer.py`: `telegram_summary()` (LLM mock)
- `test_scheduler_notifier.py`: regex 테스트 제거, `.telegram.txt` 읽기 테스트 추가
- `test_scheduler_jobs.py`: `telegram_summary()` 호출 확인
