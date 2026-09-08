"""관절 수학: 방향벡터→오일러, 몸통 11관절·손가락·루트모션 계산.

입출력 포맷(rig/compact/BVH)은 exports.py. 둘로 나뉘기 전 retarget.py였다.
"""
import math
import os

# 좌표계 전제 (실측): MediaPipe world/2D 모두 y-아래+ (코 -0.6, 발목 +0.7).
# 따라서 화면-아래 방향 = (0,+1,0), 화면-위 = (0,-1,0). ref는 이 기준이다.
DOWN = (0.0, 1.0, 0.0)
UP = (0.0, -1.0, 0.0)

# MediaPipe Pose 인덱스
L_SH, R_SH, L_EL, R_EL, L_WR, R_WR = 11, 12, 13, 14, 15, 16
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANK, R_ANK = 23, 24, 25, 26, 27, 28
L_HEEL, R_HEEL, L_FOOT, R_FOOT = 29, 30, 31, 32
NOSE = 0

RIG_JOINTS = ("Spine", "Chest", "Neck", "Head", "Hips",
              "LeftShoulder", "RightShoulder",
              "LeftUpperArm", "LeftLowerArm", "RightUpperArm", "RightLowerArm",
              "LeftUpperLeg", "LeftLowerLeg", "RightUpperLeg", "RightLowerLeg",
              "LeftFoot", "RightFoot", "LeftToe", "RightToe")
# 발 정면 rest: 카메라 쪽(+z) 수평. 서서 정면 기준 근사치.
FWD = (0.0, 0.0, 1.0)


def _v(a, b):
    return (b[0] - a[0], b[1] - a[1], b[2] - a[2])


def _norm(v):
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-9 else (0.0, 1.0, 0.0)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _quat_from_to(a, b):
    """단위벡터 a→b 최단호 quaternion (x,y,z,w)."""
    d = max(-1.0, min(1.0, _dot(a, b)))
    if d > 0.999999:
        return (0.0, 0.0, 0.0, 1.0)
    if d < -0.999999:
        # 정반대: a와 직교하는 축 선택
        axis = _cross(a, (1.0, 0.0, 0.0))
        if _dot(axis, axis) < 1e-9:
            axis = _cross(a, (0.0, 0.0, 1.0))
        axis = _norm(axis)
        return (axis[0], axis[1], axis[2], 0.0)
    axis = _cross(a, b)
    s = math.sqrt((1 + d) * 2)
    inv = 1 / s
    return (axis[0] * inv, axis[1] * inv, axis[2] * inv, s * 0.5)


def _quat_to_euler_xyz(q):
    # three.js Euler.setFromRotationMatrix 'XYZ' 분기와 동일.
    # 임계값(0.9999999)과 짐벌식(atan2(m21, m11))이 핵심. 어긋나면 다축에서 수십° 오차.
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    m00 = 1 - 2 * (y * y + z * z)
    m01 = 2 * (x * y - w * z)
    m02 = 2 * (x * z + w * y)
    m11 = 1 - 2 * (x * x + z * z)
    m12 = 2 * (y * z - w * x)
    m21 = 2 * (y * z + w * x)
    m22 = 1 - 2 * (x * x + y * y)
    sy = max(-1.0, min(1.0, m02))
    if abs(sy) >= 0.9999999:
        ey = math.pi / 2 if sy > 0 else -math.pi / 2
        ex = math.atan2(m21, m11)
        ez = 0.0
    else:
        ey = math.asin(sy)
        ex = math.atan2(-m12, m22)
        ez = math.atan2(-m01, m00)
    return (round(ex, 3), round(ey, 3), round(ez, 3))


def _pt(lm, i):
    p = lm[i]
    if isinstance(p, dict):
        return (float(p.get("x", 0)), float(p.get("y", 0)), float(p.get("z", 0)))
    return (float(p[0]), float(p[1]), float(p[2]))


def _vis(lm, i):
    p = lm[i]
    if isinstance(p, dict):
        return float(p.get("visibility", 0))
    return float(p[3]) if len(p) > 3 else 1.0


def _bone_euler(lm, a_idx, b_idx, ref=DOWN):
    try:
        d = _norm(_v(_pt(lm, a_idx), _pt(lm, b_idx)))
    except (IndexError, TypeError, ValueError):
        return (0.0, 0.0, 0.0)
    return _quat_to_euler_xyz(_quat_from_to(ref, d))


