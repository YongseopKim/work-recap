# UI Redesign Design — 거울 / 나침반 / 무기

**Date**: 2026-02-28
**Status**: Approved

## 배경

work-recap은 GHES 활동 데이터 수집 → 정규화 → 계층적 요약(daily/weekly/monthly/yearly) + 자유 질문 + 시맨틱 검색을 지원하는 개인 생산성 도구다. 현재 웹 UI는 3탭(Pipeline/Summaries/Ask) MVP로, 백엔드 API의 약 30%만 노출하고 있다.

**갈증 영역**: 회고/반성, 목표 연계, 산출물 활용, 데이터 디스커버리 전반.

## 세 가지 접근법

| 접근법 | 비유 | 핵심 질문 |
|---|---|---|
| A. 거울 | 자기 인식 도구 | "나는 지금 어떤 상태인가?" |
| B. 나침반 | 목표 연계 도구 | "내 방향이 맞는가?" |
| C. 무기 | 산출물 활용 도구 | "이걸 어디에 쓸 수 있는가?" |

## 기술 스택

- **Alpine.js** — CDN `<script>` 1줄, 빌드 도구 없음. 기존 바닐라 JS의 `getElementById` + `classList.toggle` 패턴을 선언적 `x-data`, `x-show`, `@click`으로 전환
- **Chart.js** — CDN, Intent 분포 차트 등 시각화용
- **Pico CSS** — 기존 유지, 다크 모드 `data-theme` 전환 추가
- **marked.js** — 기존 유지, Markdown 렌더링
- **ES modules** — `<script type="module">`로 파일 분할, 빌드 불필요

## 탭 구조

```
Dashboard │ Goals │ Summaries │ Export │ Pipeline │ Ask
```

| 탭 | 접근법 | 사용 빈도 | 핵심 질문 |
|---|---|---|---|
| Dashboard | A. 거울 | 매일 | "나는 지금 어떤 상태인가?" |
| Goals | B. 나침반 | 주간/분기 | "내 방향이 맞는가?" |
| Summaries | 기존 개선 | 수시 | "어떤 일을 했는가?" |
| Export | C. 무기 | 이벤트성 | "이걸 어디에 쓸 수 있는가?" |
| Pipeline | 관리 | 가끔 | "데이터를 수집/처리하자" |
| Ask | 탐색 | 수시 | "궁금한 것을 물어보자" |

진입점은 Dashboard. 앱을 열면 "오늘의 나"부터 보여준다.

---

## 섹션별 상세 설계

### 1. Dashboard 탭 (A. 거울)

**위젯 4개:**

**1-1. Today / This Week 카드**
- 오늘/이번 주 활동 수치: authored PR, reviewed PR, commits, issues
- 어제 대비 변화량 (예: PR +2, 코드 +450/-120)
- 데이터 없으면 "아직 오늘 데이터가 없습니다" 표시

**1-2. Activity Heatmap (GitHub 스타일)**
- 최근 6개월 날짜별 활동량 칸 표시
- 활동량에 따라 4단계 색상
- 클릭 → Summaries 탭으로 이동 + 해당 날짜 daily summary 자동 로드
- 데이터 없는 날짜는 회색
- SVG 직접 생성, Alpine `x-for`로 날짜 순회

**1-3. Intent 분포 차트 (최근 4주)**
- 주별 feature/bugfix/refactor/review 비율 변화
- Chart.js horizontalBar 또는 CSS width% 기반 바 차트
- "이번 주 bugfix 비율이 평소보다 높음" 같은 인사이트 자동 표시
- 인사이트는 프론트에서 이번 주 vs 4주 평균 비교로 생성

**1-4. 주간 회고 프롬프트**
- weekly summary 하단에 회고 질문 표시
- [생성하기] 클릭 시 `POST /api/query`로 자동 답변 생성

### 2. Goals 탭 (B. 나침반)

**2-1. 목표 등록**
- 텍스트 1줄 + 기간(주간/월간/분기) + 키워드(선택)
- `data/state/goals.json`에 저장 (DB 불필요)

**저장 구조:**
```json
{
  "goals": [
    {
      "id": "g-1",
      "text": "성능 개선 프로젝트 완료",
      "keywords": ["performance", "latency", "cache"],
      "scope": "quarterly",
      "start_date": "2025-01-01",
      "end_date": "2025-03-31",
      "created_at": "2025-01-05T09:00:00",
      "archived": false
    }
  ]
}
```

