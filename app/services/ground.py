"""지면(바닥) 처리: 접지 검출 + 지면 기준 + 루트 Y 보정.

전제: 카메라는 고정 (유튜브 안무 직캠 기준). 카메라가 움직이면 2D 높이가
절대 기준이 안 되므로 보정이 어긋날 수 있다 — 그 경우 GROUND_ENABLED=0.

파이프라인 위치: step_enrich 맨 끝 (attach_root + attach_fk_joints 다음).
 full 프레임에 contacts 주입, root[y]만 보정 (x/z는 그대로).

물리 정확도가 목표가 아니라 "발이 바닥을 뚫지 않기"가 목표다.
무릎 굽힘 흡수량은 휴리스틱 (대퇴+하퇴 0.8, avatar 단위).
"""
import math
import os

L_ANK, R_ANK = 27, 28
LEG_LEN = 0.8  # avatar 단위 환산 다리 길이 (BVH OFFSET -40-40cm 기준)


def ground_enabled() -> bool:
    return os.getenv("GROUND_ENABLED", "1").strip() not in ("0", "false", "no")


def _contact_drop() -> float:
    """지면 아래 허용 오차 (정규화 y). 발목이 ground-drop보다 위에 있으면 접지 후보."""
    try:
        return max(0.0, float(os.getenv("CONTACT_DROP", "0.03")))
    except ValueError:
        return 0.03


def _contact_vel() -> float:
    """프레임당 발목 이동 상한 (정규화). 박힌 발은 거의 안 움직인다."""
    try:
        return max(0.0, float(os.getenv("CONTACT_VEL", "0.012")))
    except ValueError:
        return 0.012


def _contact_vis() -> float:
    try:
        return max(0.0, float(os.getenv("CONTACT_MIN_VIS", "0.25")))
    except ValueError:
        return 0.25


def _ankle(lm2d, idx):
    """2D 발목 → (x, y, vis). 불가시 None."""
    try:
        if not lm2d or len(lm2d) <= idx:
            return None
        p = lm2d[idx]
        if isinstance(p, dict):
            return (float(p.get("x", -1)), float(p.get("y", -1)),
                    float(p.get("visibility", 0)))
        return (float(p[0]), float(p[1]), float(p[3]) if len(p) > 3 else 1.0)
    except (IndexError, TypeError, ValueError):
        return None


def calibrate_ground(frames) -> float | None:
    """지면 높이 = 유효 프레임 낮은쪽 발목 y의 중앙값 (정규화, 클수록 아래).

    서 있는 자세가多数라는 전제. 점프투성이 영상은 중앙값이 떠서
    접지가 적게 잡히는 쪽으로 (보수적 → 보정 스킵, 뚫림보다 안전).
    """
    vis_thr = _contact_vis()
    lows = []
    for f in frames:
        lm2d = f.get("poseLandmarks") or []
        la, ra = _ankle(lm2d, L_ANK), _ankle(lm2d, R_ANK)
        if la is None or ra is None:
            continue
        if la[2] < vis_thr or ra[2] < vis_thr:
            continue
        if not (0.0 <= la[1] <= 1.0 and 0.0 <= ra[1] <= 1.0):
            continue
        lows.append(max(la[1], ra[1]))
    if len(lows) < 5:
        return None
    lows.sort()
    return lows[len(lows) // 2]


def detect_contacts(frames, ground) -> list:
    """프레임별 (L접지, R접지). raw 판정 후 앞뒤 1프레임 다수결로 깜빡임 제거."""
    drop, vel_thr, vis_thr = _contact_drop(), _contact_vel(), _contact_vis()
    raw: list = []
    prev_xy: dict = {}
    for f in frames:
        lm2d = f.get("poseLandmarks") or []
        cur = []
        for side, idx in (("L", L_ANK), ("R", R_ANK)):
            a = _ankle(lm2d, idx)
            ok = False
            if a is not None and a[2] >= vis_thr and 0.0 <= a[1] <= 1.0:
                low_enough = a[1] >= ground - drop
                px, py = prev_xy.get(side, (a[0], a[1]))
                speed = math.hypot(a[0] - px, a[1] - py)
                ok = bool(low_enough and speed <= vel_thr)
            cur.append(ok)
            if a is not None:
                prev_xy[side] = (a[0], a[1])
        raw.append(tuple(cur))
    # centered majority-3 (post-process라 다음 프레임 사용 가능)
    out = []
    n = len(raw)
    for i in range(n):
        lv = sum([raw[j][0] for j in (i - 1, i, i + 1) if 0 <= j < n]) >= 2
        rv = sum([raw[j][1] for j in (i - 1, i, i + 1) if 0 <= j < n]) >= 2
        out.append((lv, rv))
    return out


def _knee_bend(fk: dict, side: str) -> float:
    """무릎 상대 굽힘각 (rad). LowerLeg 오일러 노름 근사."""
    try:
        e = (fk or {}).get(f"{side}LowerLeg") or [0.0, 0.0, 0.0]
        return max(0.0, min(math.pi, math.sqrt(sum(float(c) ** 2 for c in e))))
    except (TypeError, ValueError):
        return 0.0


def attach_ground(payload: dict) -> dict:
    """contacts 주입 + 접지 중 바닥 뚫림만큼 root[y] 들어올림.

    lift = max(0, -root_y - absorb). 점프(접지 없음)는 손대지 않음.
    lift는 인과 윈도우-3 평균으로 펴서 착지 팝을 완화.
    """
    frames = payload.get("frames", [])
    if not ground_enabled() or not frames:
        return payload
    ground = calibrate_ground(frames)
    if ground is None:
        for f in frames:
            f["contacts"] = {"left": False, "right": False}
        payload["groundY"] = None
        return payload
    payload["groundY"] = round(ground, 4)
    contacts = detect_contacts(frames, ground)
    lifts: list = []
    for f, (lc, rc) in zip(frames, contacts):
        f["contacts"] = {"left": bool(lc), "right": bool(rc)}
        if not (lc or rc):
            lifts.append(0.0)
            continue
        root = f.get("root") or [0.0, 0.0, 0.0]
        try:
            ry = float(root[1])
        except (IndexError, TypeError, ValueError):
            lifts.append(0.0)
            continue
        fk = f.get("fkJoints") or {}
        bends = [_knee_bend(fk, s) for s, c in (("Left", lc), ("Right", rc)) if c]
        absorb = LEG_LEN * (1 - math.cos(min(bends) / 2)) if bends else 0.0
        lifts.append(max(0.0, -ry - absorb))
    # 인과 윈도우-3 평균 (착지 경계 0.1초에 분산)
    sm: list = []
    for i, v in enumerate(lifts):
        win = lifts[max(0, i - 2):i + 1]
        sm.append(sum(win) / len(win))
    for f, raw_lift, lift in zip(frames, lifts, sm):
        root = list(f.get("root") or [0.0, 0.0, 0.0])
        f["rootRaw"] = [round(float(c), 4) for c in (root + [0.0, 0.0])[:3]]
        if lift > 1e-6:
            try:
                root[1] = round(float(root[1]) + lift, 4)
            except (IndexError, TypeError, ValueError):
                pass
            f["root"] = root
    return payload
