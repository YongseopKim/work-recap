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
    summaries_available,
    summarize_pipeline,
    summary,
)
from workrecap.api.routes import scheduler as scheduler_routes
from workrecap.exceptions import WorkRecapError
from workrecap.logging_config import setup_logging

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"


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
        # Provide a disabled-mode scheduler so routes still respond
        from workrecap.scheduler.config import ScheduleConfig
        from workrecap.scheduler.core import SchedulerService
        from workrecap.scheduler.history import SchedulerHistory
        from workrecap.scheduler.notifier import LogNotifier

        fallback_config = ScheduleConfig()  # enabled=False
        fallback_history = SchedulerHistory(Path("/dev/null"))
        scheduler = SchedulerService(fallback_config, fallback_history, LogNotifier())
        app.state.scheduler = scheduler
        app.state.scheduler_history = fallback_history
    yield
    if scheduler is not None:
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