def _root_raw(lm2d):
    """2D 한 프레임 → (hipx, hipy, torso_len, vis). 불가시 None."""
    if not lm2d or len(lm2d) < 29:
        return None
    try:
        hx = (_pt(lm2d, L_HIP)[0] + _pt(lm2d, R_HIP)[0]) / 2
        hy = (_pt(lm2d, L_HIP)[1] + _pt(lm2d, R_HIP)[1]) / 2
        sx = (_pt(lm2d, L_SH)[0] + _pt(lm2d, R_SH)[0]) / 2
        sy = (_pt(lm2d, L_SH)[1] + _pt(lm2d, R_SH)[1]) / 2
        torso = math.sqrt((sx - hx) ** 2 + (sy - hy) ** 2)
        vis = min(_vis(lm2d, L_HIP), _vis(lm2d, R_HIP))
        if torso < 1e-6:
            return None
        return (hx, hy, torso, vis)
    except (IndexError, TypeError, ValueError):
        return None


def _sil_raw(sil):
    """실루엣 한 프레임 → (cx, cy, height). 면적 비정상시 None."""
    if not sil:
        return None
    try:
        area = float(sil.get("area", 0))
        if not (0.005 <= area <= 0.95):
            return None
        x0, y0, x1, y1 = (float(v) for v in sil.get("bbox", []))
        return ((x0 + x1) / 2, (y0 + y1) / 2, max(y1 - y0, 1e-6))
    except (IndexError, TypeError, ValueError):
        return None


def _depth_raw(depth):
    try:
        v = float(depth)
        import math
        if not math.isfinite(v) or v <= 0:
            return None
        return v
    except (TypeError, ValueError):
        return None


def attach_root(payload: dict, k: float | None = None, win: int = 5) -> dict:
    """full 페이로드 각 프레임에 root[x,y,z](아바타 단위 상대 오프셋) 주입.

    힙 기준 + 실루엣 + 뎁스 3개 독립 측정의 평균. 소스별 첫 유효값을
    기준으로 삼아 어느 하나만 있어도 동작하고, 구 저장분(키 없음)도 그대로.
    """
    if k is None:
        try:
            k = float(os.getenv("ROOT_K", "2.0"))
        except ValueError:
            k = 2.0
    frames = payload.get("frames", [])
    ref_h = ref_s = ref_d = None
    for f in frames:
        if ref_h is None:
            r = _root_raw(f.get("poseLandmarks") or [])
            if r is not None and r[3] > 0.2:
                ref_h = (r[0], r[1], r[2])
        if ref_s is None:
            s = _sil_raw(f.get("silhouette"))
            if s is not None:
                ref_s = s
        if ref_d is None:
            d = _depth_raw(f.get("depth"))
            if d is not None:
                ref_d = d
        if ref_h is not None and ref_s is not None and ref_d is not None:
            break
    if ref_h is None and ref_s is None and ref_d is None:
        for f in frames:
            f["root"] = [0.0, 0.0, 0.0]
        return payload
    buf: list = []
    last = [0.0, 0.0, 0.0]
    for f in frames:
        dxs, dys, dzs = [], [], []
        if ref_h is not None:
            r = _root_raw(f.get("poseLandmarks") or [])
            if r is not None and r[3] > 0.2:
                dxs.append((r[0] - ref_h[0]) * k)
                dys.append(-(r[1] - ref_h[1]) * k)  # 영상 y(아래+) → 월드 y(위+)
                dzs.append((r[2] / ref_h[2] - 1.0) * k * 0.5)
        if ref_s is not None:
            s = _sil_raw(f.get("silhouette"))
            if s is not None:
                dxs.append((s[0] - ref_s[0]) * k)
                dys.append(-(s[1] - ref_s[1]) * k)
                dzs.append((s[2] / ref_s[2] - 1.0) * k * 0.5)
        if ref_d is not None:
            d = _depth_raw(f.get("depth"))
            if d is not None:
                dzs.append((d / ref_d - 1.0) * k * 0.5)  # MiDaS 클수록 가까움
        if not dxs and not dys and not dzs:
            f["root"] = list(last)  # 검출 실패 프레임은 직전값 유지
            continue
        pt = [sum(dxs) / len(dxs) if dxs else last[0],
              sum(dys) / len(dys) if dys else last[1],
              sum(dzs) / len(dzs) if dzs else last[2]]
        buf.append(pt)
        buf = buf[-win:]
        avg = [sum(c) / len(buf) for c in zip(*buf)]
        f["root"] = [round(v, 4) for v in avg]
        last = avg
    return payload


