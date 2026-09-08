"""SMPL(HMR2) pose → rig fkJoints 변환 + 하이브리드 정제.

변환: 72D axis-angle → 19관절 오일러. SMPL pose는 부모-상대 회전이라
T-pose 기준 Xbot 바인드와 직접 호환 (둘 다 T-pose, identity 휴지).
루트만 카메라→아바타 고정회전 필요. 22·23번 관절은 미사용 확인됨 (무시).

정제: SMPL 팔다리+목머리+쇄골+손목을 FK에 이식. 몸통(Spine/Chest/Hips)·
발·손가락·루트·지면은 기존 스택 유지 (측정 비교로 결정).
SMPL_PKL 비어있으면 스킵.
"""
import math
import os

from app.services.kinematics import RIG_JOINTS, _fk_max_step, _qmul, _quat_to_euler_xyz

# SMPL 관절 인덱스 (HMR2 body_pose 순서와 동일)
PELVIS = 0
L_HIP, R_HIP = 1, 2
SPINE1 = 3
L_KNEE, R_KNEE = 4, 5
SPINE2 = 6
L_ANKLE, R_ANKLE = 7, 8
SPINE3 = 9
L_FOOT, R_FOOT = 10, 11
NECK = 12
L_COLLAR, R_COLLAR = 13, 14
HEAD = 15
L_SH, R_SH = 16, 17
L_ELB, R_ELB = 18, 19
L_WR, R_WR = 20, 21

# 카메라(y-down) → 아바타(y-up): X축 180도
ROOT_FLIP = [1.0, 0.0, 0.0, 0.0]


def _aa_to_quat(a) -> list:
    """axis-angle(3) → quaternion [x,y,z,w]."""
    try:
        ax, ay, az = float(a[0]), float(a[1]), float(a[2])
    except (IndexError, TypeError, ValueError):
        return [0.0, 0.0, 0.0, 1.0]
    th = math.sqrt(ax * ax + ay * ay + az * az)
    if th < 1e-9 or not math.isfinite(th):
        return [0.0, 0.0, 0.0, 1.0]
    s = math.sin(th / 2) / th
    return [ax * s, ay * s, az * s, math.cos(th / 2)]


def smpl_frame_to_fk(aa72) -> tuple:
    """72D 한 프레임 → (fkJoints 19관절 오일러, wrist {Left,Right} 오일러).

    실패시 (None, None).
    """
    try:
        if aa72 is None or len(aa72) < 72:
            return None, None
        q = [_aa_to_quat(aa72[i * 3:(i + 1) * 3]) for i in range(24)]

        def E(i):
            return list(_quat_to_euler_xyz(q[i]))

        def EC(i, j):
            return list(_quat_to_euler_xyz(_qmul(q[i], q[j])))

        root = _qmul(ROOT_FLIP, q[PELVIS])
        fk = {
            "Hips": list(_quat_to_euler_xyz(root)),
            "Spine": E(SPINE1),
            "Chest": EC(SPINE2, SPINE3),
            "Neck": E(NECK),
            "Head": E(HEAD),
            "LeftShoulder": E(L_COLLAR),
            "RightShoulder": E(R_COLLAR),
            "LeftUpperArm": E(L_SH),
            "LeftLowerArm": E(L_ELB),
            "RightUpperArm": E(R_SH),
            "RightLowerArm": E(R_ELB),
            "LeftUpperLeg": E(L_HIP),
            "LeftLowerLeg": E(L_KNEE),
            "RightUpperLeg": E(R_HIP),
            "RightLowerLeg": E(R_KNEE),
            "LeftFoot": E(L_ANKLE),
            "RightFoot": E(R_ANKLE),
            "LeftToe": E(L_FOOT),
            "RightToe": E(R_FOOT),
        }
        assert all(k in fk for k in RIG_JOINTS)
        wrist = {"Left": E(L_WR), "Right": E(R_WR)}
        return fk, wrist
    except (IndexError, TypeError, ValueError):
        return None, None


# FK에 이식하는 관절 (비교 측정으로 결정: SMPL 우위만).
# 몸통·발·손가락은 기존 스택 유지.
GRAFT_JOINTS = ("Neck", "Head",
                "LeftShoulder", "RightShoulder",
                "LeftUpperArm", "LeftLowerArm",
                "RightUpperArm", "RightLowerArm",
                "LeftUpperLeg", "LeftLowerLeg",
                "RightUpperLeg", "RightLowerLeg")


def _smpl_pkl_path() -> str:
    return os.getenv("SMPL_PKL", "").strip()


def _smpl_subject() -> str:
    return os.getenv("SMPL_SUBJ", "auto").strip() or "auto"


def _load_smpl_track(pkl_path: str, subject: str):
    """slim pkl → (N×72 리스트, N). subject auto=최장 트랙."""
    import joblib
    try:
        slim = joblib.load(pkl_path)
    except Exception:
        return None, 0
    try:
        items = list((slim or {}).items())
    except (AttributeError, TypeError, ValueError):
        return None, 0
    if not items:
        return None, 0
    if subject == "auto":
        key = max(items, key=lambda kv: len((kv[1] or {}).get("frame_ids", [])))[0]
    else:
        try:
            key = int(subject)
        except (TypeError, ValueError):
            key = subject
        if key not in dict(items):
            key = items[0][0]
    track = dict(items).get(key) or {}
    pose = track.get("pose")
    try:
        n = len(pose)
    except TypeError:
        return None, 0
    return pose, n


def attach_smpl_refine(payload: dict, pkl_path: str | None = None,
                       rows: list | None = None) -> dict:
    """SMPL 정제: 팔다리+목머리+쇄골+손목을 FK에 이식.

    rows 직접 전달(클라우드) 또는 pkl_path/SMPL_PKL 파일.
    둘 다 없으면 스킵. attach_fk_joints 다음, attach_leg_smooth 이전에 호출.
    프레임 매칭은 frame 번호 기준 (양쪽 동일 영상 전제). 못 찾으면 FK 유지.
    자체 slew 스무딩 (FK 상태와 독립).
    """
    from app.services.kinematics import _slew_quat
    frames = payload.get("frames", [])
    if not frames:
        return payload
    if rows is None:
        path = (pkl_path or _smpl_pkl_path()).strip() if pkl_path or _smpl_pkl_path() else ""
        if not path:
            return payload
        pose, n = _load_smpl_track(path, _smpl_subject())
        if pose is None or n <= 0:
            return payload
        try:
            rows = [list(r) for r in pose]
        except TypeError:
            return payload
    by_frame: dict = {}
    for i, r in enumerate(rows):
        by_frame[i] = r
    step = _fk_max_step()
    prev: dict = {}
    grafted = 0
    for f in frames:
        try:
            fi = int(f.get("frame", -1))
        except (TypeError, ValueError):
            continue
        row = by_frame.get(fi)
        if row is None:
            continue
        fk, wrist = smpl_frame_to_fk(row)
        if fk is None:
            continue
        held = f.get("fkJoints") or {}
        for name in GRAFT_JOINTS:
            if name not in fk:
                continue
            cur = [round(float(c), 3) for c in fk[name]]
            if name in prev:
                cur = _slew_quat(prev[name], cur, step)
            prev[name] = cur
            held[name] = cur
        f["fkJoints"] = held
        if wrist:
            hj = f.get("handJoints") or {"Left": None, "Right": None}
            for side in ("Left", "Right"):
                if hj.get(side) is not None and side in wrist:
                    hj[side]["wrist"] = wrist[side]
            f["handJoints"] = hj
        grafted += 1
    payload["smplRefined"] = grafted
    return payload
