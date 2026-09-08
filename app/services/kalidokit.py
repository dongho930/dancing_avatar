"""Kalidokit PoseSolver의 Python 포팅 (MIT, yeemachine/kalidokit).

원본: src/PoseSolver/{index,calcArms,calcHips,calcLegs}.ts + src/utils/{vector,helpers}.ts
동작을 1:1로 옮긴 것으로, Mixamo용 스케일·오프셋·가드까지 동일하다.
(기존 kinematics 최단호 방식과 달리 평면별 2D각 + 부위별 rig 보정.)

입력: 33개 dictランド마크 [{x,y,z,visibility}] (world + image).
출력: Mixamo 본 이름 → [x,y,z] 라디안. Neck은 Kalidokit에 없어 별도 계산(호출부).
"""
import math
import os

PI = math.pi
TWO_PI = math.pi * 2


def _finger_deadzone() -> float:
    """정규화 각도 deadzone. 이하 미세 굽힘은 노이즈로 보고 0 처리."""
    try:
        return max(0.0, float(os.getenv("FINGER_DEADZONE", "0.045")))
    except ValueError:
        return 0.045


def _dz(v: float, dz: float) -> float:
    return 0.0 if abs(v) < dz else v


def _num(p, k, default=0.0):
    try:
        v = p.get(k, default) if isinstance(p, dict) else p[{"x": 0, "y": 1, "z": 2}[k]]
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError, IndexError, KeyError):
        return default


def _v(p):
    return [_num(p, "x"), _num(p, "y"), _num(p, "z")]


def _vis(p):
    try:
        f = float(p.get("visibility", 0.0)) if isinstance(p, dict) else 1.0
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _clamp(val, lo, hi):
    return max(min(val, hi), lo)


def _remap(val, lo, hi):
    return (_clamp(val, lo, hi) - lo) / (hi - lo)


def _normalize_angle(rad):
    # JS와 동일하게 부호유지 나머지(fmod) 사용. Python %는 내림이라 결과가 달라짐.
    a = math.fmod(rad, TWO_PI)
    if a > PI:
        a -= TWO_PI
    elif a < -PI:
        a += TWO_PI
    return a / PI


def _normalize_radians(rad):
    if rad >= PI / 2:
        rad -= TWO_PI
    if rad <= -PI / 2:
        rad += TWO_PI
        rad = PI - rad
    return rad / PI


def _find2d(cx, cy, ex, ey):
    return math.atan2(ey - cy, ex - cx)


def _find_rotation(a, b):
    """두 벡터 간 3평면 2D각 (정규화). Kalidokit Vector.findRotation."""
    return [_normalize_radians(_find2d(a[2], a[0], b[2], b[0])),
            _normalize_radians(_find2d(a[2], a[1], b[2], b[1])),
            _normalize_radians(_find2d(a[0], a[1], b[0], b[1]))]


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _len(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _roll_pitch_yaw(a, b):
    return [_normalize_angle(_find2d(a[2], a[1], b[2], b[1])),
            _normalize_angle(_find2d(a[2], a[0], b[2], b[0])),
            _normalize_angle(_find2d(a[0], a[1], b[0], b[1]))]


def _angle_between_3d(a, b, c):
    v1 = _sub(a, b)
    v2 = _sub(c, b)
    n1, n2 = _len(v1), _len(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    d = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]) / (n1 * n2)))
    return _normalize_radians(math.acos(d))


def _lerp_vec(a, b, t):
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]


def _spherical(v, ax):
    n = _len(v)
    if n < 1e-9:
        return {"theta": 0.0, "phi": 0.0}
    theta = math.atan2(v[ax[1]], v[ax[0]])
    phi = math.acos(max(-1.0, min(1.0, v[ax[2]] / n)))
    return {"theta": theta, "phi": phi}


