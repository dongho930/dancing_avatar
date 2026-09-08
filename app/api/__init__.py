"""v1 라우터 집계. 엔드포인트 추가는 파일 단위로."""
from fastapi import APIRouter

from app.api import assets, frames, motions, previews, ws

api_router = APIRouter()
api_router.include_router(motions.router)
api_router.include_router(frames.router)
api_router.include_router(assets.router)
api_router.include_router(previews.router)
api_router.include_router(ws.router)
