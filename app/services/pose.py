"""포즈+손 추출 오케스트레이션. 검출(detect.py)+추적(tracking.py) 조합.

target: auto(1명 전제) | left | center | right | face(기준사진 필요)
반환: {fps, total_frames, img_w, img_h, target:{mode,label,persons_max},
       frames:[{frame,timestamp,poseWorldLandmarks,poseLandmarks,hands,persons,lost,flow_filled,silhouette,depth,midSpine}]}
얼굴 표정은 의도적으로 미지원.
"""
import os
from collections.abc import Callable

from app.services.detect import (
    detect_hands,
    detect_poses,
    ensure_hand_model,
    ensure_pose_model,
    null_context,
    silhouette_from_mask,
    spine_from_mask,
)
from app.services.flow import MIN_VALID, advect_points, compute_flow, flow_enabled, small_gray
from app.services.tracking import Tracker, _person_entries, assign_hands


def process_video_pose(video_path: str, max_frames: int = 900,
                       progress_cb: Callable[[int, int], None] | None = None,
                       target: str = "auto",
                       ref_image_b64: str | None = None,
                       target_point=None) -> dict:
    import cv2
    import mediapipe as mp

    if hasattr(mp, "solutions") and hasattr(getattr(mp, "solutions", None), "pose"):
        return _legacy_process(cv2, mp, video_path, max_frames, progress_cb)

    from mediapipe.tasks.python import vision as mp_vision
    from mediapipe.tasks.python import BaseOptions

    from app.core.config import get_settings
    settings = get_settings()
    # 대상 지정: auto면 1명만 검출(기존 속도), 그 외는 다인 검출+추적
    multi = target in ("left", "center", "right", "face", "point")
    max_poses = max(1, int(os.getenv("MAX_POSES", "4"))) if multi else 1
    sil_on = os.getenv("SILHOUETTE_ENABLED", "1").strip() not in ("0", "false", "no")
    options = mp_vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=ensure_pose_model(settings.tmp_dir)),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_poses=max_poses,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        output_segmentation_masks=sil_on,
    )
    enable_hands = os.getenv("ENABLE_HANDS", "1").strip() not in ("0", "false", "no")
    max_hands = int(os.getenv("MAX_HANDS", "2"))
    hand_options = None
    if enable_hands:
        hand_options = mp_vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=ensure_hand_model(settings.tmp_dir)),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=max_hands,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
    ref_emb = None
    if target == "face":
        if not (ref_image_b64 or "").strip():
            raise RuntimeError("target=face인데 기준 얼굴사진(ref_image_b64)이 없습니다")
        from app.services.face_match import decode_ref_image, embed_face_bgr
        ref_emb = embed_face_bgr(decode_ref_image(ref_image_b64))
        if ref_emb is None:
            raise RuntimeError("기준 얼굴사진에서 얼굴을 찾지 못했습니다")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"동영상 파일을 열 수 없음: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    img_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1.0
    img_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1.0
    tracker = Tracker(target if multi else "auto", target_point=target_point)
    flow_on = flow_enabled()
    prev_gray = None
    prev_lms2d = None
    prev_lms3d = None
    motion_data: list[dict] = []
    frame_idx = 0
    try:
        with mp_vision.PoseLandmarker.create_from_options(options) as landmarker:
            hand_ctx = null_context() if hand_options is None else \
                mp_vision.HandLandmarker.create_from_options(hand_options)
            with hand_ctx as hands:
                while True:
                    ret, frame = cap.read()
                    if not ret or frame_idx >= max_frames:
                        break
                    image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
                    ts_ms = int(frame_idx / fps * 1000) if fps > 0 else frame_idx * 33
                    gray, _, _ = small_gray(frame) if flow_on else (None, 0, 0)
                    if sil_on:
                        from app.services.detect import detect_poses_with_masks
                        lms2d, lms3d, masks = detect_poses_with_masks(landmarker, mp_image, ts_ms)
                    else:
                        lms2d, lms3d = detect_poses(landmarker, mp_image, ts_ms)
                        masks = []
                    persons = _person_entries(lms2d, lms3d)
                    lms3d_pick, lms2d_pick, pick, lost = tracker.update(
                        persons, image_rgb, ref_emb)
                    sil = None
                    if sil_on and pick is not None and pick < len(masks):
                        sil = silhouette_from_mask(masks[pick])
                    mid = None
                    if (sil_on and pick is not None and pick < len(masks)
                            and lms2d_pick and len(lms2d_pick) >= 29):
                        try:
                            shx = (float(lms2d_pick[11].get("x", 0.5))
                                   + float(lms2d_pick[12].get("x", 0.5))) / 2
                            shy = (float(lms2d_pick[11].get("y", 0.5))
                                   + float(lms2d_pick[12].get("y", 0.5))) / 2
                            hxx = (float(lms2d_pick[23].get("x", 0.5))
                                   + float(lms2d_pick[24].get("x", 0.5))) / 2
                            hyy = (float(lms2d_pick[23].get("y", 0.5))
                                   + float(lms2d_pick[24].get("y", 0.5))) / 2
                            mid = spine_from_mask(masks[pick], (shx, shy), (hxx, hyy))
                        except (IndexError, TypeError, ValueError, AttributeError):
                            mid = None
                    dep = None
                    if lms2d_pick:
                        from app.services.depth import depth_enabled, estimate_depth, sample_hip_depth
                        if depth_enabled():
                            dep = sample_hip_depth(estimate_depth(image_rgb), lms2d_pick)
                    flow_filled = False
                    if flow_on and not lms2d_pick and prev_lms2d and prev_gray is not None:
                        adv, n_valid = advect_points(
                            prev_lms2d, compute_flow(prev_gray, gray))
                        if n_valid >= MIN_VALID:
                            lms2d_pick = adv
                            lms3d_pick = prev_lms3d or []
                            flow_filled = True
                    hand_entries = detect_hands(hands, mp_image, ts_ms) if hands is not None else []
                    if multi and persons:
                        hand_entries = assign_hands(hand_entries, persons, pick)
                    motion_data.append({
                        "frame": frame_idx,
                        "timestamp": frame_idx / fps if fps > 0 else 0,
                        "poseWorldLandmarks": lms3d_pick,
                        "poseLandmarks": lms2d_pick,
                        "hands": hand_entries,
                        "persons": len(persons),
                        "lost": lost,
                        "flow_filled": flow_filled,
                        "silhouette": sil,
                        "depth": dep,
                        "midSpine": mid,
                    })
                    if flow_on:
                        prev_gray = gray
                        if lms2d_pick:
                            prev_lms2d = lms2d_pick
                            prev_lms3d = lms3d_pick
                    frame_idx += 1
                    if progress_cb and frame_idx % 30 == 0:
                        progress_cb(frame_idx, max_frames)
    finally:
        cap.release()
    return {"fps": fps, "total_frames": frame_idx, "frames": motion_data,
            "img_w": img_w, "img_h": img_h,
            "target": {"mode": target, "label": tracker.label,
                       "persons_max": tracker.persons_max}}


