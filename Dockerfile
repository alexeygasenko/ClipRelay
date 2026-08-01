FROM node:24-alpine AS frontend

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci

COPY frontend/index.html frontend/vite.config.js ./frontend/
COPY frontend/src ./frontend/src
RUN cd frontend && npm run build

FROM denoland/deno:bin-2.5.6 AS deno

FROM python:3.13-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python -m venv /opt/spotify-player \
    && /opt/spotify-player/bin/pip install --no-cache-dir \
        librespot==0.0.10 protobuf==3.20.1

ENV SPOTIFY_PLAYER_PYTHON=/opt/spotify-player/bin/python

COPY app ./app
COPY --from=frontend /build/app/static/frontend ./app/static/frontend

RUN useradd --create-home app \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app

CMD ["python", "-m", "app.main"]
