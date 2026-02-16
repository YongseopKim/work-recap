"""FastAPI 앱 팩토리 + CORS + exception handler + 정적 파일 서빙."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from git_recap.api.routes import pipeline, query, summary
from git_recap.exceptions import GitRecapError

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"


def create_app() -> FastAPI:
    app = FastAPI(title="git-recap", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
    app.include_router(summary.router, prefix="/api/summary", tags=["summary"])
    app.include_router(query.router, prefix="/api", tags=["query"])

    @app.exception_handler(GitRecapError)
    async def handle_git_recap_error(request: Request, exc: GitRecapError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )

    # 정적 파일 서빙 (API 라우터 뒤에 마운트)
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()
