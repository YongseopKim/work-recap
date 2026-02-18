# Phase 3-2~5: SummarizerService 상세 설계

## 목적

정규화된 Activity/Stats 데이터와 하위 레벨 summary를 LLM에 전달하여
daily/weekly/monthly/yearly markdown summary를 생성한다.
자유 질문(query) 기능도 포함한다.

---

## 위치

`src/workrecap/services/summarizer.py`

## 의존성

- `workrecap.config.AppConfig`
- `workrecap.infra.llm_client.LLMClient`
- `workrecap.exceptions.SummarizeError`
- `workrecap.models` — load_json, load_jsonl, activity_from_dict, daily_stats_from_dict
- `jinja2` (프롬프트 템플릿 렌더링)

---

## 프롬프트 템플릿 설계

### prompts/daily.md

```markdown
당신은 소프트웨어 엔지니어의 일일 업무 리포트를 작성하는 어시스턴트입니다.

아래 제공되는 활동 데이터와 통계를 기반으로 **한국어**로 일일 업무 요약을 작성하세요.

## 규칙
- 수치(PR 수, line count)는 아래 제공된 통계를 **그대로** 사용하세요. 직접 계산하지 마세요.
- 각 PR의 URL을 evidence로 포함하세요.
- Markdown 형식으로 작성하세요.
- 간결하되 핵심 내용을 빠뜨리지 마세요.

## 출력 형식
```
# Daily Summary: {{ date }}

## 개요
(1-2문장 요약)

## 주요 활동
### 작성한 PR ({{ stats.authored_count }}건)
- [PR 제목](URL): 핵심 변경사항 1줄 설명

### 리뷰한 PR ({{ stats.reviewed_count }}건)
- [PR 제목](URL): 리뷰 포인트

### 코멘트 ({{ stats.commented_count }}건)
- [PR 제목](URL): 코멘트 맥락

## 수치
- 작성 코드: +{{ stats.total_additions }}/-{{ stats.total_deletions }}
- 관련 저장소: {{ stats.repos_touched | join(", ") }}
```
```

### prompts/weekly.md

```markdown
당신은 소프트웨어 엔지니어의 주간 업무 리포트를 작성하는 어시스턴트입니다.

아래 제공되는 일일 요약들을 기반으로 **한국어**로 주간 업무 요약을 작성하세요.

## 규칙
- 일일 요약의 수치를 합산하지 마세요. 주요 흐름과 패턴을 파악하세요.
- 가장 임팩트 있는 작업을 강조하세요.
- 각 PR URL은 원본 daily summary에서 가져오세요.

## 출력 형식
```
# Weekly Summary: {{ year }}-W{{ "%02d" | format(week) }}

## 주간 개요
(2-3문장 핵심 요약)

## 주요 성과
(가장 중요한 작업 3-5개, URL 포함)

## 리뷰 활동
(리뷰한 주요 PR 요약)

## 다음 주 예상
(진행 중인 작업 기반 전망, 있을 경우만)
```
```

### prompts/monthly.md

```markdown
당신은 소프트웨어 엔지니어의 월간 업무 리포트를 작성하는 어시스턴트입니다.

아래 제공되는 주간 요약들을 기반으로 **한국어**로 월간 업무 요약을 작성하세요.

## 규칙
- 한 달간의 주요 테마와 프로젝트를 파악하세요.
- 기여도가 큰 작업을 중심으로 서술하세요.
- 수치는 주간 요약에서 추출하되 정확하게 기재하세요.

## 출력 형식
```
# Monthly Summary: {{ year }}-{{ "%02d" | format(month) }}

## 월간 개요
(3-4문장 핵심 요약)

## 주요 프로젝트/테마
(카테고리별 정리)

## 주요 성과
(임팩트 순 정리, URL 포함)

## 리뷰 기여
(리뷰 활동 요약)
```
```

### prompts/yearly.md

```markdown
당신은 소프트웨어 엔지니어의 연간 업무 리포트를 작성하는 어시스턴트입니다.

아래 제공되는 월간 요약들을 기반으로 **한국어**로 연간 업무 요약을 작성하세요.

## 규칙
- 연간 주요 프로젝트와 성장 방향을 파악하세요.
- 분기별 흐름을 보여주세요.
- 가장 임팩트 있는 기여를 강조하세요.

## 출력 형식
```
# Yearly Summary: {{ year }}

