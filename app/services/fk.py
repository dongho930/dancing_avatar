"""모델상대 FK 리타겟 (Xbot 바인드 기준).

Kalidokit 절대값 방식이 Xbot 바인드(T자세, identity 회전)와 어긋나
팔이 위로 뒤집히므로, 바인드 상대 회전으로 직접 계산한다:
  Qlocal = P_new^-1 * align(d_rest_world, d_target) * P_new * Q0
관절 방향이 영상을 그대로 재현하므로 휴지자세 가정이 필요 없다.

좌표: 아바타 공간 A(p) = (x, -y, -z) (three.js Y-up, 카메라는 +z).
좌우: 해부학적 매핑 고정 (홀수 인덱스=왼쪽: 11,13,15,23,25,27).
"""
import math

# Xbot.glb 바인드에서 추출 (추출 스크립트: Temp bake_bind.py). 전 본 Q0=identity.
BIND = {
    'Hips': {'parent': None, 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': None},
    'Spine': {'parent': 'Hips', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, 0.995111, -0.098767]},
    'Spine1': {'parent': 'Spine', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, 0.988802, -0.149234]},
    'Spine2': {'parent': 'Spine1', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': None},
    'Neck': {'parent': 'Spine2', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, 0.984998, 0.172567]},
    'Head': {'parent': 'Neck', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, 0.989636, 0.143596]},
    'LeftShoulder': {'parent': 'Spine2', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [1.0, 0.0, 0.0]},
    'RightShoulder': {'parent': 'Spine2', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [-1.0, 0.0, 0.0]},
    'LeftArm': {'parent': 'LeftShoulder', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [1.0, 0.0, 0.0]},
    'LeftForeArm': {'parent': 'LeftArm', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [1.0, 0.0, 0.0]},
    'LeftHand': {'parent': 'LeftForeArm', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [1.0, 0.0, 0.0]},
    'RightArm': {'parent': 'RightShoulder', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [-1.0, 0.0, 0.0]},
    'RightForeArm': {'parent': 'RightArm', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [-1.0, 0.0, 0.0]},
    'RightHand': {'parent': 'RightForeArm', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [-1.0, 0.0, 0.0]},
    'LeftUpLeg': {'parent': 'Hips', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, -0.999979, 0.006415]},
    'LeftLeg': {'parent': 'LeftUpLeg', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, -0.997755, -0.066974]},
    'LeftFoot': {'parent': 'LeftLeg', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, -0.63174, 0.77518]},
    'LeftToeBase': {'parent': 'LeftFoot', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, 0.0, 1.0]},
    'RightUpLeg': {'parent': 'Hips', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, -0.999979, 0.006449]},
    'RightLeg': {'parent': 'RightUpLeg', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, -0.997752, -0.06701]},
    'RightFoot': {'parent': 'RightLeg', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, -0.63174, 0.77518]},
    'RightToeBase': {'parent': 'RightFoot', 'q0': [0.0, 0.0, 0.0, 1.0], 'dlocal': [0.0, 0.0, 1.0]},
}

# rig 관절명 → 본명
BONE_OF = {
    "Spine": "Spine", "Chest": "Spine1", "Neck": "Neck", "Head": "Head", "Hips": "Hips",
    "LeftShoulder": "LeftShoulder", "RightShoulder": "RightShoulder",
    "LeftUpperArm": "LeftArm", "LeftLowerArm": "LeftForeArm",
    "RightUpperArm": "RightArm", "RightLowerArm": "RightForeArm",
    "LeftUpperLeg": "LeftUpLeg", "LeftLowerLeg": "LeftLeg",
    "RightUpperLeg": "RightUpLeg", "RightLowerLeg": "RightLeg",
    "LeftFoot": "LeftFoot", "RightFoot": "RightFoot",
    "LeftToe": "LeftToeBase", "RightToe": "RightToeBase",
}