def frame_to_rig(world_lm: list) -> dict | None:
    """한 프레임 world landmarks(33) → 11개 오일러각. 실패시 None."""
    if not world_lm or len(world_lm) < 29:
        return None
    try:
        hip_c = (( _pt(world_lm, L_HIP)[0] + _pt(world_lm, R_HIP)[0]) / 2,
                 (_pt(world_lm, L_HIP)[1] + _pt(world_lm, R_HIP)[1]) / 2,
                 (_pt(world_lm, L_HIP)[2] + _pt(world_lm, R_HIP)[2]) / 2)
        sh_c = ((_pt(world_lm, L_SH)[0] + _pt(world_lm, R_SH)[0]) / 2,
                (_pt(world_lm, L_SH)[1] + _pt(world_lm, R_SH)[1]) / 2,
                (_pt(world_lm, L_SH)[2] + _pt(world_lm, R_SH)[2]) / 2)
        torso = _norm(_v(hip_c, sh_c))
        # torso는 화면-위쪽(-Y)을 가리키므로 ref를 UP으로
        spine = _quat_to_euler_xyz(_quat_from_to(UP, torso))
        neck = _bone_euler(world_lm, L_SH, NOSE, ref=UP)
        joints = {
            "Spine": list(spine),
            "Chest": list(spine),
            "Neck": list(neck),
            "LeftUpperArm": list(_bone_euler(world_lm, L_SH, L_EL)),
            "LeftLowerArm": list(_bone_euler(world_lm, L_EL, L_WR)),
            "RightUpperArm": list(_bone_euler(world_lm, R_SH, R_EL)),
            "RightLowerArm": list(_bone_euler(world_lm, R_EL, R_WR)),
            "LeftUpperLeg": list(_bone_euler(world_lm, L_HIP, L_KNEE)),
            "LeftLowerLeg": list(_bone_euler(world_lm, L_KNEE, L_ANK)),
            "RightUpperLeg": list(_bone_euler(world_lm, R_HIP, R_KNEE)),
            "RightLowerLeg": list(_bone_euler(world_lm, R_KNEE, R_ANK)),
        }
        vis = round(sum(_vis(world_lm, i) for i in
                        (L_SH, R_SH, L_EL, R_EL, L_WR, R_WR, L_HIP, R_HIP, L_KNEE, R_KNEE)) / 10, 3)
        return {"joints": joints, "vis": vis}
    except (IndexError, TypeError, ValueError):
        return None


def hand_to_joints(hand_lm: list, side: str = "Right") -> dict | None:
    """손 21점 → {wrist, fingers{5×3}, vis}. Kalidokit HandSolver 포팅 사용."""
    if not hand_lm or len(hand_lm) < 21:
        return None
    try:
        from app.services.kalidokit import solve_hand
        solved = solve_hand(hand_lm, side)
        if solved is None:
            return None
        vis = round(sum(_vis(hand_lm, i) for i in (0, 5, 9, 13, 17)) / 5, 3)
        solved["vis"] = vis
        return solved
    except (IndexError, TypeError, ValueError):
        return None


def resolve_hand_side(hand: dict, world33: list) -> str | None:
    """손이 어느 팔에 달렸는지 기하학으로 판정. 검출기 라벨보다 신뢰.

    HandLandmarker handedness는 동작 흐림·가려짐에서 자주 뒤집힘(실측 41%).
    포즈 손목(15/16)과 같은 world 공간에서 최근접으로 판정하면 팔 체인과
    항상 일치한다. 애매하면 None → 호출부가 기존 라벨 로직 사용.
    """
    try:
        lms = hand.get("world") or hand.get("landmarks") or []
        if len(lms) < 21 or not world33 or len(world33) < 29:
            return None
        wx = float(lms[0].get("x", 0.5))
        wy = float(lms[0].get("y", 0.5))
        lx = float(world33[15].get("x", 0.5))
        ly = float(world33[15].get("y", 0.5))
        rx = float(world33[16].get("x", 0.5))
        ry = float(world33[16].get("y", 0.5))
        dl = (wx - lx) ** 2 + (wy - ly) ** 2
        dr = (wx - rx) ** 2 + (wy - ry) ** 2
        # 양쪽 손목에서 다 멀면 오검출 → 판정 보류
        if min(dl, dr) > 0.09:
            return None
        # 너무 박빙이면 플리커 방지용으로 판정 보류
        if abs(dl - dr) / max(dl + dr, 1e-9) < 0.1:
            return None
        return "Left" if dl < dr else "Right"
    except (IndexError, TypeError, ValueError, AttributeError, KeyError):
        return None