## 연간 개요

## 분기별 요약
### Q1 / Q2 / Q3 / Q4

## 핵심 성과 Top 5

## 기술 영역
(주로 기여한 기술 분야/저장소)
```
```

### prompts/query.md

```markdown
당신은 소프트웨어 엔지니어의 업무 기록을 기반으로 질문에 답변하는 어시스턴트입니다.

아래 제공되는 월간 요약들을 context로 사용하여 질문에 **한국어**로 답변하세요.

## 규칙
- 제공된 context에 근거하여 답변하세요.
- context에 없는 내용은 "기록에서 확인되지 않습니다"라고 답하세요.
- 가능하면 구체적 PR URL을 포함하세요.
```

---

## 상세 구현

### 클래스 구조

```python
import logging
from datetime import date, timedelta
from pathlib import Path

from jinja2 import Template

from workrecap.config import AppConfig
from workrecap.exceptions import SummarizeError
from workrecap.infra.llm_client import LLMClient
from workrecap.models import (
    load_json, load_jsonl, activity_from_dict, daily_stats_from_dict,
)

logger = logging.getLogger(__name__)


class SummarizerService:
    def __init__(self, config: AppConfig, llm_client: LLMClient) -> None:
        self._config = config
        self._llm = llm_client
```

### daily()

```python
    def daily(self, target_date: str) -> Path:
        """
        Daily summary 생성.

        Input:
            - data/normalized/{Y}/{M}/{D}/activities.jsonl
            - data/normalized/{Y}/{M}/{D}/stats.json
            - prompts/daily.md

        Returns:
            data/summaries/{Y}/daily/{MM}-{DD}.md
        """
        norm_dir = self._config.date_normalized_dir(target_date)
        activities_path = norm_dir / "activities.jsonl"
        stats_path = norm_dir / "stats.json"

        if not activities_path.exists():
            raise SummarizeError(f"Activities file not found: {activities_path}")
        if not stats_path.exists():
            raise SummarizeError(f"Stats file not found: {stats_path}")

        activities = load_jsonl(activities_path)
        stats = load_json(stats_path)

        # 프롬프트 렌더링
        system_prompt = self._render_prompt("daily.md", date=target_date, stats=stats)

        # 유저 컨텐츠: activities JSONL을 텍스트로
        user_content = self._format_activities(activities)

        # LLM 호출
        response = self._llm.chat(system_prompt, user_content)

        # 저장
        output_path = self._config.daily_summary_path(target_date)
        self._save_markdown(output_path, response)

        logger.info("Generated daily summary: %s", output_path)
        return output_path
```

### weekly()

```python
    def weekly(self, year: int, week: int) -> Path:
        """
        Weekly summary 생성.

        Input: 해당 주의 daily.md 파일들 (ISO week 기준)
        Returns: data/summaries/{Y}/weekly/W{NN}.md
        """
        daily_contents = self._collect_daily_for_week(year, week)
        if not daily_contents:
            raise SummarizeError(
                f"No daily summaries found for {year}-W{week:02d}"
            )

        system_prompt = self._render_prompt(
            "weekly.md", year=year, week=week
        )
        user_content = "\n\n---\n\n".join(daily_contents)

        response = self._llm.chat(system_prompt, user_content)

        output_path = self._config.weekly_summary_path(year, week)
        self._save_markdown(output_path, response)

        logger.info("Generated weekly summary: %s", output_path)
        return output_path

    def _collect_daily_for_week(self, year: int, week: int) -> list[str]:
        """ISO week 기준으로 해당 주의 daily.md 파일 내용 수집."""
        # ISO week의 월요일 계산
        monday = date.fromisocalendar(year, week, 1)
        contents = []

        for i in range(7):
            d = monday + timedelta(days=i)
            date_str = d.isoformat()
            path = self._config.daily_summary_path(date_str)
            if path.exists():
                contents.append(path.read_text(encoding="utf-8"))

        return contents
```

### monthly(), yearly()

