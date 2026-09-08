"""첫 프레임 미리보기: 생성/상태/프레임 이미지."""
import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.api.security import check_rate_limit, require_api_key
from app.core.config import get_settings
from app.schemas.motion import PreviewCreated, PreviewRequest, PreviewStatus
from app.services import preview as svc

router = APIRouter(prefix="/api/v1/previews", tags=["previews"])


def _ttl() -> int:
    return int(os.getenv("PREVIEW_TTL_SEC", "3600"))


@router.post("", response_model=PreviewCreated, status_code=202,
            dependencies=[Depends(require_api_key)])
def create_preview(req: PreviewRequest, request: Request,
                   background_tasks: BackgroundTasks) -> PreviewCreated:
    check_rate_limit(request, "create")
    url = str(req.url)
    pid = svc.preview_id_for(url)
    tmp = get_settings().tmp_dir
    meta = svc.load_meta(tmp, pid)
    if meta is None and (svc.get_status(pid) or {}).get("status") not in ("queued", "downloading", "detecting"):
        svc.set_status(pid, status="queued", progress=0, message="미리보기 준비 중")
        background_tasks.add_task(svc.build_preview, url, tmp, _ttl())
    return PreviewCreated(
        preview_id=pid,
        status_url=f"/api/v1/previews/{pid}",
        frame_url=f"/api/v1/previews/{pid}/frame",
    )


@router.get("/{preview_id}", response_model=PreviewStatus,
            dependencies=[Depends(require_api_key)])
def get_preview(preview_id: str, request: Request) -> PreviewStatus:
    check_rate_limit(request, "read")
    tmp = get_settings().tmp_dir
    meta = svc.load_meta(tmp, preview_id)
    if meta is not None:
        return PreviewStatus(preview_id=preview_id, status="done", progress=100,
                             candidates=meta.get("candidates", []))
    st = svc.get_status(preview_id)
    if st is None:
        raise HTTPException(status_code=404, detail="preview not found")
    return PreviewStatus(preview_id=preview_id, status=st.get("status", "queued"),
                         progress=st.get("progress", 0),
                         message=st.get("message", ""),
                         error=st.get("error", ""))


@router.get("/{preview_id}/frame", dependencies=[Depends(require_api_key)])
def get_preview_frame(preview_id: str, request: Request):
    check_rate_limit(request, "read")
    path = svc.frame_path(get_settings().tmp_dir, preview_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=409, detail="preview not ready")
    return FileResponse(path, media_type="image/jpeg",
                        filename=f"preview_{preview_id}.jpg")
