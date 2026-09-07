# dancing-avarta
# Dance Pose Extractor API

유튜브 영상(주로 Shorts) URL을 입력받아 영상을 다운로드하고, MediaPipe로 사람의
관절(pose) 좌표를 프레임 단위로 추출해 JSON으로 반환하는 FastAPI 백엔드입니다.
프론트엔드(`index.html`)는 이 JSON을 받아 Kalidokit으로 3D 아바타(Mixamo 리그)에
모션을 입혀 재생합니다.

---

## 왜 서버가 2개 필요한가?

이 프로젝트를 실행하려면 서버를 **2개** 띄워야 합니다.

| 서버 | 포트 | 역할 | 외부 공개 여부 |
|---|---|---|---|
| **FastAPI 백엔드** (`main.py`) | 8000 | 프론트엔드의 요청을 받아 유튜브 다운로드 + 모션 추출을 수행 | **Public** (브라우저가 직접 호출) |
| **bgutil PO Token 서버** | 4416 | yt-dlp가 유튜브에서 영상을 받아올 때 필요한 인증 토큰(PO Token)을 발급 | **Private** (내 서버 프로세스만 호출) |

### bgutil 서버가 왜 필요한가

유튜브는 최근 봇/크롤러 차단을 위해 영상 다운로드 URL을 받을 때
**PO Token(Proof-of-Origin Token)** 검증을 요구하기 시작했습니다. 이 토큰이
없으면 yt-dlp가 아무리 정상적인 방식으로 요청해도 유튜브가 "이미지(썸네일)만
줄게, 영상 스트림 URL은 안 줄게" 하는 식으로 응답을 제한해버립니다
(`Only images are available for download` 에러가 이 경우입니다).

`bgutil-ytdlp-pot-provider`는 이 토큰을 로컬에서 대신 생성해주는 작은
Node.js 서버입니다. `main.py`가 유튜브에 다운로드를 요청하기 **직전에**
내부적으로(`http://127.0.0.1:4416`) 이 서버에 "토큰 하나 줘"라고 물어보고,
받은 토큰을 유튜브 요청에 실어 보냅니다.

- 이 서버는 **브라우저나 외부 사용자와 통신하지 않습니다.** 오직 같은 머신 안에서
  `main.py`(정확히는 그 안의 yt-dlp)가 호출하는 내부 인프라입니다.
- 그래서 **Public으로 열 필요가 없고, 오히려 열면 안 됩니다** (외부에 노출되면
  누구나 이 토큰 발급기를 마음대로 호출할 수 있게 되어 악용 위험이 있습니다).

즉 정리하면: **8000번(백엔드)은 프론트엔드용, 4416번(bgutil)은 yt-dlp의
내부 보조 도구**입니다. 두 서버 다 켜져 있어야 다운로드 단계가 정상 동작합니다.

---

## main.py 전체 흐름

프론트엔드가 `POST /api/process-youtube`로 유튜브 URL을 보내면, 아래 순서로
처리됩니다.

```
[요청 수신]
    │
    ▼
1) URL 파싱, 임시 파일명 생성 (temp_<uuid>.mp4)
    │
    ▼
2) 유튜브 다운로드 시도 (yt-dlp) — 실패하면 다음 방식으로 자동 재시도
    │
    ├─ 1차 시도: player_client = android, ios (쿠키 없음)
    │      └─ 공개 영상은 대부분 여기서 성공. 실패 시 ↓
    │
    └─ 2차 시도: player_client = web, tv, mweb (쿠키 사용)
           └─ 봇 차단/연령제한 등으로 1차가 막혔을 때의 폴백.
              이 단계에서 bgutil 서버로부터 PO Token을 받아와 사용.
    │
    ▼
3) 두 시도 모두 실패 → 400 에러 반환 (마지막 에러 메시지 포함)
   성공 → 임시 mp4 파일 생성됨
    │
    ▼
4) 다운로드된 mp4에서 MediaPipe Pose로 프레임별 관절 좌표 추출
   (process_video_pose)
     - OpenCV(cv2)로 프레임을 하나씩 읽음
     - 각 프레임을 MediaPipe Pose에 통과시켜 33개 관절의
       poseWorldLandmarks(3D, 실제 좌표계)와
       poseLandmarks(2D, 화면 정규화 좌표)를 추출
    │
    ▼
5) 프레임 배열 + fps + 총 프레임 수를 JSON으로 응답
   { "status": "success", "data": { fps, total_frames, frames: [...] } }
    │
    ▼
6) 백그라운드 태스크로 임시 mp4 파일 삭제 (cleanup_file)
```

### 다운로드 단계에서 겪었던 이슈들과 왜 이런 구조가 됐는지

이 재시도/폴백 구조는 개발 중 실제로 마주쳤던 문제들을 하나씩 해결하며
누적된 결과입니다.

- **n-challenge (시그니처 검증) 실패**
  → 유튜브가 다운로드 URL을 암호화된 파라미터로 감싸는데, 이걸 풀려면
    JS 런타임(Deno)과 yt-dlp의 챌린지 솔버 스크립트가 필요합니다.
    (`remote_components: {'ejs:github'}` 옵션으로 자동 다운로드하도록 설정)

- **`LOGIN_REQUIRED` (android/ios 클라이언트)**
  → 일부 영상은 로그인 없이 android/ios 클라이언트로 접근이 막혀 있어,
    쿠키가 필요한 web 계열로 폴백해야 합니다.