```python
    def monthly(self, year: int, month: int) -> Path:
        """
        Monthly summary 생성.

        Input: 해당 월에 걸치는 weekly.md 파일들
        Returns: data/summaries/{Y}/monthly/{MM}.md
        """
        weekly_contents = self._collect_weekly_for_month(year, month)
        if not weekly_contents:
            raise SummarizeError(
                f"No weekly summaries found for {year}-{month:02d}"
            )

        system_prompt = self._render_prompt(
            "monthly.md", year=year, month=month
        )
        user_content = "\n\n---\n\n".join(weekly_contents)

        response = self._llm.chat(system_prompt, user_content)

        output_path = self._config.monthly_summary_path(year, month)
        self._save_markdown(output_path, response)

        logger.info("Generated monthly summary: %s", output_path)
        return output_path

    def yearly(self, year: int) -> Path:
        """
        Yearly summary 생성.

        Input: 해당 연도의 monthly.md 파일들
        """
        monthly_contents = []
        for m in range(1, 13):
            path = self._config.monthly_summary_path(year, m)
            if path.exists():
                monthly_contents.append(path.read_text(encoding="utf-8"))

        if not monthly_contents:
            raise SummarizeError(f"No monthly summaries found for {year}")

        system_prompt = self._render_prompt("yearly.md", year=year)
        user_content = "\n\n---\n\n".join(monthly_contents)

        response = self._llm.chat(system_prompt, user_content)

        output_path = self._config.yearly_summary_path(year)
        self._save_markdown(output_path, response)

        logger.info("Generated yearly summary: %s", output_path)
        return output_path

    def _collect_weekly_for_month(self, year: int, month: int) -> list[str]:
        """해당 월에 걸치는 주의 weekly.md 수집."""
        contents = []
        # 해당 월의 첫째 날과 마지막 날
        first_day = date(year, month, 1)
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)

        # 첫째 날이 속한 주 ~ 마지막 날이 속한 주
        first_week = first_day.isocalendar()[1]
        last_week = last_day.isocalendar()[1]

        # 연말 week 번호가 1로 돌아가는 경우 처리
        iso_year = first_day.isocalendar()[0]

        seen_weeks = set()
        d = first_day
        while d <= last_day:
            iso_y, iso_w, _ = d.isocalendar()
            if (iso_y, iso_w) not in seen_weeks:
                seen_weeks.add((iso_y, iso_w))
                path = self._config.weekly_summary_path(iso_y, iso_w)
                if path.exists():
                    contents.append(path.read_text(encoding="utf-8"))
            d += timedelta(days=7)

        return contents
```

### query()

```python
    def query(self, question: str, months_back: int = 3) -> str:
        """
        자유 질문 응답.

        최근 N개월 monthly.md를 context로 사용.
        monthly가 없으면 최근 weekly/daily로 fallback.
        """
        context = self._collect_recent_context(months_back)
        if not context:
            raise SummarizeError("No summary data available for query context")

        system_prompt = self._render_prompt("query.md")
        user_content = f"## Context\n\n{context}\n\n## 질문\n\n{question}"

        return self._llm.chat(system_prompt, user_content)

    def _collect_recent_context(self, months_back: int) -> str:
        """최근 N개월 monthly summary 수집. 없으면 빈 문자열."""
        today = date.today()
        contents = []

        for i in range(months_back):
            # i개월 전
            target_month = today.month - i
            target_year = today.year
            while target_month <= 0:
                target_month += 12
                target_year -= 1

            path = self._config.monthly_summary_path(target_year, target_month)
            if path.exists():
                contents.append(path.read_text(encoding="utf-8"))

        return "\n\n---\n\n".join(contents)
```

### 유틸리티

```python
    def _render_prompt(self, template_name: str, **kwargs) -> str:
        """Jinja2 템플릿 렌더링."""
        template_path = self._config.prompts_dir / template_name
        if not template_path.exists():
            raise SummarizeError(f"Prompt template not found: {template_path}")

        template_text = template_path.read_text(encoding="utf-8")
        template = Template(template_text)
        return template.render(**kwargs)

    @staticmethod
    def _format_activities(activities: list[dict]) -> str:
        """activities JSONL 데이터를 읽기 좋은 텍스트로 변환."""
        if not activities:
            return "(활동 없음)"

        lines = []
        for act in activities:
            line = (
                f"- [{act['kind']}] {act['title']} ({act['repo']}) "
                f"+{act.get('additions', 0)}/-{act.get('deletions', 0)} "
                f"URL: {act['url']}"
            )
            if act.get('files'):
                line += f"\n  Files: {', '.join(act['files'][:5])}"
                if len(act['files']) > 5:
                    line += f" 외 {len(act['files']) - 5}개"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _save_markdown(path: Path, content: str) -> None:
        """Markdown 파일 저장."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
```

