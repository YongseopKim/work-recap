# 설계 결정: Sync + ThreadPoolExecutor를 선택한 이유

## 요약

git-recap의 서비스 레이어는 **동기(sync) 코드 + `ThreadPoolExecutor` 기반 병렬 처리**로 구현되어 있다.
`asyncio` 기반 비동기 전환을 검토했으나, 실질적 이득 대비 전환 비용과 복잡도가 높아 현재 구조를 유지한다.

---

## 현재 아키텍처

```
CLI (Typer, sync)
  ↓
Service Layer (sync methods + ThreadPoolExecutor)
  ↓ ────────────────────────────────────────────
  │  GHESClientPool ← queue.Queue 기반 스레드 안전 풀
  │  ThreadPoolExecutor ← date별/enrichment별 병렬 처리
  │  threading.Lock ← checkpoint, rate limit, usage 보호
  ↓ ────────────────────────────────────────────
Infrastructure (httpx.Client sync, OpenAI/Anthropic sync SDK)
```

### 병렬 처리 지점

| 서비스 | 병렬 단위 | 방식 |
|--------|----------|------|
| Fetcher | PR/commit/issue enrichment | `ThreadPoolExecutor` + `GHESClientPool` |
| Fetcher | date별 처리 | `ThreadPoolExecutor` (`_process_dates_parallel`) |
| Normalizer | date별 처리 | `ThreadPoolExecutor` (`_normalize_range_parallel`) |
| Summarizer | date별 daily 요약 | `ThreadPoolExecutor` (`_daily_range_parallel`) |

`max_workers` 설정(기본 5, CLI `--workers/-w`로 조절)으로 병렬도를 제어한다.

---

## Async 전환 시 이점

### 1. 네트워크 동시성 향상

- **HTTP 호출**: 스레드 5개 = 동시 5개 요청 → async로 100+ 동시 요청 가능
- **LLM 호출**: 동일하게 스레드 제한 없이 다수 동시 호출 가능
- 이론적 **5-10배** 처리량 증가

### 2. 리소스 효율

- 스레드 생성/컨텍스트 스위칭 오버헤드 제거
- 하나의 이벤트 루프에서 수천 개 동시 I/O 처리
- `GHESClientPool` (Queue 기반) 대신 단일 `httpx.AsyncClient` 사용 가능

### 3. FastAPI와의 자연스러운 조합

- FastAPI는 본래 async 프레임워크
- 현재 sync 함수를 `BackgroundTasks`에 넘기면 스레드풀에서 실행됨
- async 전환 시 이벤트 루프 안에서 직접 실행되어 더 효율적

---

## Async 전환을 하지 않는 이유

### 1. 실제 병목이 동시성 모델이 아님

git-recap의 실제 병목 지점:

| 병목 | 원인 | async로 해결? |
|------|------|--------------|
| GitHub Search API 2초 throttle | 30 req/min rate limit | 아니오 — API 제한은 동시성과 무관 |
| LLM 응답 시간 (수 초~수십 초) | 모델 추론 시간 | 부분적 — 동시 호출 수를 늘릴 수는 있으나 API rate limit 존재 |
| GitHub API rate limit (5,000/hr) | 서버 측 제한 | 아니오 — 더 빨리 보내도 429 에러만 증가 |

**핵심**: 외부 API의 rate limit이 처리량의 상한을 결정한다. 동시성 모델을 바꿔도 이 상한은 변하지 않는다.
`ThreadPoolExecutor(max_workers=20)`이면 이미 rate limit에 충분히 도달할 수 있다.

### 2. 전환 비용이 매우 큼

전체 코드베이스에 걸친 대규모 리팩토링이 필요하다:

| 영역 | 변경 범위 | 예상 시간 |
|------|----------|----------|
| Infrastructure (ghes_client, llm_client, client_pool) | 3개 파일, sync→async 전면 교체 | 7-11시간 |
| Services (fetcher, normalizer, summarizer, orchestrator) | 4개 파일, ThreadPoolExecutor→TaskGroup | 22-35시간 |
| State (daily_state, checkpoint) | 2개 파일, Lock→asyncio.Lock | 3-4시간 |
| CLI | 1개 파일, asyncio.run() 래퍼 추가 | 4-6시간 |
| API routes | 6개 파일, async 엔드포인트 전환 | 12-18시간 |
| **테스트 리라이트** | **17+ 파일, ~8,500줄** | **40-60시간** |
| **합계** | **~33개 파일** | **100-160시간** |

테스트 리라이트가 전체의 **40-50%**를 차지한다. 686개 테스트가 안정적으로 통과하는 상태에서 이 비용은 정당화하기 어렵다.

### 3. Async 감염 문제 (Function Coloring Problem)

Python의 async/await는 호출 체인 전체를 감염시킨다:

```python
# 현재 (sync) — 아무 데서나 자유롭게 호출 가능
result = fetcher.fetch(date)
stats = normalizer.normalize(date)
summary = summarizer.daily(date)

# async 전환 후 — 모든 호출자가 async여야 함
result = await fetcher.fetch(date)      # ← 호출자도 async def 필요
stats = await normalizer.normalize(date) # ← 그 호출자도 async def 필요
summary = await summarizer.daily(date)   # ← 끝까지 전파
```

