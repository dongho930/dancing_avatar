"""첫 프레임 미리보기: URL 캐시 영상 → 첫 프레임 JPEG + 후보자 목록.

본 잡과 다운로드를 공유해 2회 다운로드를 피한다. preview_id는 URL 해시라
같은 URL은 같은 미리보기를 공유한다.
"""
import json
import os
import threading

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def preview_id_for(url: str) -> str:
    from app.services.youtube import url_hash
    return url_hash(url)


def frame_path(tmp_dir: str, preview_id: str) -> str:
    return os.path.join(tmp_dir, f"preview_{preview_id}.jpg")


def meta_path(tmp_dir: str, preview_id: str) -> str:
    return os.path.join(tmp_dir, f"preview_{preview_id}.json")


def get_status(preview_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(preview_id)
        return dict(job) if job else None


def set_status(preview_id: str, **kw) -> None:
    with _lock:
        job = _jobs.setdefault(preview_id, {"status": "queued"})
        job.update(kw)


def load_meta(tmp_dir: str, preview_id: str) -> dict | None:
    path = meta_path(tmp_dir, preview_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_preview(url: str, tmp_dir: str, ttl_sec: int = 3600) -> None:
    """BackgroundTasks용. 다운로드 → 첫 프레임 → 1회 검출 → 저장."""
    from app.core.secrets import ensure_cookies_file
    from app.core.config import get_settings
    pid = preview_id_for(url)
    if load_meta(tmp_dir, pid):
        set_status(pid, status="done")
        return
    set_status(pid, status="downloading", progress=10)
    try:
        settings = get_settings()
        cookies = ensure_cookies_file(settings.cookies_b64, settings.cookies_secret_file,
                                      settings.cookies_path, settings.tmp_dir)
        from app.services.youtube import download_cached
        vpath, _ = download_cached(
            url, tmp_dir, ttl_sec=ttl_sec, cookies_path=cookies,
            progress_cb=lambda m: set_status(pid, message=m))
        set_status(pid, status="detecting", progress=80)
        meta = detect_first_frame(vpath, tmp_dir, pid)
        set_status(pid, status="done", progress=100, candidates=len(meta["candidates"]))
    except Exception as e:  # noqa: BLE001 - 상태로 확정
        set_status(pid, status="error", error=str(e))


def detect_first_frame(video_path: str, tmp_dir: str, preview_id: str) -> dict:
    """첫 프레임 JPEG + 후보자 [{index, center, box, score}] 저장 후 메타 반환."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import vision as mp_vision
    from mediapipe.tasks.python import BaseOptions

    from app.core.config import get_settings
    from app.services.detect import ensure_pose_model, lm_to_dict
    from app.services.tracking import _person_entries

    settings = get_settings()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"동영상 파일을 열 수 없음: {video_path}")
    try:
        ret, frame = cap.read()
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
    finally:
        cap.release()
    if not ret or w <= 0 or h <= 0:
        raise RuntimeError("첫 프레임을 읽지 못했습니다")
    max_poses = max(1, int(os.getenv("MAX_POSES", "4")))
    options = mp_vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=ensure_pose_model(settings.tmp_dir)),
        running_mode=mp_vision.RunningMode.IMAGE,
        num_poses=max_poses,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
    with mp_vision.PoseLandmarker.create_from_options(options) as landmarker:
        res = landmarker.detect(mp_image)
    lms2d = [[lm_to_dict(lm) for lm in p] for p in (res.pose_landmarks or [])]
    persons = _person_entries(lms2d, [])
    candidates = []
    for i, p in enumerate(persons):
        pts = _solid_points(p["lms2d"])
        hull = _convex_hull(pts)
        hx = [q[0] for q in hull]
        hy = [q[1] for q in hull]
        pad = 0.02
        candidates.append({
            "index": i,
            "center": [round(p["center"][0], 4), round(p["center"][1], 4)],
            "box": [round(max(0.0, min(hx) - pad), 4), round(max(0.0, min(hy) - pad), 4),
                    round(min(1.0, max(hx) + pad), 4), round(min(1.0, max(hy) + pad), 4)],
            "poly": [[round(x, 4), round(y, 4)] for x, y in hull],
            "score": round(p["score"], 3),
        })
    os.makedirs(tmp_dir, exist_ok=True)
    _annotate_frame(frame, persons, candidates)
    cv2.imwrite(frame_path(tmp_dir, preview_id), frame)
    meta = {"width": w, "height": h, "candidates": candidates}
    with open(meta_path(tmp_dir, preview_id), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return meta


def _solid_points(lms2d: list) -> list:
    """가시성 높은 점만 외곽 계산에 사용. 부족하면 기준 완화."""
    pts = []
    for thr in (0.5, 0.3, 0.0):
        pts = []
        for lm in lms2d or []:
            try:
                x, y, v = float(lm.get("x", 0.5)), float(lm.get("y", 0.5)), float(lm.get("visibility", 0))
            except (TypeError, ValueError, AttributeError):
                continue
            if v >= thr and 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                pts.append((x, y))
        if len(pts) >= 3:
            break
    return pts


def _convex_hull(points: list) -> list:
    """monotone chain. 의존성 없이 외곽 꼭짓점 반환."""
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# 인물별 표시 색 (BGR)
_PALETTE = [(0, 255, 0), (255, 128, 0), (0, 165, 255), (255, 0, 255)]


def _annotate_frame(frame, persons: list, candidates: list) -> None:
    """첫 프레임에 인물 형태를 따라 스켈레톤+외곽선+번호 오버레이. 클릭 영역은 프론트 담당."""
    import cv2
    import numpy as np

    from app.services.pose_video import POSE_LINKS

    h, w = frame.shape[:2]
    thick = max(2, w // 240)
    for i, p in enumerate(persons):
        color = _PALETTE[i % len(_PALETTE)]
        pts = []
        for lm in p["lms2d"]:
            try:
                x = float(lm.get("x", -1)) * w
                y = float(lm.get("y", -1)) * h
                v = float(lm.get("visibility", 0))
            except (TypeError, ValueError, AttributeError):
                x, y, v = -1, -1, 0.0
            pts.append((x, y, v))
        for a, b in POSE_LINKS:
            if a < len(pts) and b < len(pts) and pts[a][2] > 0.3 and pts[b][2] > 0.3:
                cv2.line(frame, (int(pts[a][0]), int(pts[a][1])),
                         (int(pts[b][0]), int(pts[b][1])), color, thick)
        for (x, y, v) in pts:
            if v > 0.3 and 0 <= x < w and 0 <= y < h:
                cv2.circle(frame, (int(x), int(y)), max(2, w // 320), color, -1)
        # 외곽선: 인물 겉 테두리 표시
        if i < len(candidates) and len(candidates[i].get("poly", [])) >= 3:
            hull_px = np.array([(int(x * w), int(y * h)) for x, y in candidates[i]["poly"]],
                               dtype=np.int32)
            cv2.polylines(frame, [hull_px], True, color, thick)
        # 번호 라벨: 코 위치, 없으면 박스 좌상단
        nose = pts[0] if len(pts) > 0 and pts[0][2] > 0.3 else None
        if nose is not None:
            lx, ly = int(nose[0]), max(int(nose[1]) - 10, 10)
        else:
            box = candidates[i]["box"] if i < len(candidates) else [0, 0, 0, 0]
            lx, ly = int(box[0] * w), max(int(box[1] * h) - 8, 12)
        cv2.putText(frame, f"#{i + 1}", (lx, ly),
                    cv2.FONT_HERSHEY_SIMPLEX, max(0.6, w / 640), color, 2)