def _hand_xy(h) -> tuple | None:
    """슬롯 추적용 손목 위치 (정규화 이미지 공간). world는 손목 원점이라 사용 금지."""
    try:
        lms = h.get("landmarks") or []
        if len(lms) < 21:
            return None
        return (float(lms[0].get("x", 0.5)), float(lms[0].get("y", 0.5)))
    except (IndexError, TypeError, ValueError, AttributeError):
        return None


def _pose_wrists_2d(pose2d: list) -> dict | None:
    try:
        if not pose2d or len(pose2d) < 29:
            return None
        return {"Left": (float(pose2d[15].get("x", 0.5)), float(pose2d[15].get("y", 0.5))),
                "Right": (float(pose2d[16].get("x", 0.5)), float(pose2d[16].get("y", 0.5)))}
    except (IndexError, TypeError, ValueError, AttributeError):
        return None


def frame_hand_joints(hands_entries: list, world33: list | None = None,
                      slots: dict | None = None) -> dict:
    """프레임의 hands[] → {Left:{...}|None, Right:{...}|None}. 라벨 불명확시 빈 슬롯에."""
    out: dict = {"Left": None, "Right": None}
    indexed = list(hands_entries or [])
    for key, h in enumerate(indexed):
        if slots is not None:
            side = next((s for s, k in slots.items() if k == key), None)
            if side is None:
                continue
        else:
            side = resolve_hand_side(h, world33 or [])
            if side is None:
                side = str(h.get("side", "")).capitalize()
                if side not in ("Left", "Right"):
                    side = "Left" if out["Left"] is None else "Right"
        lms = h.get("world") or h.get("landmarks") or []
        joints = hand_to_joints(lms, side)
        if joints is None:
            continue
        if out[side] is None:
            out[side] = joints
    return out


def attach_hand_joints(payload: dict) -> dict:
    """full 페이로드 각 프레임에 handJoints(손가락 오일러각) 주입. 저장 전에 호출."""
    from app.services.tracking import HandTracker
    tracker = HandTracker()
    for f in payload.get("frames", []):
        entries = list(f.get("hands") or [])
        cands, labels = [], {}
        for key, h in enumerate(entries):
            xy = _hand_xy(h)
            if xy is None:
                continue
            cands.append((key, xy[0], xy[1]))
            labels[key] = h.get("side", "")
        slots = tracker.assign(cands, _pose_wrists_2d(f.get("poseLandmarks") or []), labels)
        f["_hand_slots"] = slots
        f["handJoints"] = frame_hand_joints(entries, f.get("poseWorldLandmarks") or [],
                                            slots=slots)
    return payload


def attach_foot_joints(payload: dict) -> dict:
    """full 페이로드 각 프레임에 footJoints(발/발가락) 주입. 저장 전에 호출."""
    for f in payload.get("frames", []):
        f["footJoints"] = foot_to_joints(f.get("poseWorldLandmarks") or [])
    return payload


# carry-forward용 관절별 가시성 기준 랜드마크
JOINT_VIS = {
    "Spine": (23, 24, 11, 12), "Chest": (23, 24, 11, 12),
    "Neck": (11, 12, 0), "Head": (11, 12, 0), "Hips": (23, 24),
    "LeftShoulder": (11, 12), "RightShoulder": (11, 12),
    "LeftUpperArm": (11, 13), "LeftLowerArm": (13, 15),
    "RightUpperArm": (12, 14), "RightLowerArm": (14, 16),
    "LeftUpperLeg": (23, 25), "LeftLowerLeg": (25, 27),
    "RightUpperLeg": (24, 26), "RightLowerLeg": (26, 28),
    "LeftFoot": (27, 31), "LeftToe": (29, 31),
    "RightFoot": (28, 32), "RightToe": (30, 32),
}
VIS_HOLD = 0.25


def _joint_ok(world, name):
    try:
        return all(float(world[i].get("visibility", 0)) >= VIS_HOLD
                   for i in JOINT_VIS[name])
    except (IndexError, TypeError, ValueError, AttributeError):
        return False


def wrist_landmarks(hands_entries, world33: list | None = None,
                    slots: dict | None = None):
    """FK 손목 방향용 {Left:[21점]|None, Right:...}. world 우선."""
    out = {"Left": None, "Right": None}
    for key, h in enumerate(hands_entries or []):
        if slots is not None:
            side = next((s for s, k in slots.items() if k == key), None)
            if side is None:
                continue
        else:
            side = resolve_hand_side(h, world33 or [])
            if side is None:
                side = str(h.get("side", "")).capitalize()
                if side not in ("Left", "Right"):
                    side = "Left" if out["Left"] is None else "Right"
        lms = h.get("world") or h.get("landmarks") or []
        if len(lms) >= 21 and out[side] is None:
            out[side] = lms
    return out


