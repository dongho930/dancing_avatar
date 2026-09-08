"""yt-dlp 다운로드 전담. 기존 main.py의 2단계 폴백 로직을 그대로 이관.

import yt_dlp를 함수 안에서 lazy import → mediapipe/cv2 없는 환경에서도
API 스켈레톤 임포트/테스트가 깨지지 않게 한다 (앱 빌드/CI 대응).
"""
import os
from collections.abc import Callable


def build_base_opts(outtmpl: str) -> dict:
    fmt = os.getenv("YTDL_FORMAT", "").strip()
    if not fmt:
        # Free Tier 기본: progressive mp4 우선 (별도 merge 불필요 → apt ffmpeg 불필요,
        # CPU/RAM 절감). 고화질 분리 스트림이 필요하면 YTDL_FORMAT으로 재정의.
        maxh = os.getenv("YTDL_MAX_HEIGHT", "720").strip() or "720"
        fmt = f"best[height<={maxh}][ext=mp4]/best[ext=mp4]/best"
    return {
        'format': fmt,
        'merge_output_format': 'mp4',
        'outtmpl': outtmpl,
        'quiet': False,
        'no_warnings': False,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'remote_components': {'ejs:github'},
    }


def download_youtube(url: str, out_path: str, cookies_path: str = "cookies.txt",
                     progress_cb: Callable[[str], None] | None = None) -> str:
    import yt_dlp  # lazy: 서버 워커에만 필요

    # cookies_path가 비어있으면(시크릿 없음) cookiefile 옵션 자체를 생략한다
    effective_cookies = cookies_path if cookies_path and os.path.exists(cookies_path) else ""
    # 진단용 (값 노출 없음): 쿠키 적용 여부 + 버전만 로그
    print(f"[youtube] yt-dlp {getattr(yt_dlp, '__version__', '?')}, "
          f"cookies={'ON' if effective_cookies else 'OFF'}", flush=True)

    base = build_base_opts(out_path)
    # PO Token 스크립트 모드: 번들된 bgutil 서버 소스를 Deno로 직접 실행.
    # 디렉터리가 없으면(로컬 등) 조용히 생략 → 기존 동작 유지.
    pot_args: dict = {}
    server_home = os.getenv("BGUTIL_SERVER_HOME", "/srv/bgutil-ytdlp-pot-provider/server")
    if server_home and os.path.isdir(server_home):
        pot_args = {"youtubepot-bgutilscript": {"server_home": server_home}}
    attempt_1 = {**base, 'extractor_args': {'youtube': {'player_client': ['android', 'ios']}, **pot_args}}
    attempt_2 = {**base, 'extractor_args': {'youtube': {'player_client': ['web', 'tv', 'mweb']}, **pot_args}}
    if effective_cookies:
        attempt_2['cookiefile'] = effective_cookies

    last_error: Exception | None = None
    for name, opts in (("android/ios (쿠키 없음)", attempt_1), ("web/tv/mweb (쿠키)", attempt_2)):
        try:
            if progress_cb:
                progress_cb(f"다운로드 시도: {name}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            last_error = None
            break
        except Exception as e:  # noqa: BLE001 - yt-dlp 예외 종류가 다양해 메시지 보존
            last_error = e
            continue
    if last_error is not None:
        raise RuntimeError(f"유튜브 다운로드 실패: {last_error}") from last_error
    if not os.path.exists(out_path):
        # merge 등으로 확장자가 달라진 경우 같은 prefix 파일 탐색
        prefix = os.path.splitext(out_path)[0]
        for cand in [prefix + ".mp4", prefix + ".webm", prefix + ".mkv"]:
            if os.path.exists(cand):
                return cand
        raise FileNotFoundError("동영상 파일 생성 실패")
    return out_path


def url_hash(url: str) -> str:
    """URL → 16자리 캐시 키. 미리보기와 본 잡이 같은 영상을 공유."""
    import hashlib
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def url_cache_path(tmp_dir: str, url: str) -> str:
    return os.path.join(tmp_dir, f"cache_{url_hash(url)}.mp4")


def download_cached(url: str, tmp_dir: str, ttl_sec: int = 3600,
                    cookies_path: str = "cookies.txt",
                    progress_cb: Callable[[str], None] | None = None) -> tuple[str, bool]:
    """신선한 캐시가 있으면 재사용 (True), 없으면 다운로드 후 캐시 저장.

    미리보기와 본 잡이 같은 파일을 공유해 다운로드를 1회로 줄인다.
    """
    import time
    os.makedirs(tmp_dir, exist_ok=True)
    cpath = url_cache_path(tmp_dir, url)
    if os.path.exists(cpath) and time.time() - os.path.getmtime(cpath) < ttl_sec:
        if progress_cb:
            progress_cb("캐시된 영상 재사용")
        return cpath, True
    tmp_dl = cpath + ".downloading"
    got = download_youtube(url, tmp_dl, cookies_path=cookies_path, progress_cb=progress_cb)
    if os.path.exists(cpath):
        os.remove(cpath)
    os.replace(got, cpath)
    return cpath, False