def _get_spherical(a, b, ax=("x", "y", "z")):
    m = {"x": 0, "y": 1, "z": 2}
    s = _spherical(_sub(b, a), (m[ax[0]], m[ax[1]], m[ax[2]]))
    return {"theta": _normalize_angle(-s["theta"]),
            "phi": _normalize_angle(PI / 2 - s["phi"])}


def _get_relative_spherical(a, b, c, ax=("x", "y", "z")):
    m = {"x": 0, "y": 1, "z": 2}
    idx = (m[ax[0]], m[ax[1]], m[ax[2]])
    s1 = _spherical(_sub(b, a), idx)
    s2 = _spherical(_sub(c, b), idx)
    return {"theta": _normalize_angle(s1["theta"] - s2["theta"]),
            "phi": _normalize_angle(s1["phi"] - s2["phi"])}


_REST_Z_LEFT_UPPER_ARM = 1.25
_REST_Z_RIGHT_UPPER_ARM = -1.25


def _rig_arm(upper, lower, hand, side):
    invert = 1 if side == "r" else -1
    upper[2] *= -2.3 * invert
    upper[1] *= PI * invert
    upper[1] -= lower[0]
    upper[1] -= -invert * max(lower[2], 0)
    upper[0] -= 0.3 * invert
    lower[2] *= -2.14 * invert
    lower[1] *= 2.14 * invert
    lower[0] *= 2.14 * invert
    upper[0] = _clamp(upper[0], -0.5, PI)
    lower[0] = _clamp(lower[0], -0.3, 0.3)
    hand = [hand[0], _clamp(hand[2] * 2, -0.6, 0.6), hand[2] * -2.3 * invert]
    return upper, lower, hand


def _calc_arms(lm):
    upper_r = _find_rotation(lm[11], lm[13])
    upper_l = _find_rotation(lm[12], lm[14])
    upper_r[1] = _angle_between_3d(lm[12], lm[11], lm[13])
    upper_l[1] = _angle_between_3d(lm[11], lm[12], lm[14])
    lower_r = _find_rotation(lm[13], lm[15])
    lower_l = _find_rotation(lm[14], lm[16])
    lower_r[1] = _angle_between_3d(lm[11], lm[13], lm[15])
    lower_l[1] = _angle_between_3d(lm[12], lm[14], lm[16])
    lower_r[2] = _clamp(lower_r[2], -2.14, 0)
    lower_l[2] = _clamp(lower_l[2], -2.14, 0)
    hand_r = _find_rotation(lm[15], _lerp_vec(lm[17], lm[19], 0.5))
    hand_l = _find_rotation(lm[16], _lerp_vec(lm[18], lm[20], 0.5))
    upper_r, lower_r, hand_r = _rig_arm(upper_r, lower_r, hand_r, "r")
    upper_l, lower_l, hand_l = _rig_arm(upper_l, lower_l, hand_l, "l")
    return {"upper_r": upper_r, "upper_l": upper_l,
            "lower_r": lower_r, "lower_l": lower_l,
            "hand_r": hand_r, "hand_l": hand_l}


def _rig_hips(hips_rot, spine):
    hips_rot = [hips_rot[0] * PI, hips_rot[1] * PI, hips_rot[2] * PI]
    spine = [spine[0] * PI, spine[1] * PI, spine[2] * PI]
    return hips_rot, spine


