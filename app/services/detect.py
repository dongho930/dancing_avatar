"""모델 로딩 + 프레임 검출. 추적(tracking.py)과 분리."""
import os
import urllib.request

_MODEL_URLS = {
    "lite": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "full": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
    "heavy": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/1/pose_landmarker_heavy.task",
}

_HAND_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"


def ensure_pose_model(tmp_dir: str) -> str:
    variant = os.getenv("POSE_MODEL_VARIANT", "lite").lower()
    override = os.getenv("POSE_MODEL_PATH", "").strip()
    if override and os.path.exists(override):
        return override
    url = _MODEL_URLS.get(variant, _MODEL_URLS["lite"])
    dest = os.path.join(tmp_dir, "models", f"pose_landmarker_{variant}.task")
    if not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp_dl = dest + ".downloading"
        print(f"포즈 모델 다운로드 중 ({variant}): {url}", flush=True)
        urllib.request.urlretrieve(url, tmp_dl)
        os.replace(tmp_dl, dest)
        print(f"포즈 모델 저장: {dest}", flush=True)
    return dest


def ensure_hand_model(tmp_dir: str) -> str:
    override = os.getenv("HAND_MODEL_PATH", "").strip()
    if override and os.path.exists(override):
        return override
    dest = os.path.join(tmp_dir, "models", "hand_landmarker.task")
    if not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp_dl = dest + ".downloading"
        print(f"손 모델 다운로드 중: {_HAND_MODEL_URL}", flush=True)
        urllib.request.urlretrieve(_HAND_MODEL_URL, tmp_dl)
        os.replace(tmp_dl, dest)
        print(f"손 모델 저장: {dest}", flush=True)
    return dest


def lm_to_dict(lm) -> dict:
    return {"x": float(lm.x), "y": float(lm.y), "z": float(lm.z),
            "visibility": float(getattr(lm, "visibility", 0.0) or 0.0)}


class null_context:
    """손 검출 OFF일 때 `with` 자리를 채우는 더미."""
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def detect_poses(landmarker, mp_image, ts_ms: int) -> tuple[list, list]:
    """한 프레임 다인 검출 → (2D 리스트, 3D 리스트). 실패시 ([], [])."""
    try:
        res = landmarker.detect_for_video(mp_image, ts_ms)
    except Exception:  # noqa: BLE001
        return [], []
    lms2d = [[lm_to_dict(lm) for lm in p] for p in (res.pose_landmarks or [])]
    lms3d = [[lm_to_dict(lm) for lm in p] for p in (res.pose_world_landmarks or [])]
    return lms2d, lms3d


def detect_poses_with_masks(landmarker, mp_image, ts_ms: int) -> tuple[list, list, list]:
    """다인 검출 + 세그멘테이션 마스크. 마스크는 numpy float32 리스트 (실패시 [])."""
    try:
        res = landmarker.detect_for_video(mp_image, ts_ms)
    except Exception:  # noqa: BLE001
        return [], [], []
    lms2d = [[lm_to_dict(lm) for lm in p] for p in (res.pose_landmarks or [])]
    lms3d = [[lm_to_dict(lm) for lm in p] for p in (res.pose_world_landmarks or [])]
    masks = []
    for m in (res.segmentation_masks or []):
        try:
            import numpy as np
            arr = np.array(m.numpy_view(), dtype=np.float32)
            masks.append(arr)
        except Exception:  # noqa: BLE001
            continue
    return lms2d, lms3d, masks


def silhouette_from_mask(mask) -> dict | None:
    """마스크 → {bbox, area, cx, cy} (정규화). 너무 작거나 크면 None."""
    try:
        import numpy as np
        arr = np.asarray(mask)
        if arr.ndim == 3:
            arr = arr[..., 0]  # (H, W, 1) → (H, W)
        binm = arr > 0.5
        area = float(binm.mean())
        if not (0.005 <= area <= 0.95):
            return None
        ys, xs = np.nonzero(binm)
        h, w = binm.shape[:2]
        x0, x1 = float(xs.min()) / w, float(xs.max()) / w
        y0, y1 = float(ys.min()) / h, float(ys.max()) / h
        return {"bbox": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                "area": round(area, 4),
                "cx": round(float(xs.mean()) / w, 4),
                "cy": round(float(ys.mean()) / h, 4)}
    except Exception:  # noqa: BLE001
        return None


def detect_hands(hands, mp_image, ts_ms: int) -> list[dict]:
    """한 프레임의 손 검출 → [{side,score,landmarks[21],world[21]}]. 없으면 []."""
    try:
        res = hands.detect_for_video(mp_image, ts_ms)
    except Exception:  # noqa: BLE001 - 손 실패가 본체까지 깨뜨리면 안 됨
        return []
    out = []
    n = len(res.hand_landmarks or [])
    for i in range(n):
        try:
            side, score = "Unknown", 0.0
            if res.handedness and i < len(res.handedness) and res.handedness[i]:
                cat = res.handedness[i][0]
                side, score = str(cat.category_name or "Unknown"), float(cat.score or 0.0)
            world = []
            if res.hand_world_landmarks and i < len(res.hand_world_landmarks):
                world = [lm_to_dict(lm) for lm in res.hand_world_landmarks[i]]
            out.append({
                "side": side,
                "score": round(score, 3),
                "landmarks": [lm_to_dict(lm) for lm in res.hand_landmarks[i]],
                "world": world,
            })
        except (IndexError, TypeError, ValueError):
            continue
    return out
