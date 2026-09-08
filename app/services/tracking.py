"""대상 지정·프레임 추적. 검출(detect.py)과 분리.

Tracker: 첫 유효 프레임에서 선택 → 이후 최근접 중심+크기로 추적.
게이트 이탈시 공백 허용(TRACK_GAP_TOL) 후 재선택.
"""
import os


def _d2(lm, i):
    """2D dict 리스트에서 (x, y, visibility)."""
    try:
        p = lm[i]
        if isinstance(p, dict):
            return (float(p.get("x", 0.5)), float(p.get("y", 0.5)), float(p.get("visibility", 0)))
        return (float(p[0]), float(p[1]), float(p[3]) if len(p) > 3 else 1.0)
    except (IndexError, TypeError, ValueError):
        return (0.5, 0.5, 0.0)


def _person_entries(lms2d_list, lms3d_list) -> list[dict]:
    """프레임의 N명 → [{center, size, score, lms2d, lms3d}]."""
    out = []
    for k, lms2d in enumerate(lms2d_list or []):
        if not lms2d or len(lms2d) < 29:
            continue
        lms3d = lms3d_list[k] if lms3d_list and k < len(lms3d_list) else []
        hx = (_d2(lms2d, 23)[0] + _d2(lms2d, 24)[0]) / 2
        hy = (_d2(lms2d, 23)[1] + _d2(lms2d, 24)[1]) / 2
        sx = (_d2(lms2d, 11)[0] + _d2(lms2d, 12)[0]) / 2
        sy = (_d2(lms2d, 11)[1] + _d2(lms2d, 12)[1]) / 2
        size = max(((sx - hx) ** 2 + (sy - hy) ** 2) ** 0.5, 1e-6)
        score = sum(_d2(lms2d, i)[2] for i in (11, 12, 23, 24)) / 4
        out.append({"center": (hx, hy), "size": size, "score": score,
                    "lms2d": lms2d, "lms3d": lms3d})
    return out


def _pick_initial(persons: list, mode: str) -> int | None:
    """위치 힌트로 첫 대상 선택. left=화면왼쪽, right=오른쪽, 그 외=중앙."""
    if not persons:
        return None
    if mode == "left":
        return min(range(len(persons)), key=lambda i: persons[i]["center"][0])
    if mode == "right":
        return max(range(len(persons)), key=lambda i: persons[i]["center"][0])
    return min(range(len(persons)), key=lambda i: abs(persons[i]["center"][0] - 0.5))


def _pick_nearest(persons: list, point) -> int | None:
    """미리보기에서 선택한 점(x, y 정규화)에 가장 가까운 사람."""
    if not persons or point is None:
        return None
    try:
        px, py = float(point[0]), float(point[1])
    except (IndexError, TypeError, ValueError):
        return None
    return min(range(len(persons)),
               key=lambda i: (persons[i]["center"][0] - px) ** 2
                             + (persons[i]["center"][1] - py) ** 2)


def _track_cost(p: dict, prev: dict) -> float:
    dc = ((p["center"][0] - prev["center"][0]) ** 2
          + (p["center"][1] - prev["center"][1]) ** 2) ** 0.5
    ds = abs(p["size"] - prev["size"]) / max(prev["size"], 1e-6)
    return dc + 0.5 * ds


def _wrist_mid_2d(lms2d):
    try:
        return ((_d2(lms2d, 15)[0] + _d2(lms2d, 16)[0]) / 2,
                (_d2(lms2d, 15)[1] + _d2(lms2d, 16)[1]) / 2)
    except (IndexError, TypeError):
        return (0.5, 0.5)


def assign_hands(hand_entries: list, persons: list, target_pos: int | None) -> list:
    """손들을 가장 가까운 사람의 손목에 할당, 대상 사람 것만 반환."""
    if target_pos is None or not persons or target_pos >= len(persons):
        return hand_entries if len(persons) <= 1 else []
    if len(persons) <= 1:
        return hand_entries
    wrists = [_wrist_mid_2d(p["lms2d"]) for p in persons]
    out = []
    for h in hand_entries:
        try:
            lms = h.get("landmarks") or []
            wx = float(lms[0].get("x", 0.5))
            wy = float(lms[0].get("y", 0.5))
        except (IndexError, TypeError, ValueError, AttributeError):
            continue
        best = min(range(len(wrists)),
                   key=lambda i: (wrists[i][0] - wx) ** 2 + (wrists[i][1] - wy) ** 2)
        d = ((wrists[best][0] - wx) ** 2 + (wrists[best][1] - wy) ** 2) ** 0.5
        if best == target_pos and d < 0.25:
            out.append(h)
    return out


