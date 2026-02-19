"""Raw PR/Commit/Issue 데이터를 정규화된 Activity + DailyStats로 변환하는 서비스."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workrecap.infra.llm_router import LLMRouter
    from workrecap.services.daily_state import DailyStateStore

from jinja2 import Template

from workrecap.config import AppConfig
from workrecap.exceptions import NormalizeError
from workrecap.services.date_utils import date_range
from workrecap.models import (
    Activity,
    ActivityKind,
    CommitRaw,
    DailyStats,
    GitHubStats,
    IssueRaw,
    PRRaw,
    commit_raw_from_dict,
    issue_raw_from_dict,
    load_json,
    pr_raw_from_dict,
    save_json,
    save_jsonl,
)

logger = logging.getLogger(__name__)


class NormalizerService:
    def __init__(
        self,
        config: AppConfig,
        daily_state: DailyStateStore | None = None,
        llm: LLMRouter | None = None,
    ) -> None:
        self._config = config
        self._username = config.username
        self._daily_state = daily_state
        self._llm = llm

    @property
    def source_name(self) -> str:
        return "github"

    def normalize(
        self, target_date: str, progress: Callable[[str], None] | None = None
    ) -> tuple[Path, Path]:
        """
        Raw PR 데이터를 Activity 목록과 통계로 변환.

        Args:
            target_date: "YYYY-MM-DD"
            progress: 진행 상황 콜백.

        Returns:
            (activities_path, stats_path)

        Raises:
            NormalizeError: 입력 파일 없음 또는 파싱 실패
        """
        logger.info("Normalizing %s", target_date)
        if progress:
            progress(f"Normalizing {target_date}...")
        raw_path = self._config.date_raw_dir(target_date) / "prs.json"
        if not raw_path.exists():
            raise NormalizeError(f"Raw file not found: {raw_path}")

        try:
            raw_data = load_json(raw_path)
        except Exception as e:
            raise NormalizeError(f"Failed to parse {raw_path}: {e}") from e

        prs = [pr_raw_from_dict(d) for d in raw_data]
        logger.debug("Loaded %d PRs from %s", len(prs), raw_path)

        # Commit/Issue 로드 (optional — 없으면 빈 리스트, 하위 호환)
        raw_dir = self._config.date_raw_dir(target_date)

        commits_path = raw_dir / "commits.json"
        commits: list[CommitRaw] = []
        if commits_path.exists():
            try:
                commits = [commit_raw_from_dict(d) for d in load_json(commits_path)]
            except Exception:
                logger.warning("Failed to parse %s, skipping commits", commits_path)

        issues_path = raw_dir / "issues.json"
        issues: list[IssueRaw] = []
        if issues_path.exists():
            try:
                issues = [issue_raw_from_dict(d) for d in load_json(issues_path)]
            except Exception:
                logger.warning("Failed to parse %s, skipping issues", issues_path)

        pr_activities = self._convert_activities(prs, target_date)
        commit_activities = self._convert_commit_activities(commits, target_date)
        issue_activities = self._convert_issue_activities(issues, target_date)

        activities = pr_activities + commit_activities + issue_activities
        activities.sort(key=lambda a: a.ts)

        self._enrich_activities(activities)

        stats = self._compute_stats(activities, target_date)

        out_dir = self._config.date_normalized_dir(target_date)
        activities_path = out_dir / "activities.jsonl"
        stats_path = out_dir / "stats.json"

        save_jsonl(activities, activities_path)
        save_json(stats, stats_path)

        logger.info(
            "Normalized %d activities for %s → %s",
            len(activities),
            target_date,
            out_dir,
        )

        self._update_checkpoint(target_date)
        return activities_path, stats_path

    def normalize_range(
        self,
        since: str,
        until: str,
        force: bool = False,
        progress: Callable[[str], None] | None = None,
        max_workers: int = 1,
        batch: bool = False,
    ) -> list[dict]:
        """날짜 범위 순회하며 normalize. skip/force/resilience 지원.

        Args:
            batch: If True, use batch API for LLM enrichment (all dates in one batch call).
        """
        dates = date_range(since, until)
        logger.info(
            "normalize_range %s..%s (%d dates, force=%s, workers=%d, batch=%s)",
            since,
            until,
            len(dates),
            force,
            max_workers,
            batch,
        )
        if progress:
            progress(f"Normalizing {since}..{until} ({len(dates)} dates)")

        if batch and self._llm is not None:
            return self._normalize_range_batch(dates, force, progress)

        if max_workers <= 1:
            return self._normalize_range_sequential(dates, force, progress)
        return self._normalize_range_parallel(dates, force, progress, max_workers)

    def _normalize_range_sequential(
        self,
        dates: list[str],
        force: bool,
        progress: Callable[[str], None] | None,
    ) -> list[dict]:
        results: list[dict] = []
        for d in dates:
            try:
                if not force and self._is_date_normalized(d):
                    results.append({"date": d, "status": "skipped"})
                    continue
                self.normalize(d, progress=progress)
                results.append({"date": d, "status": "success"})
            except Exception as e:
                logger.warning("Failed to normalize %s: %s", d, e)
                results.append({"date": d, "status": "failed", "error": str(e)})
        return results

    def _normalize_range_parallel(
        self,
        dates: list[str],
        force: bool,
        progress: Callable[[str], None] | None,
        max_workers: int,
    ) -> list[dict]:
        results_by_date: dict[str, dict] = {}

        def process_date(d: str) -> dict:
            try:
                if not force and self._is_date_normalized(d):
                    return {"date": d, "status": "skipped"}
                self.normalize(d, progress=progress)
                return {"date": d, "status": "success"}
            except Exception as e:
                logger.warning("Failed to normalize %s: %s", d, e)
                return {"date": d, "status": "failed", "error": str(e)}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_date, d): d for d in dates}
            for future in as_completed(futures):
                result = future.result()
                results_by_date[result["date"]] = result

        # Return in original date order
        return [results_by_date[d] for d in dates]

    def _normalize_range_batch(
        self,
        dates: list[str],
        force: bool,
        progress: Callable[[str], None] | None,
    ) -> list[dict]:
        """Batch mode: normalize all dates, then enrich via single batch API call."""
        # Phase 1: Normalize all dates without enrichment, collecting activities per date
        date_activities: dict[str, list[Activity]] = {}
        results: list[dict] = []

        for d in dates:
            try:
                if not force and self._is_date_normalized(d):
                    results.append({"date": d, "status": "skipped"})
                    continue
                activities = self._normalize_without_enrich(d, progress)
                date_activities[d] = activities
                results.append({"date": d, "status": "success"})
            except Exception as e:
                logger.warning("Failed to normalize %s: %s", d, e)
                results.append({"date": d, "status": "failed", "error": str(e)})

        # Phase 2: Batch enrichment for all collected activities
        if date_activities:
            self._batch_enrich(date_activities)

        # Phase 3: Save enriched activities
        for d, activities in date_activities.items():
            try:
                out_dir = self._config.date_normalized_dir(d)
                save_jsonl(activities, out_dir / "activities.jsonl")
            except Exception as e:
                logger.warning("Failed to save enriched activities for %s: %s", d, e)

        return results

    def _normalize_without_enrich(
        self, target_date: str, progress: Callable[[str], None] | None
    ) -> list[Activity]:
        """Normalize a single date without LLM enrichment. Returns activities list."""
        logger.info("Normalizing (no enrich) %s", target_date)
        if progress:
            progress(f"Normalizing {target_date}...")
        raw_path = self._config.date_raw_dir(target_date) / "prs.json"
        if not raw_path.exists():
            raise NormalizeError(f"Raw file not found: {raw_path}")

        raw_data = load_json(raw_path)
        prs = [pr_raw_from_dict(d) for d in raw_data]

        raw_dir = self._config.date_raw_dir(target_date)
        commits: list[CommitRaw] = []
        commits_path = raw_dir / "commits.json"
        if commits_path.exists():
            try:
                commits = [commit_raw_from_dict(d) for d in load_json(commits_path)]
            except Exception:
                logger.warning("Failed to parse %s, skipping commits", commits_path)

        issues: list[IssueRaw] = []
        issues_path = raw_dir / "issues.json"
        if issues_path.exists():
            try:
                issues = [issue_raw_from_dict(d) for d in load_json(issues_path)]
            except Exception:
                logger.warning("Failed to parse %s, skipping issues", issues_path)

        pr_activities = self._convert_activities(prs, target_date)
        commit_activities = self._convert_commit_activities(commits, target_date)
        issue_activities = self._convert_issue_activities(issues, target_date)

        activities = pr_activities + commit_activities + issue_activities
        activities.sort(key=lambda a: a.ts)

        stats = self._compute_stats(activities, target_date)

        out_dir = self._config.date_normalized_dir(target_date)
        save_jsonl(activities, out_dir / "activities.jsonl")
        save_json(stats, out_dir / "stats.json")

        self._update_checkpoint(target_date)
        return activities

    def _batch_enrich(self, date_activities: dict[str, list[Activity]]) -> None:
        """Submit enrichment for all dates as a single batch and apply results."""
        batch_requests: list[dict] = []

        for d, activities in date_activities.items():
            if not activities:
                continue
            prompt = self._prepare_enrich_prompt(activities)
            if prompt is None:
                continue
            system_prompt, user_content = prompt
            batch_requests.append(
                {
                    "custom_id": f"enrich-{d}",
                    "system_prompt": system_prompt,
                    "user_content": user_content,
                    "json_mode": True,
                }
            )

        if not batch_requests:
            logger.info("No enrichment prompts prepared for batch")
            return

        try:
            logger.info("Submitting batch enrichment for %d dates", len(batch_requests))
            batch_id = self._llm.submit_batch(batch_requests, task="enrich")
            results = self._llm.wait_for_batch(batch_id, task="enrich")

            # Build result map: custom_id → content
            result_map: dict[str, str] = {}
            for r in results:
                if r.content is not None:
                    result_map[r.custom_id] = r.content
                elif r.error:
                    logger.warning("Batch enrichment error for %s: %s", r.custom_id, r.error)

            # Apply enrichment results
            for d, activities in date_activities.items():
                key = f"enrich-{d}"
                if key in result_map:
                    self._apply_enrichment(activities, result_map[key])

        except Exception as e:
            logger.warning("Batch enrichment failed, continuing without enrichment: %s", e)

    def _prepare_enrich_prompt(self, activities: list[Activity]) -> tuple[str, str] | None:
        """Prepare (system_prompt, user_content) for enrichment. Returns None if no template."""
        template_path = self._config.prompts_dir / "enrich.md"
        if not template_path.exists():
            return None

        template_text = template_path.read_text(encoding="utf-8")
        marker = "<!-- SPLIT -->"

        act_dicts = [
            {
                "kind": act.kind.value,
                "title": act.title,
                "repo": act.repo,
                "body": act.body,
                "files": act.files,
                "file_patches": act.file_patches,
                "review_bodies": act.review_bodies,
                "comment_bodies": act.comment_bodies,
            }
            for act in activities
        ]

        if marker in template_text:
            static_part, dynamic_part = template_text.split(marker, 1)
            system_prompt = static_part.strip()
            user_content = Template(dynamic_part).render(activities=act_dicts).strip()
        else:
            system_prompt = "You are a code change classifier."
            user_content = Template(template_text).render(activities=act_dicts)

        return system_prompt, user_content

    @staticmethod
    def _apply_enrichment(activities: list[Activity], response_text: str) -> None:
        """Parse enrichment JSON and apply to activities."""
        try:
            enrichments = json.loads(response_text)
            for entry in enrichments:
                idx = entry.get("index")
                if idx is not None and 0 <= idx < len(activities):
                    activities[idx].change_summary = entry.get("change_summary", "")
                    activities[idx].intent = entry.get("intent", "")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to parse enrichment response: %s", e)

    def _is_date_normalized(self, date_str: str) -> bool:
        """daily_state 있으면 timestamp cascade 체크, 없으면 파일 존재 체크."""
        if self._daily_state is not None:
            return not self._daily_state.is_normalize_stale(date_str)
        norm_dir = self._config.date_normalized_dir(date_str)
        return (norm_dir / "activities.jsonl").exists() and (norm_dir / "stats.json").exists()

    def _update_checkpoint(self, target_date: str) -> None:
        """last_normalize_date 키 업데이트. Thread-safe with date comparison guard."""
        from workrecap.services.checkpoint import update_checkpoint

        update_checkpoint(self._config.checkpoints_path, "last_normalize_date", target_date)

        if self._daily_state is not None:
            self._daily_state.set_timestamp("normalize", target_date)

    # ── LLM Enrichment ──

    def _enrich_activities(self, activities: list[Activity]) -> None:
        """LLM으로 change_summary/intent 분류. 실패 시 빈 필드로 계속."""
        if not activities:
            logger.info("Skipping LLM enrichment: no activities")
            return
        if self._llm is None:
            logger.info("Skipping LLM enrichment: LLM client not configured (use --enrich)")
            return

        logger.info("Enriching %d activities with LLM", len(activities))
        try:
            prompt = self._prepare_enrich_prompt(activities)
            if prompt is None:
                logger.warning("Enrich template not found")
                return

            system_prompt, user_content = prompt
            response = self._llm.chat(
                system_prompt,
                user_content,
                task="enrich",
                json_mode=True,
            )

            self._apply_enrichment(activities, response)
            logger.info("Enrichment complete for %d activities", len(activities))
        except Exception as e:
            logger.warning("LLM enrichment failed, continuing without enrichment: %s", e)

    # ── Activity 변환 ──

    def _convert_activities(self, prs: list[PRRaw], target_date: str) -> list[Activity]:
        """
        PR 목록에서 사용자의 활동을 추출.

        규칙:
          - author == username → PR_AUTHORED (ts = created_at)
          - reviews에 username → PR_REVIEWED (ts = submitted_at), self-review 제외
          - comments에 username → PR_COMMENTED (ts = earliest created_at)
          - 각 activity의 ts가 target_date에 해당하지 않으면 제외
        """
        activities: list[Activity] = []

        for pr in prs:
            is_author = pr.author.lower() == self._username.lower()

            # PR_AUTHORED
            if is_author and self._matches_date(pr.created_at, target_date):
                activities.append(self._make_activity(pr, ActivityKind.PR_AUTHORED, pr.created_at))

            # PR_REVIEWED (self-review 제외)
            if not is_author:
                for review in pr.reviews:
                    if review.author.lower() == self._username.lower() and self._matches_date(
                        review.submitted_at, target_date
                    ):
                        reviewer_inline = [
                            {
                                "path": c.path,
                                "line": c.line,
                                "diff_hunk": c.diff_hunk,
                                "body": c.body,
                            }
                            for c in pr.comments
                            if c.author.lower() == self._username.lower() and c.path
                        ]
                        activities.append(
                            self._make_activity(
                                pr,
                                ActivityKind.PR_REVIEWED,
                                review.submitted_at,
                                evidence_urls=[review.url],
                                review_bodies=[review.body],
                                comment_contexts=reviewer_inline,
                            )
                        )
                        break  # PR당 1개 review activity

            # PR_COMMENTED
            user_comments = [
                c
                for c in pr.comments
                if c.author.lower() == self._username.lower()
                and self._matches_date(c.created_at, target_date)
            ]
            if user_comments:
                earliest = min(user_comments, key=lambda c: c.created_at)
                contexts = [
                    {
                        "path": c.path,
                        "line": c.line,
                        "diff_hunk": c.diff_hunk,
                        "body": c.body,
                    }
                    for c in user_comments
                    if c.path
                ]
                activities.append(
                    self._make_activity(
                        pr,
                        ActivityKind.PR_COMMENTED,
                        earliest.created_at,
                        evidence_urls=[c.url for c in user_comments],
                        comment_bodies=[c.body for c in user_comments],
                        comment_contexts=contexts,
                    )
                )

        activities.sort(key=lambda a: a.ts)
        return activities

    # ── Commit → Activity 변환 ──

    def _convert_commit_activities(
        self, commits: list[CommitRaw], target_date: str
    ) -> list[Activity]:
        """Commit 목록에서 COMMIT Activity를 생성."""
        activities: list[Activity] = []
        for commit in commits:
            if not self._matches_date(commit.committed_at, target_date):
                continue

            # 제목: commit message 첫 줄 (truncation 없음)
            title = commit.message.split("\n", 1)[0]

            total_adds = sum(f.additions for f in commit.files)
            total_dels = sum(f.deletions for f in commit.files)
            file_names = [f.filename for f in commit.files]
            file_patches = {f.filename: f.patch for f in commit.files if f.patch}

            activities.append(
                Activity(
                    ts=commit.committed_at,
                    kind=ActivityKind.COMMIT,
                    repo=commit.repo,
                    external_id=0,
                    title=title,
                    url=commit.url,
                    summary=f"commit: {title} ({commit.repo}) +{total_adds}/-{total_dels}",
                    sha=commit.sha,
                    body=commit.message,
                    files=file_names,
                    file_patches=file_patches,
                    additions=total_adds,
                    deletions=total_dels,
                )
            )
        return activities

    # ── Issue → Activity 변환 ──

    def _convert_issue_activities(self, issues: list[IssueRaw], target_date: str) -> list[Activity]:
        """Issue 목록에서 ISSUE_AUTHORED / ISSUE_COMMENTED Activity를 생성."""
        activities: list[Activity] = []
        for issue in issues:
            # ISSUE_AUTHORED
            if issue.author.lower() == self._username.lower() and self._matches_date(
                issue.created_at, target_date
            ):
                activities.append(
                    Activity(
                        ts=issue.created_at,
                        kind=ActivityKind.ISSUE_AUTHORED,
                        repo=issue.repo,
                        external_id=issue.number,
                        title=issue.title,
                        url=issue.url,
                        summary=f"issue_authored: {issue.title} ({issue.repo})",
                        body=issue.body,
                        labels=issue.labels,
                    )
                )

            # ISSUE_COMMENTED
            user_comments = [
                c
                for c in issue.comments
                if c.author.lower() == self._username.lower()
                and self._matches_date(c.created_at, target_date)
            ]
            if user_comments:
                earliest = min(user_comments, key=lambda c: c.created_at)
                activities.append(
                    Activity(
                        ts=earliest.created_at,
                        kind=ActivityKind.ISSUE_COMMENTED,
                        repo=issue.repo,
                        external_id=issue.number,
                        title=issue.title,
                        url=issue.url,
                        summary=f"issue_commented: {issue.title} ({issue.repo})",
                        body=issue.body,
                        comment_bodies=[c.body for c in user_comments],
                        labels=issue.labels,
                        evidence_urls=[c.url for c in user_comments],
                    )
                )
        return activities

    def _make_activity(
        self,
        pr: PRRaw,
        kind: ActivityKind,
        ts: str,
        evidence_urls: list[str] | None = None,
        review_bodies: list[str] | None = None,
        comment_bodies: list[str] | None = None,
        comment_contexts: list[dict] | None = None,
    ) -> Activity:
        total_adds = sum(f.additions for f in pr.files)
        total_dels = sum(f.deletions for f in pr.files)
        file_names = [f.filename for f in pr.files]
        file_patches = {f.filename: f.patch for f in pr.files if f.patch}

        return Activity(
            ts=ts,
            kind=kind,
            repo=pr.repo,
            external_id=pr.number,
            title=pr.title,
            url=pr.url,
            summary=self._auto_summary(pr, kind, total_adds, total_dels),
            body=pr.body,
            review_bodies=review_bodies or [],
            comment_bodies=comment_bodies or [],
            files=file_names,
            file_patches=file_patches,
            additions=total_adds,
            deletions=total_dels,
            labels=pr.labels,
            evidence_urls=evidence_urls or [],
            comment_contexts=comment_contexts or [],
        )

    # ── Auto Summary ──

    @staticmethod
    def _auto_summary(pr: PRRaw, kind: ActivityKind, adds: int, dels: int) -> str:
        """1줄 자동 요약. body 없으면 파일 경로 기반 fallback (D-2)."""
        if pr.body and pr.body.strip():
            return f"{kind.value}: {pr.title} ({pr.repo}) +{adds}/-{dels}"

        dirs = set()
        for f in pr.files:
            parts = f.filename.split("/")
            if len(parts) > 1:
                dirs.add(parts[0])
            else:
                dirs.add(f.filename)

        dir_hint = ", ".join(sorted(dirs)[:3])
        if len(dirs) > 3:
            dir_hint += " 외"

        return f"{kind.value}: [{dir_hint}] {len(pr.files)}개 파일 변경 ({pr.repo}) +{adds}/-{dels}"

    # ── 날짜 필터링 ──

    @staticmethod
    def _matches_date(iso_timestamp: str, target_date: str) -> bool:
        """ISO 8601 타임스탬프의 날짜 부분이 target_date와 일치하는지 확인."""
        return iso_timestamp[:10] == target_date

    # ── DailyStats 계산 ──

    @staticmethod
    def _compute_stats(activities: list[Activity], target_date: str) -> DailyStats:
        authored = [a for a in activities if a.kind == ActivityKind.PR_AUTHORED]
        reviewed = [a for a in activities if a.kind == ActivityKind.PR_REVIEWED]
        commented = [a for a in activities if a.kind == ActivityKind.PR_COMMENTED]
        commits = [a for a in activities if a.kind == ActivityKind.COMMIT]
        issue_authored = [a for a in activities if a.kind == ActivityKind.ISSUE_AUTHORED]
        issue_commented = [a for a in activities if a.kind == ActivityKind.ISSUE_COMMENTED]

        # additions/deletions: authored PR + commit 합산
        total_adds = sum(a.additions for a in authored) + sum(a.additions for a in commits)
        total_dels = sum(a.deletions for a in authored) + sum(a.deletions for a in commits)

        repos = sorted(set(a.repo for a in activities))

        return DailyStats(
            date=target_date,
            github=GitHubStats(
                authored_count=len(authored),
                reviewed_count=len(reviewed),
                commented_count=len(commented),
                total_additions=total_adds,
                total_deletions=total_dels,
                repos_touched=repos,
                authored_prs=[{"url": a.url, "title": a.title, "repo": a.repo} for a in authored],
                reviewed_prs=[{"url": a.url, "title": a.title, "repo": a.repo} for a in reviewed],
                commit_count=len(commits),
                issue_authored_count=len(issue_authored),
                issue_commented_count=len(issue_commented),
                commits=[
                    {"url": a.url, "title": a.title, "repo": a.repo, "sha": a.sha} for a in commits
                ],
                authored_issues=[
                    {"url": a.url, "title": a.title, "repo": a.repo} for a in issue_authored
                ],
            ),
        )
