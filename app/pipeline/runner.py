"""백그라운드 잡 실행기. FastAPI BackgroundTasks에서 호출된다."""
import os

from app.store import store
from app.pipeline import steps


def run_motion_job(job_id: str, url: str,
                   target: str = "auto", ref_image_b64: str = "",
                   render: str = "avatar", char_image_b64: str = "",
                   target_point=None) -> None:
    ctx = {"job_id": job_id, "url": url, "target": target,
           "ref_image_b64": ref_image_b64 or "", "render": render,
           "char_image_b64": char_image_b64 or "",
           "target_point": target_point}
    try:
        steps.step_download(ctx)
        steps.step_extract(ctx)
        steps.step_smpl_fetch(ctx)
        steps.step_enrich(ctx)
        steps.step_save(ctx)
        steps.step_audio(ctx)
        steps.step_finish(ctx)
        steps.step_video(ctx)
    except Exception as e:  # noqa: BLE001 - 잡 상태를 에러로 확정해야 해서 전체 catch
        store.update(job_id, status="error", message="실패", error=str(e))
    finally:
        try:
            if ctx.get("tmp_path") and os.path.exists(ctx["tmp_path"]):
                os.remove(ctx["tmp_path"])
        except OSError:
            pass
