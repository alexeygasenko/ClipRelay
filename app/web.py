from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import math
import mimetypes
import re
import secrets
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from zipfile import ZIP_DEFLATED, ZipFile

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import FormData, UploadFile
from starlette.middleware.sessions import SessionMiddleware

from app.config import Config, TelegramChannel
from app.media_sources import detect_media_platform
from app.security import safe_redirect_target, verify_bootstrap_credentials
from app.service import (
    SpotifyTrack,
    TikTokToTelegram,
    Video,
    YouTubeVideo,
    is_tiktok_video_url,
)
from app.storage import User
from app.web_passwords import check_password_hash, generate_password_hash
from app.web_payloads import (
    SERVICE_META,
    channel_payload,
    spotify_payload,
    user_payload,
    video_payload,
    youtube_payload,
)

LOGGER = logging.getLogger(__name__)
JOB_TTL_SECONDS = 6 * 60 * 60
MAX_REQUEST_BYTES = 6 * 1024 * 1024
STATIC_DIRECTORY = Path(__file__).parent / "static"
SPA_DIRECTORY = STATIC_DIRECTORY / "frontend"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


def _secure_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", ascii_value).strip("._")
    return safe[:180]


@dataclass
class PreparedJob:
    job_id: str
    video: Video
    paths: tuple[Path, ...]
    created_at: float
    selected_chat_id: str
    user_id: int = 1

    @property
    def path(self) -> Path:
        if not self.paths:
            raise _not_found()
        return self.paths[0]


@dataclass
class YouTubeJob:
    job_id: str
    video: YouTubeVideo
    path: Path | None
    created_at: float
    user_id: int = 1


class JobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, PreparedJob] = {}
        self.lock = threading.Lock()

    def add(
        self,
        video: Video,
        path: Path | tuple[Path, ...],
        selected_chat_id: str,
        user_id: int = 1,
    ) -> PreparedJob:
        paths = (path,) if isinstance(path, Path) else tuple(path)
        job = PreparedJob(
            uuid.uuid4().hex,
            video,
            paths,
            time.time(),
            selected_chat_id,
            user_id,
        )
        with self.lock:
            self._cleanup()
            self.jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> PreparedJob:
        with self.lock:
            self._cleanup()
            job = self.jobs.get(job_id)
        if not job:
            raise _not_found()
        return job

    def remove(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.pop(job_id, None)
        if job:
            for path in job.paths:
                path.unlink(missing_ok=True)

    def _cleanup(self) -> None:
        expired = [
            job_id
            for job_id, job in self.jobs.items()
            if time.time() - job.created_at > JOB_TTL_SECONDS
        ]
        for job_id in expired:
            job = self.jobs.pop(job_id)
            for path in job.paths:
                path.unlink(missing_ok=True)


class YouTubeJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, YouTubeJob] = {}
        self.lock = threading.Lock()

    def add(self, video: YouTubeVideo, user_id: int = 1) -> YouTubeJob:
        job = YouTubeJob(uuid.uuid4().hex, video, None, time.time(), user_id)
        with self.lock:
            self._cleanup()
            self.jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> YouTubeJob:
        with self.lock:
            self._cleanup()
            job = self.jobs.get(job_id)
        if not job:
            raise _not_found()
        return job

    def set_path(self, job_id: str, path: Path) -> None:
        with self.lock:
            self.jobs[job_id].path = path

    def remove(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.pop(job_id, None)
        if job and job.path:
            job.path.unlink(missing_ok=True)

    def _cleanup(self) -> None:
        expired = [
            job_id
            for job_id, job in self.jobs.items()
            if time.time() - job.created_at > JOB_TTL_SECONDS
        ]
        for job_id in expired:
            job = self.jobs.pop(job_id)
            if job.path:
                job.path.unlink(missing_ok=True)


def create_app(config: Config, service: TikTokToTelegram) -> FastAPI:
    """Build the FastAPI web application consumed by the Vue SPA."""
    app = FastAPI(
        title="ClipRelay",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    auth_storage = getattr(service, "storage", None)
    auth_supported = all(
        hasattr(auth_storage, name)
        for name in ("get_user", "get_user_by_username", "create_user")
    )
    if auth_supported:
        secret = auth_storage.setting("session_secret", "", 1)
        if not secret:
            secret = secrets.token_hex(32)
            auth_storage.set_setting("session_secret", secret, user_id=1)
    else:
        secret = config.web_password or secrets.token_hex(32)

    jobs = JobStore()
    youtube_jobs = YouTubeJobStore()
    fallback_telegram_channels = config.telegram_channels or (
        TelegramChannel(config.telegram_chat_id, config.telegram_chat_id),
    )

    def route_path(name: str, **params: object) -> str:
        return str(app.url_path_for(name, **{key: str(value) for key, value in params.items()}))

    def with_query(path: str, **params: object) -> str:
        values = {
            key: str(value)
            for key, value in params.items()
            if value is not None and str(value) != ""
        }
        return f"{path}?{urlencode(values)}" if values else path

    def render_spa() -> Response:
        index_file = SPA_DIRECTORY / "index.html"
        if not index_file.is_file():
            return PlainTextResponse(
                "Vue frontend is not built. Run `npm install && npm run build` in frontend/.",
                status_code=503,
            )
        return FileResponse(
            index_file,
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    def wants_json(request: Request) -> bool:
        content_type = request.headers.get("content-type", "").lower()
        accept = request.headers.get("accept", "").lower()
        return (
            request.headers.get("X-Requested-With") == "fetch"
            or content_type.startswith("application/json")
            or ("application/json" in accept and "text/html" not in accept)
        )

    def payload(request: Request) -> dict | FormData:
        return getattr(request.state, "payload", {})

    def request_value(request: Request, name: str, default: str = "") -> str:
        value = payload(request).get(name, default)
        if value is None or isinstance(value, UploadFile):
            return default
        return str(value)

    def request_optional_value(request: Request, name: str) -> str | None:
        values = payload(request)
        if name not in values or values.get(name) is None:
            return None
        value = values.get(name)
        return None if isinstance(value, UploadFile) else str(value)

    def request_bool(request: Request, name: str) -> bool:
        value = payload(request).get(name, False)
        return value if isinstance(value, bool) else str(value).lower() in {
            "1",
            "true",
            "on",
            "yes",
        }

    def request_list(request: Request, name: str) -> list[str]:
        values = payload(request)
        if isinstance(values, FormData):
            raw_values = values.getlist(name)
        else:
            raw = values.get(name, [])
            raw_values = raw if isinstance(raw, list) else [raw]
        return [str(item) for item in raw_values if not isinstance(item, UploadFile)]

    def json_error(error: Exception | str, status: int = 400) -> JSONResponse:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=status)

    def success_response(
        *,
        redirect_to: str | None = None,
        status: int = 200,
        **response_payload: object,
    ) -> JSONResponse:
        body: dict[str, object] = {"ok": True, **response_payload}
        if redirect_to:
            body["redirect"] = redirect_to
        return JSONResponse(body, status_code=status)

    def current_user(request: Request) -> User | None:
        if not auth_supported:
            return None
        user_id = request.session.get("user_id")
        if not user_id:
            return None
        return auth_storage.get_user(int(user_id))

    def login_user(request: Request, user: User) -> None:
        request.session.clear()
        request.session["user_id"] = user.id

    def active_user_id(request: Request) -> int:
        user = getattr(request.state, "current_user", None)
        return user.id if user else 1

    def settings_user_id(request: Request) -> int:
        requested = request_value(request, "settings_user_id")
        user = getattr(request.state, "current_user", None)
        if requested and user and user.is_admin:
            try:
                return int(requested)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Некорректный ID пользователя",
                ) from None
        return active_user_id(request)

    def service_permissions(
        request: Request,
        user_id: int | None = None,
    ) -> dict[str, bool]:
        if not auth_supported:
            return {name: True for name in SERVICE_META}
        user = auth_storage.get_user(user_id or active_user_id(request))
        if not user or user.is_disabled:
            return {name: False for name in SERVICE_META}
        return {
            name: bool(getattr(user, f"allow_{name}", False))
            for name in SERVICE_META
        }

    def require_service(
        request: Request,
        service_name: str,
        user_id: int | None = None,
    ) -> None:
        if not service_permissions(request, user_id).get(service_name, False):
            raise PermissionError(f"Сервис {service_name} отключён для пользователя")

    def with_user_arg(args: tuple, user_id: int) -> tuple:
        return (*args, user_id) if auth_supported else args

    def telegram_channels(
        request: Request,
        user_id: int | None = None,
    ) -> tuple[TelegramChannel, ...]:
        if hasattr(service, "telegram_channels"):
            return service.telegram_channels(
                *with_user_arg((), user_id or active_user_id(request))
            )
        return fallback_telegram_channels

    def validate_chat_id(
        request: Request,
        chat_id: str | None,
        user_id: int | None = None,
    ) -> str:
        channels = telegram_channels(request, user_id)
        if not channels:
            raise ValueError("Сначала добавьте Telegram-канал в настройках")
        selected = (chat_id or channels[0].chat_id).strip()
        if selected not in {channel.chat_id for channel in channels}:
            raise ValueError("Выбран неизвестный Telegram-канал")
        return selected

    def monitored_tiktok_channels(
        request: Request,
        user_id: int | None = None,
    ) -> tuple[str, ...]:
        if hasattr(service, "monitored_tiktok_channels"):
            return service.monitored_tiktok_channels(
                *with_user_arg((), user_id or active_user_id(request))
            )
        return config.tiktok_channels

    def poll_interval_seconds(request: Request, user_id: int | None = None) -> int:
        if hasattr(service, "poll_interval_seconds"):
            return service.poll_interval_seconds(
                *with_user_arg((), user_id or active_user_id(request))
            )
        return config.poll_interval_seconds

    def cookie_status(service_name: str, user_id: int) -> dict[str, object]:
        if hasattr(service, "cookie_status"):
            return service.cookie_status(
                *with_user_arg((service_name,), user_id)
            )
        return {
            "uploaded": False,
            "valid": False,
            "reason": "missing",
            "cookie_count": 0,
        }

    def bootstrap_payload(request: Request) -> dict:
        user = current_user(request)
        if user and user.is_disabled:
            request.session.clear()
            user = None
        admin = auth_storage.get_user(1) if auth_supported else None
        setup_required = bool(
            admin and (admin.must_set_password or not admin.password_hash)
        )
        user_id = user.id if user else 1
        return {
            "auth_supported": auth_supported,
            "authenticated": not auth_supported or bool(user),
            "setup_required": setup_required,
            "setup_credentials_required": setup_required,
            "user": user_payload(user),
            "permissions": (
                service_permissions(request, user_id)
                if not auth_supported or user
                else {name: False for name in SERVICE_META}
            ),
            "telegram_channels": (
                [
                    channel_payload(channel)
                    for channel in telegram_channels(request, user_id)
                ]
                if not auth_supported or user
                else []
            ),
            "services": [
                {"id": name, **metadata}
                for name, metadata in SERVICE_META.items()
            ],
        }

    def settings_payload(request: Request, user_id: int) -> dict:
        settings_user = auth_storage.get_user(user_id) if auth_supported else None
        permissions = service_permissions(request, user_id)
        return {
            "settings_user": user_payload(settings_user),
            "admin_mode": bool(
                settings_user and settings_user.id != active_user_id(request)
            ),
            "permissions": permissions,
            "telegram_channels": [
                channel_payload(channel)
                for channel in telegram_channels(request, user_id)
            ],
            "monitored_tiktok_channels": list(
                monitored_tiktok_channels(request, user_id)
            ),
            "poll_interval_seconds": poll_interval_seconds(request, user_id),
            "cookie_services": [
                {
                    "id": name,
                    "name": SERVICE_META[name]["label"],
                    **SERVICE_META[name],
                    "cookies": cookie_status(name, user_id),
                }
                for name in SERVICE_META
                if permissions.get(name, False)
            ],
        }

    def settings_result(
        request: Request,
        user_id: int,
        event: str,
        **extra: object,
    ) -> Response:
        if wants_json(request):
            return success_response(
                event=event,
                settings=settings_payload(request, user_id),
                channels=[
                    {"chat_id": channel.chat_id}
                    for channel in telegram_channels(request, user_id)
                ],
                **extra,
            )
        if user_id != active_user_id(request):
            destination = with_query(
                route_path("settings"),
                user_id=user_id,
                settings=event,
                **extra,
            )
        else:
            destination = with_query(route_path("settings"), settings=event, **extra)
        return RedirectResponse(destination, status_code=302)

    def settings_failure(
        request: Request,
        error: Exception,
        status: int = 400,
    ) -> Response:
        if wants_json(request):
            return json_error(error, status)
        return PlainTextResponse(str(error), status_code=status)

    def prepared_job_payload(
        request: Request,
        job: PreparedJob,
        selected_chat_id: str | None = None,
    ) -> dict:
        all_previews = [
            route_path(
                "preview_item",
                job_id=job.job_id,
                item_index=index,
            )
            for index in range(len(job.paths))
        ]
        post = video_payload(job.video)
        destination = selected_chat_id or job.selected_chat_id
        return {
            "job_id": job.job_id,
            "post": post,
            "media": post,
            "media_type": job.video.media_type,
            "preview_url": all_previews[0] if all_previews else None,
            "preview_urls": (
                all_previews if job.video.media_type == "image" else []
            ),
            # Text posts are downloaded as a UTF-8 .txt attachment by this route.
            "download_url": route_path("media_video", job_id=job.job_id),
            "post_url": with_query(
                route_path("media_post", job_id=job.job_id),
                chat_id=destination,
            ),
            "send_url": route_path("send", job_id=job.job_id),
            "cancel_url": route_path("cancel", job_id=job.job_id),
            "selected_chat_id": destination,
            "telegram_channels": [
                channel_payload(channel)
                for channel in telegram_channels(request, job.user_id)
            ],
        }

    def youtube_job_payload(request: Request, job: YouTubeJob) -> dict:
        return {
            "job_id": job.job_id,
            "video": {
                **youtube_payload(job.video),
                "duration": int(job.video.duration or 0),
            },
            "thumbnail_download_url": route_path(
                "youtube_thumbnail", job_id=job.job_id
            ),
            "video_download_url": route_path(
                "youtube_video", job_id=job.job_id
            ),
            "post_url": route_path("youtube_post", job_id=job.job_id),
            "send_url": route_path("youtube_send", job_id=job.job_id),
            "telegram_channels": [
                channel_payload(channel)
                for channel in telegram_channels(request, job.user_id)
            ],
        }

    def require_admin(request: Request) -> User:
        user = getattr(request.state, "current_user", None)
        if not user or not user.is_admin:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    return json_error("Размер запроса превышает 6 МБ", 413)
            except ValueError:
                return json_error("Некорректный Content-Length", 400)

        request.state.payload = {}
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            content_type = request.headers.get("content-type", "").lower()
            try:
                if content_type.startswith("application/json"):
                    body = await request.body()
                    request.state.payload = json.loads(body) if body else {}
                    if not isinstance(request.state.payload, dict):
                        return json_error("JSON-запрос должен быть объектом", 400)
                elif (
                    content_type.startswith("application/x-www-form-urlencoded")
                    or content_type.startswith("multipart/form-data")
                ):
                    request.state.payload = await request.form()
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                return json_error("Не удалось разобрать тело запроса", 400)

        public_path = request.url.path in {
            "/api/bootstrap",
            "/login",
            "/logout",
            "/register",
            "/setup-admin",
        } or request.url.path.startswith(("/assets/", "/static/"))

        if auth_supported:
            user = current_user(request)
            request.state.current_user = user
            if not public_path:
                if not user or user.is_disabled:
                    request.session.clear()
                    if request.url.path.startswith("/api/") or wants_json(request):
                        return json_error("Требуется авторизация", 401)
                    destination = with_query(
                        route_path("login"),
                        next=request.url.path
                        + (f"?{request.url.query}" if request.url.query else ""),
                    )
                    return RedirectResponse(destination, status_code=302)
                if user.must_set_password or not user.password_hash:
                    if request.url.path.startswith("/api/") or wants_json(request):
                        return json_error(
                            "Требуется завершить первичную настройку",
                            403,
                        )
                    return RedirectResponse(
                        route_path("setup_admin_password"),
                        status_code=302,
                    )
        elif config.web_username and config.web_password:
            authorization = request.headers.get("authorization", "")
            username = password = ""
            if authorization.lower().startswith("basic "):
                try:
                    decoded = base64.b64decode(
                        authorization.split(None, 1)[1],
                        validate=True,
                    ).decode("utf-8")
                    username, password = decoded.split(":", 1)
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    pass
            valid = hmac.compare_digest(username, config.web_username) & hmac.compare_digest(
                password, config.web_password
            )
            if not valid:
                return PlainTextResponse(
                    "Требуется авторизация",
                    status_code=401,
                    headers={
                        "WWW-Authenticate": 'Basic realm="TikTok to Telegram"'
                    },
                )
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        if request.url.path.startswith("/api/") or request.url.path in {
            "/login",
            "/register",
            "/setup-admin",
        }:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # SessionMiddleware is registered last so request.session is available to
    # request_context, which is immediately inside it.
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        same_site="strict",
        https_only=bool(
            getattr(config, "web_https", False)
            or getattr(config, "session_cookie_secure", False)
        ),
    )

    assets_directory = SPA_DIRECTORY / "assets"
    if assets_directory.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_directory), name="assets")
    if STATIC_DIRECTORY.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=STATIC_DIRECTORY),
            name="static",
        )

    @app.get("/api/bootstrap", name="api_bootstrap")
    def api_bootstrap(request: Request) -> dict:
        return bootstrap_payload(request)

    @app.get("/login", name="login")
    def login(request: Request) -> Response:
        if not auth_supported:
            return RedirectResponse(route_path("index"), status_code=302)
        admin = auth_storage.get_user(1)
        if admin and (admin.must_set_password or not admin.password_hash):
            return RedirectResponse(
                route_path("setup_admin_password"),
                status_code=302,
            )
        return render_spa()

    @app.post("/login", name="login_post")
    def login_post(request: Request) -> Response:
        if not auth_supported:
            destination = route_path("index")
            return (
                success_response(redirect_to=destination)
                if wants_json(request)
                else RedirectResponse(destination, status_code=302)
            )
        username = request_value(request, "username").strip()
        password = request_value(request, "password")
        user = auth_storage.get_user_by_username(username)
        if (
            not user
            or user.is_disabled
            or not user.password_hash
            or not check_password_hash(user.password_hash, password)
        ):
            return (
                json_error("Неверный логин или пароль", 401)
                if wants_json(request)
                else PlainTextResponse(
                    "Неверный логин или пароль",
                    status_code=401,
                )
            )
        login_user(request, user)
        if user.must_set_password:
            destination = route_path("setup_admin_password")
        else:
            destination = safe_redirect_target(
                request_value(request, "next"),
                str(request.base_url),
            ) or route_path("index")
        return (
            success_response(redirect_to=destination)
            if wants_json(request)
            else RedirectResponse(destination, status_code=302)
        )

    @app.api_route(
        "/setup-admin",
        methods=["GET", "POST"],
        name="setup_admin_password",
    )
    def setup_admin_password(request: Request) -> Response:
        if not auth_supported:
            return RedirectResponse(route_path("index"), status_code=302)
        admin = auth_storage.get_user(1)
        if not admin:
            raise _not_found()
        logged_user = current_user(request)
        if admin.password_hash and not (
            logged_user and logged_user.id == admin.id
        ):
            return RedirectResponse(route_path("login"), status_code=302)
        if request.method == "GET":
            return render_spa()

        first_setup = bool(admin.must_set_password or not admin.password_hash)
        if first_setup:
            configured_username = (
                getattr(config, "setup_username", None) or config.web_username
            )
            configured_password = (
                getattr(config, "setup_password", None) or config.web_password
            )
            valid_bootstrap = verify_bootstrap_credentials(
                configured_username,
                configured_password,
                request_value(request, "setup_username"),
                request_value(request, "setup_password"),
            )
            if not valid_bootstrap:
                return json_error(
                    "Для первичной настройки нужны web.username и web.password из config.yaml",
                    403,
                ) if wants_json(request) else PlainTextResponse(
                    "Для первичной настройки нужны web.username и web.password из config.yaml",
                    status_code=403,
                )
        password = request_value(request, "password")
        confirm = request_value(request, "confirm_password")
        if len(password) < 8:
            return json_error(
                "Пароль должен быть не короче 8 символов"
            ) if wants_json(request) else PlainTextResponse(
                "Пароль должен быть не короче 8 символов",
                status_code=400,
            )
        if password != confirm:
            return json_error("Пароли не совпадают") if wants_json(
                request
            ) else PlainTextResponse("Пароли не совпадают", status_code=400)
        auth_storage.set_user_password(admin.id, generate_password_hash(password))
        updated_admin = auth_storage.get_user(admin.id)
        assert updated_admin is not None
        login_user(request, updated_admin)
        destination = route_path("index")
        return (
            success_response(redirect_to=destination)
            if wants_json(request)
            else RedirectResponse(destination, status_code=302)
        )

    @app.api_route("/register", methods=["GET", "POST"], name="register")
    def register(request: Request) -> Response:
        if not auth_supported:
            return RedirectResponse(route_path("index"), status_code=302)
        admin = auth_storage.get_user(1)
        if admin and (admin.must_set_password or not admin.password_hash):
            if wants_json(request):
                return json_error(
                    "Сначала завершите первичную настройку администратора",
                    403,
                )
            return RedirectResponse(
                route_path("setup_admin_password"),
                status_code=302,
            )
        if request.method == "GET":
            return render_spa()
        username = request_value(request, "username").strip()
        password = request_value(request, "password")
        confirm = request_value(request, "confirm_password")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            error = (
                "Логин: 3-32 символа, латиница, цифры, точка, "
                "дефис или подчёркивание"
            )
            return json_error(error) if wants_json(request) else PlainTextResponse(
                error,
                status_code=400,
            )
        if len(password) < 8:
            return json_error(
                "Пароль должен быть не короче 8 символов"
            ) if wants_json(request) else PlainTextResponse(
                "Пароль должен быть не короче 8 символов",
                status_code=400,
            )
        if password != confirm:
            return json_error("Пароли не совпадают") if wants_json(
                request
            ) else PlainTextResponse("Пароли не совпадают", status_code=400)
        try:
            user = auth_storage.create_user(
                username,
                generate_password_hash(password),
            )
        except Exception:
            return json_error("Логин уже занят") if wants_json(
                request
            ) else PlainTextResponse("Логин уже занят", status_code=400)
        login_user(request, user)
        destination = route_path("index")
        return (
            success_response(redirect_to=destination)
            if wants_json(request)
            else RedirectResponse(destination, status_code=302)
        )

    @app.post("/logout", name="logout")
    def logout(request: Request) -> Response:
        request.session.clear()
        destination = route_path("login")
        return (
            success_response(redirect_to=destination)
            if wants_json(request)
            else RedirectResponse(destination, status_code=302)
        )

    @app.get("/admin/users", name="admin_users")
    def admin_users(request: Request) -> Response:
        require_admin(request)
        return render_spa()

    @app.get("/api/admin/users", name="api_admin_users")
    def api_admin_users(request: Request, page: int = 1) -> dict:
        require_admin(request)
        page = max(1, page)
        per_page = 20
        total = auth_storage.user_count()
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)
        users = auth_storage.users(limit=per_page, offset=(page - 1) * per_page)
        return {
            "users": [user_payload(user) for user in users],
            "page": page,
            "pages": pages,
            "total": total,
        }

    @app.get("/admin/users/{user_id}", name="admin_user_detail")
    def admin_user_detail(request: Request, user_id: int) -> Response:
        require_admin(request)
        if not auth_storage.get_user(user_id):
            raise _not_found()
        return render_spa()

    @app.get("/api/admin/users/{user_id}", name="api_admin_user_detail")
    def api_admin_user_detail(request: Request, user_id: int) -> dict:
        require_admin(request)
        user = auth_storage.get_user(user_id)
        if not user:
            raise _not_found()
        return {
            "user": user_payload(user),
            "services": [
                {"id": name, **metadata}
                for name, metadata in SERVICE_META.items()
            ],
        }

    @app.post("/admin/users/{user_id}", name="admin_user_update")
    def admin_user_update(request: Request, user_id: int) -> Response:
        admin = require_admin(request)
        user = auth_storage.get_user(user_id)
        if not user:
            raise _not_found()
        username = request_value(request, "username", user.username).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            return json_error(
                "Логин: 3-32 символа, латиница, цифры, точка, дефис или подчёркивание"
            )
        existing_user = auth_storage.get_user_by_username(username)
        if existing_user and existing_user.id != user_id:
            return json_error("Логин уже занят", 409)
        is_admin = request_bool(request, "is_admin")
        is_disabled = request_bool(request, "is_disabled")
        if user_id == admin.id:
            is_admin = True
            is_disabled = False
        auth_storage.update_user(
            user_id,
            username=username,
            is_admin=is_admin,
            is_disabled=is_disabled,
            **{
                f"allow_{name}": request_bool(request, f"allow_{name}")
                for name in SERVICE_META
            },
        )
        if wants_json(request):
            return success_response(
                user=user_payload(auth_storage.get_user(user_id))
            )
        return RedirectResponse(
            with_query(
                route_path("admin_user_detail", user_id=user_id),
                saved="1",
            ),
            status_code=302,
        )

    @app.post(
        "/admin/users/{user_id}/toggle-disabled",
        name="admin_user_toggle_disabled",
    )
    def admin_user_toggle_disabled(request: Request, user_id: int) -> Response:
        admin = require_admin(request)
        user = auth_storage.get_user(user_id)
        if not user:
            raise _not_found()
        if user.id == admin.id:
            raise HTTPException(status_code=400, detail="Bad request")
        auth_storage.update_user(
            user_id,
            username=user.username,
            is_admin=user.is_admin,
            is_disabled=not user.is_disabled,
            **{
                f"allow_{name}": bool(
                    getattr(user, f"allow_{name}", False)
                )
                for name in SERVICE_META
            },
        )
        if wants_json(request):
            return success_response(
                user=user_payload(auth_storage.get_user(user_id))
            )
        return RedirectResponse(route_path("admin_users"), status_code=302)

    @app.get(
        "/admin/users/{user_id}/settings",
        name="admin_user_settings",
    )
    def admin_user_settings(request: Request, user_id: int) -> Response:
        require_admin(request)
        if not auth_storage.get_user(user_id):
            raise _not_found()
        return RedirectResponse(
            with_query(route_path("settings"), user_id=user_id),
            status_code=302,
        )

    @app.get("/", name="index")
    def index() -> Response:
        return render_spa()

    @app.get("/settings", name="settings")
    def settings() -> Response:
        return render_spa()

    @app.get("/api/settings", name="api_settings")
    def api_settings(request: Request, user_id: int | None = None) -> dict:
        selected_user_id = active_user_id(request)
        if user_id is not None:
            current = getattr(request.state, "current_user", None)
            if not current or not current.is_admin:
                raise HTTPException(status_code=403, detail="Forbidden")
            if not auth_storage.get_user(user_id):
                raise _not_found()
            selected_user_id = user_id
        return settings_payload(request, selected_user_id)

    @app.post("/settings/telegram", name="add_telegram_channel")
    def add_telegram_channel(request: Request) -> Response:
        user_id = settings_user_id(request)
        try:
            service.add_telegram_destination(
                *with_user_arg(
                    (
                        request_value(request, "name"),
                        request_value(request, "chat_id"),
                        request_value(request, "bot_token"),
                    ),
                    user_id,
                )
            )
            return settings_result(request, user_id, "telegram-added")
        except Exception as error:
            LOGGER.warning(
                "Failed to add Telegram destination: %s",
                type(error).__name__,
            )
            return settings_failure(request, error)

    @app.post(
        "/settings/telegram/discover",
        name="discover_telegram_channels",
    )
    def discover_telegram_channels(request: Request) -> Response:
        user_id = settings_user_id(request)
        try:
            found = service.discover_telegram_destinations(
                *with_user_arg(
                    (request_value(request, "bot_token"),),
                    user_id,
                )
            )
            return settings_result(
                request,
                user_id,
                "telegram-discovered",
                found=len(found),
            )
        except Exception as error:
            LOGGER.warning(
                "Failed to discover Telegram destinations: %s",
                type(error).__name__,
            )
            return settings_failure(request, error)

    @app.post(
        "/settings/telegram/delete",
        name="delete_telegram_channel",
    )
    def delete_telegram_channel(request: Request) -> Response:
        user_id = settings_user_id(request)
        try:
            service.delete_telegram_destination(
                *with_user_arg(
                    (request_value(request, "chat_id"),),
                    user_id,
                )
            )
            return settings_result(request, user_id, "telegram-deleted")
        except Exception as error:
            return settings_failure(request, error)

    @app.post("/settings/telegram/move", name="move_telegram_channel")
    def move_telegram_channel(request: Request) -> Response:
        user_id = settings_user_id(request)
        try:
            service.move_telegram_destination(
                *with_user_arg(
                    (
                        request_value(request, "chat_id"),
                        request_value(request, "direction"),
                    ),
                    user_id,
                )
            )
            return settings_result(request, user_id, "telegram-moved")
        except Exception as error:
            return settings_failure(request, error)

    @app.post(
        "/settings/tiktok/monitor",
        name="add_monitored_tiktok_channel",
    )
    def add_monitored_tiktok_channel(request: Request) -> Response:
        user_id = settings_user_id(request)
        try:
            require_service(request, "tiktok", user_id)
            service.add_monitored_tiktok_channel(
                *with_user_arg(
                    (request_value(request, "channel"),),
                    user_id,
                )
            )
            return settings_result(request, user_id, "tiktok-monitor-added")
        except Exception as error:
            return settings_failure(request, error)

    @app.post(
        "/settings/tiktok/monitor/delete",
        name="delete_monitored_tiktok_channel",
    )
    def delete_monitored_tiktok_channel(request: Request) -> Response:
        user_id = settings_user_id(request)
        try:
            require_service(request, "tiktok", user_id)
            service.delete_monitored_tiktok_channel(
                *with_user_arg(
                    (request_value(request, "channel"),),
                    user_id,
                )
            )
            return settings_result(request, user_id, "tiktok-monitor-deleted")
        except Exception as error:
            return settings_failure(request, error)

    @app.post(
        "/settings/tiktok/interval",
        name="update_tiktok_interval",
    )
    def update_tiktok_interval(request: Request) -> Response:
        user_id = settings_user_id(request)
        try:
            require_service(request, "tiktok", user_id)
            service.set_poll_interval_seconds(
                *with_user_arg(
                    (request_value(request, "poll_interval_seconds"),),
                    user_id,
                )
            )
            return settings_result(request, user_id, "tiktok-interval-updated")
        except Exception as error:
            return settings_failure(request, error)

    @app.post("/settings/cookies/{service_name}", name="update_cookies")
    def update_cookies(request: Request, service_name: str) -> Response:
        user_id = settings_user_id(request)
        try:
            require_service(request, service_name, user_id)
            uploaded = payload(request).get("cookies_file")
            if (
                not isinstance(uploaded, UploadFile)
                or not uploaded.filename
            ):
                raise ValueError("Выберите cookies.txt")
            content = uploaded.file.read()
            service.update_cookies(
                *with_user_arg((service_name, content), user_id)
            )
            return settings_result(
                request,
                user_id,
                f"{service_name}-cookies-updated",
            )
        except Exception as error:
            LOGGER.warning(
                "Failed to update %s cookies: %s",
                service_name,
                type(error).__name__,
            )
            return settings_failure(request, error)

    @app.post("/prepare", name="prepare")
    @app.post("/tiktok/prepare", name="tiktok_prepare")
    def prepare(request: Request) -> Response:
        user_id = active_user_id(request)
        media_url = request_value(request, "tiktok_url").strip()
        selected_chat_id = request_value(request, "chat_id")
        try:
            selected_chat_id = validate_chat_id(
                request,
                selected_chat_id,
                user_id,
            )
            platform = detect_media_platform(media_url)
            is_single_post = (
                platform != "tiktok" or is_tiktok_video_url(media_url)
            )
            if is_single_post:
                require_service(request, platform, user_id)
                video, paths = service.prepare_url(
                    *with_user_arg((media_url,), user_id)
                )
                job = jobs.add(
                    video,
                    paths,
                    selected_chat_id,
                    user_id,
                )
                job_payload = prepared_job_payload(
                    request,
                    job,
                    selected_chat_id,
                )
                if wants_json(request):
                    return success_response(job=job_payload)
                return RedirectResponse(
                    job_payload["post_url"],
                    status_code=302,
                )
            require_service(request, "tiktok", user_id)
            post_existing = request_bool(request, "post_existing")
            found, published = service.import_channel(
                *with_user_arg(
                    (
                        media_url,
                        post_existing,
                        selected_chat_id,
                    ),
                    user_id,
                )
            )
            if wants_json(request):
                return success_response(
                    channel=media_url,
                    found=found,
                    published=published,
                    post_existing=post_existing,
                )
            return RedirectResponse(
                with_query(
                    route_path("index"),
                    channel=media_url,
                    found=found,
                    published=published,
                ),
                status_code=302,
            )
        except Exception as error:
            LOGGER.exception("Failed to prepare URL %s", media_url)
            return (
                json_error(error)
                if wants_json(request)
                else PlainTextResponse(str(error), status_code=400)
            )

    @app.post("/media/info", name="media_info")
    def media_info(request: Request) -> Response:
        user_id = active_user_id(request)
        media_url = request_value(request, "media_url").strip()
        try:
            platform = detect_media_platform(media_url)
            require_service(request, platform, user_id)
            selected_chat_id = validate_chat_id(
                request,
                request_value(request, "chat_id"),
                user_id,
            )
            video, paths = service.prepare_url(
                *with_user_arg((media_url,), user_id)
            )
            job = jobs.add(video, paths, selected_chat_id, user_id)
            result = prepared_job_payload(request, job, selected_chat_id)
            return JSONResponse(
                {
                    **result,
                    "description": video.description,
                    "author": result["post"]["author"],
                    "video_download_url": result["download_url"],
                    "media_type": video.media_type,
                    "image_count": (
                        len(job.paths) if video.media_type == "image" else 0
                    ),
                }
            )
        except Exception as error:
            LOGGER.exception("Failed to inspect media URL %s", media_url)
            return JSONResponse({"error": str(error)}, status_code=400)

    @app.get("/media/video/{job_id}", name="media_video")
    def media_video(request: Request, job_id: str) -> Response:
        job = jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        base_name = (
            f"{_secure_filename(job.video.username) or 'post'}-"
            f"{_secure_filename(job.video.video_id) or 'media'}"
        )
        if not job.paths:
            text = (job.video.description or "").strip()
            canonical_url = (job.video.url or "").strip()
            if canonical_url and canonical_url not in text:
                text = f"{text}\n\n{canonical_url}".strip()
            return Response(
                text.encode("utf-8"),
                media_type="text/plain; charset=utf-8",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{base_name}.txt"'
                    )
                },
            )
        if len(job.paths) > 1:
            archive = BytesIO()
            with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
                for index, path in enumerate(job.paths, start=1):
                    zip_file.write(
                        path,
                        f"{job.video.video_id}-{index:02d}{path.suffix}",
                    )
            return Response(
                archive.getvalue(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{base_name}-media.zip"'
                    )
                },
            )
        return FileResponse(
            job.path,
            filename=f"{base_name}{job.path.suffix}",
        )

    @app.get("/media/post/{job_id}", name="media_post")
    def media_post(request: Request, job_id: str) -> Response:
        job = jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        return render_spa()

    @app.get("/api/media/jobs/{job_id}", name="api_media_job")
    def api_media_job(
        request: Request,
        job_id: str,
        chat_id: str | None = None,
    ) -> Response:
        job = jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        try:
            selected_chat_id = (
                validate_chat_id(request, chat_id, job.user_id)
                if chat_id
                else job.selected_chat_id
            )
        except ValueError as error:
            return json_error(error)
        return JSONResponse(
            prepared_job_payload(
                request,
                job,
                selected_chat_id,
            )
        )

    @app.post("/youtube/info", name="youtube_info")
    def youtube_info(request: Request) -> Response:
        user_id = active_user_id(request)
        youtube_url = request_value(request, "youtube_url").strip()
        try:
            require_service(request, "youtube", user_id)
            video = service.get_youtube_info(
                *with_user_arg((youtube_url,), user_id)
            )
            job = youtube_jobs.add(video, user_id)
            result = youtube_job_payload(request, job)
            return JSONResponse(
                {
                    **result,
                    "title": video.title,
                    "channel": video.channel,
                    "duration": video.duration,
                    "thumbnail_url": video.thumbnail_url,
                }
            )
        except Exception as error:
            LOGGER.exception("Failed to inspect YouTube URL %s", youtube_url)
            return JSONResponse({"error": str(error)}, status_code=400)

    @app.post("/spotify/info", name="spotify_info")
    def spotify_info(request: Request) -> Response:
        user_id = active_user_id(request)
        spotify_url = request_value(request, "spotify_url").strip()
        try:
            require_service(request, "spotify", user_id)
            track = service.get_spotify_info(
                *with_user_arg((spotify_url,), user_id)
            )
            selected_chat_id = request_value(request, "chat_id").strip()
            if selected_chat_id:
                selected_chat_id = validate_chat_id(
                    request,
                    selected_chat_id,
                    user_id,
                )
            return JSONResponse(
                {
                    "track": spotify_payload(track),
                    "track_id": track.track_id,
                    "title": track.title,
                    "artist": track.artist,
                    "thumbnail_url": track.thumbnail_url,
                    "audio_download_url": route_path(
                        "spotify_audio",
                        track_id=track.track_id,
                    ),
                    "post_url": with_query(
                        route_path(
                            "spotify_post",
                            track_id=track.track_id,
                        ),
                        chat_id=selected_chat_id,
                    ),
                }
            )
        except Exception as error:
            LOGGER.exception("Failed to inspect Spotify URL %s", spotify_url)
            return JSONResponse({"error": str(error)}, status_code=400)

    @app.get("/spotify/audio/{track_id}", name="spotify_audio")
    def spotify_audio(request: Request, track_id: str) -> Response:
        user_id = active_user_id(request)
        try:
            require_service(request, "spotify", user_id)
            track = service.get_spotify_info(
                *with_user_arg(
                    (f"https://open.spotify.com/track/{track_id}",),
                    user_id,
                )
            )
            path = service.download_spotify(
                *with_user_arg(
                    (track, f"spotify-{user_id}-{track.track_id}"),
                    user_id,
                )
            )
            filename = _secure_filename(track.title) or track.track_id
            return FileResponse(
                path,
                media_type="audio/mpeg",
                filename=f"{filename}.mp3",
            )
        except Exception as error:
            LOGGER.exception("Failed to download Spotify track %s", track_id)
            return JSONResponse({"error": str(error)}, status_code=400)

    @app.get("/spotify/post/{track_id}", name="spotify_post")
    def spotify_post(request: Request, track_id: str) -> Response:
        require_service(request, "spotify", active_user_id(request))
        return render_spa()

    @app.get("/api/spotify/tracks/{track_id}", name="api_spotify_track")
    def api_spotify_track(
        request: Request,
        track_id: str,
        chat_id: str = "",
    ) -> dict:
        user_id = active_user_id(request)
        require_service(request, "spotify", user_id)
        track = service.get_spotify_info(
            *with_user_arg(
                (f"https://open.spotify.com/track/{track_id}",),
                user_id,
            )
        )
        return {
            "track": spotify_payload(track),
            "selected_chat_id": chat_id,
            "audio_download_url": route_path(
                "spotify_audio",
                track_id=track.track_id,
            ),
            "send_url": route_path(
                "spotify_send",
                track_id=track.track_id,
            ),
            "telegram_channels": [
                channel_payload(channel)
                for channel in telegram_channels(request, user_id)
            ],
        }

    @app.post("/spotify/send/{track_id}", name="spotify_send")
    def spotify_send(request: Request, track_id: str) -> Response:
        user_id = active_user_id(request)
        before_text = request_value(request, "before_text")
        after_text = request_value(request, "after_text")
        caption_html = request_optional_value(request, "caption_html")
        selected_chat_id = request_value(request, "chat_id")
        track: SpotifyTrack | None = None
        try:
            require_service(request, "spotify", user_id)
            selected_chat_id = validate_chat_id(
                request,
                selected_chat_id,
                user_id,
            )
            track = service.get_spotify_info(
                *with_user_arg(
                    (f"https://open.spotify.com/track/{track_id}",),
                    user_id,
                )
            )
            path = service.download_spotify(
                *with_user_arg(
                    (track, f"spotify-{user_id}-{track.track_id}"),
                    user_id,
                )
            )
            service.publish_spotify(
                *with_user_arg(
                    (
                        track,
                        path,
                        before_text,
                        after_text,
                        selected_chat_id,
                        caption_html,
                    ),
                    user_id,
                )
            )
            destination = with_query(route_path("done"), source="spotify")
            return (
                success_response(redirect_to=destination)
                if wants_json(request)
                else RedirectResponse(destination, status_code=302)
            )
        except Exception as error:
            LOGGER.exception("Failed to publish Spotify track %s", track_id)
            return (
                json_error(error, 502 if track is not None else 400)
                if wants_json(request)
                else PlainTextResponse(
                    str(error),
                    status_code=502 if track is not None else 400,
                )
            )

    @app.get("/youtube/thumbnail/{job_id}", name="youtube_thumbnail")
    def youtube_thumbnail(request: Request, job_id: str) -> Response:
        job = youtube_jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        remote = requests.get(job.video.thumbnail_url, timeout=60)
        remote.raise_for_status()
        filename = _secure_filename(job.video.title) or "youtube-thumbnail"
        content_type = remote.headers.get("Content-Type", "image/jpeg")
        extension = ".webp" if "webp" in content_type else ".jpg"
        return Response(
            remote.content,
            media_type=content_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{filename}{extension}"'
                )
            },
        )

    @app.get("/youtube/video/{job_id}", name="youtube_video")
    def youtube_video(request: Request, job_id: str) -> Response:
        job = youtube_jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        if not job.path or not job.path.exists():
            path = service.download_youtube(
                *with_user_arg(
                    (job.video, f"youtube-{job.job_id}"),
                    job.user_id,
                )
            )
            youtube_jobs.set_path(job.job_id, path)
            job.path = path
        filename = _secure_filename(job.video.title) or "youtube-video"
        return FileResponse(
            job.path,
            filename=f"{filename}{job.path.suffix}",
        )

    @app.get("/youtube/post/{job_id}", name="youtube_post")
    def youtube_post(request: Request, job_id: str) -> Response:
        job = youtube_jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        return render_spa()

    @app.get("/api/youtube/jobs/{job_id}", name="api_youtube_job")
    def api_youtube_job(request: Request, job_id: str) -> dict:
        job = youtube_jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        return youtube_job_payload(request, job)

    @app.post("/youtube/send/{job_id}", name="youtube_send")
    def youtube_send(request: Request, job_id: str) -> Response:
        job = youtube_jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        before_text = request_value(request, "before_text")
        after_text = request_value(request, "after_text")
        caption_html = request_optional_value(request, "caption_html")
        selected_chat_id = request_value(request, "chat_id")
        try:
            selected_chat_id = validate_chat_id(
                request,
                selected_chat_id,
                job.user_id,
            )
            service.publish_youtube(
                *with_user_arg(
                    (
                        job.video,
                        before_text,
                        after_text,
                        selected_chat_id,
                        caption_html,
                    ),
                    job.user_id,
                )
            )
            youtube_jobs.remove(job_id)
            destination = with_query(route_path("done"), source="youtube")
            return (
                success_response(redirect_to=destination)
                if wants_json(request)
                else RedirectResponse(destination, status_code=302)
            )
        except Exception as error:
            LOGGER.exception("Failed to publish YouTube job %s", job_id)
            return (
                json_error(error, 502)
                if wants_json(request)
                else PlainTextResponse(str(error), status_code=502)
            )

    @app.get("/preview/{job_id}", name="preview")
    def preview(request: Request, job_id: str) -> Response:
        return preview_item(request, job_id, 0)

    @app.get(
        "/preview/{job_id}/{item_index}",
        name="preview_item",
    )
    def preview_item(
        request: Request,
        job_id: str,
        item_index: int,
    ) -> Response:
        job = jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        if item_index < 0 or item_index >= len(job.paths):
            raise _not_found()
        path = job.paths[item_index]
        media_type = mimetypes.guess_type(path.name)[0]
        if not media_type:
            media_type = (
                "image/jpeg"
                if job.video.media_type == "image"
                else "video/mp4"
            )
        return FileResponse(path, media_type=media_type)

    @app.post("/send/{job_id}", name="send")
    def send(request: Request, job_id: str) -> Response:
        job = jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        before_text = request_value(request, "before_text")
        quote_text = request_value(request, "quote_text")
        after_text = request_value(request, "after_text")
        caption_html = request_optional_value(request, "caption_html")
        options_present = (
            request_value(request, "caption_options_present") == "1"
        )
        include_author = (
            request_bool(request, "include_author")
            if options_present
            else True
        )
        include_description = (
            request_bool(request, "include_description")
            if options_present
            else True
        )
        selected_chat_id = (
            request_value(request, "chat_id").strip()
            or job.selected_chat_id
        )
        selected_image_indices: tuple[int, ...] | None = None
        publish_paths = job.paths
        try:
            selected_chat_id = validate_chat_id(
                request,
                selected_chat_id,
                job.user_id,
            )
            if (
                job.video.media_type == "image"
                and request_value(request, "image_selection_present") == "1"
            ):
                requested_indices = set(
                    request_list(request, "selected_image_indices")
                )
                selected_image_indices = tuple(
                    index
                    for index in range(len(job.paths))
                    if str(index) in requested_indices
                )
                if not selected_image_indices:
                    raise ValueError(
                        "Выберите хотя бы одно изображение для публикации"
                    )
                publish_paths = tuple(
                    job.paths[index] for index in selected_image_indices
                )
            service.publish(
                *with_user_arg(
                    (
                        job.video,
                        publish_paths,
                        quote_text,
                        before_text,
                        after_text,
                        selected_chat_id,
                        include_author,
                        include_description,
                        caption_html,
                    ),
                    job.user_id,
                )
            )
            if hasattr(service, "mark_processed"):
                service.mark_processed(
                    *with_user_arg((job.video,), job.user_id)
                )
            else:
                service.storage.mark(
                    *with_user_arg(
                        (job.video.video_id, job.video.username),
                        job.user_id,
                    )
                )
            jobs.remove(job_id)
            destination = route_path("done")
            return (
                success_response(redirect_to=destination)
                if wants_json(request)
                else RedirectResponse(destination, status_code=302)
            )
        except Exception as error:
            LOGGER.exception("Failed to publish prepared job %s", job_id)
            return (
                json_error(error, 502)
                if wants_json(request)
                else PlainTextResponse(str(error), status_code=502)
            )

    @app.post("/cancel/{job_id}", name="cancel")
    def cancel(request: Request, job_id: str) -> Response:
        job = jobs.get(job_id)
        if job.user_id != active_user_id(request):
            raise _not_found()
        jobs.remove(job_id)
        destination = route_path("index")
        return (
            success_response(redirect_to=destination)
            if wants_json(request)
            else RedirectResponse(destination, status_code=302)
        )

    @app.get("/done", name="done")
    def done() -> Response:
        return render_spa()

    return app
