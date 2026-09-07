FROM python:3.11-slim

# Render Free Tier용 최소 시스템 의존성.
# - opencv-headless는 libglib만 필요 (libgl 불필요)
# - ffmpeg apt 패키지 제거: 다운로드는 progressive mp4(merge 불필요),
#   오디오는 imageio-ffmpeg 동봉 바이너리 사용
# - Deno: yt-dlp n-challenge(ejs) 실행용. 없으면 유튜브가 403으로 차단.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 curl unzip \
 && rm -rf /var/lib/apt/lists/*
ARG DENO_VERSION=2.1.4
RUN curl -fsSL -o /tmp/deno.zip https://dl.deno.land/release/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip \
 && unzip -q /tmp/deno.zip -d /usr/local/bin \
 && rm /tmp/deno.zip \
 && deno --version

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py ./
COPY frontend ./frontend
RUN mkdir -p tmp

ENV HOST=0.0.0.0 PORT=8000 TMP_DIR=/srv/tmp PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
# Render가 $PORT를 주입하므로 shell form으로 확장. Free 512MB 기준 단일 워커.
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
