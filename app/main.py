"""FastAPI 앱 팩토리. `uvicorn app.main:app` / `python main.py` 둘 다 지원."""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api import api_router as v1_router
from app.api.legacy import router as legacy_router
from app.core.config import get_settings
from app.store import store


@asynccontextmanager
async def _lifespan(app: FastAPI):
    async def _run() -> None:
        while True:
            await asyncio.sleep(600)
            try:
                store.cleanup_expired()
            except Exception:  # noqa: BLE001
                pass
    task = asyncio.create_task(_run())
    yield
    task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Dance Pose Extractor API", version="1.2.0", lifespan=_lifespan)

    origins = settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins != ["*"] else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 모바일 데이터 절약: 프레임 JSON gzip 강제
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    app.include_router(v1_router)
    app.include_router(legacy_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": app.version,
                "store": getattr(store, "backend_name", "memory")}

    @app.get("/", include_in_schema=False)
    def root():
        # 루트 접속 시 프론트로 이동 (빈 화면/Not Found 방지)
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/app", status_code=307)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        # 브라우저 자동 요청. 204로 조용히 응답해 로그 404 소음 제거.
        from fastapi.responses import Response
        return Response(status_code=204)

    # 프론트 정적 서빙 (앱 WebView용: /app → index.html)
    # frontend/가 있으면 디렉토리 서빙, 없으면 루트 index.html 단일 서빙
    root_dir = os.path.dirname(os.path.dirname(__file__))
    frontend_dir = os.path.join(root_dir, "frontend")
    if os.path.isdir(frontend_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/app", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    else:
        index_file = os.path.join(root_dir, "index.html")
        if os.path.exists(index_file):
            from fastapi.responses import FileResponse

            @app.get("/app", include_in_schema=False)
            def serve_app() -> FileResponse:
                return FileResponse(index_file, media_type="text/html")

    return app


app = create_app()
