"""옵티컬 플로우 보간. 검출 실패 프레임을 이전 프레임 흐름으로 메움.

cv2 Farneback 내장이라 의존성 추가 없음. 실패 프레임에서만 flow를
계산하므로 정상 프레임 추가 비용은 축소 그레이스케일 1장 저장뿐.
보간점 visibility=0.3: 루트는 사용(>0.2)하고 3D는 직전값 홀드 유지.
"""
import os

FLOW_VIS = 0.3
MIN_VALID = 10  # 이하 유효점이면 장면 전환으로 보고 보간 포기


def flow_enabled() -> bool:
    return os.getenv("FLOW_FILL", "1").strip() not in ("0", "false", "no")


def flow_width() -> int:
    try:
        return max(64, int(os.getenv("FLOW_WIDTH", "256")))
    except ValueError:
        return 256


def small_gray(frame_bgr):
    """축소 그레이스케일 + 크기 반환. (gray, sw, sh)"""
    import cv2
    h, w = frame_bgr.shape[:2]
    tw = flow_width()
    scale = tw / max(w, 1)
    small = cv2.resize(frame_bgr, (tw, max(1, int(h * scale))),
                       interpolation=cv2.INTER_LINEAR)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), small.shape[1], small.shape[0]


def compute_flow(prev_gray, gray):
    import cv2
    return cv2.calcOpticalFlowFarneback(prev_gray, gray, None,
                                        0.5, 3, 15, 3, 5, 1.1, 0)


def advect_points(lms2d, flow):
    """정규화 2D를 흐름 따라 이동. (신規 리스트, 유효 수)"""
    sh, sw = flow.shape[:2]
    out = []
    valid = 0
    for lm in lms2d or []:
        try:
            x = float(lm.get("x", -1))
            y = float(lm.get("y", -1))
        except (TypeError, ValueError, AttributeError):
            out.append({"x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.0})
            continue
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            out.append({"x": min(max(x, 0.0), 1.0), "y": min(max(y, 0.0), 1.0),
                        "z": 0.0, "visibility": 0.0})
            continue
        xi = min(max(int(x * sw), 0), sw - 1)
        yi = min(max(int(y * sh), 0), sh - 1)
        dx, dy = flow[yi, xi]
        nx, ny = x + float(dx) / sw, y + float(dy) / sh
        if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0:
            out.append({"x": nx, "y": ny, "z": 0.0, "visibility": FLOW_VIS})
            valid += 1
        else:
            out.append({"x": min(max(nx, 0.0), 1.0), "y": min(max(ny, 0.0), 1.0),
                        "z": 0.0, "visibility": 0.0})
    return out, valid
