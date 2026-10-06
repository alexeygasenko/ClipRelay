from __future__ import annotations

import html
import http.cookiejar
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
import yt_dlp

from app.config import Config, TelegramChannel
from app.media_sources import (
    MEDIA_SOURCE_REGISTRY,
    MediaSourcePost,
    detect_media_platform,
    fetch_reddit_post,
    fetch_twitter_syndication_post,
    is_reddit_url,
    is_twitter_url,
    parse_twitter_post,
    reddit_post_id_from_url,
    reddit_url_from_text,
    twitter_post_id_from_url,
    twitter_url_from_text,
    validate_reddit_url,
    validate_twitter_url,
)
from app.storage import Storage, User

LOGGER = logging.getLogger(__name__)
MAX_CAPTION_LENGTH = 1024
MAX_MESSAGE_LENGTH = 4096
MAX_VIDEO_BYTES = 49 * 1024 * 1024
STALE_DOWNLOAD_SECONDS = 24 * 60 * 60
YOUTUBE_AUTH_COOKIE_NAMES = {
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "LOGIN_INFO",
    "__Secure-1PSID",
    "__Secure-3PSID",
}
SERVICE_AUTH_COOKIE_NAMES = {
    "tiktok": frozenset({"sessionid", "sessionid_ss", "sid_tt"}),
    "instagram": frozenset({"sessionid"}),
    "youtube": frozenset(YOUTUBE_AUTH_COOKIE_NAMES),
    "spotify": frozenset({"sp_dc"}),
    "twitter": frozenset({"auth_token"}),
    "reddit": frozenset({"reddit_session", "token_v2"}),
}
INSTAGRAM_SHORTCODE_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
INSTAGRAM_GRAPHQL_DOC_ID = "8845758582119845"
INSTAGRAM_URL_IN_TEXT_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/[^\s<>]+",
    flags=re.IGNORECASE,
)
SPOTIFY_TRACK_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")
SPOTIFY_TRACK_URL_IN_TEXT_RE = re.compile(
    r"https?://(?:open\.)?spotify\.com/[^\s<>]+",
    flags=re.IGNORECASE,
)
TELEGRAM_DOWNLOAD_COMMAND_RE = re.compile(
    r"^/(?P<command>spotifysearch|spotify|instagram|twitter|reddit|x)"
    r"(?:@[A-Za-z0-9_]+)?(?:\s|$)",
    flags=re.IGNORECASE,
)
TELEGRAM_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TELEGRAM_ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "code",
    "i",
    "pre",
    "s",
    "tg-spoiler",
    "u",
}
TELEGRAM_TAG_ALIASES = {
    "strong": "b",
    "em": "i",
    "ins": "u",
    "strike": "s",
    "del": "s",
}


@dataclass(frozen=True)
class Video:
    video_id: str
    username: str
    description: str
    url: str
    timestamp: int
    platform: str = "tiktok"
    author_url: str = ""
    media_type: str = "video"


def processed_media_key(video: Video) -> str:
    if video.platform == "tiktok":
        return video.video_id
    return f"{video.platform}:{video.video_id}"


@dataclass(frozen=True)
class YouTubeVideo:
    video_id: str
    title: str
    url: str
    thumbnail_url: str
    duration: int
    channel: str


@dataclass(frozen=True)
class SpotifyTrack:
    track_id: str
    title: str
    url: str
    thumbnail_url: str
    artist: str = ""


class TelegramHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = TELEGRAM_TAG_ALIASES.get(tag, tag)
        if tag in {"div", "p"}:
            self._soft_break()
            return
        if tag == "br":
            self.parts.append("\n")
            return
        if normalized not in TELEGRAM_ALLOWED_TAGS:
            return
        if normalized == "a":
            href = next((value for name, value in attrs if name == "href"), "")
            if not href or not self._safe_href(href):
                return
            self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
        elif normalized == "blockquote":
            expandable = any(name == "expandable" for name, _ in attrs)
            self.parts.append("<blockquote expandable>" if expandable else "<blockquote>")
        else:
            self.parts.append(f"<{normalized}>")
        self.stack.append(normalized)

    def handle_endtag(self, tag: str) -> None:
        normalized = TELEGRAM_TAG_ALIASES.get(tag, tag)
        if tag in {"div", "p"}:
            self._soft_break()
            return
        if normalized not in TELEGRAM_ALLOWED_TAGS:
            return
        if normalized in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.parts.append(f"</{current}>")
                if current == normalized:
                    break

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data))

    def close_open_tags(self) -> None:
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")

    def _soft_break(self) -> None:
        text = "".join(self.parts)
        if text and not text.endswith("\n\n"):
            self.parts.append("\n\n" if not text.endswith("\n") else "\n")

    @staticmethod
    def _safe_href(href: str) -> bool:
        parsed = urlparse(href)
        return parsed.scheme in {"http", "https", "tg", "mailto"} and bool(
            parsed.netloc or parsed.scheme == "tg"
        )


def sanitize_telegram_html(
    value: str,
    max_length: int = MAX_CAPTION_LENGTH,
) -> str:
    parser = TelegramHTMLSanitizer()
    parser.feed(value or "")
    parser.close_open_tags()
    text = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
    if len(text) > max_length:
        raise ValueError(
            f"Текст слишком длинный: {len(text)} символов HTML при лимите {max_length}."
        )
    return text


def normalize_channel(channel: str) -> tuple[str, str]:
    channel = channel.strip()
    if "tiktok.com" in channel:
        path = urlparse(channel).path.rstrip("/")
        username = path.split("/")[-1].lstrip("@")
    else:
        username = channel.rstrip("/").split("/")[-1].lstrip("@")
    if not re.fullmatch(r"[\w.]+", username):
        raise ValueError(f"Invalid TikTok channel: {channel}")
    return username, f"https://www.tiktok.com/@{username}"


def validate_tiktok_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        host == "tiktok.com" or host.endswith(".tiktok.com")
    ):
        raise ValueError("Нужна полная ссылка на видео с домена tiktok.com")
    return url


def validate_instagram_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        host == "instagram.com" or host.endswith(".instagram.com")
    ):
        raise ValueError("Нужна полная ссылка на пост Instagram")
    return url


def is_instagram_url(url: str) -> bool:
    try:
        validate_instagram_url(url)
        return True
    except ValueError:
        return False


def instagram_url_from_text(text: str) -> str:
    for match in INSTAGRAM_URL_IN_TEXT_RE.finditer(text or ""):
        try:
            return validate_instagram_url(match.group(0).rstrip(".,);]"))
        except ValueError:
            continue
    raise ValueError("Добавьте ссылку на пост Instagram после команды /instagram")


def validate_youtube_url(url: str) -> str:
    value = (url or "").strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    is_youtube_host = host in {"youtube.com", "youtu.be"} or host.endswith(
        ".youtube.com"
    )
    if (
        parsed.scheme not in {"http", "https"}
        or not is_youtube_host
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Нужна полная ссылка на отдельное видео YouTube")

    parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if host == "youtu.be":
        video_id = parts[0] if parts else ""
    elif parts and parts[0].lower() == "watch":
        video_id = (parse_qs(parsed.query).get("v") or [""])[0].strip()
    elif len(parts) >= 2 and parts[0].lower() in {
        "embed",
        "live",
        "shorts",
        "v",
    }:
        video_id = parts[1]

    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{2,150}", video_id):
        raise ValueError("Нужна ссылка на отдельное видео YouTube.")
    return value


def validate_spotify_track_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme not in {"http", "https"}
        or not (host == "spotify.com" or host.endswith(".spotify.com"))
        or len(path_parts) < 2
        or path_parts[-2] != "track"
        or not SPOTIFY_TRACK_ID_RE.fullmatch(path_parts[-1])
    ):
        raise ValueError("Нужна полная ссылка на отдельный трек Spotify")
    return f"https://open.spotify.com/track/{path_parts[-1]}"


def spotify_track_id_from_url(url: str) -> str:
    return urlparse(validate_spotify_track_url(url)).path.rstrip("/").split("/")[-1]


def spotify_track_url_from_text(text: str) -> str:
    for match in SPOTIFY_TRACK_URL_IN_TEXT_RE.finditer(text or ""):
        try:
            return validate_spotify_track_url(match.group(0).rstrip(".,);]"))
        except ValueError:
            continue
    raise ValueError("Добавьте ссылку на отдельный трек Spotify после команды /spotify")


def spotify_artist_from_embed_html(document: str) -> str:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    try:
        payload = json.loads(match.group(1))
        entity = payload["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return ""
    artists = entity.get("artists") if isinstance(entity, dict) else None
    if not isinstance(artists, list):
        return ""
    names = [
        str(artist.get("name") or "").strip()
        for artist in artists
        if isinstance(artist, dict)
    ]
    return ", ".join(name for name in names if name)


def is_tiktok_photo_url(url: str) -> bool:
    return "/photo/" in urlparse(validate_tiktok_url(url)).path


def is_tiktok_video_url(url: str) -> bool:
    return "/video/" in urlparse(validate_tiktok_url(url)).path or is_tiktok_photo_url(url)


def tiktok_post_id_from_url(url: str) -> str:
    match = re.search(r"/(?:video|photo)/([^/?#]+)", urlparse(url).path)
    return match.group(1) if match else ""


def instagram_post_id_from_url(url: str) -> str:
    match = re.search(r"/(?:p|reels?|tv)/([^/?#]+)", urlparse(url).path)
    return match.group(1) if match else ""


def instagram_shortcode_to_media_id(shortcode: str) -> int:
    if len(shortcode) > 28:
        shortcode = shortcode[:-28]
    media_id = 0
    for character in shortcode:
        try:
            value = INSTAGRAM_SHORTCODE_ALPHABET.index(character)
        except ValueError as error:
            raise ValueError("Некорректная ссылка на Instagram-пост") from error
        media_id = media_id * 64 + value
    return media_id


def username_from_info(info: dict[str, Any], webpage_url: str) -> str:
    username_match = re.search(r"tiktok\.com/@([^/?]+)", webpage_url)
    return str(
        (username_match.group(1) if username_match else None)
        or info.get("uploader")
        or info.get("channel")
        or info.get("channel_id")
        or info.get("uploader_id")
        or "tiktok"
    ).lstrip("@")


def media_author_from_info(
    info: dict[str, Any], webpage_url: str, platform: str
) -> tuple[str, str]:
    if platform == "instagram":
        username = ""
        for url_key in ("uploader_url", "channel_url"):
            parsed = urlparse(str(info.get(url_key) or ""))
            if parsed.netloc.endswith("instagram.com"):
                candidate = parsed.path.strip("/").split("/", 1)[0]
                if (
                    candidate
                    and candidate not in {"p", "reel", "tv", "stories"}
                    and re.fullmatch(r"[\w.]+", candidate)
                ):
                    username = candidate.lstrip("@")
                    break
        if not username:
            for key in ("uploader", "channel", "creator", "uploader_id"):
                candidate = str(info.get(key) or "").strip().lstrip("@")
                if candidate and not candidate.isdigit() and re.fullmatch(r"[\w.]+", candidate):
                    username = candidate
                    break
        if not username:
            username = "instagram"
        return username, f"https://www.instagram.com/{username}/"
    if platform == "twitter":
        username = str(
            info.get("uploader_id")
            or info.get("channel")
            or info.get("uploader")
            or "twitter"
        ).strip().lstrip("@")
        author_url = str(info.get("uploader_url") or "").strip()
        return username, author_url or f"https://x.com/{username}"
    if platform == "reddit":
        username = str(
            info.get("uploader")
            or info.get("uploader_id")
            or info.get("channel")
            or "reddit"
        ).strip().lstrip("@")
        author_url = str(info.get("uploader_url") or "").strip()
        return username, author_url or f"https://www.reddit.com/user/{username}/"
    username = username_from_info(info, webpage_url)
    return username, f"https://www.tiktok.com/@{username}"


def best_thumbnail_url(info: dict[str, Any]) -> str:
    thumbnails = [item for item in info.get("thumbnails") or [] if item.get("url")]
    if thumbnails:
        best = max(
            thumbnails,
            key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0),
        )
        return str(best["url"])
    return str(info.get("thumbnail") or "")


