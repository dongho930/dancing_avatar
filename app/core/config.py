"""앱 전역 설정. 환경변수로 오버라이드 (Docker/.env/앱 빌드 대응)."""
import os
from dataclasses import dataclass, field


def _getlist(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    cors_origins: list[str] = field(default_factory=lambda: _getlist("CORS_ORIGINS", "*"))
    tmp_dir: str = field(default_factory=lambda: os.getenv("TMP_DIR", "/tmp" if os.getenv("RENDER") else "tmp"))
    job_ttl_sec: int = field(default_factory=lambda: int(os.getenv("JOB_TTL_SEC", "1800")))
    cookies_path: str = field(default_factory=lambda: os.getenv("COOKIES_PATH", "cookies.txt"))
    max_frames: int = field(default_factory=lambda: int(os.getenv("MAX_FRAMES", "300")))  # 30fps*10s (Free Tier 안정 상한)
    frame_stride_default: int = field(default_factory=lambda: int(os.getenv("FRAME_STRIDE", "1")))
    # 모바일 페이로드 보호: 한 페이지 최대 프레임
    frames_page_max: int = field(default_factory=lambda: int(os.getenv("FRAMES_PAGE_MAX", "120")))
    # --- 1) 저장소 백엔드: auto(환경보고 선택) | memory | redis | postgres | hybrid ---
    store_backend: str = field(default_factory=lambda: os.getenv("STORE_BACKEND", "auto"))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", ""))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    # --- 2) 보안 ---
    # 쉼표구분 API키. 비어있으면 개발모드(경고 로그, 인증 우회). 운영에선 필수.
    api_keys: list[str] = field(default_factory=lambda: _getlist("API_KEYS", ""))
    api_key_file: str = field(default_factory=lambda: os.getenv("API_KEY_FILE", ""))
    # 분당 생성/조회 한도. 0이면 무제한(개발용).
    rate_limit_create_per_min: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_CREATE_PER_MIN", "20")))
    rate_limit_read_per_min: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_READ_PER_MIN", "300")))
    # --- 쿠키 시크릿: COOKIES_B64 > /run/secrets 파일 > COOKIES_PATH 순 ---
    cookies_b64: str = field(default_factory=lambda: os.getenv("COOKIES_B64", ""))
    cookies_secret_file: str = field(default_factory=lambda: os.getenv("COOKIES_SECRET_FILE", "/run/secrets/youtube_cookies"))
    # --- 3) 공유 Blob(S3 호환): 비어있으면 로컬 tmp 사용 ---
    s3_endpoint: str = field(default_factory=lambda: os.getenv("S3_ENDPOINT", ""))
    s3_bucket: str = field(default_factory=lambda: os.getenv("S3_BUCKET", ""))
    s3_prefix: str = field(default_factory=lambda: os.getenv("S3_PREFIX", "motions/"))
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", ""))


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        os.makedirs(_settings.tmp_dir, exist_ok=True)
    return _settings
