"""MiDaS-small 뎁스. torch lazy import, 없으면 조용히 스킵.

MiDaS 출력은 상대 역뎁스(클수록 가까움). 절대 거리가 아닌 첫 프레임
대비 변화량만 루트 z에 사용하므로 스케일 문제는 없다.
"""
import os
import threading

_estimator = None
_lock = threading.Lock()
_disabled = False


def depth_enabled() -> bool:
    return os.getenv("DEPTH_ENABLED", "1").strip() not in ("0", "false", "no")


def get_estimator():
    """MiDaS-small 싱글턴. torch/모델 없으면 None (1회 경고 후 스킵)."""
    global _estimator, _disabled
    if _disabled:
        return None
    with _lock:
        if _estimator is not None:
            return _estimator
        try:
            import torch
        except ImportError:
            print("DEPTH: torch 없음, 뎁스 스킵 (pip install torch)", flush=True)
            _disabled = True
            return None
        try:
            model = torch.hub.load("intel-isl/MiDaS", "MiDaS_small", trust_repo=True)
            model.eval()
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model.to(device)
            _estimator = (model, device)
            print(f"DEPTH: MiDaS-small 로드 ({device})", flush=True)
            return _estimator
        except Exception as e:  # noqa: BLE001 - 네트워크/버전 문제 등
            print(f"DEPTH: 모델 로드 실패, 스킵: {type(e).__name__}", flush=True)
            _disabled = True
            return None


def estimate_depth(image_rgb):
    """RGB 프레임 → 역뎁스 float32 맵 (클수록 가까움). 실패시 None."""
    est = get_estimator()
    if est is None:
        return None
    try:
        import cv2
        import numpy as np
        import torch
        model, device = est
        small = cv2.resize(image_rgb, (256, 256), interpolation=cv2.INTER_LINEAR)
        x = small.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        x = (x - mean) / std
        x = torch.from_numpy(x.transpose(2, 0, 1)).unsqueeze(0).to(device)
        with torch.no_grad():
            out = model(x)
        out = torch.nn.functional.interpolate(
            out.unsqueeze(1), size=image_rgb.shape[:2], mode="bicubic",
            align_corners=False).squeeze().cpu().numpy()
        return out.astype(np.float32)
    except Exception:  # noqa: BLE001 - 프레임 하나 실패가 전체를 막으면 안 됨
        return None


def sample_hip_depth(depth_map, lms2d):
    """힙중점 뎁스값. 불가시 None."""
    try:
        if depth_map is None or not lms2d or len(lms2d) < 29:
            return None
        hx = (float(lms2d[23].get("x", 0.5)) + float(lms2d[24].get("x", 0.5))) / 2
        hy = (float(lms2d[23].get("y", 0.5)) + float(lms2d[24].get("y", 0.5))) / 2
        h, w = depth_map.shape[:2]
        v = float(depth_map[min(max(int(hy * h), 0), h - 1)][min(max(int(hx * w), 0), w - 1)])
        import math
        if not math.isfinite(v) or v <= 0:
            return None
        return v
    except (IndexError, TypeError, ValueError):
        return None