# 관절 타깃 방향 (world 인덱스 쌍). L=홀수=해부학적 왼쪽.
# 어깨(쇄골): 어깨중점→각 어깨. 으쓱/앞말림 같은 견갑 움직임을 포착.
TARGETS = {
    "Spine": ("torso",), "Chest": ("torso",), "Neck": ("neck",), "Head": ("neck",),
    "LeftShoulder": ("shoM", 11), "RightShoulder": ("shoM", 12),
    "LeftUpperArm": (11, 13), "LeftLowerArm": (13, 15),
    "RightUpperArm": (12, 14), "RightLowerArm": (14, 16),
    "LeftUpperLeg": (23, 25), "LeftLowerLeg": (25, 27),
    "RightUpperLeg": (24, 26), "RightLowerLeg": (26, 28),
    "LeftFoot": (27, 31), "RightFoot": (28, 32),
    "LeftToe": (29, 31), "RightToe": (30, 32),
}

ORDER = ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head",
         "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
         "RightShoulder", "RightArm", "RightForeArm", "RightHand",
         "LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase",
         "RightUpLeg", "RightLeg", "RightFoot", "RightToeBase"]


def _qmul(a, b):
    x1, y1, z1, w1 = a
    x2, y2, z2, w2 = b
    return [w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2]


def _qconj(q):
    return [-q[0], -q[1], -q[2], q[3]]


