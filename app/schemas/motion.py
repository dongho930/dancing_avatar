from pydantic import BaseModel, Field, HttpUrl


class YouTubeRequest(BaseModel):
    url: HttpUrl
    # 추적 대상: auto | left | center | right | face | point(첫 화면 직접 지정)
    target: str = Field(default="auto", pattern="^(auto|left|center|right|face|point)$")
    # target=point용 클릭점 (x, y 정규화 좌표). 미리보기 후보 center를 그대로 반환.
    target_point: list[float] | None = None
    # target=face용 기준 얼굴사진 (base64, dataURL 허용, 1장)
    ref_image_b64: str = ""
    # 렌더: avatar(아바타만) | ref(아바타+참고용 AI 영상) — AI 영상은 참고용이며 결과물 아님
    render: str = Field(default="avatar", pattern="^(avatar|ref|both)$")
    # 참고영상용 캐릭터 사진 1장 (없으면 영상 첫 프레임에서 자동 크롭)
    char_image_b64: str = ""


class JobCreated(BaseModel):
    job_id: str
    status_url: str
    ws_url: str
    bvh_url: str = ""
    video_url: str = ""
    control_url: str = ""
    audio_url: str = ""


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: int
    message: str = ""
    fps: float = 0.0
    total_frames: int = 0
    error: str = ""
    formats: list[str] = []


class PreviewRequest(BaseModel):
    url: HttpUrl


class PreviewCreated(BaseModel):
    preview_id: str
    status_url: str
    frame_url: str


class PreviewStatus(BaseModel):
    preview_id: str
    status: str
    progress: int = 0
    message: str = ""
    candidates: list[dict] = []
    error: str = ""