`fetcher.fetch()`를 async로 바꾸면 → `orchestrator.run_daily()` → `cli.run()` → 최상위까지 전부 async로 변경해야 한다. Typer(Click 기반)는 sync-only이므로 `asyncio.run()` 래퍼가 필수적으로 들어간다.

### 4. Typer(CLI)와의 불일치

```python
# 현재 — 자연스러운 sync 흐름
@app.command()
def run(date: str = typer.Argument(None)):
    service.run_daily(date)

# async 전환 후 — 어색한 래퍼
@app.command()
def run(date: str = typer.Argument(None)):
    asyncio.run(_run_async(date))  # sync→async 경계

async def _run_async(date: str):
    await service.run_daily(date)
```

모든 CLI 커맨드에 `asyncio.run()` 래퍼가 필요하고, 이벤트 루프 생성/종료 오버헤드가 매 호출마다 발생한다. CLI는 동기 실행이 자연스럽다.

### 5. 테스트 복잡도 증가

현재 테스트 패턴과 async 전환 후 비교:

```python
# 현재 — 단순한 mock + 동기 호출
def test_fetch_single(fetcher, mock_ghes):
    mock_ghes.get.return_value = {"items": [...]}
    result = fetcher.fetch("2026-02-18")
    assert result["prs"].exists()

# async 전환 후 — 비동기 mock + pytest-asyncio + 이벤트 루프
@pytest.mark.asyncio
async def test_fetch_single(fetcher, mock_ghes):
    mock_ghes.get = AsyncMock(return_value={"items": [...]})
    result = await fetcher.fetch("2026-02-18")
    assert result["prs"].exists()
```

변경 사항:
- 모든 테스트 함수 `async def`로 전환
- `Mock()` → `AsyncMock()`으로 교체
- `ThreadPoolExecutor` 모킹 → `asyncio.TaskGroup` 모킹
- `pytest-asyncio` 의존성 추가 및 이벤트 루프 관리
- `CliRunner` 테스트에서 이벤트 루프 충돌 가능성

### 6. 디버깅 난이도 증가

- async 스택 트레이스는 이벤트 루프 프레임이 끼어들어 읽기 어려움
- 동시성 버그(race condition)가 threading과 다른 패턴으로 발생
- `asyncio.Lock` 기반 deadlock은 스레드 deadlock보다 진단이 까다로움

---

## 현재 구조로 충분한 이유

### ThreadPoolExecutor는 I/O-bound 작업에 효과적

Python의 GIL은 CPU-bound 작업에만 영향을 준다. git-recap의 주요 작업(HTTP 요청, LLM 호출, 파일 I/O)은 모두 I/O-bound이므로 threading으로 충분한 병렬성을 얻는다.

```
max_workers=5  → 동시 5개 HTTP/LLM 호출 (현재 기본값)
max_workers=20 → 동시 20개 (설정 변경만으로 가능)
```

GitHub API rate limit(5,000/hr, Search 30 req/min)이 상한이므로 `max_workers=20` 정도면 rate limit에 충분히 도달한다.

### 스레드 안전 장치가 이미 완비

| 보호 대상 | 메커니즘 |
|----------|---------|
| Search API throttle | `threading.Lock` + 2초 간격 |
| Rate limit 추적 | `threading.Lock` |
| DailyStateStore | `threading.RLock` |
| Checkpoint 갱신 | `threading.Lock` + date comparison guard |
| LLM usage 누적 | `threading.Lock` |
| GHESClient 풀 | `queue.Queue` (스레드 안전) |

### 코드 단순성

동기 코드는 위에서 아래로 읽히고, 디버깅이 직관적이며, Python 개발자라면 누구나 즉시 이해할 수 있다. async/await는 강력하지만 코드 전체에 전파되는 복잡도 비용이 있다.

---

## 성능이 부족할 때의 대안

async 전면 전환 대신 단계적으로 검토할 수 있는 대안:

| 단계 | 방법 | 작업량 | 기대 효과 |
|------|------|--------|----------|
| 1 | `max_workers` 증가 (5→20) | 설정값 변경 | 4배 추가 병렬 |
| 2 | `httpx.AsyncClient`로 Fetcher만 하이브리드 전환 | 30-40시간 | HTTP 5-10배 |
| 3 | 전면 async 전환 | 100-160시간 | 전체 5-10배 (rate limit 상한 존재) |

단계 1만으로도 대부분의 사용 시나리오에서 충분하며, 실제 프로파일링 결과를 보고 단계 2 이상을 판단하는 것이 합리적이다.

---

## 결론

| 항목 | 판단 |
|------|------|
| Async 이론적 이점 | 있음 (동시성 5-10배) |
| 실제 병목 해결 여부 | 제한적 (API rate limit이 상한) |
| 전환 비용 | 높음 (100-160시간, 33개 파일) |
| 리스크 | 중-고 (686개 테스트 리라이트, 이벤트 루프 복잡도) |
| **결정** | **Sync + ThreadPoolExecutor 유지** |

현재 아키텍처는 I/O-bound 워크로드에 적합하고, 스레드 안전 장치가 완비되어 있으며, 외부 API rate limit이 처리량의 실질적 상한이다. `max_workers` 조절만으로 필요한 병렬도를 달성할 수 있어, async 전환의 ROI는 낮다.
