"""Dance SMPL Worker (HF Spaces, ZeroGPU).

춤 영상 mp4 → SMPL pose 72D 시퀀스 JSON.
dancing-avatar 백엔드의 하이브리드 정제 스테이지용 워커.

- HMR2 직접 실행 (ViTPose/mmpose/chumpy/SMPL .pkl 불필요)
- 검출 YOLOv8 (자동 다운로드), 박스 중앙값 스무딩
- 반환: {pose: [[72]...], betas_med: [10], n_frames, fps, truncated}
"""
import json
import os
import sys
import tempfile

import cv2
import joblib
import numpy as np
import torch
import gradio as gr
import spaces
from scipy.signal import medfilt

REPO = "/data/wham"
CKPT_HMR2 = "/data/hmr2a.ckpt"
HMR2_URL = "https://drive.google.com/uc?id=1J6l8teyZrL0zFzHhzkC7efRhU0ZJ5G9Y&export=download&confirm=t"
DEVICE = "cuda"
CONF = 0.5
MAX_FRAMES = 1200

_model = None
_yolo = None


def _ensure_assets():
    """리포지토리 sparse-clone + 체크포인트 (없을 때만, /data 영속)."""
    need_repo = not os.path.exists(os.path.join(REPO, "lib/models/preproc/backbone/hmr2.py"))
    if need_repo:
        os.makedirs("/data", exist_ok=True)
        rc = os.system(
            f"git clone --depth 1 --filter=blob:none --sparse https://github.com/yohanshin/WHAM {REPO} && "
            f"git -C {REPO} sparse-checkout set lib/models/preproc/backbone lib/utils configs"
        )
        if rc != 0:
            raise RuntimeError("WHAM sparse-clone 실패")
    if not os.path.exists(CKPT_HMR2):
        import gdown
        gdown.download(HMR2_URL, CKPT_HMR2, quiet=False)
        if not os.path.exists(CKPT_HMR2):
            raise RuntimeError("hmr2a.ckpt 다운로드 실패")


def _load():
    global _model, _yolo
    if _model is not None:
        return
    sys.path.insert(0, REPO)
    from lib.models.preproc.backbone.hmr2 import hmr2
    from ultralytics import YOLO
    _yolo = YOLO("yolov8x.pt")
    _model = hmr2(CKPT_HMR2).to(DEVICE).eval()


def _mat2aa(m):
    r, _ = cv2.Rodrigues(np.asarray(m, dtype=np.float64))
    return [float(v) for v in r[:, 0]]


def _track_boxes(video_path, fps):
    """프레임별 최대 인물 박스 [cx, cy, scale] + 중앙값 스무딩."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("영상을 열 수 없음")
    raw = []
    while True:
        ret, img = cap.read()
        if not ret:
            break
        res = _yolo.predict(img, device=DEVICE, classes=0, conf=CONF,
                            save=False, verbose=False)[0]
        if len(res.boxes) == 0:
            raw.append(None)
            continue
        xyxy = res.boxes.xyxy.detach().cpu().numpy()
        b = xyxy[np.argmax((xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1]))]
        raw.append([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2,
                    max(b[2] - b[0], b[3] - b[1]) / 200 * 1.05])
    cap.release()
    if not raw:
        raise ValueError("프레임 없음")
    valid = [i for i, b in enumerate(raw) if b]
    if not valid:
        raise ValueError("인물 검출 없음")
    arr = np.array([
        raw[i] if raw[i] else raw[min(valid, key=lambda k: abs(k - i))]
        for i in range(len(raw))
    ], dtype=float)
    k = int(int((fps or 30.0) / 2) / 2) * 2 + 1
    return np.stack([medfilt(arr[:, j], k) for j in range(3)], axis=1)


@spaces.GPU(duration=240)
def estimate(video_path):
    """Gradio API: 영상 경로 → SMPL pose JSON 파일 경로."""
    _ensure_assets()
    _load()
    from lib.models.preproc.backbone.utils import process_image

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    if total <= 0:
        raise ValueError("프레임 수 확인 불가")

    boxes = _track_boxes(video_path, fps)
    n = min(len(boxes), MAX_FRAMES)
    truncated = len(boxes) > MAX_FRAMES

    cap = cv2.VideoCapture(video_path)
    poses, betas, fids = [], [], []
    with torch.no_grad():
        for i in range(n):
            ret, img = cap.read()
            if not ret:
                break
            cx, cy, s = (float(v) for v in boxes[i])
            norm_img, _ = process_image(img[..., ::-1], [cx, cy], s, 256, 256)
            go, bp, be, _cam = _model(
                torch.from_numpy(norm_img).unsqueeze(0).to(DEVICE), encode=False)
            go = np.asarray(go.cpu()).reshape(-1, 3, 3)
            bp = np.asarray(bp.cpu()).reshape(-1, 3, 3)
            aa = [_mat2aa(m) for m in go] + [_mat2aa(m) for m in bp]
            poses.append([c for v in aa for c in v])
            betas.append([float(v) for v in np.asarray(be.cpu()).reshape(-1)])
            fids.append(i)
    cap.release()
    if not poses:
        raise ValueError("추론 결과 없음")
    out = {
        "pose": poses,
        "betas_med": [float(v) for v in np.median(np.asarray(betas, dtype=float), axis=0)],
        "frame_ids": fids,
        "n_frames": len(poses),
        "fps": fps,
        "truncated": truncated,
    }
    fd, jp = tempfile.mkstemp(suffix=".json", prefix="smpl_")
    with os.fdopen(fd, "w") as f:
        json.dump(out, f)
    return jp


demo = gr.Interface(
    fn=estimate,
    inputs=gr.Video(label="dance video (mp4)"),
    outputs=gr.File(label="smpl pose json"),
    title="Dance SMPL Worker",
    description="mp4 → SMPL 72D pose sequence (HMR2, local-only)",
    api_name="predict",
    allow_flagging="never",
)
demo.queue(max_size=4, api_open=True)
demo.launch()
