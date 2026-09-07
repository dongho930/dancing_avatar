"""호환용 엔트리포인트. 실제 앱은 app/main.py를 사용.
구 실행법 `python main.py`도 그대로 동작한다.
"""
from app.main import app, create_app  # noqa: F401

if __name__ == "__main__":
    import os

    import uvicorn

    from app.core.config import get_settings

    settings = get_settings()
    # reload는 로컬 개발용. 프로덕션(Render)은 리로더 프로세스가 메모리를 2배 쓰므로 OFF.
    reload = os.getenv("UVICORN_RELOAD", "").strip().lower() in ("1", "true", "yes")
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=reload)