def _calc_hips(lm3d, lm2d):
    hip_c = _lerp_vec(lm2d[23], lm2d[24], 1)  # lerp 1 → 우측값 (원본 그대로)
    sho_c = _lerp_vec(lm2d[11], lm2d[12], 1)
    dx, dy = sho_c[0] - hip_c[0], sho_c[1] - hip_c[1]
    spine_len = math.sqrt(dx * dx + dy * dy)
    pos = [_clamp(hip_c[0] - 0.4, -1, 1), 0,
           _clamp(spine_len - 1, -2, 0)]
    wz = pos[2] * (pos[2] * -2) ** 2
    wpos = [pos[0] * wz, 0, wz]
    rot = _roll_pitch_yaw(lm3d[23], lm3d[24])
    if rot[1] > 0.5:
        rot[1] -= 2
    rot[1] += 0.5
    if rot[2] > 0:
        rot[2] = 1 - rot[2]
    if rot[2] < 0:
        rot[2] = -1 - rot[2]
    rot[2] *= 1 - _remap(abs(rot[1]), 0.2, 0.4)
    rot[0] = 0
    spine = _roll_pitch_yaw(lm3d[11], lm3d[12])
    if spine[1] > 0.5:
        spine[1] -= 2
    spine[1] += 0.5
    if spine[2] > 0:
        spine[2] = 1 - spine[2]
    if spine[2] < 0:
        spine[2] = -1 - spine[2]
    spine[2] *= 1 - _remap(abs(spine[1]), 0.2, 0.4)
    spine[0] = 0
    rot, spine = _rig_hips(rot, spine)
    return {"pos": pos, "wpos": wpos, "rot": rot, "spine": spine}


_LEG_AX = ("y", "z", "x")


def _rig_leg(upper, lower, side):
    invert = 1 if side == "r" else -1
    return ([_clamp(upper[0], 0, 0.5) * PI,
             _clamp(upper[1], -0.25, 0.25) * PI,
             _clamp(upper[2], -0.5, 0.5) * PI + invert * 0.1],
            [lower[0] * PI, lower[1] * PI, lower[2] * PI])


def _calc_legs(lm):
    ru = _get_spherical(lm[23], lm[25], _LEG_AX)
    lu = _get_spherical(lm[24], lm[26], _LEG_AX)
    rr = _get_relative_spherical(lm[23], lm[25], lm[27], _LEG_AX)
    lr = _get_relative_spherical(lm[24], lm[26], lm[28], _LEG_AX)
    hip_rot = _find_rotation(lm[23], lm[24])
    upper_r = [ru["theta"], rr["phi"], ru["phi"] - hip_rot[2]]
    upper_l = [lu["theta"], lr["phi"], lu["phi"] - hip_rot[2]]
    lower_r = [-abs(rr["theta"]), 0, 0]
    lower_l = [-abs(lr["theta"]), 0, 0]
    upper_r, lower_r = _rig_leg(upper_r, lower_r, "r")
    upper_l, lower_l = _rig_leg(upper_l, lower_l, "l")
    return {"upper_r": upper_r, "upper_l": upper_l,
            "lower_r": lower_r, "lower_l": lower_l}