---

## 파일 수집 로직 정리

| 메서드 | Input 파일 | 수집 기준 |
|---|---|---|
| daily() | activities.jsonl + stats.json | target_date 직접 지정 |
| weekly() | daily/*.md | ISO week의 월~일 7일 순회 |
| monthly() | weekly/W*.md | 해당 월의 첫날~마지막날이 속한 주 |
| yearly() | monthly/*.md | 1~12월 순회 |
| query() | monthly/*.md | 최근 N개월 역순 |

---

## 테스트 명세

### test_summarizer.py

LLMClient를 mock하여 SummarizerService의 로직을 검증한다.

```python
"""tests/unit/test_summarizer.py"""

class TestRenderPrompt:
    def test_renders_template_with_vars(self):
        """Jinja2 변수가 치환된다."""

    def test_template_not_found(self):
        """템플릿 파일 없으면 SummarizeError."""

class TestFormatActivities:
    def test_formats_activities(self):
        """activities dict 목록 → 텍스트 포맷."""

    def test_empty_activities(self):
        """빈 목록 → '(활동 없음)'."""

    def test_truncates_files(self):
        """파일 5개 초과 시 '외 N개' 표시."""

class TestDaily:
    def test_generates_daily_summary(self, summarizer, mock_llm, test_config):
        """activities + stats → LLM 호출 → .md 저장."""

    def test_activities_not_found(self, summarizer):
        """activities 파일 없으면 SummarizeError."""

    def test_stats_not_found(self, summarizer, test_config):
        """stats 파일 없으면 SummarizeError."""

class TestWeekly:
    def test_collects_daily_for_week(self, summarizer, test_config):
        """ISO week 기준 daily.md 수집."""

    def test_generates_weekly_summary(self, summarizer, mock_llm, test_config):
        """daily.md들 → LLM → weekly.md."""

    def test_no_daily_found(self, summarizer):
        """daily 없으면 SummarizeError."""

class TestMonthly:
    def test_collects_weekly_for_month(self, summarizer, test_config):
        """해당 월의 weekly.md 수집."""

    def test_generates_monthly_summary(self, summarizer, mock_llm, test_config):
        """weekly.md들 → LLM → monthly.md."""

class TestYearly:
    def test_generates_yearly_summary(self, summarizer, mock_llm, test_config):
        """monthly.md들 → LLM → yearly.md."""

    def test_no_monthly_found(self, summarizer):
        """monthly 없으면 SummarizeError."""

class TestQuery:
    def test_query_with_context(self, summarizer, mock_llm, test_config):
        """monthly context + 질문 → LLM 응답."""

    def test_no_context(self, summarizer):
        """context 없으면 SummarizeError."""

class TestCollectDailyForWeek:
    def test_iso_week_calculation(self, summarizer, test_config):
        """ISO week 월~일 7일 정확히 순회."""

    def test_partial_week(self, summarizer, test_config):
        """일부 daily만 있는 주."""

class TestCollectWeeklyForMonth:
    def test_february(self, summarizer, test_config):
        """2월의 주간 수집."""

    def test_month_boundary(self, summarizer, test_config):
        """월 경계에 걸치는 주 포함."""
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 3.2.1 | 프롬프트 템플릿 파일 작성 (daily/weekly/monthly/yearly/query) | TestRenderPrompt |
| 3.2.2 | `_render_prompt()` + `_format_activities()` + `_save_markdown()` | TestRenderPrompt, TestFormatActivities |
| 3.2.3 | `daily()` 구현 | TestDaily |
| 3.2.4 | `_collect_daily_for_week()` + `weekly()` 구현 | TestWeekly, TestCollectDailyForWeek |
| 3.2.5 | `_collect_weekly_for_month()` + `monthly()` 구현 | TestMonthly, TestCollectWeeklyForMonth |
| 3.2.6 | `yearly()` 구현 | TestYearly |
| 3.2.7 | `_collect_recent_context()` + `query()` 구현 | TestQuery |
