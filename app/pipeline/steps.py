"""파이프라인 단계. 치명적(download/extract) vs 비치명적(rig/video) 구분.

치명적 실패는 예외 → 잡 error. 비치명적 실패는 메시지에 기록하고 계속.
"""
import os

from app.core.config import get_settings
from app.core.secrets import ensure_cookies_file
from app.store import store
from app.services.pose import process_video_pose
from app.services.youtube import download_cached


def step_download(ctx: dict) -> None:
    import shutil
    settings = get_settings()
    cookies = ensure_cookies_file(settings.cookies_b64, settings.cookies_secret_file,
                                  settings.cookies_path, settings.tmp_dir)
    preview_ttl = int(os.getenv("PREVIEW_TTL_SEC", "3600"))
    ctx["tmp_path"] = os.path.join(settings.tmp_dir, f"dl_{ctx['job_id']}.mp4")
    store.update(ctx["job_id"], status="downloading", progress=5, message="유튜브 다운로드 중")
    cached, reused = download_cached(
        ctx["url"], settings.tmp_dir, ttl_sec=preview_ttl, cookies_path=cookies,
        progress_cb=lambda m: store.update(ctx["job_id"], message=m))
    if reused:
        shutil.copy2(cached, ctx["tmp_path"])
        ctx["video_path"] = ctx["tmp_path"]
    else:
        if os.path.exists(ctx["tmp_path"]):
            os.remove(ctx["tmp_path"])
        os.replace(cached, ctx["tmp_path"])
        ctx["video_path"] = ctx["tmp_path"]


def step_extract(ctx: dict) -> None:
    settings = get_settings()
    store.update(ctx["job_id"], status="extracting", progress=30, message="관절 추출 중")

    def on_pose(done: int, cap: int) -> None:
        pct = 30 + min(65, int(done / max(cap, 1) * 65))
        store.update(ctx["job_id"], progress=pct, message=f"관절 추출 중 ({done}프레임)")

    if ctx.get("target") == "point" and not ctx.get("target_point"):
        raise RuntimeError("target=point인데 선택 좌표(target_point)가 없습니다. 미리보기에서 대상을 먼저 선택하세요")
    ctx["result"] = process_video_pose(
        ctx["video_path"], max_frames=settings.max_frames, progress_cb=on_pose,
        target=ctx["target"], ref_image_b64=ctx["ref_image_b64"],
        target_point=ctx.get("target_point"))


def step_smpl_fetch(ctx: dict) -> None:
    """SMPL GPU 워커 조회 (선택). 실패해도 잡은 계속 (FK 폴백).

    우선순위: 수동 SMPL_PKL 파일 > URL 캐시 > 워커 호출.
    결과 rows는 ctx에 보관, enrich에서 이식.
    """
    import os
    if os.getenv("SMPL_PKL", "").strip():
        return  # 수동 파일은 enrich에서 직접 처리
    from app.core.config import get_settings
    from app.services import smplcloud
    from app.services.youtube import url_hash
    if not smplcloud.space_enabled():
        return
    settings = get_settings()
    store.update(ctx["job_id"], message="SMPL 정제 요청 중")
    rows = smplcloud.fetch_smpl(
        ctx.get("video_path", ""), settings.tmp_dir, url_hash(ctx["url"]),
        progress_cb=lambda m: store.update(ctx["job_id"], message=m))
    if rows is not None:
        ctx["smpl_rows"] = rows
        store.update(ctx["job_id"], message=f"SMPL 정제 수신 ({len(rows)}프레임)")
    else:
        store.update(ctx["job_id"], message="SMPL 없이 진행 (FK)")


def step_enrich(ctx: dict) -> None:
    """손가락·발·목 회전값 + 루트모션 + 지면 보정 주입 (저장 전)."""
    from app.services.kinematics import (
        attach_fk_joints,
        attach_foot_joints,
        attach_hand_joints,
        attach_neck_head,
        attach_root,
    )
    attach_hand_joints(ctx["result"])
    attach_foot_joints(ctx["result"])
    attach_neck_head(ctx["result"])
    attach_fk_joints(ctx["result"])
    from app.services.smplxfer import attach_smpl_refine
    attach_smpl_refine(ctx["result"], rows=ctx.get("smpl_rows"))  # 수동 파일·클라우드rows·스킵
    from app.services.kinematics import attach_leg_smooth
    attach_leg_smooth(ctx["result"])  # 다리 떨림 제거 (FK 다음, root 이전 무관)
    attach_root(ctx["result"])
    from app.services.ground import attach_ground
    attach_ground(ctx["result"])  # fkJoints(무릎) + root 다음. 지면 뚫림 보정.