def match_face(persons: list, frame_rgb, ref_emb, threshold: float) -> tuple[int | None, float]:
    """첫 유효 프레임에서 후보 얼굴 크롭 매칭. (인덱스, 유사도)."""
    from app.services.face_match import face_crop_rgb, match_candidates
    crops = []
    for p in persons:
        try:
            crops.append(face_crop_rgb(frame_rgb, p["lms2d"]))
        except Exception:  # noqa: BLE001
            crops.append(None)
    best, sim = match_candidates(crops, ref_emb)
    if best is None or sim < threshold:
        return None, sim
    return best, sim


class HandTracker:
    """손 슬롯의 시간 추적. 검출기 라벨 무시, 손목 위치 연속성으로 동일 손 유지.

    정규화 이미지 공간에서 동작. 한 번 잡은 슬롯은 놓치지 않는 한 유지해
    교차 팔에서도 뒤바뀌지 않는다. 기하 시드는 트랙 없을 때만 사용.
    """

    def __init__(self, gate: float = 0.02, gap_tol: int = 10) -> None:
        self.gate = gate  # dist^2 (실측 p90 0.017 → 여유 0.02)
        self.gap_tol = gap_tol
        self.slots: dict = {}  # side -> {"pos": (x, y), "vel": (vx, vy), "gap": int}

    @staticmethod
    def _xy(lms) -> tuple | None:
        try:
            if len(lms) < 21:
                return None
            return (float(lms[0].get("x", 0.5)), float(lms[0].get("y", 0.5)))
        except (IndexError, TypeError, ValueError, AttributeError):
            return None

    def _seed_side(self, x: float, y: float, pose_wrists) -> str | None:
        if not pose_wrists:
            return None
        try:
            lx, ly = pose_wrists["Left"]
            rx, ry = pose_wrists["Right"]
        except (TypeError, KeyError):
            return None
        dl = (x - lx) ** 2 + (y - ly) ** 2
        dr = (x - rx) ** 2 + (y - ry) ** 2
        if min(dl, dr) > 0.09 or abs(dl - dr) / max(dl + dr, 1e-9) < 0.15:
            return None
        return "Left" if dl < dr else "Right"

    @staticmethod
    def _dist2(ax, ay, bx, by) -> float:
        return (ax - bx) ** 2 + (ay - by) ** 2

    def _predict(self, st: dict) -> tuple:
        vx, vy = st.get("vel", (0.0, 0.0))
        return (st["pos"][0] + vx, st["pos"][1] + vy)

    def assign(self, cands: list, pose_wrists=None, labels: dict | None = None) -> dict:
        """cands: [(key, x, y)] → {side: key}.

        살아있는 트랙끼리는 전역 최소비용 매칭(동점시 라벨 일치 우선).
        남는 검출은 기하 시드 → 라벨 순으로 빈 슬롯에.
        """
        import itertools
        # 중복 검출 제거 (같은 손을 두 번 잡은 경우)
        uniq = []
        for c in cands:
            if all(self._dist2(c[1], c[2], u[1], u[2]) > 0.0004 for u in uniq):
                uniq.append(c)
        live = [s for s in ("Left", "Right")
                if self.slots.get(s) and self.slots[s]["gap"] <= self.gap_tol]
        out: dict = {}
        if live and uniq:
            pred = {s: self._predict(self.slots[s]) for s in live}
            best, best_cost = None, None
            # live 슬롯들에 대한 전단사 매칭 전부 평가 (최대 2! = 2가지)
            for perm in itertools.permutations([c[0] for c in uniq], min(len(live), len(uniq))):
                if len(live) == 2 and len(perm) < 2:
                    continue
                mapping = dict(zip(live, perm))
                cost = sum(self._dist2(*pred[s], *next(c[1:] for c in uniq if c[0] == k))
                           for s, k in mapping.items())
                if best is None or cost < best_cost - 1e-9:
                    best, best_cost = mapping, cost
                elif abs(cost - best_cost) <= 1e-9 and labels:
                    # 동점(교차 순간): 라벨 일치 많은 쪽. 그래도 동점이면 기존 유지.
                    def _score(m):
                        return sum(1 for s, k in m.items()
                                   if str(labels.get(k, "")).capitalize() == s)
                    if _score(mapping) > _score(best):
                        best, best_cost = mapping, cost
            if best is not None:
                # 게이트 밖 매칭은 버림 (급격한 점프 = 다른 손)
                for s, k in list(best.items()):
                    pos = next(c[1:] for c in uniq if c[0] == k)
                    if self._dist2(*self._predict(self.slots[s]), *pos) > self.gate:
                        del best[s]
                out.update(best)
        free = [c for c in uniq if c[0] not in out.values()]
        # 남은 건 기하 시드 → 라벨 순으로 빈 슬롯에
        for c in free:
            if len(out) >= 2:
                break
            side = self._seed_side(c[1], c[2], pose_wrists)
            if side is None and labels:
                lab = str(labels.get(c[0], "")).capitalize()
                side = lab if lab in ("Left", "Right") else None
            if side is None:
                side = "Left" if "Left" not in out else "Right"
            if side not in out:
                out[side] = c[0]
        # 상태 갱신 (속도 포함)
        posmap = {c[0]: (c[1], c[2]) for c in uniq}
        for side in ("Left", "Right"):
            if side in out and out[side] in posmap:
                new_pos = posmap[out[side]]
                prev = self.slots.get(side, {"pos": new_pos, "vel": (0.0, 0.0), "gap": 0})
                old_pos = prev["pos"]
                self.slots[side] = {"pos": new_pos,
                                    "vel": (new_pos[0] - old_pos[0], new_pos[1] - old_pos[1]),
                                    "gap": 0}
            else:
                prev = self.slots.get(side, {"pos": (0.5, 0.5), "vel": (0.0, 0.0), "gap": 0})
                self.slots[side] = {"pos": prev["pos"], "vel": (0.0, 0.0),
                                    "gap": prev["gap"] + 1}
        return out