def _qapply(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    t = [w * vx + y * vz - z * vy, w * vy + z * vx - x * vz,
         w * vz + x * vy - y * vx, -x * vx - y * vy - z * vz]
    r = _qmul(t, _qconj(q))
    return r[:3]


def _norm(v):
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return [v[0] / n, v[1] / n, v[2] / n] if n > 1e-9 else [0.0, 1.0, 0.0]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _align(a, b):
    """단위벡터 a→b 최단호 quaternion."""
    d = max(-1.0, min(1.0, _dot(a, b)))
    if d > 0.999999:
        return [0.0, 0.0, 0.0, 1.0]
    if d < -0.999999:
        ax = _cross(a, [1.0, 0.0, 0.0])
        if _dot(ax, ax) < 1e-9:
            ax = _cross(a, [0.0, 0.0, 1.0])
        ax = _norm(ax)
        return [ax[0], ax[1], ax[2], 0.0]
    ax = _cross(a, b)
    s = math.sqrt((1 + d) * 2)
    return [ax[0] / s, ax[1] / s, ax[2] / s, s * 0.5]


def _mat_to_quat(m):
    """3x3 회전행렬(열=X,Y,Z) → quaternion."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        s = 0.5 / math.sqrt(t + 1.0)
        return [(m[2][1] - m[1][2]) * s, (m[0][2] - m[2][0]) * s,
                (m[1][0] - m[0][1]) * s, 0.25 / s]
    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = 2.0 * math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2])
        return [0.25 * s, (m[0][1] + m[1][0]) / s,
                (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s]
    if m[1][1] > m[2][2]:
        s = 2.0 * math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2])
        return [(m[0][1] + m[1][0]) / s, 0.25 * s,
                (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s]
    s = 2.0 * math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1])
    return [(m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s,
            0.25 * s, (m[1][0] - m[0][1]) / s]


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
    return [round(ex, 3), round(ey, 3), round(ez, 3)]


def _A(p):
    """MediaPipe → 아바타 공간 (x, -y, -z)."""
    return [float(p["x"]), -float(p["y"]), -float(p["z"])]


def solve_fk(world33: list, wrists: dict | None = None) -> dict | None:
    """한 프레임 world 33관절 → 19관절 오일러(XYZ, 라디안). 불가시 None.

    wrists: {"Left": [21점]|None, "Right": ...} — 손목 방향용. 없으면 포즈 기반 폴백.
    """
    if not world33 or len(world33) < 33:
        return None
    try:
        Pw = {}
        Ql = {}
        joints = {}

        def Pav(bone):
            par = BIND[bone]["parent"]
            return Pw[par] if par else [0.0, 0.0, 0.0, 1.0]

        def displace(bone, d_target):
            """d_target(world 방향)으로 본 회전. Qlocal 반환 + Pw 갱신."""
            P = Pav(bone)
            Q0 = BIND[bone]["q0"]
            d_cur = _qapply(_qmul(P, Q0), BIND[bone]["dlocal"])
            al = _align(_norm(d_cur), _norm(d_target))
            Ql_new = _qmul(_qmul(_qconj(P), al), _qmul(P, Q0))
            Ql_new = _clamp_bend(bone, Ql_new)
            Pw[bone] = _qmul(P, Ql_new)
            Ql[bone] = Ql_new
            return _quat_to_euler_xyz(Ql_new)

        def pts(*idx):
            return [_A(world33[i]) for i in idx]

        def seg(a, b):
            pa, pb = _A(world33[a]), _A(world33[b])
            return [pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]]

        def mid(*idx):
            ps = pts(*idx)
            n = len(ps)
            return [sum(p[k] for p in ps) / n for k in range(3)]

        def diff(a, b):
            return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]

        # Hips: 골반 기저 → 목표 기저
        hipL = _A(world33[23])
        hipR = _A(world33[24])
        hipM = [(hipL[0] + hipR[0]) / 2, (hipL[1] + hipR[1]) / 2, (hipL[2] + hipR[2]) / 2]
        shoM = mid(11, 12)
        Xt = _norm(diff(hipL, hipR))
        Yt = _norm(diff(shoM, hipM))
        Zt = _norm(_cross(Xt, Yt))
        Yt = _cross(Zt, Xt)
        qT = _mat_to_quat([[Xt[0], Yt[0], Zt[0]],
                           [Xt[1], Yt[1], Zt[1]],
                           [Xt[2], Yt[2], Zt[2]]])
        qHips = _qmul(qT, BIND["Hips"]["q0"])  # q0=identity이나 일반식 유지
        Pw["Hips"] = qHips
        Ql["Hips"] = qHips
        joints["Hips"] = _quat_to_euler_xyz(qHips)

        torso = _norm(diff(shoM, hipM))
        joints["Spine"] = displace("Spine", torso)
        joints["Chest"] = displace("Spine1", torso)
        # Spine2는 정적 (어깨 부모 프레임 유지)
        Pw["Spine2"] = _qmul(Pav("Spine2"), BIND["Spine2"]["q0"])
        Ql["Spine2"] = BIND["Spine2"]["q0"]
        # 어깨(쇄골): 어깨중점→각 어깨 방향. 으쓱/앞말림 포착.
        # shoM이 부모(Spine2) 위치 근사라 displace 체인과 일치한다.
        joints["LeftShoulder"] = displace("LeftShoulder", diff(shoM, _A(world33[11])))
        joints["RightShoulder"] = displace("RightShoulder", diff(shoM, _A(world33[12])))
        nose = _A(world33[0])
        neck_dir = _norm(diff(nose, shoM))
        joints["Neck"] = displace("Neck", neck_dir)
        joints["Head"] = displace("Head", neck_dir)

        for side, a, e, w in (("Left", 11, 13, 15), ("Right", 12, 14, 16)):
            joints[f"{side}UpperArm"] = displace(f"{side}Arm", seg(a, e))
            joints[f"{side}LowerArm"] = displace(f"{side}ForeArm", seg(e, w))
            wdir = _wrist_dir(world33, wrists, side, w)
            joints[f"{side}Hand"] = displace(f"{side}Hand", wdir)
        for side, h, k, n, f, t, he in (
                ("Left", 23, 25, 27, 31, 31, 29), ("Right", 24, 26, 28, 32, 32, 30)):
            joints[f"{side}UpperLeg"] = displace(f"{side}UpLeg", seg(h, k))
            joints[f"{side}LowerLeg"] = displace(f"{side}Leg", seg(k, n))
            joints[f"{side}Foot"] = displace(f"{side}Foot", seg(n, f))
            joints[f"{side}Toe"] = displace(f"{side}ToeBase", seg(he, t))

        # rig명 매핑 (LeftHand→손목은 handJoints 체계와 별개로 joints에 포함하지 않음)
        out = {k: joints[k] for k in
               ("Spine", "Chest", "Neck", "Head", "Hips",
                "LeftShoulder", "RightShoulder",
                "LeftUpperArm", "LeftLowerArm", "RightUpperArm", "RightLowerArm",
                "LeftUpperLeg", "LeftLowerLeg", "RightUpperLeg", "RightLowerLeg",
                "LeftFoot", "RightFoot", "LeftToe", "RightToe")}
        out["_wrist"] = {"Left": joints["LeftHand"], "Right": joints["RightHand"]}
        return out
    except (IndexError, TypeError, ValueError, KeyError, ZeroDivisionError):
        return None


# 본별 허용 최대 굽힘각(rad). 바인드 기준 상대 회전량 기준이라 방향 보존.
# Hips는 절대 방위라 제외 (턴 제한 금지). 오일러 norm이 아닌 쿼터니언 각도 기준.
_BEND_LIMITS = {
    "Spine": 0.9, "Spine1": 0.9, "Neck": 0.8, "Head": 0.5,
    "LeftShoulder": 0.6, "RightShoulder": 0.6,
    "LeftArm": 3.0, "RightArm": 3.0,
    "LeftForeArm": 2.5, "RightForeArm": 2.5,
    "LeftHand": 1.2, "RightHand": 1.2,
    "LeftUpLeg": 3.0, "RightUpLeg": 3.0,
    "LeftLeg": 2.5, "RightLeg": 2.5,
    "LeftFoot": 1.2, "RightFoot": 1.2,
    "LeftToeBase": 1.0, "RightToeBase": 1.0,
}


def _limit_scale() -> float:
    """전체 한계 배율(env). 춤 종류에 따라 완화/강화."""
    import os
    try:
        return max(0.2, float(os.getenv("JOINT_LIMIT_SCALE", "1.0")))
    except ValueError:
        return 1.0


def _clamp_bend(bone: str, q: list) -> list:
    """상대 회전각만 cap까지 slerp. 회전축 보존이라 방향이 틀어지지 않음."""
    limit = _BEND_LIMITS.get(bone)
    if limit is None:
        return q
    cap = limit * _limit_scale()
    w = max(-1.0, min(1.0, q[3]))
    ang = 2 * math.acos(abs(w))
    if ang > cap > 0 and ang > 1e-9:
        s = math.sin(cap / 2) / math.sin(ang / 2)
        return [q[0] * s, q[1] * s, q[2] * s, math.cos(cap / 2) if w >= 0 else -math.cos(cap / 2)]
    return q


def _wrist_dir(world33, wrists, side, w_idx):
    """손목 방향: 손 랜드마크 우선, 없으면 포즈 기반 폴백."""
    if wrists and wrists.get(side):
        lm = wrists[side]
        try:
            a = _A(lm[0])
            b = _A(lm[9])
            return [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
        except (IndexError, TypeError, KeyError):
            pass
    # 폴백: 손목→(새끼MCP+검지MCP 중점). L=15/17/19, R=16/18/20
    if side == "Left":
        a, b, c = 15, 17, 19
    else:
        a, b, c = 16, 18, 20
    pa = _A(world33[a])
    pb = _A(world33[b])
    pc = _A(world33[c])
    m = [(pb[0] + pc[0]) / 2, (pb[1] + pc[1]) / 2, (pb[2] + pc[2]) / 2]
    return [m[0] - pa[0], m[1] - pa[1], m[2] - pa[2]]