def solve(world33: list, img33: list) -> dict | None:
    """Kalidokit PoseSolver.solve 포팅 (mediapipe, enableLegs=True).

    Mixamo 본 이름 → [x,y,z] 라디안. 검출 불량이면 RestingDefault로 폴백.
    Neck/Chest는 Kalidokit 출력에 없어 호출부가 별도 계산.
    """
    if not world33 or len(world33) < 29 or not img33 or len(img33) < 29:
        return None
    try:
        lm3d = [_v(p) for p in world33]
        lm2d = [_v(p) for p in img33]
        arms = _calc_arms(lm3d)
        hips = _calc_hips(lm3d, lm2d)
        legs = _calc_legs(lm3d)

        # 오프스크린 판정: 원본은 world-y>0.1도 보지만, 춤에선 만세가 정상
        # 동작이라 visibility + 2D 하단 이탈만 본다 (좌표계 부호와 무관하게 안전).
        def _off3(i, thr):
            return _vis(world33[i]) < thr

        rh_off = _off3(15, 0.23) or lm2d[15][1] > 0.995
        lh_off = _off3(16, 0.23) or lm2d[16][1] > 0.995
        lf_off = _off3(23, 0.63) or hips["pos"][2] > -0.4
        rf_off = _off3(24, 0.63) or hips["pos"][2] > -0.4

        def _zero(v):
            return [0.0, 0.0, 0.0]

        u_r = list(arms["upper_r"])
        u_l = list(arms["upper_l"])
        if rh_off:
            u_r = _zero(u_r)
            u_r[2] = _REST_Z_RIGHT_UPPER_ARM
        if lh_off:
            u_l = _zero(u_l)
            u_l[2] = _REST_Z_LEFT_UPPER_ARM
        lo_r = arms["lower_r"] if not rh_off else _zero(arms["lower_r"])
        lo_l = arms["lower_l"] if not lh_off else _zero(arms["lower_l"])
        h_r = arms["hand_r"] if not rh_off else _zero(arms["hand_r"])
        h_l = arms["hand_l"] if not lh_off else _zero(arms["hand_l"])
        # 다리는 좌우 교차 매핑 (원본 그대로)
        ul_r = legs["upper_r"] if not lf_off else _zero(legs["upper_r"])
        ul_l = legs["upper_l"] if not rf_off else _zero(legs["upper_l"])
        ll_r = legs["lower_r"] if not lf_off else _zero(legs["lower_r"])
        ll_l = legs["lower_l"] if not rf_off else _zero(legs["lower_l"])

        r = lambda v: [round(float(x), 3) for x in v]
        return {
            "RightUpperArm": r(u_r), "RightLowerArm": r(lo_r),
            "LeftUpperArm": r(u_l), "LeftLowerArm": r(lo_l),
            "RightHand": r(h_r), "LeftHand": r(h_l),
            "RightUpperLeg": r(ul_r), "RightLowerLeg": r(ll_r),
            "LeftUpperLeg": r(ul_l), "LeftLowerLeg": r(ll_l),
            "Hips": {"position": r(hips["pos"]), "worldPosition": r(hips["wpos"]),
                     "rotation": r(hips["rot"])},
            "Spine": r(hips["spine"]),
        }
    except (IndexError, TypeError, ValueError):
        return None


_HAND_DIGITS = ("Ring", "Index", "Little", "Middle", "Thumb")
_HAND_SEGS = ("Proximal", "Intermediate", "Distal")
# Kalidokit 손가락명 → 우리명 (Little=Pinky)
_HAND_NAME_MAP = {"Thumb": "Thumb", "Index": "Index", "Middle": "Middle",
                  "Ring": "Ring", "Little": "Pinky"}


def solve_hand(lm21: list, side: str = "Right") -> dict | None:
    """Kalidokit HandSolver.solve 포팅. 21점 → {wrist, fingers{5×3}}."""
    if not lm21 or len(lm21) < 21:
        return None
    try:
        side = "Left" if str(side) == "Left" else "Right"
        lm = [_v(p) for p in lm21]
        p0 = lm[0]
        pA = lm[17 if side == "Right" else 5]
        pB = lm[5 if side == "Right" else 17]
        hr = _roll_pitch_yaw_vec(p0, pA, pB)
        hx, hy, hz = hr
        hy = hz
        hy -= 0.4
        wrist = [hx, hy, hz]

        finger_pts = {
            "Ring": [(0, 13, 14), (13, 14, 15), (14, 15, 16)],
            "Index": [(0, 5, 6), (5, 6, 7), (6, 7, 8)],
            "Middle": [(0, 9, 10), (9, 10, 11), (10, 11, 12)],
            "Thumb": [(0, 1, 2), (1, 2, 3), (2, 3, 4)],
            "Little": [(0, 17, 18), (17, 18, 19), (18, 19, 20)],
        }
        raw = {"Wrist": wrist}
        for name in _HAND_DIGITS:
            for seg, (a, b, c) in zip(_HAND_SEGS, finger_pts[name]):
                raw[name + seg] = [0.0, 0.0, _angle_between_3d(lm[a], lm[b], lm[c])]
        rigged = _rig_fingers(raw, side)
        fingers = {}
        for name in ("Thumb", "Index", "Middle", "Ring", "Little"):
            fingers[_HAND_NAME_MAP[name]] = [
                [round(float(rigged[name + s][0]), 3),
                 round(float(rigged[name + s][1]), 3),
                 round(float(rigged[name + s][2]), 3)] for s in _HAND_SEGS]
        w = rigged["Wrist"]
        return {"wrist": [round(float(w[0]), 3), round(float(w[1]), 3), round(float(w[2]), 3)],
                "fingers": fingers}
    except (IndexError, TypeError, ValueError):
        return None