def image_url_dedupe_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "tiktokcdn" in host or "muscdn" in host or "ttwstatic" in host:
        return parsed.path
    return f"{host}{parsed.path}"


def unique_image_urls(urls: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = image_url_dedupe_key(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)
    return unique


def tiktok_image_post_urls_from_data(data: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add_url(value: Any) -> None:
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            return
        key = image_url_dedupe_key(value)
        if key in seen:
            return
        seen.add(key)
        urls.append(value)

    def collect_from_image(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("urlList", "url_list"):
                url_list = value.get(key)
                if isinstance(url_list, list):
                    for item in url_list:
                        add_url(item)
            for item in value.values():
                collect_from_image(item)
        elif isinstance(value, list):
            for item in value:
                collect_from_image(item)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("imagePost", "image_post_info"):
                image_post = value.get(key)
                if image_post:
                    images = image_post.get("images") if isinstance(image_post, dict) else None
                    collect_from_image(images if images else image_post)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return urls


def instagram_image_post_from_media(
    media: dict[str, Any], webpage_url: str
) -> tuple[dict[str, Any], list[str]]:
    carousel_media = media.get("carousel_media")
    if not isinstance(carousel_media, list):
        sidecar = media.get("edge_sidecar_to_children")
        edges = sidecar.get("edges") if isinstance(sidecar, dict) else []
        carousel_media = [edge.get("node") for edge in edges or [] if isinstance(edge, dict)]
    media_items = carousel_media or [media]

    image_urls: list[str] = []
    for item in media_items:
        if not isinstance(item, dict):
            continue
        typename = str(item.get("__typename") or "").lower()
        if (
            item.get("is_video") is True
            or item.get("media_type") == 2
            or "video" in typename
            or item.get("video_url")
            or item.get("video_versions")
        ):
            continue

        candidates: list[tuple[int, str]] = []
        image_versions = item.get("image_versions2") or {}
        version_candidates = (
            image_versions.get("candidates") if isinstance(image_versions, dict) else []
        )
        for candidate in version_candidates or []:
            if not isinstance(candidate, dict) or not candidate.get("url"):
                continue
            resolution = int(candidate.get("width") or 0) * int(
                candidate.get("height") or 0
            )
            candidates.append((resolution, str(candidate["url"])))
        for candidate in item.get("display_resources") or []:
            if not isinstance(candidate, dict) or not candidate.get("src"):
                continue
            resolution = int(candidate.get("config_width") or 0) * int(
                candidate.get("config_height") or 0
            )
            candidates.append((resolution, str(candidate["src"])))
        for key in ("display_url", "display_src"):
            if item.get(key):
                candidates.append((0, str(item[key])))
        if candidates:
            image_urls.append(max(candidates, key=lambda candidate: candidate[0])[1])

    user = media.get("user") or media.get("owner") or {}
    if not isinstance(user, dict):
        user = {}
    caption = media.get("caption")
    if isinstance(caption, dict):
        description = str(caption.get("text") or "")
    else:
        description = str(caption or "")
    if not description:
        caption_data = media.get("edge_media_to_caption")
        edges = caption_data.get("edges") if isinstance(caption_data, dict) else []
        if edges and isinstance(edges[0], dict):
            description = str((edges[0].get("node") or {}).get("text") or "")
    username = str(user.get("username") or "").strip().lstrip("@")
    post_id = str(
        media.get("code")
        or media.get("shortcode")
        or instagram_post_id_from_url(webpage_url)
    )
    info = {
        "id": post_id,
        "description": description,
        "title": description,
        "channel": username,
        "uploader": str(user.get("full_name") or username),
        "uploader_url": f"https://www.instagram.com/{username}/" if username else "",
        "timestamp": int(media.get("taken_at") or media.get("taken_at_timestamp") or 0),
        "webpage_url": webpage_url,
    }
    return info, unique_image_urls(image_urls)


def instagram_media_candidates_from_data(
    data: Any, post_id: str
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add(value: Any) -> None:
        if not isinstance(value, dict) or id(value) in seen:
            return
        seen.add(id(value))
        candidates.append(value)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("xdt_shortcode_media", "shortcode_media"):
                add(value.get(key))
            code = str(value.get("code") or value.get("shortcode") or "")
            if code == post_id:
                add(value)
            for key, item in value.items():
                if key in {"carousel_media", "edge_sidecar_to_children"}:
                    continue
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return candidates


def has_youtube_auth_cookies(path: Path | None) -> bool:
    if not path or not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and parts[5] in YOUTUBE_AUTH_COOKIE_NAMES:
            return True
    return False


def has_spotify_auth_cookie(path: Path | None) -> bool:
    if not path or not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and parts[5] == "sp_dc" and parts[6].strip():
            return True
    return False


def build_caption(
    video: Video,
    quote_text: str | None = None,
    before_text: str = "",
    after_text: str = "",
    include_author: bool = True,
    include_description: bool = True,
    max_length: int = MAX_CAPTION_LENGTH,
) -> str:
    author = html.escape(f"@{video.username}")
    author_url = html.escape(
        video.author_url or f"https://www.tiktok.com/@{video.username}", quote=True
    )
    author_link = f'<a href="{author_url}">{author}</a>'
    texts = [
        before_text.strip(),
        (
            video.description.strip() if quote_text is None else quote_text.strip()
        )
        if include_description
        else "",
        after_text.strip(),
    ]

    def render(parts: list[str], truncated: bool = False) -> str:
        before, quote, after = (
            html.escape(part) + ("…" if truncated and part else "") for part in parts
        )
        sections = [author_link] if include_author else []
        if before:
            sections.append(before)
        if quote:
            sections.append(f"<blockquote>{quote}</blockquote>")
        if after:
            sections.append(after)
        return "\n\n".join(sections)

    caption = render(texts)
    if len(caption) <= max_length:
        return caption

    low, high = 0, max(len(text) for text in texts)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = render([text[:middle] for text in texts], truncated=True)
        if len(candidate) <= max_length:
            low = middle
        else:
            high = middle - 1
    return render([text[:low] for text in texts], truncated=True)


def build_youtube_caption(
    video: YouTubeVideo, before_text: str = "", after_text: str = ""
) -> str:
    video_url = html.escape(video.url, quote=True)
    texts = [before_text.strip(), video.title.strip(), video.channel.strip(), after_text.strip()]

    def render(parts: list[str], truncated: bool = False) -> str:
        before, title, channel, after = (
            html.escape(part) + ("…" if truncated and part else "") for part in parts
        )
        sections: list[str] = []
        if before:
            sections.append(before)
        sections.append(f'<a href="{video_url}">{title or "Смотреть на YouTube"}</a>')
        if channel:
            sections.append(channel)
        if after:
            sections.append(after)
        return "\n\n".join(sections)

    caption = render(texts)
    if len(caption) <= MAX_CAPTION_LENGTH:
        return caption

    low, high = 0, max(len(text) for text in texts)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = render([text[:middle] for text in texts], truncated=True)
        if len(candidate) <= MAX_CAPTION_LENGTH:
            low = middle
        else:
            high = middle - 1
    return render([text[:low] for text in texts], truncated=True)


def build_spotify_caption(
    track: SpotifyTrack, before_text: str = "", after_text: str = ""
) -> str:
    track_url = html.escape(track.url, quote=True)
    texts = [
        before_text.strip(),
        track.title.strip(),
        track.artist.strip(),
        after_text.strip(),
    ]

    def render(parts: list[str], truncated: bool = False) -> str:
        before, title, artist, after = (
            html.escape(part) + ("…" if truncated and part else "") for part in parts
        )
        sections: list[str] = []
        if before:
            sections.append(before)
        sections.append(f'<a href="{track_url}">{title or "Открыть в Spotify"}</a>')
        if artist:
            sections.append(artist)
        if after:
            sections.append(after)
        return "\n\n".join(sections)

    caption = render(texts)
    if len(caption) <= MAX_CAPTION_LENGTH:
        return caption

    low, high = 0, max(len(text) for text in texts)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = render([text[:middle] for text in texts], truncated=True)
        if len(candidate) <= MAX_CAPTION_LENGTH:
            low = middle
        else:
            high = middle - 1
    return render([text[:low] for text in texts], truncated=True)


def resolve_caption_html(
    caption_html: str | None = None,
    fallback: str = "",
    max_length: int = MAX_CAPTION_LENGTH,
) -> str:
    if caption_html is None:
        return fallback
    return sanitize_telegram_html(caption_html, max_length=max_length)


class TikTokToTelegram:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir = self.config.data_dir / "downloads"
        self.download_dir.mkdir(exist_ok=True)
        for pattern in ("manual-*", "youtube-*", "spotify-*"):
            for stale_file in self.download_dir.glob(pattern):
                try:
                    age_seconds = time.time() - stale_file.stat().st_mtime
                except OSError:
                    continue
                if age_seconds < STALE_DOWNLOAD_SECONDS:
                    continue
                if stale_file.is_dir():
                    shutil.rmtree(stale_file, ignore_errors=True)
                else:
                    stale_file.unlink(missing_ok=True)
        self.storage = Storage(self.config.data_dir / "state.sqlite3")
        configured_destinations = self.config.telegram_channels or (
            TelegramChannel(self.config.telegram_chat_id, self.config.telegram_chat_id),
        )
        for channel in configured_destinations:
            self.storage.add_telegram_destination(
                channel.name,
                channel.chat_id,
                self.config.telegram_bot_token,
                is_default=channel.chat_id == self.config.telegram_chat_id,
                replace=False,
                user_id=1,
            )
        for channel in self.config.tiktok_channels:
            username, _ = normalize_channel(channel)
            self.storage.add_monitored_tiktok_channel(username, user_id=1)
        self.storage.set_setting(
            "poll_interval_seconds",
            str(self.config.poll_interval_seconds),
            only_if_missing=True,
            user_id=1,
        )
        self.ydl_lock = threading.Lock()
        self.tiktok_cookies_file = self._writable_cookie_copy(
            self.config.cookies_file, "tiktok-cookies.txt", user_id=1
        )
        self.instagram_cookies_file = self._writable_cookie_copy(
            self.config.instagram_cookies_file, "instagram-cookies.txt", user_id=1
        )
        self.youtube_cookies_file = self._writable_cookie_copy(
            self.config.youtube_cookies_file, "youtube-cookies.txt", user_id=1
        )
        self.spotify_cookies_file = self._writable_cookie_copy(
            self.config.spotify_cookies_file, "spotify-cookies.txt", user_id=1
        )
        self.twitter_cookies_file = self._writable_cookie_copy(
            self.config.twitter_cookies_file, "twitter-cookies.txt", user_id=1
        )
        self.reddit_cookies_file = self._writable_cookie_copy(
            self.config.reddit_cookies_file, "reddit-cookies.txt", user_id=1
        )
        self.spotify_lock = threading.Lock()
        self.telegram_sent_messages_lock = threading.Lock()
        self.telegram_commands_initialized: set[tuple[str, tuple[str, ...]]] = set()
        self.telegram_poll_errors: set[str] = set()

    def _user_data_dir(self, user_id: int) -> Path:
        destination = self.config.data_dir / "users" / str(user_id)
        destination.mkdir(parents=True, exist_ok=True)
        return destination

    def _writable_cookie_copy(
        self, source: Path | None, filename: str, user_id: int
    ) -> Path | None:
        destination = self._user_data_dir(user_id) / filename
        if destination.is_file():
            return destination
        legacy = self.config.data_dir / filename
        if user_id == 1 and legacy.is_file():
            shutil.copyfile(legacy, destination)
            return destination
        if not source:
            return None
        if not source.is_file():
            raise FileNotFoundError(f"Cookies file not found: {source}")
        shutil.copyfile(source, destination)
        return destination

    def _cookie_file(self, service_name: str, user_id: int = 1) -> Path | None:
        filenames = {
            "tiktok": "tiktok-cookies.txt",
            "instagram": "instagram-cookies.txt",
            "youtube": "youtube-cookies.txt",
            "spotify": "spotify-cookies.txt",
            "twitter": "twitter-cookies.txt",
            "reddit": "reddit-cookies.txt",
        }
        filename = filenames.get(service_name)
        if not filename:
            return None
        path = self._user_data_dir(user_id) / filename
        return path if path.is_file() else None

    def cookie_status(self, service_name: str, user_id: int = 1) -> dict[str, object]:
        """Inspect an uploaded Netscape cookie file without making a network request."""
        def result(
            uploaded: bool,
            valid: bool,
            reason: str,
            cookie_count: int = 0,
        ) -> dict[str, object]:
            return {
                "uploaded": uploaded,
                "valid": valid,
                "reason": reason,
                "cookie_count": cookie_count,
            }

        path = self._cookie_file(service_name, user_id)
        if not path:
            return result(False, False, "missing")

        try:
            content = path.read_bytes()
        except OSError:
            return result(True, False, "read_error")
        if b"Netscape HTTP Cookie File" not in content[:256]:
            return result(True, False, "invalid_format")

        try:
            lines = content.decode("utf-8-sig").splitlines()
        except UnicodeError:
            return result(True, False, "invalid_format")

        cookies: list[tuple[str, str, int]] = []
        for raw_line in lines:
            if not raw_line.strip():
                continue
            if raw_line.startswith("#HttpOnly_"):
                line = raw_line.removeprefix("#HttpOnly_")
            elif raw_line.startswith("#"):
                continue
            else:
                line = raw_line
            parts = line.split("\t")
            if len(parts) != 7:
                return result(True, False, "invalid_format")
            domain, include_subdomains, cookie_path, secure, expires, name, value = parts
            if (
                not domain
                or include_subdomains not in {"TRUE", "FALSE"}
                or not cookie_path
                or secure not in {"TRUE", "FALSE"}
                or not name
                or (include_subdomains == "TRUE") != domain.startswith(".")
            ):
                return result(True, False, "invalid_format")
            try:
                expires_at = int(expires)
            except ValueError:
                return result(True, False, "invalid_format")
            if expires_at < 0:
                return result(True, False, "invalid_format")
            cookies.append((name, value, expires_at))

        if not cookies:
            return result(True, False, "empty")
        if not any(value for _, value, _ in cookies):
            return result(True, False, "empty")

        now = time.time()
        active_cookies = tuple(
            (name, value)
            for name, value, expires_at in cookies
            if value and (expires_at == 0 or expires_at > now)
        )
        if not active_cookies:
            return result(True, False, "expired")

        required_names = SERVICE_AUTH_COOKIE_NAMES.get(service_name, frozenset())
        if required_names and not any(
            name in required_names for name, _ in active_cookies
        ):
            return result(True, False, "missing_auth_cookie", len(active_cookies))

        return result(True, True, "ready", len(active_cookies))

    def user(self, user_id: int) -> User:
        user = self.storage.get_user(user_id)
        if not user:
            raise ValueError("Пользователь не найден")
        return user

    def ensure_service_allowed(self, service_name: str, user_id: int = 1) -> None:
        user = self.user(user_id)
        if user.is_disabled:
            raise PermissionError("Пользователь отключён")
        if not user.allows(service_name):
            raise PermissionError(f"Сервис {service_name} отключён для пользователя")

    def mark_processed(self, video: Video, user_id: int = 1) -> None:
        self.storage.mark(
            processed_media_key(video),
            video.username,
            user_id,
        )

    def is_processed(self, video: Video, user_id: int = 1) -> bool:
        return self.storage.has(processed_media_key(video), user_id)

    def telegram_channels(self, user_id: int = 1) -> tuple[TelegramChannel, ...]:
        return tuple(
            TelegramChannel(
                destination.name, destination.chat_id, destination.destination_type
            )
            for destination in self.storage.telegram_destinations(user_id)
        )

    def _telegram_chat(self, bot_token: str, chat_id: str) -> dict[str, Any]:
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{bot_token}/getChat",
                params={"chat_id": chat_id},
                timeout=30,
            )
        except requests.RequestException:
            raise ValueError("Не удалось проверить бота через Telegram API") from None
        try:
            payload = response.json()
        except requests.JSONDecodeError as error:
            raise ValueError("Telegram не ответил корректно на проверку бота") from error
        if not payload.get("ok"):
            raise ValueError(
                "Telegram не подтвердил доступ. Проверьте токен, ID канала и права бота."
            )
        return dict(payload.get("result") or {})

    def delete_telegram_destination(self, chat_id: str, user_id: int = 1) -> None:
        self.storage.delete_telegram_destination(chat_id.strip(), user_id=user_id)

    def move_telegram_destination(
        self, chat_id: str, direction: str, user_id: int = 1
    ) -> None:
        self.storage.move_telegram_destination(
            chat_id.strip(), -1 if direction == "up" else 1, user_id=user_id
        )

    def add_telegram_destination(
        self, name: str, chat_id: str, bot_token: str, user_id: int = 1
    ) -> None:
        name = name.strip()
        chat_id = chat_id.strip()
        bot_token = bot_token.strip()
        if not name or not chat_id or not bot_token:
            raise ValueError("Укажите название, ID канала и токен бота")
        if not chat_id.startswith("@") and not re.fullmatch(r"-\d+", chat_id):
            raise ValueError(
                "Укажите публичный @тег или отрицательный числовой ID канала или чата"
            )
        result = self._telegram_chat(bot_token, chat_id)
        destination_type = str(result.get("type") or "channel")
        username = str(result.get("username") or "").strip().lstrip("@")
        telegram_id = str(result.get("id") or "").strip() or None
        canonical_chat_id = f"@{username}" if username else chat_id
        if canonical_chat_id != chat_id:
            self.storage.canonicalize_telegram_destination(
                chat_id,
                name,
                canonical_chat_id,
                bot_token,
                destination_type,
                telegram_id,
                user_id=user_id,
            )
        else:
            self.storage.add_telegram_destination(
                name,
                canonical_chat_id,
                bot_token,
                destination_type=destination_type,
                telegram_id=telegram_id,
                user_id=user_id,
            )

    def _save_telegram_chat(
        self,
        chat: dict[str, Any],
        bot_token: str,
        user_id: int,
    ) -> TelegramChannel | None:
        destination_type = str(chat.get("type") or "")
        if (
            destination_type not in {"channel", "group", "supergroup"}
            or not chat.get("id")
        ):
            return None
        numeric_chat_id = str(chat["id"])
        username = str(chat.get("username") or "").strip().lstrip("@")
        chat_id = f"@{username}" if username else numeric_chat_id
        name = str(chat.get("title") or username or numeric_chat_id)
        if username:
            self.storage.canonicalize_telegram_destination(
                numeric_chat_id,
                name,
                chat_id,
                bot_token,
                destination_type,
                numeric_chat_id,
                user_id=user_id,
            )
        else:
            self.storage.add_telegram_destination(
                name,
                chat_id,
                bot_token,
                destination_type=destination_type,
                telegram_id=numeric_chat_id,
                user_id=user_id,
            )
        return TelegramChannel(name, chat_id, destination_type)

    def discover_telegram_destinations(
        self, bot_token: str, user_id: int = 1
    ) -> tuple[TelegramChannel, ...]:
        bot_token = bot_token.strip()
        if not bot_token:
            raise ValueError("Укажите токен бота")
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{bot_token}/getUpdates",
                params={
                    "limit": 100,
                    "timeout": 0,
                    "allowed_updates": '["channel_post","edited_channel_post","my_chat_member"]',
                },
                timeout=30,
            )
            payload = response.json()
        except (requests.RequestException, requests.JSONDecodeError):
            raise ValueError("Не удалось получить каналы через Telegram API") from None
        if not payload.get("ok"):
            raise ValueError(
                "Telegram не разрешил поиск каналов. Проверьте токен и отсутствие webhook."
            )
        found: dict[str, TelegramChannel] = {}
        for update in payload.get("result") or []:
            chat = (
                (update.get("channel_post") or {}).get("chat")
                or (update.get("edited_channel_post") or {}).get("chat")
                or (update.get("my_chat_member") or {}).get("chat")
                or {}
            )
            destination_type = str(chat.get("type") or "")
            if destination_type not in {"channel", "group", "supergroup"} or not chat.get("id"):
                continue
            numeric_chat_id = str(chat["id"])
            try:
                chat = {**chat, **self._telegram_chat(bot_token, numeric_chat_id)}
            except ValueError:
                LOGGER.warning("Could not refresh Telegram chat %s", numeric_chat_id)
            channel = self._save_telegram_chat(chat, bot_token, user_id)
            if channel:
                found[channel.chat_id] = channel
        if not found:
            raise ValueError(
                "Каналы не найдены. Добавьте бота администратором и опубликуйте новый пост."
            )
        return tuple(found.values())

    @staticmethod
    def _telegram_command_offset_key(bot_token: str) -> str:
        bot_id = bot_token.partition(":")[0]
        return f"telegram_spotify_offset_{bot_id}"

    def _telegram_bot_owners(self) -> dict[str, int]:
        owners: dict[str, int] = {}
        for user in self.storage.active_users():
            if not (
                user.allow_tiktok
                or user.allow_spotify
                or user.allow_instagram
                or user.allow_twitter
                or user.allow_reddit
            ):
                continue
            for destination in self.storage.telegram_destinations(user.id):
                token = destination.bot_token.strip()
                if token:
                    owners.setdefault(token, user.id)
        return owners

    def _ensure_telegram_commands(self, bot_token: str, user_id: int) -> None:
        user = self.storage.get_user(user_id)
        if not user:
            return
        commands = []
        if user.allow_spotify:
            commands.append(
                {
                    "command": "spotify",
                    "description": "Скачать трек по ссылке Spotify",
                }
            )
            commands.append(
                {
                    "command": "spotifysearch",
                    "description": "Найти и скачать трек Spotify",
                }
            )
        if user.allow_instagram:
            commands.append(
                {
                    "command": "instagram",
                    "description": "Скачать пост по ссылке Instagram",
                }
            )
        if user.allow_twitter:
            commands.append(
                {
                    "command": "x",
                    "description": "Скачать пост по ссылке X / Twitter",
                }
            )
        if user.allow_reddit:
            commands.append(
                {
                    "command": "reddit",
                    "description": "Скачать пост по ссылке Reddit",
                }
            )
        command_state = (
            bot_token,
            tuple(command["command"] for command in commands),
        )
        if command_state in self.telegram_commands_initialized:
            return
        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/setMyCommands",
            data={
                "commands": json.dumps(
                    commands,
                    ensure_ascii=False,
                )
            },
            timeout=30,
        )
        payload = response.json()
        if not payload.get("ok"):
            raise ValueError("Telegram не разрешил настроить команды бота")
        self.telegram_commands_initialized = {
            state
            for state in self.telegram_commands_initialized
            if state[0] != bot_token
        }
        self.telegram_commands_initialized.add(command_state)

    def _telegram_chat_is_registered(
        self,
        chat: dict[str, Any],
        bot_token: str,
        user_id: int,
    ) -> bool:
        numeric_chat_id = str(chat.get("id") or "")
        username = str(chat.get("username") or "").strip().lstrip("@")
        public_chat_id = f"@{username}" if username else ""
        return any(
            destination.bot_token == bot_token
            and (
                destination.chat_id in {numeric_chat_id, public_chat_id}
                or destination.telegram_id == numeric_chat_id
            )
            for destination in self.storage.telegram_destinations(user_id)
        )

    @staticmethod
    def _telegram_message_text(message: dict[str, Any]) -> str:
        return str(message.get("text") or message.get("caption") or "")

    @staticmethod
    def _telegram_message_urls(message: dict[str, Any]) -> tuple[str, ...]:
        urls: list[str] = []
        for text_key, entities_key in (
            ("text", "entities"),
            ("caption", "caption_entities"),
        ):
            text = str(message.get(text_key) or "")
            # Telegram entity offsets count UTF-16 units, including both units
            # of an emoji. Use the same coordinate system for visible URLs.
            encoded = text.encode("utf-16-le")
            candidates: list[tuple[int, str]] = []
            link_spans: list[tuple[int, int]] = []
            for entity in message.get(entities_key) or []:
                if entity.get("type") not in {"url", "text_link"}:
                    continue
                offset = int(entity.get("offset") or 0)
                length = int(entity.get("length") or 0)
                if offset < 0 or length <= 0 or (offset + length) * 2 > len(encoded):
                    continue
                if entity["type"] == "text_link":
                    url = str(entity.get("url") or "")
                else:
                    url = encoded[offset * 2 : (offset + length) * 2].decode(
                        "utf-16-le", errors="ignore"
                    )
                    if not re.match(r"https?://", url, re.IGNORECASE):
                        url = f"https://{url}"
                candidates.append((offset, url))
                link_spans.append((offset, offset + length))
            for match in TELEGRAM_URL_IN_TEXT_RE.finditer(text):
                offset = len(text[:match.start()].encode("utf-16-le")) // 2
                # A clickable link's label may itself look like another URL.
                # Its Telegram entity supplies the actual destination.
                if any(start <= offset < end for start, end in link_spans):
                    continue
                candidates.append((offset, match.group().rstrip(".,;:!?)]}»”’")))
            for _, url in sorted(candidates, key=lambda item: item[0]):
                if url and url not in urls:
                    urls.append(url)
        return tuple(urls)

    @classmethod
    def _telegram_message_links(
        cls, message: dict[str, Any]
    ) -> tuple[tuple[str, str], ...]:
        links: list[tuple[str, str]] = []
        for url in cls._telegram_message_urls(message):
            try:
                host = (urlparse(url).hostname or "").lower().rstrip(".")
                if host == "spotify.com" or host.endswith(".spotify.com"):
                    platform = "spotify"
                    validated = validate_spotify_track_url(url)
                else:
                    platform = detect_media_platform(url)
                    validated = MEDIA_SOURCE_REGISTRY[platform].validator(url)
                    path = urlparse(validated).path
                    if platform == "instagram" and not re.fullmatch(
                        r"/(?:[^/]+/)?(?:p|reels?|tv)/[^/]+/?", path
                    ):
                        continue
                    if platform == "tiktok" and not (
                        re.fullmatch(r"/@[^/]+/(?:video|photo)/\d+/?", path)
                        or (host in {"vm.tiktok.com", "vt.tiktok.com"} and path.strip("/"))
                        or re.fullmatch(r"/t/[^/]+/?", path)
                    ):
                        continue
            except ValueError:
                continue
            link = (platform, validated)
            if link not in links:
                links.append(link)
        return tuple(links)

    @staticmethod
    def _telegram_sent_messages_key(bot_token: str, chat_id: str) -> str:
        return f"telegram_sent_messages_{bot_token.partition(':')[0]}_{chat_id}"

    def _telegram_remember_sent_messages(
        self, payload: dict[str, Any], bot_token: str, chat_id: str
    ) -> None:
        result = payload.get("result") or []
        messages = result if isinstance(result, list) else [result]
        by_chat: dict[str, list[int]] = {}
        for message in messages:
            if not isinstance(message, dict) or not message.get("message_id"):
                continue
            destination = str((message.get("chat") or {}).get("id") or chat_id)
            by_chat.setdefault(destination, []).append(int(message["message_id"]))
        for destination, message_ids in by_chat.items():
            key = self._telegram_sent_messages_key(bot_token, destination)
            # Persist a bounded history so channel updates and their automatic
            # discussion forwards cannot repost our output after a restart.
            with self.telegram_sent_messages_lock:
                previous = json.loads(self.storage.setting(key, "[]"))
                self.storage.set_setting(key, json.dumps((previous + message_ids)[-1000:]))

    def _telegram_message_was_sent(
        self, message: dict[str, Any], bot_token: str
    ) -> bool:
        chat_id = str((message.get("chat") or {}).get("id") or "")
        message_id = message.get("message_id")
        if message.get("is_automatic_forward"):
            origin = message.get("forward_origin") or {}
            if origin.get("type") == "channel":
                chat_id = str((origin.get("chat") or {}).get("id") or "")
                message_id = origin.get("message_id")
        if not chat_id or not message_id:
            return False
        key = self._telegram_sent_messages_key(bot_token, chat_id)
        return int(message_id) in json.loads(self.storage.setting(key, "[]"))

    def _telegram_send_text(
        self,
        bot_token: str,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
    ) -> int | None:
        data: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if message_thread_id:
            data["message_thread_id"] = message_thread_id
        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=data,
            timeout=60,
        )
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")
        self._telegram_remember_sent_messages(payload, bot_token, chat_id)
        message_id = (payload.get("result") or {}).get("message_id")
        return int(message_id) if message_id is not None else None

    @staticmethod
    def _telegram_edit_text(
        bot_token: str,
        chat_id: str,
        message_id: int | None,
        text: str,
    ) -> None:
        if message_id is None:
            return
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{bot_token}/editMessageText",
                data={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text[:4096],
                },
                timeout=30,
            )
            payload = response.json()
            if not payload.get("ok"):
                LOGGER.info("Could not update Telegram command status message")
        except (requests.RequestException, requests.JSONDecodeError):
            LOGGER.info("Could not update Telegram command status message")

    @staticmethod
    def _telegram_finish_status(
        bot_token: str,
        chat_id: str,
        message_id: int | None,
        error: str | None = None,
        error_prefix: str = "Не удалось скачать трек",
    ) -> None:
        if message_id is None:
            return
        method = "editMessageText" if error else "deleteMessage"
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if error:
            data["text"] = f"{error_prefix}: {error}"[:4096]
        try:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/{method}",
                data=data,
                timeout=30,
            )
        except requests.RequestException:
            LOGGER.info("Could not finish Telegram command status message")

    def process_telegram_update(
        self,
        update: dict[str, Any],
        bot_token: str,
        user_id: int,
    ) -> None:
        membership = update.get("my_chat_member") or {}
        if membership:
            return

        message = update.get("message") or update.get("channel_post") or {}
        sender = message.get("from") or {}
        if (
            sender.get("is_bot") and not message.get("sender_chat")
        ) or str(sender.get("id") or "") == bot_token.partition(":")[0] or (
            self._telegram_message_was_sent(message, bot_token)
        ):
            return
        text = self._telegram_message_text(message)
        command_match = TELEGRAM_DOWNLOAD_COMMAND_RE.match(text.strip())
        # Keep slash commands explicit: an unrelated bot command must not
        # unexpectedly download its arguments.
        if not command_match and text.lstrip().startswith("/"):
            return
        links = () if command_match else self._telegram_message_links(message)
        if not command_match and not links:
            return
        chat = dict(message.get("chat") or {})
        if not chat.get("id"):
            return
        chat_id = str(chat["id"])
        message_thread_id = message.get("message_thread_id")
        registered = self._telegram_chat_is_registered(chat, bot_token, user_id)
        if not registered:
            if command_match:
                self._telegram_send_text(
                    bot_token,
                    chat_id,
                    "Сначала добавьте чат в настройках ClipRelay",
                    message_thread_id,
                )
            return

        if not command_match:
            user = self.storage.get_user(user_id)
            if not user or user.is_disabled:
                return
            for platform, url in links:
                if not user.allows(platform):
                    continue
                try:
                    if platform == "spotify":
                        self._process_telegram_spotify_command(
                            url, bot_token, chat_id, user_id, message_thread_id
                        )
                    else:
                        self._process_telegram_social_command(
                            platform, url, bot_token, chat_id, user_id, message_thread_id
                        )
                except Exception:
                    LOGGER.exception(
                        "Failed to process Telegram %s link for user %s", platform, user_id
                    )
            return

        command = command_match.group("command").lower()
        reply = message.get("reply_to_message") or {}
        link_source = "\n".join(
            part
            for part in (
                text,
                self._telegram_message_text(reply),
                *self._telegram_message_urls(message),
                *self._telegram_message_urls(reply),
            )
            if part
        )

        if command == "instagram":
            self._process_telegram_instagram_command(
                link_source,
                bot_token,
                chat_id,
                user_id,
                message_thread_id,
            )
            return
        if command in {"twitter", "x"}:
            self._process_telegram_social_command(
                "twitter",
                link_source,
                bot_token,
                chat_id,
                user_id,
                message_thread_id,
            )
            return
        if command == "reddit":
            self._process_telegram_social_command(
                "reddit",
                link_source,
                bot_token,
                chat_id,
                user_id,
                message_thread_id,
            )
            return
        if command == "spotifysearch":
            query = text.strip()[command_match.end() :].strip()
            if not query:
                query = self._telegram_message_text(reply).strip()
            self._process_telegram_spotify_search_command(
                query,
                bot_token,
                chat_id,
                user_id,
                message_thread_id,
            )
            return
        self._process_telegram_spotify_command(
            link_source,
            bot_token,
            chat_id,
            user_id,
            message_thread_id,
        )

    def _process_telegram_spotify_command(
        self,
        link_source: str,
        bot_token: str,
        chat_id: str,
        user_id: int,
        message_thread_id: int | None,
    ) -> None:
        try:
            spotify_url = spotify_track_url_from_text(link_source)
        except ValueError as error:
            self._telegram_send_text(
                bot_token,
                chat_id,
                str(error),
                message_thread_id,
            )
            return

        status_message_id = self._telegram_send_text(
            bot_token,
            chat_id,
            "Готовлю MP3 320 кбит/с…",
            message_thread_id,
        )
        try:
            track = self.get_spotify_info(spotify_url, user_id)
            path = self.download_spotify(
                track,
                f"spotify-{user_id}-{track.track_id}",
                user_id,
            )
            self._publish_spotify_to_telegram(
                track,
                path,
                bot_token,
                chat_id,
                caption_html=None,
                message_thread_id=message_thread_id,
            )
        except Exception as error:
            LOGGER.exception(
                "Failed to process Telegram Spotify command for user %s",
                user_id,
            )
            self._telegram_finish_status(
                bot_token,
                chat_id,
                status_message_id,
                str(error),
            )
            return
        self._telegram_finish_status(
            bot_token,
            chat_id,
            status_message_id,
        )

    def _process_telegram_spotify_search_command(
        self,
        query: str,
        bot_token: str,
        chat_id: str,
        user_id: int,
        message_thread_id: int | None,
    ) -> None:
        if not query:
            self._telegram_send_text(
                bot_token,
                chat_id,
                "Добавьте название трека или исполнителя после команды "
                "/spotifysearch",
                message_thread_id,
            )
            return

        status_message_id = self._telegram_send_text(
            bot_token,
            chat_id,
            "Ищу трек в Spotify…",
            message_thread_id,
        )
        try:
            track = self.search_spotify(query, user_id)
            found = f"Нашёл: {track.title}"
            if track.artist:
                found += f" — {track.artist}"
            self._telegram_edit_text(
                bot_token,
                chat_id,
                status_message_id,
                f"{found}\nГотовлю MP3 320 кбит/с…",
            )
            path = self.download_spotify(
                track,
                f"spotify-{user_id}-{track.track_id}",
                user_id,
            )
            self._publish_spotify_to_telegram(
                track,
                path,
                bot_token,
                chat_id,
                caption_html=None,
                message_thread_id=message_thread_id,
            )
        except Exception as error:
            LOGGER.exception(
                "Failed to process Telegram Spotify search for user %s",
                user_id,
            )
            self._telegram_finish_status(
                bot_token,
                chat_id,
                status_message_id,
                str(error),
                "Не удалось найти или скачать трек",
            )
            return
        self._telegram_finish_status(
            bot_token,
            chat_id,
            status_message_id,
        )

    def _process_telegram_social_command(
        self,
        platform: str,
        link_source: str,
        bot_token: str,
        chat_id: str,
        user_id: int,
        message_thread_id: int | None,
    ) -> None:
        source_options = {
            "tiktok": (
                validate_tiktok_url,
                "TikTok",
                "Скачиваю TikTok-пост…",
                "Не удалось скачать TikTok-пост",
            ),
            "instagram": (
                instagram_url_from_text,
                "Instagram",
                "Скачиваю Instagram-пост…",
                "Не удалось скачать Instagram-пост",
            ),
            "twitter": (
                twitter_url_from_text,
                "X / Twitter",
                "Скачиваю пост X / Twitter…",
                "Не удалось скачать пост X / Twitter",
            ),
            "reddit": (
                reddit_url_from_text,
                "Reddit",
                "Скачиваю пост Reddit…",
                "Не удалось скачать пост Reddit",
            ),
        }
        parser, _, progress_text, failure_text = source_options[platform]
        try:
            media_url = parser(link_source)
        except ValueError as error:
            self._telegram_send_text(
                bot_token,
                chat_id,
                str(error),
                message_thread_id,
            )
            return

        status_message_id = self._telegram_send_text(
            bot_token,
            chat_id,
            progress_text,
            message_thread_id,
        )
        paths: tuple[Path, ...] = ()
        try:
            video, paths = self.prepare_url(media_url, user_id)
            max_length = (
                MAX_MESSAGE_LENGTH
                if video.media_type == "text"
                else MAX_CAPTION_LENGTH
            )
            caption = resolve_caption_html(
                None,
                build_caption(video, max_length=max_length),
                max_length=max_length,
            )
            self._publish_media_to_telegram(
                video,
                paths,
                bot_token,
                chat_id,
                caption,
                message_thread_id=message_thread_id,
            )
        except Exception as error:
            LOGGER.exception(
                "Failed to process Telegram %s command for user %s",
                platform,
                user_id,
            )
            self._telegram_finish_status(
                bot_token,
                chat_id,
                status_message_id,
                str(error),
                failure_text,
            )
            return
        finally:
            for path in paths:
                path.unlink(missing_ok=True)
        self._telegram_finish_status(
            bot_token,
            chat_id,
            status_message_id,
        )

    def _process_telegram_instagram_command(
        self,
        link_source: str,
        bot_token: str,
        chat_id: str,
        user_id: int,
        message_thread_id: int | None,
    ) -> None:
        self._process_telegram_social_command(
            "instagram",
            link_source,
            bot_token,
            chat_id,
            user_id,
            message_thread_id,
        )

    def poll_telegram_commands_once(self) -> int:
        owners = self._telegram_bot_owners()
        for bot_token, user_id in owners.items():
            try:
                self._ensure_telegram_commands(bot_token, user_id)
                offset_key = self._telegram_command_offset_key(bot_token)
                offset = int(self.storage.setting(offset_key, "0", user_id))
                response = requests.get(
                    f"https://api.telegram.org/bot{bot_token}/getUpdates",
                    params={
                        "offset": offset,
                        "limit": 100,
                        "timeout": 0,
                        "allowed_updates": json.dumps(
                            ["message", "channel_post", "my_chat_member"]
                        ),
                    },
                    timeout=30,
                )
                payload = response.json()
                if not payload.get("ok"):
                    raise ValueError(
                        "Telegram не разрешил polling. Проверьте, что webhook отключён."
                    )
                for update in payload.get("result") or []:
                    update_id = int(update.get("update_id") or 0)
                    try:
                        self.process_telegram_update(update, bot_token, user_id)
                    except Exception:
                        LOGGER.exception(
                            "Failed to process Telegram update for user %s",
                            user_id,
                        )
                    finally:
                        if update_id:
                            offset = max(offset, update_id + 1)
                            self.storage.set_setting(
                                offset_key,
                                str(offset),
                                user_id=user_id,
                            )
                self.telegram_poll_errors.discard(bot_token)
            except Exception as error:
                if bot_token not in self.telegram_poll_errors:
                    LOGGER.warning(
                        "Telegram command polling failed for user %s: %s",
                        user_id,
                        error,
                    )
                    self.telegram_poll_errors.add(bot_token)
        return len(owners)

    def monitored_tiktok_channels(self, user_id: int = 1) -> tuple[str, ...]:
        return self.storage.monitored_tiktok_channels(user_id)

    def add_monitored_tiktok_channel(self, channel: str, user_id: int = 1) -> str:
        self.ensure_service_allowed("tiktok", user_id)
        username, _ = normalize_channel(channel)
        self.storage.add_monitored_tiktok_channel(username, user_id)
        return username

    def delete_monitored_tiktok_channel(self, channel: str, user_id: int = 1) -> None:
        self.storage.delete_monitored_tiktok_channel(normalize_channel(channel)[0], user_id)

    def poll_interval_seconds(self, user_id: int = 1) -> int:
        return max(30, int(self.storage.setting("poll_interval_seconds", "300", user_id)))

    def set_poll_interval_seconds(self, value: str, user_id: int = 1) -> int:
        interval = max(30, int(value))
        self.storage.set_setting("poll_interval_seconds", str(interval), user_id=user_id)
        return interval

    def update_cookies(self, service_name: str, content: bytes, user_id: int = 1) -> Path:
        self.ensure_service_allowed(service_name, user_id)
        if len(content) > 5 * 1024 * 1024:
            raise ValueError("Файл cookies не должен превышать 5 МБ")
        if b"Netscape HTTP Cookie File" not in content[:256]:
            raise ValueError("Нужен cookies.txt в Netscape-формате")
        cookie_targets = {
            "tiktok": ("tiktok-cookies.txt", "tiktok_cookies_file"),
            "instagram": ("instagram-cookies.txt", "instagram_cookies_file"),
            "youtube": ("youtube-cookies.txt", "youtube_cookies_file"),
            "spotify": ("spotify-cookies.txt", "spotify_cookies_file"),
            "twitter": ("twitter-cookies.txt", "twitter_cookies_file"),
            "reddit": ("reddit-cookies.txt", "reddit_cookies_file"),
        }
        target = cookie_targets.get(service_name)
        if not target:
            raise ValueError("Неизвестный сервис cookies")
        filename, attribute = target
        destination = self._user_data_dir(user_id) / filename
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(content)
        if service_name == "spotify" and not has_spotify_auth_cookie(temporary):
            temporary.unlink(missing_ok=True)
            raise ValueError(
                "В Spotify cookies не найден sp_dc. Экспортируйте cookies.txt "
                "с open.spotify.com после входа в аккаунт."
            )
        temporary.replace(destination)
        if user_id == 1:
            setattr(self, attribute, destination)
        return destination

    def _ydl_options(self, cookies_file: Path | None = None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "js_runtimes": {"deno": {}},
        }
        if cookies_file:
            options["cookiefile"] = str(cookies_file)
        return options

    def _youtube_options(self, cookies_file: Path | None = None) -> dict[str, Any]:
        options = self._ydl_options(cookies_file)
        if self.config.youtube_po_token_provider_url:
            options["extractor_args"] = {
                "youtube": {"player_client": ["mweb"]},
                "youtubepot-bgutilhttp": {
                    "base_url": [self.config.youtube_po_token_provider_url]
                },
            }
        return options

    def _cookie_session(self, cookies_file: Path | None = None) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                )
            }
        )

        if cookies_file and cookies_file.exists():
            jar = http.cookiejar.MozillaCookieJar(str(cookies_file))
            try:
                jar.load(ignore_discard=True, ignore_expires=True)
                session.cookies.update(jar)
            except Exception:
                LOGGER.warning("Could not load cookies from %s", cookies_file)
        return session

    def _tiktok_image_post_urls(self, url: str, cookies_file: Path | None) -> list[str]:
        response = self._cookie_session(cookies_file).get(url, timeout=60)
        response.raise_for_status()
        script_matches = re.findall(
            r'<script[^>]+id="(?:SIGI_STATE|__UNIVERSAL_DATA_FOR_REHYDRATION__)"[^>]*>(.*?)</script>',
            response.text,
            flags=re.DOTALL,
        )
        urls: list[str] = []
        seen: set[str] = set()
        for script in script_matches:
            try:
                data = json.loads(html.unescape(script))
            except json.JSONDecodeError:
                continue
            for image_url in tiktok_image_post_urls_from_data(data):
                key = image_url_dedupe_key(image_url)
                if key in seen:
                    continue
                seen.add(key)
                urls.append(image_url)
        return urls

    def _instagram_image_post_info(
        self, url: str, cookies_file: Path | None
    ) -> tuple[dict[str, Any], list[str]]:
        post_id = instagram_post_id_from_url(url)
        if not post_id:
            return {}, []
        session = self._cookie_session(cookies_file)
        session.headers.update(
            {
                "Accept": "*/*",
                "Origin": "https://www.instagram.com",
                "Referer": url,
                "X-ASBD-ID": "198387",
                "X-IG-App-ID": "936619743392459",
                "X-IG-WWW-Claim": "0",
            }
        )

        def cookie_value(name: str) -> str:
            return next(
                (cookie.value for cookie in session.cookies if cookie.name == name), ""
            )

        def extract(payload: Any) -> tuple[dict[str, Any], list[str]]:
            fallback_info: dict[str, Any] = {}
            for media in instagram_media_candidates_from_data(payload, post_id):
                info, image_urls = instagram_image_post_from_media(media, url)
                if image_urls:
                    return info, image_urls
                if not fallback_info:
                    fallback_info = info
            return fallback_info, []

        if cookie_value("sessionid"):
            try:
                media_id = instagram_shortcode_to_media_id(post_id)
                response = session.get(
                    f"https://i.instagram.com/api/v1/media/{media_id}/info/",
                    timeout=60,
                )
                response.raise_for_status()
                info, image_urls = extract(response.json())
                if image_urls:
                    return info, image_urls
                if info:
                    return info, []
            except (requests.RequestException, ValueError) as error:
                LOGGER.info("Could not inspect Instagram post through media API: %s", error)

        try:
            media_id = instagram_shortcode_to_media_id(post_id)
            response = session.get(
                "https://www.instagram.com/api/v1/web/get_ruling_for_content/",
                params={"content_type": "MEDIA", "target_id": media_id},
                timeout=60,
            )
            response.raise_for_status()
        except (requests.RequestException, ValueError) as error:
            LOGGER.info("Could not initialize Instagram GraphQL session: %s", error)

        try:
            csrf_token = cookie_value("csrftoken")
            response = session.get(
                "https://www.instagram.com/graphql/query/",
                params={
                    "doc_id": INSTAGRAM_GRAPHQL_DOC_ID,
                    "variables": json.dumps(
                        {
                            "shortcode": post_id,
                            "child_comment_count": 3,
                            "fetch_comment_count": 40,
                            "parent_comment_count": 24,
                            "has_threaded_comments": True,
                        },
                        separators=(",", ":"),
                    ),
                },
                headers={
                    "X-CSRFToken": csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=60,
            )
            response.raise_for_status()
            info, image_urls = extract(response.json())
            if image_urls:
                return info, image_urls
            if info:
                return info, []
        except (requests.RequestException, ValueError) as error:
            LOGGER.info("Could not inspect Instagram post through GraphQL: %s", error)

        response = session.get(url, timeout=60)
        response.raise_for_status()
        script_matches = re.findall(
            r'<script[^>]+type=["\']application/(?:ld\+)?json["\'][^>]*>(.*?)</script>',
            response.text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        for script in script_matches:
            try:
                payload = json.loads(html.unescape(script))
            except json.JSONDecodeError:
                continue
            info, image_urls = extract(payload)
            if image_urls:
                return info, image_urls

        if "og:video" not in response.text.lower():
            image_match = re.search(
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                response.text,
                flags=re.IGNORECASE,
            ) or re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                response.text,
                flags=re.IGNORECASE,
            )
            if image_match:
                return {
                    "id": post_id,
                    "webpage_url": url,
                }, [html.unescape(image_match.group(1))]
        return {}, []

    def _tikwm_image_post_info(self, url: str) -> tuple[dict[str, Any], list[str]]:
        response = requests.get(
            "https://www.tikwm.com/api/",
            params={"url": url},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return {}, []
        image_urls = unique_image_urls(
            [
                str(image_url)
                for image_url in data.get("images") or []
                if str(image_url).startswith(("http://", "https://"))
            ]
        )
        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        info = {
            "id": str(data.get("id") or tiktok_post_id_from_url(url)),
            "description": str(data.get("title") or data.get("content_desc") or ""),
            "title": str(data.get("title") or data.get("content_desc") or ""),
            "uploader": str(author.get("unique_id") or ""),
            "channel": str(author.get("nickname") or ""),
            "timestamp": int(data.get("create_time") or 0),
            "webpage_url": url,
        }
        return info, image_urls

    def _download_images(
        self, urls: list[str], output_id: str, cookies_file: Path | None
    ) -> tuple[Path, ...]:
        urls = unique_image_urls(urls)
        if not urls:
            raise ValueError("Пост не содержит изображений")
        session = self._cookie_session(cookies_file)
        paths: list[Path] = []
        try:
            for index, image_url in enumerate(urls, start=1):
                response = session.get(image_url, timeout=90)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                extension = mimetypes.guess_extension(content_type) or ".jpg"
                if extension == ".jpe":
                    extension = ".jpg"
                path = self.download_dir / f"{output_id}-{index:02d}{extension}"
                path.write_bytes(response.content)
                paths.append(path)
        except Exception:
            for path in paths:
                if path.exists():
                    path.unlink()
            raise
        return tuple(paths)

    def _twitter_authenticated_post_info(
        self,
        url: str,
        user_id: int,
    ) -> MediaSourcePost:
        cookies_file = self._cookie_file("twitter", user_id)
        if not cookies_file:
            raise ValueError("Для этой публикации X / Twitter нужны cookies")
        post_id = twitter_post_id_from_url(url)
        if not post_id:
            raise ValueError("Не удалось определить ID публикации X / Twitter")
        from yt_dlp.extractor.twitter import TwitterIE

        options = {
            **self._ydl_options(cookies_file),
            "skip_download": True,
        }
        with self.ydl_lock, yt_dlp.YoutubeDL(options) as ydl:
            status = TwitterIE(ydl)._extract_status(post_id)
        return parse_twitter_post(status, url)

    def _social_post_info(
        self,
        url: str,
        platform: str,
        user_id: int,
    ) -> MediaSourcePost:
        cookies_file = self._cookie_file(platform, user_id)
        if platform == "reddit":
            session = self._cookie_session(cookies_file)
            session.headers["User-Agent"] = "ClipRelay/1.0 (Reddit media relay)"
            return fetch_reddit_post(session, url)
        if platform != "twitter":
            raise ValueError(f"Неизвестный источник публикации: {platform}")

        try:
            return fetch_twitter_syndication_post(
                self._cookie_session(cookies_file),
                url,
            )
        except Exception as syndication_error:
            if not cookies_file:
                raise ValueError(
                    "Не удалось получить публикацию X / Twitter. "
                    "Для закрытых или ограниченных публикаций загрузите cookies."
                ) from syndication_error
            try:
                return self._twitter_authenticated_post_info(url, user_id)
            except Exception as authenticated_error:
                raise ValueError(
                    "Не удалось получить публикацию X / Twitter даже с cookies"
                ) from authenticated_error

    @staticmethod
    def _video_from_source_post(post: MediaSourcePost) -> Video:
        return Video(
            video_id=post.post_id,
            username=post.username,
            description=post.description,
            url=post.webpage_url,
            timestamp=post.timestamp,
            platform=post.platform,
            author_url=post.author_url,
            media_type=post.media_type,
        )

    def _cleanup_output_files(self, output_id: str) -> None:
        for pattern in (f"{output_id}.*", f"{output_id}-*"):
            for partial_file in self.download_dir.glob(pattern):
                if partial_file.is_file():
                    partial_file.unlink(missing_ok=True)

    def _download_video_files(
        self,
        url: str,
        platform: str,
        output_id: str,
        user_id: int,
    ) -> tuple[dict[str, Any], tuple[Path, ...]]:
        output_template = str(
            self.download_dir / f"{output_id}-%(autonumber)02d.%(ext)s"
        )
        options = {
            **self._ydl_options(self._cookie_file(platform, user_id)),
            "format": "best[ext=mp4][filesize<49M]/best[filesize<49M]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "outtmpl": output_template,
            "max_filesize": MAX_VIDEO_BYTES,
        }
        try:
            with self.ydl_lock, yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            paths = tuple(
                sorted(
                    (
                        path
                        for path in self.download_dir.glob(f"{output_id}-*")
                        if path.is_file()
                        and path.suffix not in {".part", ".ytdl"}
                        and not re.search(r"\.f\d+\.[^.]+$", path.name)
                    ),
                    key=lambda path: path.name,
                )
            )
            if not paths:
                raise FileNotFoundError("Скачанные видео не найдены")
            oversized = [path for path in paths if path.stat().st_size > MAX_VIDEO_BYTES]
            if oversized:
                raise ValueError("Видео больше лимита Telegram Bot API в 50 МБ")
            return info, paths
        except Exception:
            self._cleanup_output_files(output_id)
            raise

    def _download_video_file(
        self, url: str, platform: str, output_id: str, user_id: int
    ) -> tuple[dict[str, Any], Path]:
        output_template = str(self.download_dir / f"{output_id}.%(ext)s")
        options = {
            **self._ydl_options(self._cookie_file(platform, user_id)),
            "format": "best[ext=mp4][filesize<49M]/best[filesize<49M]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "outtmpl": output_template,
            "noplaylist": True,
        }
        with self.ydl_lock, yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))

        if not path.exists():
            matches = list(self.download_dir.glob(f"{output_id}.*"))
            if not matches:
                raise FileNotFoundError("Скачанное видео не найдено")
            path = matches[0]
        if path.stat().st_size > MAX_VIDEO_BYTES:
            path.unlink()
            raise ValueError("Видео больше лимита Telegram Bot API в 50 МБ")
        return info, path

    def _extract_info(self, url: str, platform: str, user_id: int) -> dict[str, Any]:
        options = {
            **self._ydl_options(self._cookie_file(platform, user_id)),
            "noplaylist": True,
            "skip_download": True,
        }
        with self.ydl_lock, yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)

    def _prepare_media(
        self, url: str, platform: str, output_id: str, user_id: int
    ) -> tuple[Video, tuple[Path, ...]]:
        info: dict[str, Any] = {}
        webpage_url = url
        media_type = "video"
        paths: tuple[Path, ...]

        if platform in {"twitter", "reddit"}:
            try:
                source_post = self._social_post_info(url, platform, user_id)
            except Exception as metadata_error:
                LOGGER.info(
                    "Could not inspect %s post %s; trying video extraction: %s",
                    platform,
                    url,
                    metadata_error,
                )
                try:
                    info, paths = self._download_video_files(
                        url, platform, output_id, user_id
                    )
                except Exception:
                    raise metadata_error
                webpage_url = str(info.get("webpage_url") or url)
                username, author_url = media_author_from_info(
                    info, webpage_url, platform
                )
                return Video(
                    video_id=str(
                        info.get("id")
                        or (
                            twitter_post_id_from_url(webpage_url)
                            if platform == "twitter"
                            else reddit_post_id_from_url(webpage_url)
                        )
                        or output_id
                    ),
                    username=username,
                    description=str(info.get("description") or info.get("title") or ""),
                    url=webpage_url,
                    timestamp=int(info.get("timestamp") or 0),
                    platform=platform,
                    author_url=author_url,
                    media_type="video",
                ), paths

            video = self._video_from_source_post(source_post)
            if source_post.media_type == "image":
                paths = self._download_images(
                    list(source_post.image_urls),
                    output_id,
                    self._cookie_file(platform, user_id),
                )
            elif source_post.media_type == "video":
                _, paths = self._download_video_files(
                    url, platform, output_id, user_id
                )
            else:
                paths = ()
            return video, paths
        if platform == "tiktok" and is_tiktok_photo_url(url):
            image_urls = self._tiktok_image_post_urls(url, self._cookie_file(platform, user_id))
            if not image_urls:
                info, image_urls = self._tikwm_image_post_info(url)
            if not image_urls:
                raise ValueError("Не удалось получить картинки TikTok-коллажа")
            media_type = "image"
            paths = self._download_images(
                image_urls, output_id, self._cookie_file(platform, user_id)
            )
        elif platform == "tiktok":
            info = self._extract_info(url, platform, user_id)
            webpage_url = str(info.get("webpage_url") or url)
            try:
                image_urls = self._tiktok_image_post_urls(
                    webpage_url, self._cookie_file(platform, user_id)
                )
            except Exception as error:
                LOGGER.info("Could not inspect TikTok image post %s: %s", webpage_url, error)
                image_urls = []
            if image_urls:
                media_type = "image"
                paths = self._download_images(
                    image_urls, output_id, self._cookie_file(platform, user_id)
                )
            else:
                info, path = self._download_video_file(url, platform, output_id, user_id)
                paths = (path,)
        elif platform == "instagram" and "/p/" in urlparse(url).path:
            try:
                info, image_urls = self._instagram_image_post_info(
                    url, self._cookie_file(platform, user_id)
                )
            except Exception as error:
                LOGGER.info("Could not inspect Instagram image post %s: %s", url, error)
                info, image_urls = {}, []
            if image_urls:
                media_type = "image"
                paths = self._download_images(
                    image_urls, output_id, self._cookie_file(platform, user_id)
                )
            else:
                info = self._extract_info(url, platform, user_id)
                webpage_url = str(info.get("webpage_url") or url)
                info, path = self._download_video_file(url, platform, output_id, user_id)
                paths = (path,)
        else:
            info = self._extract_info(url, platform, user_id)
            webpage_url = str(info.get("webpage_url") or url)
            info, path = self._download_video_file(url, platform, output_id, user_id)
            paths = (path,)

        webpage_url = str(info.get("webpage_url") or webpage_url or url)
        username, author_url = media_author_from_info(info, webpage_url, platform)
        video = Video(
            video_id=str(
                info.get("id")
                or (
                    instagram_post_id_from_url(webpage_url)
                    if platform == "instagram"
                    else tiktok_post_id_from_url(webpage_url)
                )
                or output_id
            ),
            username=username,
            description=str(info.get("description") or info.get("title") or ""),
            url=webpage_url,
            timestamp=int(info.get("timestamp") or 0),
            platform=platform,
            author_url=author_url,
            media_type=media_type,
        )
        return video, paths

    def scan(self, channel: str, user_id: int = 1) -> list[Video]:
        self.ensure_service_allowed("tiktok", user_id)
        username, channel_url = normalize_channel(channel)
        options = {
            **self._ydl_options(self._cookie_file("tiktok", user_id)),
            "extract_flat": True,
            "playlistend": self.config.scan_limit,
        }
        with self.ydl_lock, yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.extract_info(channel_url, download=False)

        videos: list[Video] = []
        for entry in (result or {}).get("entries") or []:
            if not entry:
                continue
            video_id = str(entry.get("id") or "").strip()
            if not video_id:
                continue
            videos.append(
                Video(
                    video_id=video_id,
                    username=username,
                    description=str(entry.get("description") or entry.get("title") or ""),
                    url=str(
                        entry.get("webpage_url")
                        or f"https://www.tiktok.com/@{username}/video/{video_id}"
                    ),
                    timestamp=int(entry.get("timestamp") or 0),
                )
            )
        return sorted(videos, key=lambda item: (item.timestamp, item.video_id))

    def download(
        self, video: Video, output_id: str | None = None, user_id: int = 1
    ) -> tuple[Path, ...]:
        self.ensure_service_allowed(video.platform, user_id)
        output_id = output_id or video.video_id
        _, paths = self._prepare_media(video.url, video.platform, output_id, user_id)
        return paths

    def prepare_url(self, url: str, user_id: int = 1) -> tuple[Video, tuple[Path, ...]]:
        platform = detect_media_platform(url)
        self.ensure_service_allowed(platform, user_id)
        url = MEDIA_SOURCE_REGISTRY[platform].validator(url)
        output_id = f"manual-{uuid.uuid4().hex}"
        try:
            return self._prepare_media(url, platform, output_id, user_id)
        except Exception:
            self._cleanup_output_files(output_id)
            raise

    def search_spotify(self, query: str, user_id: int = 1) -> SpotifyTrack:
        self.ensure_service_allowed("spotify", user_id)
        query = " ".join(query.split())
        if len(query) < 2:
            raise ValueError("Название трека должно содержать хотя бы 2 символа")
        if len(query) > 200:
            raise ValueError("Название трека не должно быть длиннее 200 символов")
        cookies_file = self._cookie_file("spotify", user_id)
        if not has_spotify_auth_cookie(cookies_file):
            raise ValueError(
                "Сначала загрузите Spotify cookies.txt в настройках. "
                "В файле должен быть cookie sp_dc с open.spotify.com."
            )

        with tempfile.TemporaryDirectory(
            prefix="spotify-search-",
            dir=self.download_dir,
        ) as temporary_dir:
            output_file = Path(temporary_dir) / "result.json"
            command = [
                sys.executable,
                "-m",
                "app.spotify_search",
                str(cookies_file),
                query,
                str(output_file),
            ]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=2 * 60,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise ValueError(
                    "Spotify не успел выполнить поиск за 2 минуты"
                ) from error
            except OSError as error:
                raise ValueError("Модуль поиска Spotify не установлен") from error
            output = f"{result.stdout}\n{result.stderr}".strip()
            if result.returncode != 0 or not output_file.is_file():
                normalized = output.lower()
                if "не нашёл подходящих треков" in normalized:
                    raise ValueError("Spotify не нашёл подходящих треков")
                if (
                    "sp_dc" in normalized
                    or "could not authenticate" in normalized
                    or "bad credentials" in normalized
                ):
                    raise ValueError(
                        "Spotify cookies недействительны или устарели. "
                        "Загрузите новый cookies.txt из авторизованной сессии."
                    )
                LOGGER.warning("Spotify search failed: %s", output)
                raise ValueError(
                    "Spotify временно не разрешил поиск. Повторите попытку позже."
                )
            try:
                payload = json.loads(output_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as error:
                raise ValueError(
                    "Spotify вернул повреждённый результат поиска"
                ) from error

        track_id = str(payload.get("track_id") or "")
        if not SPOTIFY_TRACK_ID_RE.fullmatch(track_id):
            raise ValueError("Spotify вернул некорректный трек")
        track = SpotifyTrack(
            track_id=track_id,
            title=str(payload.get("title") or "").strip(),
            url=f"https://open.spotify.com/track/{track_id}",
            thumbnail_url=str(payload.get("thumbnail_url") or "").strip(),
            artist=str(payload.get("artist") or "").strip(),
        )
        if not track.title:
            raise ValueError("Spotify не вернул название найденного трека")
        if not track.thumbnail_url:
            return self.get_spotify_info(track.url, user_id)
        return track

    def get_spotify_info(self, url: str, user_id: int = 1) -> SpotifyTrack:
        self.ensure_service_allowed("spotify", user_id)
        url = validate_spotify_track_url(url)
        track_id = spotify_track_id_from_url(url)
        response = requests.get(
            "https://open.spotify.com/oembed",
            params={"url": url},
            timeout=30,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except requests.JSONDecodeError as error:
            raise ValueError("Spotify не вернул информацию о треке") from error
        title = str(payload.get("title") or "").strip()
        thumbnail_url = str(payload.get("thumbnail_url") or "").strip()
        if not title or not thumbnail_url:
            raise ValueError("Spotify не вернул название или обложку трека")
        artist = ""
        try:
            embed_response = requests.get(
                f"https://open.spotify.com/embed/track/{track_id}",
                timeout=30,
            )
            embed_response.raise_for_status()
            artist = spotify_artist_from_embed_html(embed_response.text)
        except requests.RequestException as error:
            LOGGER.info("Could not load Spotify artist for %s: %s", track_id, error)
        return SpotifyTrack(
            track_id=track_id,
            title=title,
            url=url,
            thumbnail_url=thumbnail_url,
            artist=artist,
        )

    @staticmethod
    def _spotify_download_error(output: str) -> ValueError:
        normalized = output.lower()
        if (
            "sp_dc" in normalized
            or "could not authenticate" in normalized
            or "bad credentials" in normalized
        ):
            return ValueError(
                "Spotify cookies недействительны или устарели. "
                "Загрузите новый cookies.txt из авторизованной сессии."
            )
        if "no reachable spotify access point" in normalized:
            return ValueError(
                "Не удалось подключиться к аудиосерверам Spotify. "
                "Повторите попытку через несколько секунд."
            )
        if (
            "format not available" in normalized
            or "media format" in normalized
            or "vorbis-high" in normalized
            or "very_high" in normalized
        ):
            return ValueError(
                "Spotify не отдал поток 320 кбит/с. Для прямой загрузки "
                "в этом качестве нужен активный Premium-аккаунт."
            )
        return ValueError(
            "Не удалось скачать трек напрямую из Spotify. "
            "Проверьте cookies и активность Premium-подписки."
        )

    def download_spotify(
        self, track: SpotifyTrack, output_id: str, user_id: int = 1
    ) -> Path:
        self.ensure_service_allowed("spotify", user_id)
        cookies_file = self._cookie_file("spotify", user_id)
        if not has_spotify_auth_cookie(cookies_file):
            raise ValueError(
                "Сначала загрузите Spotify cookies.txt в настройках. "
                "В файле должен быть cookie sp_dc с open.spotify.com."
            )

        target = self.download_dir / f"{output_id}.mp3"
        with self.spotify_lock:
            if target.is_file():
                return target
            with tempfile.TemporaryDirectory(
                prefix=f"spotify-{output_id}-", dir=self.download_dir
            ) as temporary_dir:
                work_dir = Path(temporary_dir)
                session_file = work_dir / "session.json"
                source = work_dir / "source.ogg"
                auth_command = [
                    sys.executable,
                    "-m",
                    "app.spotify_auth",
                    str(cookies_file),
                    str(session_file),
                ]
                try:
                    auth_result = subprocess.run(
                        auth_command,
                        capture_output=True,
                        text=True,
                        timeout=2 * 60,
                        check=False,
                    )
                except subprocess.TimeoutExpired as error:
                    raise ValueError(
                        "Spotify не успел подтвердить авторизацию за 2 минуты"
                    ) from error
                auth_output = (
                    f"{auth_result.stdout}\n{auth_result.stderr}".strip()
                )
                if auth_result.returncode != 0 or not session_file.is_file():
                    LOGGER.warning("Spotify authorization failed: %s", auth_output)
                    raise self._spotify_download_error(auth_output)
                try:
                    session_payload = json.loads(
                        session_file.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError) as error:
                    raise ValueError(
                        "Spotify вернул повреждённые данные сессии"
                    ) from error
                if not session_payload.get("premium"):
                    raise ValueError(
                        "Для прямого потока 320 кбит/с нужен активный "
                        "Spotify Premium."
                    )

                player_python = (
                    os.getenv("SPOTIFY_PLAYER_PYTHON", "").strip()
                    or sys.executable
                )
                player_command = [
                    player_python,
                    "-m",
                    "app.spotify_player",
                    str(session_file),
                    track.track_id,
                    str(source),
                ]
                try:
                    result = subprocess.run(
                        player_command,
                        capture_output=True,
                        text=True,
                        timeout=15 * 60,
                        check=False,
                    )
                except subprocess.TimeoutExpired as error:
                    raise ValueError(
                        "Spotify не успел подготовить трек за 15 минут"
                    ) from error
                except OSError as error:
                    raise ValueError(
                        "Python-плеер Spotify не установлен"
                    ) from error
                output = f"{result.stdout}\n{result.stderr}".strip()
                if result.returncode != 0 or not source.is_file():
                    LOGGER.warning("Spotify player failed: %s", output)
                    raise self._spotify_download_error(output)

                cover = None
                if track.thumbnail_url:
                    try:
                        response = requests.get(track.thumbnail_url, timeout=30)
                        response.raise_for_status()
                        if (
                            response.headers.get("Content-Type", "").startswith(
                                "image/"
                            )
                            and len(response.content) <= 10 * 1024 * 1024
                        ):
                            cover = work_dir / "cover.jpg"
                            cover.write_bytes(response.content)
                    except (requests.RequestException, OSError):
                        LOGGER.warning(
                            "Could not download Spotify cover for %s",
                            track.track_id,
                        )
                ffmpeg_command = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                ]
                if cover:
                    ffmpeg_command.extend(["-i", str(cover)])
                ffmpeg_command.extend(["-map", "0:a:0"])
                if cover:
                    ffmpeg_command.extend(
                        [
                            "-map",
                            "1:v:0",
                            "-c:v",
                            "mjpeg",
                            "-disposition:v:0",
                            "attached_pic",
                            "-metadata:s:v",
                            "title=Album cover",
                            "-metadata:s:v",
                            "comment=Cover (front)",
                        ]
                    )
                ffmpeg_command.extend(
                    [
                        "-map_metadata",
                        "0",
                        "-metadata",
                        f"title={track.title}",
                        "-metadata",
                        f"artist={track.artist}",
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "320k",
                        "-id3v2_version",
                        "3",
                        str(target),
                    ]
                )
                conversion = subprocess.run(
                    ffmpeg_command,
                    capture_output=True,
                    text=True,
                    timeout=5 * 60,
                    check=False,
                )
                if conversion.returncode != 0 or not target.is_file():
                    target.unlink(missing_ok=True)
                    raise ValueError("Не удалось преобразовать трек Spotify в MP3")
        return target

    def get_youtube_info(self, url: str, user_id: int = 1) -> YouTubeVideo:
        self.ensure_service_allowed("youtube", user_id)
        url = validate_youtube_url(url)
        errors: list[Exception] = []
        info: dict[str, Any] | None = None
        youtube_cookies_file = self._cookie_file("youtube", user_id)
        cookie_candidates = [youtube_cookies_file, None] if youtube_cookies_file else [None]
        for cookies_file in cookie_candidates:
            options = {
                **self._youtube_options(cookies_file),
                "noplaylist": True,
                "skip_download": True,
            }
            try:
                with self.ydl_lock, yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=False)
                break
            except Exception as error:
                errors.append(error)
        if not info:
            if youtube_cookies_file and not has_youtube_auth_cookies(
                youtube_cookies_file
            ):
                raise ValueError(
                    "youtube-cookies.txt содержит только гостевые cookies. "
                    "Экспортируйте cookies после входа в аккаунт YouTube; в файле "
                    "должны присутствовать SID, SSID, SAPISID или LOGIN_INFO."
                ) from errors[-1]
            if youtube_cookies_file and self.config.youtube_po_token_provider_url:
                raise ValueError(
                    "PO Token provider подключён, но YouTube отклонил account cookies. "
                    "Переэкспортируйте cookies из отдельной incognito-сессии и сразу "
                    "закройте её, чтобы YouTube не ротировал cookies."
                ) from errors[-1]
            raise ValueError(
                "YouTube не отдал видеоформаты. Cookies загружены, но для этого "
                "ролика может требоваться отдельный PO Token."
            ) from errors[-1]
        thumbnail_url = best_thumbnail_url(info)
        if not thumbnail_url:
            raise ValueError("YouTube не вернул превью для этого видео")
        return YouTubeVideo(
            video_id=str(info.get("id") or ""),
            title=str(info.get("title") or "youtube-video"),
            url=str(info.get("webpage_url") or url),
            thumbnail_url=thumbnail_url,
            duration=int(info.get("duration") or 0),
            channel=str(info.get("channel") or info.get("uploader") or ""),
        )

    def download_youtube(
        self, video: YouTubeVideo, output_id: str, user_id: int = 1
    ) -> Path:
        self.ensure_service_allowed("youtube", user_id)
        errors: list[Exception] = []
        youtube_cookies_file = self._cookie_file("youtube", user_id)
        cookie_candidates = [youtube_cookies_file, None] if youtube_cookies_file else [None]
        for cookies_file in cookie_candidates:
            for partial_file in self.download_dir.glob(f"{output_id}.*"):
                partial_file.unlink()
            options = {
                **self._youtube_options(cookies_file),
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mkv",
                "outtmpl": str(self.download_dir / f"{output_id}.%(ext)s"),
                "noplaylist": True,
            }
            try:
                with self.ydl_lock, yt_dlp.YoutubeDL(options) as ydl:
                    ydl.extract_info(video.url, download=True)
                break
            except Exception as error:
                errors.append(error)
        else:
            if youtube_cookies_file and not has_youtube_auth_cookies(
                youtube_cookies_file
            ):
                raise ValueError(
                    "youtube-cookies.txt содержит только гостевые cookies. "
                    "Экспортируйте cookies после входа в аккаунт YouTube; в файле "
                    "должны присутствовать SID, SSID, SAPISID или LOGIN_INFO."
                ) from errors[-1]
            if youtube_cookies_file and self.config.youtube_po_token_provider_url:
                raise ValueError(
                    "PO Token provider подключён, но YouTube отклонил account cookies. "
                    "Переэкспортируйте cookies из отдельной incognito-сессии и сразу "
                    "закройте её, чтобы YouTube не ротировал cookies."
                ) from errors[-1]
            raise ValueError(
                "YouTube не отдал видеоформаты. Cookies загружены, но для этого "
                "ролика может требоваться отдельный PO Token."
            ) from errors[-1]
        matches = [
            path
            for path in self.download_dir.glob(f"{output_id}.*")
            if path.suffix not in {".part", ".ytdl"}
        ]
        if not matches:
            raise FileNotFoundError("Скачанное YouTube-видео не найдено")
        return max(matches, key=lambda path: path.stat().st_size)

    def import_channel(
        self,
        channel: str,
        post_existing: bool,
        chat_id: str | None = None,
        user_id: int = 1,
    ) -> tuple[int, int]:
        self.ensure_service_allowed("tiktok", user_id)
        username, _ = normalize_channel(channel)
        self.storage.add_monitored_tiktok_channel(username, user_id)
        destination = self.storage.telegram_destination(chat_id, user_id).chat_id
        videos = self.scan(channel, user_id)
        if not post_existing:
            for video in videos:
                self.storage.mark(video.video_id, username, user_id)
            self.storage.mark_channel_initialized(username, user_id)
            return len(videos), 0

        published = 0
        for video in videos:
            if self.storage.has(video.video_id, user_id):
                continue
            paths: tuple[Path, ...] = ()
            try:
                prepared_video, paths = self._prepare_media(
                    video.url, video.platform, video.video_id, user_id
                )
                self.publish(prepared_video, paths, chat_id=destination, user_id=user_id)
                self.mark_processed(prepared_video, user_id)
                published += 1
            finally:
                for path in paths:
                    if path.exists():
                        path.unlink()
        self.storage.mark_channel_initialized(username, user_id)
        return len(videos), published

    def publish(
        self,
        video: Video,
        path: Path | tuple[Path, ...],
        quote_text: str | None = None,
        before_text: str = "",
        after_text: str = "",
        chat_id: str | None = None,
        include_author: bool = True,
        include_description: bool = True,
        caption_html: str | None = None,
        user_id: int = 1,
    ) -> None:
        self.ensure_service_allowed(video.platform, user_id)
        target = self.storage.telegram_destination(chat_id, user_id)
        max_length = (
            MAX_MESSAGE_LENGTH if video.media_type == "text" else MAX_CAPTION_LENGTH
        )
        fallback_caption = build_caption(
            video,
            quote_text,
            before_text,
            after_text,
            include_author,
            include_description,
            max_length=max_length,
        )
        caption = resolve_caption_html(
            caption_html,
            fallback_caption,
            max_length=max_length,
        )
        self._publish_media_to_telegram(
            video,
            path,
            target.bot_token,
            target.chat_id,
            caption,
        )

    def _publish_media_to_telegram(
        self,
        video: Video,
        path: Path | tuple[Path, ...],
        bot_token: str,
        chat_id: str,
        caption: str,
        message_thread_id: int | None = None,
    ) -> None:
        paths = (path,) if isinstance(path, Path) else path
        if video.media_type == "text":
            if not caption:
                raise ValueError("Текст публикации пуст")
            text_data: dict[str, Any] = {
                "chat_id": chat_id,
                "text": caption,
                "parse_mode": "HTML",
            }
            if message_thread_id:
                text_data["message_thread_id"] = message_thread_id
            response = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=text_data,
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram API error: {payload}")
            self._telegram_remember_sent_messages(payload, bot_token, chat_id)
            return
        if not paths:
            raise ValueError("Медиафайлы не найдены")

        media_kind = "photo" if video.media_type == "image" else "video"

        def check_response(response: requests.Response) -> None:
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram API error: {payload}")
            self._telegram_remember_sent_messages(payload, bot_token, chat_id)

        def send_single(media_path: Path, item_caption: str) -> None:
            method = "sendPhoto" if media_kind == "photo" else "sendVideo"
            data: dict[str, Any] = {
                "chat_id": chat_id,
                "caption": item_caption,
                "parse_mode": "HTML",
            }
            if media_kind == "video":
                data["supports_streaming"] = "true"
            if message_thread_id:
                data["message_thread_id"] = message_thread_id
            content_type = mimetypes.guess_type(media_path.name)[0] or (
                "image/jpeg" if media_kind == "photo" else "video/mp4"
            )
            with media_path.open("rb") as media_file:
                response = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/{method}",
                    data=data,
                    files={
                        media_kind: (
                            media_path.name,
                            media_file,
                            content_type,
                        )
                    },
                    timeout=180,
                )
            check_response(response)

        if len(paths) == 1:
            send_single(paths[0], caption)
            return

        first_chunk = True
        for chunk_start in range(0, len(paths), 10):
            chunk = paths[chunk_start : chunk_start + 10]
            if len(chunk) == 1:
                send_single(chunk[0], caption if first_chunk else "")
                first_chunk = False
                continue
            files: dict[str, tuple[str, Any, str]] = {}
            open_files = []
            media: list[dict[str, Any]] = []
            try:
                for index, media_path in enumerate(chunk):
                    field_name = f"{media_kind}{index}"
                    file_handle = media_path.open("rb")
                    open_files.append(file_handle)
                    content_type = mimetypes.guess_type(media_path.name)[0] or (
                        "image/jpeg" if media_kind == "photo" else "video/mp4"
                    )
                    files[field_name] = (
                        media_path.name,
                        file_handle,
                        content_type,
                    )
                    item: dict[str, Any] = {
                        "type": media_kind,
                        "media": f"attach://{field_name}",
                    }
                    if media_kind == "video":
                        item["supports_streaming"] = True
                    if first_chunk and index == 0:
                        item["caption"] = caption
                        item["parse_mode"] = "HTML"
                    media.append(item)
                media_data: dict[str, Any] = {
                    "chat_id": chat_id,
                    "media": json.dumps(media),
                }
                if message_thread_id:
                    media_data["message_thread_id"] = message_thread_id
                response = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMediaGroup",
                    data=media_data,
                    files=files,
                    timeout=180,
                )
            finally:
                for file_handle in open_files:
                    file_handle.close()
            check_response(response)
            first_chunk = False

    def publish_youtube(
        self,
        video: YouTubeVideo,
        before_text: str = "",
        after_text: str = "",
        chat_id: str | None = None,
        caption_html: str | None = None,
        user_id: int = 1,
    ) -> None:
        self.ensure_service_allowed("youtube", user_id)
        target = self.storage.telegram_destination(chat_id, user_id)
        self._publish_youtube_to_telegram(
            video, target.bot_token, target.chat_id,
            before_text=before_text, after_text=after_text, caption_html=caption_html,
        )

    def _publish_youtube_to_telegram(
        self,
        video: YouTubeVideo,
        bot_token: str,
        chat_id: str,
        before_text: str = "",
        after_text: str = "",
        caption_html: str | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": video.thumbnail_url,
            "caption": resolve_caption_html(
                caption_html, build_youtube_caption(video, before_text, after_text)
            ),
            "parse_mode": "HTML",
        }
        if message_thread_id:
            data["message_thread_id"] = message_thread_id
        url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        response = requests.post(
            url,
            data=data,
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")
        self._telegram_remember_sent_messages(payload, bot_token, chat_id)

    def publish_spotify(
        self,
        track: SpotifyTrack,
        path: Path,
        before_text: str = "",
        after_text: str = "",
        chat_id: str | None = None,
        caption_html: str | None = None,
        user_id: int = 1,
    ) -> None:
        self.ensure_service_allowed("spotify", user_id)
        target = self.storage.telegram_destination(chat_id, user_id)
        self._publish_spotify_to_telegram(
            track,
            path,
            target.bot_token,
            target.chat_id,
            before_text=before_text,
            after_text=after_text,
            caption_html=caption_html,
        )

    def _publish_spotify_to_telegram(
        self,
        track: SpotifyTrack,
        path: Path,
        bot_token: str,
        chat_id: str,
        before_text: str = "",
        after_text: str = "",
        caption_html: str | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        caption = resolve_caption_html(
            caption_html,
            build_spotify_caption(track, before_text, after_text),
        )
        cover_content = None
        if track.thumbnail_url:
            try:
                thumbnail_response = requests.get(track.thumbnail_url, timeout=30)
                thumbnail_response.raise_for_status()
                content_type = thumbnail_response.headers.get("Content-Type", "")
                if (
                    content_type.startswith("image/jpeg")
                    and len(thumbnail_response.content) <= 200 * 1024
                ):
                    cover_content = thumbnail_response.content
            except requests.RequestException as error:
                LOGGER.info(
                    "Could not attach Spotify cover for %s: %s",
                    track.track_id,
                    error,
                )

        if cover_content:
            photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            photo_data: dict[str, Any] = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML",
            }
            if message_thread_id:
                photo_data["message_thread_id"] = message_thread_id
            with BytesIO(cover_content) as photo_file:
                photo_response = requests.post(
                    photo_url,
                    data=photo_data,
                    files={"photo": ("cover.jpg", photo_file, "image/jpeg")},
                    timeout=60,
                )
            photo_response.raise_for_status()
            photo_payload = photo_response.json()
            if not photo_payload.get("ok"):
                raise RuntimeError(f"Telegram API error: {photo_payload}")
            self._telegram_remember_sent_messages(photo_payload, bot_token, chat_id)

        audio_url = f"https://api.telegram.org/bot{bot_token}/sendAudio"
        thumbnail_file = BytesIO(cover_content) if cover_content else None
        files: dict[str, tuple[str, Any, str]] = {}
        if thumbnail_file:
            files["thumbnail"] = ("cover.jpg", thumbnail_file, "image/jpeg")
        audio_data: dict[str, Any] = {
            "chat_id": chat_id,
            "caption": "" if cover_content else caption,
            "parse_mode": "HTML",
            "title": track.title,
            "performer": track.artist,
        }
        if message_thread_id:
            audio_data["message_thread_id"] = message_thread_id
        with path.open("rb") as audio_file:
            files["audio"] = (path.name, audio_file, "audio/mpeg")
            try:
                response = requests.post(
                    audio_url,
                    data=audio_data,
                    files=files,
                    timeout=300,
                )
            finally:
                if thumbnail_file:
                    thumbnail_file.close()
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")

        self._telegram_remember_sent_messages(payload, bot_token, chat_id)

    def process_channel(self, channel: str, user_id: int = 1) -> None:
        username, _ = normalize_channel(channel)
        videos = self.scan(channel, user_id)
        LOGGER.info("Found %d recent videos for @%s", len(videos), username)

        if not self.config.post_existing and not self.storage.is_channel_initialized(username, user_id):
            for video in videos:
                self.mark_processed(video, user_id)
            self.storage.mark_channel_initialized(username, user_id)
            LOGGER.info("Initial videos for @%s marked as processed", username)
            return

        for video in videos:
            if self.storage.has(video.video_id, user_id):
                continue
            paths: tuple[Path, ...] = ()
            try:
                LOGGER.info("Processing %s", video.url)
                prepared_video, paths = self._prepare_media(
                    video.url, video.platform, video.video_id, user_id
                )
                self.publish(prepared_video, paths, user_id=user_id)
                self.mark_processed(prepared_video, user_id)
                LOGGER.info("Published %s", video.url)
            finally:
                for path in paths:
                    if path.exists():
                        path.unlink()
        self.storage.mark_channel_initialized(username, user_id)

    def run_forever(self) -> None:
        LOGGER.info("TikTok monitor started")
        while True:
            sleep_seconds = self.poll_interval_seconds()
            for user in self.storage.active_users():
                if not user.allow_tiktok:
                    continue
                sleep_seconds = min(sleep_seconds, self.poll_interval_seconds(user.id))
                for channel in self.monitored_tiktok_channels(user.id):
                    try:
                        self.process_channel(channel, user.id)
                    except Exception:
                        LOGGER.exception(
                            "Failed to process channel %s for user %s",
                            channel,
                            user.username,
                        )
            time.sleep(sleep_seconds)

    def run_telegram_commands_forever(self) -> None:
        LOGGER.info("Telegram message monitor started")
        while True:
            bot_count = self.poll_telegram_commands_once()
            time.sleep(2 if bot_count else 10)