**2-2. 활성 목표 카드**
- 목표별: 관련 활동 수, 마지막 관련 활동 날짜, 매칭률(%)
- 주별 관련 활동 수 추이
- 최근 관련 PR 목록 (URL 포함)
- 3일 연속 관련 활동 0건 시 nudge 표시

**2-3. 목표-활동 매칭 전략**
- 1단계 — 키워드 매칭: `activities.jsonl`의 title, intent, change_summary에서 검색 (비용 없음, 즉시)
- 2단계 — 시맨틱 매칭: ChromaDB `storage search` 활용 (storage 활성화 시)
- 합집합 → 중복 제거 → 관련 활동 목록

**2-4. 주간 Alignment 요약**
- 전체 활동 중 목표 관련 비율 시각화
- [상세 분석 보기] → `POST /api/query`로 alignment 분석

**2-5. 기간 만료 처리**
- `end_date` 지나면 "지난 목표" 섹션으로 자동 이동
- 삭제하지 않고 보관하여 회고 시 활용

### 3. Summaries 탭 (기존 개선)

**3-1. 캘린더 뷰 (Daily)**
- 월별 캘린더 그리드, ■(요약 있음) / □(없음) / ●(선택됨) 마커
- ◀ ▶ 로 월 전환
- 클릭 → 하단에 해당 날짜 daily summary 로드
- 순수 HTML div/table, Alpine `x-for`로 생성

**3-2. 리스트 뷰 (Weekly/Monthly)**
- 주차 또는 월 목록, 존재 여부 마커
- 클릭 → 해당 요약 로드

**3-3. 계층 네비게이션**
- 아래→위: Daily → "이 날이 속한 W07 보기" → Weekly → "이 주가 속한 2월 보기" → Monthly → Yearly
- 위→아래: Yearly → Monthly 리스트 → Weekly 리스트 → Daily 캘린더(해당 주 하이라이트)

**3-4. 복사 버튼**
- 모든 summary에 📋 버튼 → `navigator.clipboard.writeText()`

### 4. Export 탭 (C. 무기)

**4-1. 프리셋 4종**

| 프리셋 | 기간 기본값 | 프롬프트 톤 | 출력 구조 |
|---|---|---|---|
| 1:1 미팅 준비 | 최근 2주 | 간결, 대화용 | 핵심 활동 3개 + 논의 포인트 + 블로커 |
| 성과 리뷰 | 분기/반기 | 어필, 성과 중심 | 핵심 성과 Top 5 + 수치 근거 + 성장 포인트 |
| 스탠드업 | 어제 | 초간결, 3줄 | 어제 한 일 / 오늘 할 일 / 블로커 |
| 인수인계 | 전체 | 객관적, 문서형 | repo별 기여 히스토리 + 주요 의사결정 + 주의사항 |

**4-2. 설정 옵션**
- 기간: 프리셋 기본값 / 직접 입력
- 언어: 한국어 / English
- 포맷: 요약형 / 상세형

**4-3. 구현 방식**
- 프리셋 = 프론트엔드에서 질문 문장 조립 → 기존 `POST /api/query` 전송
- 프리셋 선택 시 질문이 텍스트 필드에 자동 채워지되, 사용자가 직접 수정 가능
- "프리셋은 출발점, 최종 질문은 사용자 결정"

**4-4. 복사 + 다시 생성**
- 📋 복사 버튼 + 🔄 다시 생성 버튼

**새 백엔드 작업 없음** — 순수 프론트엔드만으로 구현.

### 5. Pipeline 탭 (기존 개선)

**5-1. 실행 모드 선택**
- 전체 파이프라인 / Fetch만 / Normalize만 / Summarize만

**5-2. 대상 선택**
- 단일 날짜 / 날짜 범위 / Catch-up (체크포인트 기반 자동)

**5-3. 옵션 토글**
- Force, Batch API, Workers 수, Enrich 생략
- 계층 요약: Weekly / Monthly / Yearly 체크박스

**5-4. 단계별 진행률**
- Fetch → Normalize → Summarize 단계별 상태 표시
- (job result에 progress 필드 추가 필요 — 선택적)

**5-5. 최근 실행 이력**
- 최근 10건의 실행 기록 (날짜, 명령, 상태, 소요시간)

