"""잡 생성/조회/삭제 + 상태."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.api.common import formats_for
from app.api.security import check_rate_limit, require_api_key
from app.pipeline import run_motion_job
from app.schemas.motion import JobCreated, JobStatus, YouTubeRequest
from app.store import store

router = APIRouter(prefix="/api/v1/motions", tags=["motions"])


@router.post("", response_model=JobCreated, status_code=202,
            dependencies=[Depends(require_api_key)])
def create_motion_job(req: YouTubeRequest, request: Request,
                      background_tasks: BackgroundTasks) -> JobCreated:
    check_rate_limit(request, "create")
    job = store.create(str(req.url))
    background_tasks.add_task(run_motion_job, job.id, str(req.url),
                              req.target, req.ref_image_b64 or "",
                              req.render, req.char_image_b64 or "",
                              req.target_point)
    return JobCreated(
        job_id=job.id,
        status_url=f"/api/v1/motions/{job.id}",
        ws_url=f"/api/v1/motions/{job.id}/ws",
        bvh_url=f"/api/v1/motions/{job.id}/bvh",
        video_url=f"/api/v1/motions/{job.id}/video",
        control_url=f"/api/v1/motions/{job.id}/control",
        audio_url=f"/api/v1/motions/{job.id}/audio",
    )


@router.get("/{job_id}", response_model=JobStatus,
            dependencies=[Depends(require_api_key)])
def get_motion_job(job_id: str, request: Request) -> JobStatus:
    check_rate_limit(request, "read")
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatus(job_id=job.id, status=job.status, progress=job.progress,
                     message=job.message, fps=job.fps,
                     total_frames=job.total_frames, error=job.error,
                     formats=formats_for(job_id) if job.status == "done" else [])


@router.delete("/{job_id}", dependencies=[Depends(require_api_key)])
def delete_motion_job(job_id: str, request: Request) -> dict:
    check_rate_limit(request, "read")
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    store.update(job_id, status="error", error="cancelled by client", message="취소됨")
    return {"status": "cancelled"}