def _fk_max_step() -> float:
    """프레임당 최대 회전 변화량(rad). 노이즈 스파이크를 자르고 정상 춤 동작은 통과."""
    try:
        return max(0.05, float(os.getenv("FK_MAX_STEP", "0.45")))
    except ValueError:
        return 0.45


def _slew(prev_v, cur_v, step: float) -> list:
    """wrap-aware slew 제한. ±π 경계 crossing도 연속으로 처리."""
    out = []
    for p, c in zip(prev_v, cur_v):
        d = (c - p + math.pi) % (2 * math.pi) - math.pi
        d = max(-step, min(step, d))
        out.append(round(p + d, 3))
    return out


def attach_fk_joints(payload: dict) -> dict:
    """full 페이로드 각 프레임에 fkJoints(FK 19관절) 주입. 저vis는 직전값 유지."""
    from app.services.fk import solve_fk
    step = _fk_max_step()
    prev: dict = {}
    for f in payload.get("frames", []):
        world = f.get("poseWorldLandmarks") or []
        fk = solve_fk(world, wrist_landmarks(f.get("hands") or [], world,
                                              f.get("_hand_slots"))) or {}
        joints = fk  # solve_fk는 _wrist 채널 포함
        held = {}
        for name in RIG_JOINTS:
            if name in joints and _joint_ok(world, name):
                cur = [round(float(c), 3) for c in joints[name]]
                if name in prev:
                    cur = _slew(prev[name], cur, step)
                prev[name] = cur
                held[name] = cur
            elif name in prev:
                held[name] = prev[name]
            else:
                held[name] = [0.0, 0.0, 0.0]
        f["fkJoints"] = held
        # 손목: FK 손목을 handJoints에 반영 (손 검출 없어도 포즈 기반으로 움직임)
        hj = f.get("handJoints") or {"Left": None, "Right": None}
        for side in ("Left", "Right"):
            w = (fk.get("_wrist") or {}).get(side)
            if w is None:
                continue
            if hj.get(side) is None:
                try:
                    pv = float(world[15 if side == "Left" else 16].get("visibility", 0))
                except (IndexError, TypeError, ValueError, AttributeError):
                    pv = 0.0
                if pv < VIS_HOLD and side in prev.get("_wrist", {}):
                    w = prev["_wrist"][side]
                hj[side] = {"wrist": w, "fingers": {}, "vis": round(pv, 3)}
            else:
                hj[side]["wrist"] = w
        f["handJoints"] = hj
        prev["_wrist"] = {s: (hj.get(s) or {}).get("wrist") for s in ("Left", "Right")}
    return payload


def attach_neck_head(payload: dict) -> dict:
    """full 페이로드 각 프레임에 neckJoints {neck, head=neck×0.5} 주입."""
    for f in payload.get("frames", []):
        world = f.get("poseWorldLandmarks") or []
        if world and len(world) >= 29:
            try:
                neck = list(_bone_euler(world, L_SH, NOSE, ref=UP))
            except (IndexError, TypeError, ValueError):
                neck = [0.0, 0.0, 0.0]
        else:
            neck = [0.0, 0.0, 0.0]
        f["neckJoints"] = {"neck": neck,
                           "head": [round(c * 0.5, 3) for c in neck]}
    return payload


def _toe_deadzone() -> float:
    """라디안 deadzone. 이하 미세 각도는 노이즈로 보고 0 처리."""
    try:
        return max(0.0, float(os.getenv("TOE_DEADZONE", "0.08")))
    except ValueError:
        return 0.08


def foot_to_joints(world_lm: list) -> dict:
    """발목→발끝·뒤꿈치→발끝 방향 → 발/발가락 오일러. 실패시 빈 dict."""
    out: dict = {}
    try:
        if not world_lm or len(world_lm) < 33:
            return out
        dz = _toe_deadzone()

        def _flat(e):
            return [0.0 if abs(c) < dz else c for c in e]

        out["LeftFoot"] = _flat(_bone_euler(world_lm, L_ANK, L_FOOT, ref=FWD))
        out["RightFoot"] = _flat(_bone_euler(world_lm, R_ANK, R_FOOT, ref=FWD))
        out["LeftToe"] = _flat(_bone_euler(world_lm, L_HEEL, L_FOOT, ref=FWD))
        out["RightToe"] = _flat(_bone_euler(world_lm, R_HEEL, R_FOOT, ref=FWD))
    except (IndexError, TypeError, ValueError):
        pass
    return out