def _nan0(v: float) -> float:
    """JS `|| 0` 대응. NaN만 0으로 (0.0은 그대로)."""
    return 0.0 if isinstance(v, float) and math.isnan(v) else v


def _roll_pitch_yaw_vec(a, b, c):
    # 원본과 동일하게 0除算 NaN을 전파 후 || 0 처리. 임의 fallback 금지.
    ab = _sub(b, a)
    ac = _sub(c, a)
    n = _cross(ab, ac)
    nn = _len(n)
    if nn != 0:
        uz = [n[0] / nn, n[1] / nn, n[2] / nn]
    else:
        uz = [float("nan")] * 3
    na = _len(ab)
    if na != 0:
        ux = [ab[0] / na, ab[1] / na, ab[2] / na]
    else:
        ux = [float("nan")] * 3
    uy = _cross(uz, ux)
    z0 = uz[0]
    beta = _nan0(math.asin(max(-1.0, min(1.0, z0)))) if z0 == z0 else 0.0
    alpha = _nan0(math.atan2(-uz[1], uz[2]))
    gamma = _nan0(math.atan2(-uy[0], ux[0]))
    return [_normalize_angle(alpha), _normalize_angle(beta), _normalize_angle(gamma)]


def _rig_fingers(hand: dict, side: str) -> dict:
    invert = 1 if side == "Right" else -1
    dz = _finger_deadzone()
    w = hand["Wrist"]
    hand["Wrist"] = [_clamp(w[0] * 2 * invert, -0.3, 0.3),
                     _clamp(w[1] * 2.3,
                            -1.2 if side == "Right" else -0.6,
                            0.6 if side == "Right" else 1.6),
                     w[2] * -2.3 * invert]
    for e in _HAND_DIGITS:
        for j in _HAND_SEGS:
            t = hand[e + j]
            t = [t[0], t[1], _dz(t[2], dz)]
            if e == "Thumb":
                damp = {"x": 2.2 if j == "Proximal" else 0,
                        "y": 2.2 if j == "Proximal" else (0.7 if j == "Intermediate" else 1),
                        "z": 0.5}
                start = {"x": 1.2 if j == "Proximal" else -0.2,
                         "y": (1.1 if j == "Proximal" else 0.1) * invert,
                         "z": 0.2 * invert}
                if j == "Proximal":
                    nz = _clamp(start["z"] + t[2] * -PI * damp["z"] * invert,
                                -0.6 if side == "Right" else -0.3,
                                0.3 if side == "Right" else 0.6)
                    nx = _clamp(start["x"] + t[2] * -PI * damp["x"], -0.6, 0.3)
                    ny = _clamp(start["y"] + t[2] * -PI * damp["y"] * invert,
                                -1 if side == "Right" else -0.3,
                                0.3 if side == "Right" else 1)
                else:
                    nz = _clamp(start["z"] + t[2] * -PI * damp["z"] * invert, -2, 2)
                    nx = _clamp(start["x"] + t[2] * -PI * damp["x"], -2, 2)
                    ny = _clamp(start["y"] + t[2] * -PI * damp["y"] * invert, -2, 2)
                hand[e + j] = [nx, ny, nz]
            else:
                hand[e + j] = [t[0], t[1],
                               _clamp(t[2] * -PI * invert,
                                      -PI if side == "Right" else 0,
                                      0 if side == "Right" else PI)]
    return hand


def _vis(p):
    try:
        f = float(p.get("visibility", 0.0)) if isinstance(p, dict) else 1.0
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0