def step_save(ctx: dict) -> None:
    store.save_frames(ctx["job_id"], ctx["result"])
    try:
        from app.services.exports import to_rig
        store.save_rig(ctx["job_id"], to_rig(ctx["result"]))
    except Exception:
        pass  # rig 실패해도 full은 조회 가능


def step_audio(ctx: dict) -> None:
    """원본 음원 추출 (재생 싱크용). ffmpeg 없으면 스킵, 잡은 계속."""
    if os.getenv("AUDIO_ENABLED", "1").strip() in ("0", "false", "no"):
        return
    settings = get_settings()
    try:
        from app.services.audio import extract_audio
        result = ctx["result"]
        fps = result.get("fps", 30.0) or 30.0
        duration = result.get("total_frames", 0) / fps
        if duration <= 0:
            return
        audio_tmp = os.path.join(settings.tmp_dir, f"audio_{ctx['job_id']}.m4a")
        extract_audio(ctx["video_path"], audio_tmp, duration_sec=duration)
        store.store_video_file(ctx["job_id"], "audio", audio_tmp)
    except Exception as e:  # noqa: BLE001
        store.update(ctx["job_id"], message=f"오디오 없음(무음 재생): {e}")


def step_finish(ctx: dict) -> None:
    result = ctx["result"]
    store.update(ctx["job_id"], status="done", progress=100,
                 message=f"완료 (대상: {result.get('target', {}).get('label', ctx['target'])}, "
                         f"최대 {result.get('target', {}).get('persons_max', 1)}명 검출)",
                 fps=result["fps"], total_frames=result["total_frames"])


def step_video(ctx: dict) -> None:
    """참고용 AI 춤 영상 분기. 아바타 옆 참고 패널용이며 결과물이 아님.
    실패해도 아바타 분기는 유지."""
    if ctx["render"] not in ("ref", "both"):  # both는 구버전 호환 별칭
        return
    from app.services import genvideo as G
    from app.services.pose_video import render_control_video, save_b64_image
    settings = get_settings()
    job_id = ctx["job_id"]
    store.update(job_id, progress=96, message="참고 영상: 컨트롤 영상 렌더 중")
    control_tmp = os.path.join(settings.tmp_dir, f"control_{job_id}.mp4")
    render_control_video(ctx["result"], control_tmp,
                         progress_cb=lambda d, t: store.update(
                             job_id, message=f"컨트롤 영상 렌더 중 ({d}/{t})"))
    store.store_video_file(job_id, "control", control_tmp)
    char_path = os.path.join(settings.tmp_dir, f"char_{job_id}.jpg")
    if (ctx["char_image_b64"] or "").strip():
        save_b64_image(ctx["char_image_b64"], char_path)
    else:
        G.fallback_character(ctx["video_path"], ctx["result"], char_path)
    motion_src = os.getenv("GENVIDEO_MOTION_SOURCE", "original")
    motion_path = ctx["video_path"] if motion_src == "original" else store.video_path(job_id, "control")
    store.update(job_id, progress=98,
                 message=f"참고 영상: {os.getenv('GENVIDEO_BACKEND', 'mock')} 추론 중 (수 분 소요 가능)")
    gen_tmp = os.path.join(settings.tmp_dir, f"gen_{job_id}.mp4")
    try:
        G.generate(char_path, motion_path, gen_tmp,
                   progress_cb=lambda d, t: store.update(job_id, message="참고 영상 추론 중..."))
        store.store_video_file(job_id, "genvideo", gen_tmp)
        store.update(job_id, message="완료 (참고영상 포함)")
    except Exception as e:  # noqa: BLE001
        store.update(job_id, message=f"참고영상 실패(아바타는 정상): {e}")
    finally:
        for p in (char_path, gen_tmp):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
