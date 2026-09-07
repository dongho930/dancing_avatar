"""입출력 포맷: rig / BVH. 수학은 kinematics.py + fk.py."""
import math

from app.services.kinematics import (
    RIG_JOINTS,
    _vis,
    attach_fk_joints,
    attach_root,
    frame_hand_joints,
)


def _ensure_root(payload: dict) -> dict:
    """구 저장분(루트 없음) 호환: 첫 프레임에 root가 없으면 그 자리에서 계산."""
    frames = payload.get("frames", [])
    if frames and "root" not in frames[0] and "rt" not in frames[0]:
        attach_root(payload)
    return payload


def _body_vis(world: list) -> float:
    try:
        return round(sum(_vis(world, i) for i in
                         (11, 12, 13, 14, 15, 16, 23, 24, 25, 26)) / 10, 3)
    except (IndexError, TypeError, ValueError):
        return 0.0


def to_rig(payload: dict) -> dict:
    """full payload → rig payload {fps,total_frames,frames:[{frame,timestamp,joints,vis,hands,root}]}.

    fkJoints(신규 저장분)가 있으면 그대로, 없으면(구 저장분) 그 자리에서 계산.
    """
    _ensure_root(payload)
    frames = payload.get("frames", [])
    if frames and "fkJoints" not in frames[0]:
        attach_fk_joints(payload)
    out = []
    for f in frames:
        joints = f.get("fkJoints") or {}
        world = f.get("poseWorldLandmarks") or []
        out.append({"frame": f.get("frame", 0), "timestamp": f.get("timestamp", 0),
                    "joints": joints, "vis": _body_vis(world) if world else 0.0,
                    "hands": frame_hand_joints(f.get("hands") or []),
                    "root": list(f.get("root", [0.0, 0.0, 0.0]))})
    return {"fps": payload.get("fps", 30.0), "total_frames": payload.get("total_frames", len(out)),
            "format": "rig", "joints_list": list(RIG_JOINTS), "frames": out}


_HIER = """HIERARCHY
ROOT Hips
{
 OFFSET 0.00 95.00 0.00
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT Spine
 {
  OFFSET 0.00 10.00 0.00
  CHANNELS 3 Zrotation Xrotation Yrotation
  JOINT Chest
  {
   OFFSET 0.00 12.00 0.00
   CHANNELS 3 Zrotation Xrotation Yrotation
   JOINT Neck
   {
    OFFSET 0.00 15.00 0.00
    CHANNELS 3 Zrotation Xrotation Yrotation
    JOINT Head
    {
     OFFSET 0.00 10.00 0.00
     CHANNELS 3 Zrotation Xrotation Yrotation
    End Site
     {
      OFFSET 0.00 8.00 0.00
     }
    }
   }
   JOINT LeftUpperArm
   {
    OFFSET -5.00 12.00 0.00
    CHANNELS 3 Zrotation Xrotation Yrotation
    JOINT LeftLowerArm
    {
     OFFSET -25.00 0.00 0.00
     CHANNELS 3 Zrotation Xrotation Yrotation
    End Site
     {
      OFFSET -25.00 0.00 0.00
     }
    }
   }
   JOINT RightUpperArm
   {
    OFFSET 5.00 12.00 0.00
    CHANNELS 3 Zrotation Xrotation Yrotation
    JOINT RightLowerArm
    {
     OFFSET 25.00 0.00 0.00
     CHANNELS 3 Zrotation Xrotation Yrotation
    End Site
     {
      OFFSET 25.00 0.00 0.00
     }
    }
   }
  }
  JOINT LeftUpperLeg
  {
   OFFSET -10.00 -5.00 0.00
   CHANNELS 3 Zrotation Xrotation Yrotation
   JOINT LeftLowerLeg
   {
    OFFSET 0.00 -40.00 0.00
    CHANNELS 3 Zrotation Xrotation Yrotation
   End Site
    {
     OFFSET 0.00 -40.00 0.00
    }
   }
  }
  JOINT RightUpperLeg
  {
   OFFSET 10.00 -5.00 0.00
   CHANNELS 3 Zrotation Xrotation Yrotation
   JOINT RightLowerLeg
   {
    OFFSET 0.00 -40.00 0.00
    CHANNELS 3 Zrotation Xrotation Yrotation
   End Site
    {
     OFFSET 0.00 -40.00 0.00
    }
   }
  }
 }
}
"""

_BVH_ORDER = ("Hips", "Spine", "Chest", "Neck", "Head",
              "LeftUpperArm", "LeftLowerArm", "RightUpperArm", "RightLowerArm",
              "LeftUpperLeg", "LeftLowerLeg", "RightUpperLeg", "RightLowerLeg")


def to_bvh(rig_payload: dict) -> str:
    """rig payload → BVH 텍스트. Hips 위치는 프레임별 루트 궤적(cm) + 시간 전진."""
    frames = rig_payload.get("frames", [])
    fps = rig_payload.get("fps", 30.0) or 30.0
    dt = 1.0 / fps
    lines = [_HIER, f"MOTION\nFrames: {len(frames)}\nFrame Time: {dt:.6f}"]
    to_deg = 180.0 / math.pi
    for f in frames:
        j = f.get("joints") or {}
        rt = f.get("root", [0.0, 0.0, 0.0])
        row = [f"{rt[0] * 100:.2f}", f"{95.0 + rt[1] * 100:.2f}", f"{rt[2] * 100:.2f}"]
        for name in _BVH_ORDER:
            e = j.get(name, [0.0, 0.0, 0.0])
            # BVH 채널 순서 Z X Y (도 단위). FK값 그대로 (감쇠 없음).
            row += [f"{e[2] * to_deg:.2f}", f"{e[0] * to_deg:.2f}", f"{e[1] * to_deg:.2f}"]
        lines.append(" ".join(row))
    return "\n".join(lines) + "\n"