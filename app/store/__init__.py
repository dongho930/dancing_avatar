"""Job 저장소 파사드 (`from app.store import store`).

STORE_BACKEND=auto|memory|redis|postgres|hybrid
- auto: REDIS_URL 있으면 redis → DATABASE_URL 있으면 postgres → memory
- hybrid: Redis(상태 공유) + Postgres(영속 미러). 하나라도 죽으면 살아있는 쪽으로 폴백.
- 프레임 본문은 BlobStore(파일/S3)에 저장해 레플리카가 공유한다.

호출부는 이 파일의 API만 쓰므로 백엔드 교체에 영향받지 않는다.
"""
import json
import logging
import os
import time

from app.core.config import get_settings
from app.store.backends.memory import Job, MemoryJobs  # noqa: F401 - 외부 호환 re-export
from app.store.blob import build_blob_store

log = logging.getLogger(__name__)


class HybridJobStore:
    def __init__(self) -> None:
        s = get_settings()
        self.blob = build_blob_store(s.tmp_dir, s.s3_endpoint, s.s3_bucket, s.s3_prefix)
        self.backend_name = "memory"
        self._primary: MemoryJobs | object = MemoryJobs()
        self._mirror = None
        mode = (s.store_backend or "auto").lower()
        try:
            if mode == "memory":
                pass
            elif mode == "redis":
                from app.store.backends.redis import RedisJobs
                self._primary = RedisJobs(s.redis_url, s.job_ttl_sec)
                self.backend_name = "redis"
            elif mode == "postgres":
                from app.store.backends.postgres import PostgresJobs
                self._primary = PostgresJobs(s.database_url)
                self.backend_name = "postgres"
            elif mode == "hybrid":
                from app.store.backends.postgres import PostgresJobs
                from app.store.backends.redis import RedisJobs
                ok_r = ok_p = False
                try:
                    self._primary = RedisJobs(s.redis_url, s.job_ttl_sec)
                    ok_r = True
                except Exception as e:  # noqa: BLE001
                    log.warning("Redis 접속 실패, postgres로 폴백: %s", type(e).__name__)
                try:
                    self._mirror = PostgresJobs(s.database_url)
                    ok_p = True
                except Exception as e:  # noqa: BLE001
                    log.warning("Postgres 접속 실패: %s", type(e).__name__)
                if ok_r and ok_p:
                    self.backend_name = "hybrid"
                elif ok_r:
                    self.backend_name = "redis"
                    self._mirror = None
                elif ok_p:
                    self._primary = self._mirror
                    self._mirror = None
                    self.backend_name = "postgres"
            else:  # auto
                if s.redis_url:
                    try:
                        from app.store.backends.redis import RedisJobs
                        self._primary = RedisJobs(s.redis_url, s.job_ttl_sec)
                        self.backend_name = "redis"
                    except Exception as e:  # noqa: BLE001
                        log.warning("Redis 실패, 다음 후보로: %s", type(e).__name__)
                if self.backend_name == "memory" and s.database_url:
                    try:
                        from app.store.backends.postgres import PostgresJobs
                        self._primary = PostgresJobs(s.database_url)
                        self.backend_name = "postgres"
                    except Exception as e:  # noqa: BLE001
                        log.warning("Postgres 실패, memory로: %s", type(e).__name__)
        except Exception as e:  # noqa: BLE001 - 부팅은 절대 죽지 않게
            log.warning("JobStore 초기화 폴백(memory): %s", e)
            self._primary = MemoryJobs()
            self._mirror = None
            self.backend_name = "memory"

    # ---- 메타데이터 ----
    def create(self, url: str):
        return self._primary.create(url)  # type: ignore[attr-defined]

    def get(self, job_id: str):
        job = self._primary.get(job_id)  # type: ignore[attr-defined]
        if job is None and self._mirror is not None:
            try:
                job = self._mirror.get(job_id)
            except Exception:  # noqa: BLE001
                job = None
        return job

    def update(self, job_id: str, **kw) -> None:
        try:
            self._primary.update(job_id, **kw)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            log.warning("primary update 실패: %s", e)
        if self._mirror is not None:
            try:
                # 미러에 행이 없으면 update가 조용히 무시되므로 get 후 없으면 스킵
                if self._mirror.get(job_id) is not None:
                    self._mirror.update(job_id, **kw)
            except Exception:  # noqa: BLE001
                pass

    def delete(self, job_id: str) -> None:
        for b in (self._primary, self._mirror):
            if b is None:
                continue
            try:
                b.delete(job_id)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    # ---- Blob ----
    def save_frames(self, job_id: str, payload: dict) -> None:
        self.blob.save(job_id, "frames", payload)

    def paginate(self, payload: dict, page: int, page_size: int, stride: int) -> dict:
        frames = payload.get("frames", [])
        if stride > 1:
            frames = frames[::stride]
        total = len(frames)
        start = page * page_size
        return {
            "fps": payload.get("fps", 0),
            "total_frames": payload.get("total_frames", 0),
            "filtered_frames": total,
            "page": page,
            "page_size": page_size,
            "stride": stride,
            "frames": frames[start:start + page_size],
        }

    def load_frames_page(self, job_id: str, page: int, page_size: int, stride: int) -> dict:
        try:
            payload = self.blob.load(job_id, "frames")
        except FileNotFoundError:
            raise
        except Exception as e:  # noqa: BLE001
            # S3 NoSuchKey 등을 410으로 매핑할 수 있게 FileNotFound로 변환
            raise FileNotFoundError(str(e)) from e
        if isinstance(payload, str):
            payload = json.loads(payload)
        return self.paginate(payload, page, page_size, stride)

    def save_rig(self, job_id: str, payload: dict, kind: str = "rig") -> None:
        self.blob.save(job_id, kind, payload)

    def rig_exists(self, job_id: str, kind: str = "rig") -> bool:
        return self.blob.exists(job_id, kind)

    def save_bvh(self, job_id: str, text: str, kind: str = "bvh") -> None:
        self.blob.save(job_id, kind, text)

    def load_bvh(self, job_id: str, kind: str = "bvh") -> str:
        data = self.blob.load(job_id, kind)
        return data if isinstance(data, str) else json.dumps(data)

    def bvh_exists(self, job_id: str, kind: str = "bvh") -> bool:
        return self.blob.exists(job_id, kind)

    def store_video_file(self, job_id: str, kind: str, src_path: str) -> str:
        return self.blob.store_file(job_id, kind, src_path)

    def video_exists(self, job_id: str, kind: str = "genvideo") -> bool:
        return self.blob.exists(job_id, kind)

    def video_path(self, job_id: str, kind: str = "genvideo") -> str:
        return self.blob.path(job_id, kind)

    def cleanup_expired(self) -> int:
        ttl = get_settings().job_ttl_sec
        removed = 0
        # 1) 메타 만료분 삭제
        try:
            if hasattr(self._primary, "list_expired"):
                for jid in self._primary.list_expired(ttl):  # type: ignore[attr-defined]
                    self.delete(jid)
                    self.blob.delete(jid)
                    removed += 1
        except Exception as e:  # noqa: BLE001
            log.warning("cleanup(메타) 실패: %s", e)
        # 2) 로컬 잔재 파일 TTL 정리 (레플리카 각자 수행, 안전 방향)
        tmp = get_settings().tmp_dir
        now = time.time()
        try:
            for name in os.listdir(tmp) if os.path.isdir(tmp) else []:
                p = os.path.join(tmp, name)
                try:
                    if now - os.path.getmtime(p) > ttl and (
                        name.startswith("job_") or name.startswith("dl_") or name.startswith("temp_")
                        or name.startswith("cache_") or name.startswith("preview_")):
                        os.remove(p)
                        removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        return removed


store = HybridJobStore()