def _legacy_process(cv2, mp, video_path: str, max_frames: int,
                    progress_cb: Callable[[int, int], None] | None) -> dict:
    """mediapipe<1.0 (mp.solutions 존재) 환경용 기존 경로."""
    mp_pose = mp.solutions.pose
    pose = mp_pose.Pose(
        static_image_mode=False,
        model_complexity=1,
        smooth_landmarks=True,
        enable_segmentation=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        pose.close()
        raise RuntimeError(f"동영상 파일을 열 수 없음: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    img_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1.0
    img_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1.0
    motion_data: list[dict] = []
    frame_idx = 0
    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret or frame_idx >= max_frames:
                break
            image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = pose.process(image_rgb)
            landmarks_3d, landmarks_2d = [], []
            if results.pose_world_landmarks:
                landmarks_3d = [{"x": lm.x, "y": lm.y, "z": lm.z,
                                 "visibility": lm.visibility}
                                for lm in results.pose_world_landmarks.landmark]
            if results.pose_landmarks:
                landmarks_2d = [{"x": lm.x, "y": lm.y, "z": lm.z,
                                 "visibility": lm.visibility}
                                for lm in results.pose_landmarks.landmark]
            motion_data.append({
                "frame": frame_idx,
                "timestamp": frame_idx / fps if fps > 0 else 0,
                "poseWorldLandmarks": landmarks_3d,
                "poseLandmarks": landmarks_2d,
                "hands": [],  # legacy(mp.solutions) 경로: 손 미지원
                "persons": 1 if landmarks_2d else 0,
                "lost": not bool(landmarks_2d),
                "midSpine": None,
            })
            frame_idx += 1
            if progress_cb and frame_idx % 30 == 0:
                progress_cb(frame_idx, max_frames)
    finally:
        cap.release()
        pose.close()
    return {"fps": fps, "total_frames": frame_idx, "frames": motion_data,
            "img_w": img_w, "img_h": img_h,
            "target": {"mode": "auto", "label": "auto", "persons_max": 1}}