class Tracker:
    """한 영상의 대상 추적 상태. update()마다 (lms3d, lms2d, pick, lost) 반환."""

    def __init__(self, mode: str = "auto", target_point=None) -> None:
        self.mode = mode
        self.target_point = target_point
        self.gate = float(os.getenv("TRACK_GATE", "0.25"))
        self.gap_tol = int(os.getenv("TRACK_GAP_TOL", "15"))
        self.face_thr = float(os.getenv("FACE_THRESHOLD", "0.4"))
        self.prev = None
        self.gap = 0
        self.persons_max = 0
        self.label = mode if mode != "face" else "face:?"
        self.face_matched = False

    def update(self, persons: list, frame_rgb=None, ref_emb=None):
        self.persons_max = max(self.persons_max, len(persons))
        pick, lost = None, False
        if persons:
            if self.prev is None or self.gap > self.gap_tol:
                if self.mode == "face" and not self.face_matched:
                    pick, sim = match_face(persons, frame_rgb, ref_emb, self.face_thr)
                    if pick is not None:
                        self.label = f"face:{sim:.2f}"
                        self.face_matched = True
                    else:
                        pick = _pick_initial(persons, "center")
                        self.label = "face:miss→center"
                elif self.mode == "point":
                    pick = _pick_nearest(persons, self.target_point)
                    if pick is None:
                        pick = _pick_initial(persons, "center")
                        self.label = "point:miss→center"
                    else:
                        self.label = "point"
                else:
                    pick = _pick_initial(persons, self.mode)
                self.gap = 0
            else:
                costs = [_track_cost(p, self.prev) for p in persons]
                best = min(range(len(costs)), key=lambda i: costs[i])
                if costs[best] <= self.gate:
                    pick = best
                    self.gap = 0
                else:
                    lost = True
                    self.gap += 1
        else:
            lost = True
            self.gap += 1
        if pick is not None:
            self.prev = persons[pick]
            return persons[pick]["lms3d"], persons[pick]["lms2d"], pick, lost
        return [], [], None, lost