### 6. Ask 탭 (기존 개선)

**6-1. 빠른 질문 버튼**
- "이번 주 요약", "이번 달 핵심 성과", "가장 리뷰 많은 PR" 등 원클릭 질문

**6-2. 대화 히스토리**
- 세션 내 질문/답변 누적 (Alpine `x-data`의 `messages: []`)
- 페이지 새로고침 시 초기화 (의도적)

**6-3. 답변별 복사 버튼**

---

## 새 백엔드 API 총정리

| # | 엔드포인트 | 용도 | 탭 | 복잡도 |
|---|---|---|---|---|
| 1 | `GET /api/stats/heatmap?months=6` | 날짜별 활동 수 | Dashboard | 낮음 |
| 2 | `GET /api/stats/intents?weeks=4` | 주별 intent 분포 | Dashboard | 중간 |
| 3 | `GET /api/summaries/available?year=&month=` | 요약 존재 여부 맵 | Summaries | 낮음 |
| 4 | `GET /api/goals` | 목표 목록 | Goals | 낮음 |
| 5 | `POST /api/goals` | 목표 등록 | Goals | 낮음 |
| 6 | `PUT /api/goals/{id}` | 목표 수정 | Goals | 낮음 |
| 7 | `DELETE /api/goals/{id}` | 목표 삭제(보관) | Goals | 낮음 |
| 8 | `GET /api/goals/{id}/activities` | 목표별 관련 활동 | Goals | 중간 |
| 9 | `GET /api/goals/alignment?week=` | 주간 alignment 집계 | Goals | 중간 |
| 10 | `GET /api/pipeline/history?limit=10` | 최근 실행 이력 | Pipeline | 낮음 |

기존 API 변경 없음. Export와 Ask 개선은 프론트엔드만으로 구현.

## 프론트엔드 파일 구조

```
frontend/
├── index.html              ← Alpine.js + Chart.js CDN 추가
├── style.css               ← 확장 (캘린더, heatmap, 카드 등)
├── js/
│   ├── app.js              ← Alpine 초기화 + 탭 라우팅
│   ├── api.js              ← fetch 헬퍼 + job polling (기존 로직 추출)
│   ├── dashboard.js        ← Dashboard 탭 Alpine 컴포넌트
│   ├── goals.js            ← Goals 탭 Alpine 컴포넌트
│   ├── summaries.js        ← Summaries 탭 Alpine 컴포넌트
│   ├── export.js           ← Export 탭 Alpine 컴포넌트
│   ├── pipeline.js         ← Pipeline 탭 Alpine 컴포넌트
│   ├── ask.js              ← Ask 탭 Alpine 컴포넌트
│   └── components/
│       ├── heatmap.js      ← SVG heatmap 생성 유틸
│       ├── calendar.js     ← 캘린더 그리드 유틸
│       └── chart.js        ← Chart.js 래퍼
└── presets/
    └── export-presets.js   ← Export 프리셋 정의
```

## 구현 우선순위

**Phase 1 — 기반 + 즉시 가치**
1. Alpine.js 마이그레이션 — 기존 3탭을 Alpine으로 전환
2. Summaries 캘린더 뷰 — API #3 + 프론트 캘린더
3. 다크 모드 토글
4. 복사 버튼 (모든 탭 공통)

**Phase 2 — 거울 (A)**
5. Dashboard 탭 — Today/This Week 카드 (API #1)
6. Activity Heatmap (API #1 재활용)
7. Intent 분포 차트 (API #2)
8. 주간 회고 프롬프트

**Phase 3 — 무기 (C) + Ask 개선**
9. Export 탭 — 프리셋 4종 (프론트엔드만)
10. Ask 빠른 질문 + 대화 히스토리
11. Pipeline 옵션 토글 + 실행 이력 (API #10)

**Phase 4 — 나침반 (B)**
12. Goals CRUD (API #4~7)
13. 목표-활동 매칭 (API #8)
14. 주간 alignment (API #9)

## 공통 사항

- **다크 모드**: Pico CSS `data-theme="light"/"dark"` 전환, `localStorage` 저장
- **복사 버튼**: `navigator.clipboard.writeText()` + Alpine `@click`, 모든 산출물에 적용
- **반응형**: Pico CSS 기본 반응형 + 모바일에서 탭을 가로 스크롤 또는 햄버거 메뉴