- **`Sign in to confirm you're not a bot`**
  → 데이터센터 IP(Codespaces 등)는 유튜브가 봇으로 의심하기 쉬워, 로그인
    쿠키(`cookies.txt`)를 함께 보내 정상 사용자처럼 보이게 합니다.

- **SABR 스트리밍 강제 (web 클라이언트)**
  → 유튜브가 일부 세션에 실험적으로 web 클라이언트의 다운로드 URL 자체를
    주지 않는 경우가 있어, tv/mweb 클라이언트도 함께 시도해 그중 살아있는
    쪽을 사용하도록 범위를 넓혔습니다.

- **AV1 코덱 디코딩 실패**
  → 서버에 AV1 하드웨어 디코더가 없어 소프트웨어 디코딩 중 일부 프레임이
    깨지는 문제가 있어, `format` 옵션에서 H.264(avc1) 코덱을 최우선으로
    선택하도록 지정했습니다.

즉 지금의 이중 시도 구조(`attempt_1` → 실패 시 `attempt_2`)와 다중
`player_client` 지정, `remote_components` 설정은 전부 유튜브 쪽의
지속적인 봇 차단 정책 강화에 대응하기 위한 방어 로직입니다. 이 정책은
계속 바뀌므로, 언젠가 또 막히면 비슷한 방식(다른 클라이언트 추가, yt-dlp
업데이트)으로 대응해야 할 수 있습니다.

---

## 프로젝트 구조

```
.
├── main.py                    # 호환용 진입점 (실체는 app/main.py)
├── app/
│   ├── main.py                # FastAPI 팩토리 (/health, /app 정적서빙)
│   ├── api/                   # motions(잡) · frames(rig) · assets(bvh/mp4) · ws · legacy
│   ├── core/                  # config, secrets
│   ├── store/                 # 파사드 + backends(memory/redis/postgres) + blob(파일/S3)
│   ├── schemas/               # 요청/응답 모델
│   ├── services/              # youtube, detect, tracking, face_match,
│   │                          # pose(오케스트레이션), kinematics(수학),
│   │                          # exports(rig/BVH), pose_video, genvideo, audio
│   └── pipeline/              # 잡 실행기 (runner + 단계별 steps)
├── frontend/                  # index.html + js(config/scene/api/player/ui) + css
├── cookies.txt                # 유튜브 로그인 쿠키 (git 금지, 시크릿 권장)
├── start.sh                   # bgutil + 백엔드 실행 스크립트
└── bgutil-ytdlp-pot-provider/ # PO Token 발급 서버 (yt-dlp 보조 인프라)
```

API v1: `POST /api/v1/motions` (target/render 옵션) →
`GET /{id}` 상태 → `/{id}/frames` (full 단일) →
`/{id}/bvh|video|control|audio` + `/{id}/ws` 진행률 푸시.
주 출력은 3D 아바타이며, AI 영상(`render=ref`)과 원본 음원은 참고·동기재생용.

---

## 실행 방법

```bash
chmod +x start.sh
./start.sh
```

`start.sh`가 자동으로:
1. bgutil 서버(4416)가 떠 있는지 확인하고, 없으면 백그라운드로 실행
2. 정상 기동될 때까지 대기
3. FastAPI 백엔드(`main.py`, 8000)를 포그라운드로 실행

이후 `index.html`을 열고(Codespaces면 8000번 포트를 **Public**으로 설정),
`BACKEND_URL`에 해당 포트의 공개 URL을 넣은 뒤 유튜브 URL을 입력해 사용합니다.

---

## 알려진 제약 / 주의사항

- 유튜브의 봇 차단 정책은 계속 바뀌므로, 위 대응 로직이 어느 날 다시 막힐 수
  있습니다. 그럴 땐 `yt-dlp -U --pre`로 최신 nightly 버전으로 갱신하는 것부터
  시도하세요.
- `cookies.txt`는 만료될 수 있으니, 다운로드가 계속 `LOGIN_REQUIRED`나 봇
  차단으로 실패하면 새로 export해서 교체하세요.
- MediaPipe로 추출한 원시 관절 좌표는 프레임 간 노이즈가 있어, 3D 아바타에
  그대로 입히면 부자연스러운 움직임이 나올 수 있습니다. 프론트엔드에서
  스무딩(slerp)과 관절 각도 제한, visibility 필터링으로 완화하고 있습니다.

---

## Render Free Tier 배포

`render.yaml` Blueprint 기준 최적값이 들어 있습니다.

```bash
# 대시보드 → New → Blueprint → 이 저장소 선택
```

핵심 조정 (Free 512MB/shared CPU 기준):

- `STORE_BACKEND=memory`, `TMP_DIR=/tmp` — 외부 DB/디스크 없이 동작 (슬립 시 잡 소멸)
- `MAX_FRAMES=300` (10초), `YTDL_MAX_HEIGHT=720` progressive mp4 — 디코딩/RAM 부하 절감
- `ENABLE_HANDS=0`, `MAX_POSES=1` — 2차 모델/다인 검출 OFF
- 단일 워커 (`--workers 1`), `UVICORN_RELOAD` 비활성화, `/health` 헬스체크
- 쿠키 필요시 대시보드에서 `COOKIES_B64` Secret 추가 (파일 마운트 불필요)

제약:

- 슬립(15분 무활동) 시 진행 중 잡은 소멸. 처리 중에는 헬스체크가 느려질 수 있어
  동시 1잡 권장.
- 영속 저장 필요시 유료 디스크 + `S3_*` 설정으로 교체.