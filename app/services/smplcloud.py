"""HF Spaces SMPL 워커 클라이언트 (ZeroGPU, 무료).

SMPL_AUTO=1이고 HF_SPACE가 설정됐을 때만 동작. 실패·타임아웃·미설정
모두 None 반환 → FK 그대로 진행 (SMPL 때문에 잡이 죽지 않음).

캐시: tmp/smpl_<urlhash>.json (다운로드 캐시와 같은 철학).
수동 SMPL_PKL 파일이 있으면 이 모듈을 거치지 않음 (steps.py 참조).
"""
import concurrent.futures
import json
import os


def _space() -> str:
    return os.getenv("HF_SPACE", "").strip()


def _token() -> str:
    return os.getenv("HF_TOKEN", "").strip()


def _timeout() -> int:
    try:
        return max(60, int(os.getenv("SMPL_TIMEOUT", "600")))
    except ValueError:
        return 600


def _auto() -> bool:
    return os.getenv("SMPL_AUTO", "0").strip() not in ("0", "false", "no", "")


def space_enabled() -> bool:
    return _auto() and bool(_space())


def cache_path(tmp_dir: str, url_hash: str) -> str:
    return os.path.join(tmp_dir, f"smpl_{url_hash}.json")


def _load_cache(path: str):
    try:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("pose")
        if not rows or len(rows) < 5:
            return None
        return [list(map(float, r)) for r in rows]
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _save_cache(path: str, rows: list, meta: dict | None = None) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pose": rows, "meta": meta or {}}, f)
    except (OSError, TypeError, ValueError):
        pass


def _call_space(video_path: str, timeout: int):
    """Spaces predict 1회 호출 → 결과 JSON 경로. 예외는 그대로 던짐."""
    from gradio_client import Client
    client = Client(_space(), hf_token=_token() or None)
    job = client.submit(video_path, api_name="/predict")
    return job.result(timeout=timeout)


def _result_to_rows(result_path: str):
    try:
        with open(result_path, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("pose") or []
        rows = [[float(c) for c in r] for r in rows if r and len(r) >= 72]
        if len(rows) < 5:
            return None
        return rows
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def fetch_smpl(video_path: str, tmp_dir: str, url_hash: str,
               progress_cb=None) -> list | None:
    """SMPL pose rows 확보 (캐시 우선). 불가시 None.

    progress_cb: 상태 메시지 콜백 (선택).
    """
    cpath = cache_path(tmp_dir, url_hash)
    cached = _load_cache(cpath)
    if cached is not None:
        if progress_cb:
            progress_cb("SMPL 캐시 재사용")
        return cached
    if not space_enabled():
        return None
    if not (video_path and os.path.exists(video_path)):
        return None
    timeout = _timeout()
    if progress_cb:
        progress_cb("SMPL 정제 요청 (GPU 워커, 수 분 소요 가능)")

    def _run():
        return _call_space(video_path, timeout)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_run)
            result_path = fut.result(timeout=timeout + 60)
    except Exception:
        return None
    rows = _result_to_rows(result_path) if result_path else None
    if rows is None:
        return None
    _save_cache(cpath, rows, {"frames": len(rows)})
    return rows
