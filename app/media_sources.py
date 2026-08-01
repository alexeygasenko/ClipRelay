from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from yt_dlp.jsinterp import js_number_to_string


TWITTER_HOSTS = {
    "twitter.com",
    "www.twitter.com",
    "m.twitter.com",
    "mobile.twitter.com",
    "x.com",
    "www.x.com",
    "m.x.com",
    "mobile.x.com",
}
REDDIT_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "new.reddit.com",
    "np.reddit.com",
    "m.reddit.com",
    "redd.it",
    "www.redd.it",
}
REDDIT_IMAGE_HOSTS = {
    "i.redd.it",
    "preview.redd.it",
    "external-preview.redd.it",
    "redditmedia.com",
    "www.redditmedia.com",
}

TWITTER_STATUS_PATH_RE = re.compile(
    r"^/(?:i/(?:web/)?status/\d+|[^/]+/status/\d+|statuses/\d+)"
    r"(?:/(?:photo|video)/\d+)?/?$",
    flags=re.IGNORECASE,
)
REDDIT_POST_PATH_RE = re.compile(
    r"^/(?:"
    r"(?:r|user|u)/[^/]+/comments/[^/?#]+(?:/[^/?#]+){0,2}"
    r"|comments/[^/?#]+(?:/[^/?#]+){0,2}"
    r"|(?:r/[^/]+/)?gallery/[^/?#]+"
    r"|(?:r/[^/]+/)?s/[^/?#]+"
    r"|[^/?#]+"
    r")/?$",
    flags=re.IGNORECASE,
)
TWITTER_URL_IN_TEXT_RE = re.compile(
    r"https?://(?:(?:www|m|mobile)\.)?(?:twitter|x)\.com/[^\s<>]+",
    flags=re.IGNORECASE,
)
REDDIT_URL_IN_TEXT_RE = re.compile(
    r"https?://(?:(?:www|old|new|np|m)\.)?reddit\.com/[^\s<>]+"
    r"|https?://(?:www\.)?redd\.it/[^\s<>]+",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class MediaSourcePost:
    post_id: str
    username: str
    description: str
    webpage_url: str
    timestamp: int
    platform: str
    author_url: str
    media_type: str
    image_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class MediaPlatform:
    name: str
    validator: Callable[[str], str]


def _parsed_http_url(url: str, error_message: str):
    normalized = (url or "").strip()
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(error_message)
    try:
        parsed.port
    except ValueError:
        raise ValueError(error_message) from None
    return normalized, parsed


def validate_tiktok_url(url: str) -> str:
    normalized, parsed = _parsed_http_url(
        url, "Нужна полная ссылка на видео с домена tiktok.com"
    )
    host = (parsed.hostname or "").lower()
    if not (host == "tiktok.com" or host.endswith(".tiktok.com")):
        raise ValueError("Нужна полная ссылка на видео с домена tiktok.com")
    return normalized


def validate_instagram_url(url: str) -> str:
    normalized, parsed = _parsed_http_url(url, "Нужна полная ссылка на пост Instagram")
    host = (parsed.hostname or "").lower()
    if not (host == "instagram.com" or host.endswith(".instagram.com")):
        raise ValueError("Нужна полная ссылка на пост Instagram")
    return normalized


def validate_twitter_url(url: str) -> str:
    normalized, parsed = _parsed_http_url(url, "Нужна полная ссылка на пост X / Twitter")
    if (parsed.hostname or "").lower() not in TWITTER_HOSTS or not TWITTER_STATUS_PATH_RE.fullmatch(
        parsed.path
    ):
        raise ValueError("Нужна полная ссылка на пост X / Twitter")
    return normalized


def validate_reddit_url(url: str) -> str:
    normalized, parsed = _parsed_http_url(url, "Нужна полная ссылка на пост Reddit")
    host = (parsed.hostname or "").lower()
    if host not in REDDIT_HOSTS or not REDDIT_POST_PATH_RE.fullmatch(parsed.path):
        raise ValueError("Нужна полная ссылка на пост Reddit")
    if host.endswith("reddit.com") and parsed.path.count("/") < 2:
        raise ValueError("Нужна полная ссылка на пост Reddit")
    return normalized


def is_instagram_url(url: str) -> bool:
    try:
        validate_instagram_url(url)
        return True
    except ValueError:
        return False


def is_twitter_url(url: str) -> bool:
    try:
        validate_twitter_url(url)
        return True
    except ValueError:
        return False


def is_reddit_url(url: str) -> bool:
    try:
        validate_reddit_url(url)
        return True
    except ValueError:
        return False


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


def twitter_post_id_from_url(url: str) -> str:
    match = re.search(r"/(?:status|statuses)/(\d+)", urlparse(url).path)
    return match.group(1) if match else ""


def reddit_post_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"redd.it", "www.redd.it"}:
        return parsed.path.strip("/").split("/", 1)[0]
    match = re.search(r"/(?:comments|gallery)/([^/?#]+)", parsed.path)
    return match.group(1) if match else ""


MEDIA_SOURCE_REGISTRY: dict[str, MediaPlatform] = {
    "tiktok": MediaPlatform("tiktok", validate_tiktok_url),
    "instagram": MediaPlatform("instagram", validate_instagram_url),
    "twitter": MediaPlatform("twitter", validate_twitter_url),
    "reddit": MediaPlatform("reddit", validate_reddit_url),
}


def detect_media_platform(url: str) -> str:
    _, parsed = _parsed_http_url(
        url,
        "Поддерживаются ссылки TikTok, Instagram, X / Twitter и Reddit",
    )
    host = (parsed.hostname or "").lower()
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        platform = "tiktok"
    elif host == "instagram.com" or host.endswith(".instagram.com"):
        platform = "instagram"
    elif host in TWITTER_HOSTS:
        platform = "twitter"
    elif host in REDDIT_HOSTS:
        platform = "reddit"
    else:
        raise ValueError("Поддерживаются ссылки TikTok, Instagram, X / Twitter и Reddit")
    MEDIA_SOURCE_REGISTRY[platform].validator(url)
    return platform


def _url_from_text(
    text: str,
    pattern: re.Pattern[str],
    validator: Callable[[str], str],
    error_message: str,
) -> str:
    for match in pattern.finditer(text or ""):
        try:
            return validator(match.group(0).rstrip(".,);]"))
        except ValueError:
            continue
    raise ValueError(error_message)


def twitter_url_from_text(text: str) -> str:
    return _url_from_text(
        text,
        TWITTER_URL_IN_TEXT_RE,
        validate_twitter_url,
        "Добавьте ссылку на пост X / Twitter после команды /x",
    )


def reddit_url_from_text(text: str) -> str:
    return _url_from_text(
        text,
        REDDIT_URL_IN_TEXT_RE,
        validate_reddit_url,
        "Добавьте ссылку на пост Reddit после команды /reddit",
    )


def _timestamp(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        return 0
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0
    return int(parsed.timestamp())


def _unique_urls(urls: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        key = url
        if key in seen:
            continue
        seen.add(key)
        result.append(url)
    return tuple(result)


def _twitter_original_image_url(url: str) -> str:
    parsed = urlparse(html.unescape(url))
    if (parsed.hostname or "").lower() != "pbs.twimg.com":
        return url
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["name"] = "orig"
    return urlunparse(parsed._replace(query=urlencode(query)))


def parse_twitter_post(payload: Any, webpage_url: str) -> MediaSourcePost:
    if not isinstance(payload, dict):
        raise ValueError("X / Twitter не вернул данные публикации")
    status = payload.get("retweeted_status") or payload.get("retweeted_tweet") or payload
    if not isinstance(status, dict):
        status = payload
    post_id = str(
        status.get("id_str")
        or status.get("id")
        or payload.get("id_str")
        or payload.get("id")
        or twitter_post_id_from_url(webpage_url)
    )
    user = status.get("user") or payload.get("user") or {}
    if not isinstance(user, dict):
        user = {}
    username = str(
        user.get("screen_name")
        or user.get("username")
        or status.get("uploader_id")
        or "twitter"
    ).strip().lstrip("@")
    description = str(
        status.get("full_text")
        or status.get("text")
        or payload.get("full_text")
        or payload.get("text")
        or ""
    ).strip()
    media = (
        (status.get("extended_entities") or {}).get("media")
        if isinstance(status.get("extended_entities"), dict)
        else None
    )
    if not isinstance(media, list):
        media = status.get("mediaDetails")
    if not isinstance(media, list):
        media = payload.get("mediaDetails")
    if not isinstance(media, list):
        media = []

    image_urls: list[str] = []
    has_video = False
    for item in media:
        if not isinstance(item, dict):
            continue
        media_type = str(item.get("type") or "").lower()
        if media_type in {"video", "animated_gif"} or item.get("video_info"):
            has_video = True
            continue
        if media_type != "photo":
            continue
        image_url = str(item.get("media_url_https") or item.get("media_url") or "")
        if image_url:
            image_urls.append(_twitter_original_image_url(image_url))

    images = _unique_urls(image_urls)
    media_type = "video" if has_video else ("image" if images else "text")
    timestamp = _timestamp(
        status.get("created_at")
        or payload.get("created_at")
        or status.get("timestamp")
    )
    author_url = f"https://x.com/{username}" if username != "twitter" else webpage_url
    return MediaSourcePost(
        post_id=post_id,
        username=username,
        description=description,
        webpage_url=webpage_url,
        timestamp=timestamp,
        platform="twitter",
        author_url=author_url,
        media_type=media_type,
        image_urls=images,
    )


def twitter_syndication_token(post_id: str) -> str:
    translation = str.maketrans(dict.fromkeys("0."))
    return js_number_to_string((int(post_id) / 1e15) * math.pi, 36).translate(translation)


def fetch_twitter_syndication_post(
    session: Any,
    webpage_url: str,
    *,
    timeout: int = 60,
) -> MediaSourcePost:
    validated = validate_twitter_url(webpage_url)
    post_id = twitter_post_id_from_url(validated)
    if not post_id:
        raise ValueError("Не удалось определить ID публикации X / Twitter")
    response = session.get(
        "https://cdn.syndication.twimg.com/tweet-result",
        params={"id": post_id, "token": twitter_syndication_token(post_id)},
        headers={"User-Agent": "Googlebot"},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_twitter_post(response.json(), validated)


def _reddit_post_data(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and "data" in payload:
        children = (payload.get("data") or {}).get("children")
        if isinstance(children, list) and children:
            child = children[0]
            if isinstance(child, dict) and isinstance(child.get("data"), dict):
                return child["data"]
    if isinstance(payload, list) and payload:
        return _reddit_post_data(payload[0])
    if isinstance(payload, dict):
        return payload
    raise ValueError("Reddit не вернул данные публикации")


def _reddit_media_data(post: dict[str, Any]) -> dict[str, Any]:
    target_url = str(post.get("url_overridden_by_dest") or post.get("url") or "")
    target_host = (urlparse(target_url).hostname or "").lower()
    has_local_media = any(
        (
            post.get("is_video"),
            post.get("gallery_data"),
            post.get("media_metadata"),
            post.get("secure_media"),
            post.get("media"),
            str(post.get("post_hint") or "").lower() == "image",
            target_host == "v.redd.it",
        )
    )
    if has_local_media:
        return post
    crossposts = post.get("crosspost_parent_list")
    if isinstance(crossposts, list) and crossposts and isinstance(crossposts[0], dict):
        return crossposts[0]
    return post


def _reddit_video_present(post: dict[str, Any]) -> bool:
    if post.get("is_video"):
        return True
    target_url = str(post.get("url_overridden_by_dest") or post.get("url") or "")
    if (urlparse(target_url).hostname or "").lower() == "v.redd.it":
        return True
    for key in ("secure_media", "media"):
        media = post.get(key)
        if isinstance(media, dict) and isinstance(media.get("reddit_video"), dict):
            return True
    metadata = post.get("media_metadata")
    if isinstance(metadata, dict):
        return any(
            isinstance(item, dict)
            and str(item.get("e") or "").lower() == "redditvideo"
            for item in metadata.values()
        )
    return False


def _reddit_gallery_image_urls(post: dict[str, Any]) -> tuple[str, ...]:
    metadata = post.get("media_metadata")
    if not isinstance(metadata, dict):
        return ()
    gallery = post.get("gallery_data")
    gallery_items = gallery.get("items") if isinstance(gallery, dict) else None
    media_ids = [
        str(item.get("media_id") or "")
        for item in (gallery_items or [])
        if isinstance(item, dict) and item.get("media_id")
    ]
    if not media_ids:
        media_ids = list(metadata)

    urls: list[str] = []
    for media_id in media_ids:
        item = metadata.get(media_id)
        if not isinstance(item, dict):
            continue
        source = item.get("s")
        if not isinstance(source, dict):
            continue
        image_url = source.get("u") or source.get("gif")
        if image_url:
            urls.append(html.unescape(str(image_url)))
    return _unique_urls(urls)


def _reddit_single_image_url(post: dict[str, Any]) -> tuple[str, ...]:
    if str(post.get("post_hint") or "").lower() != "image":
        return ()
    image_url = str(post.get("url_overridden_by_dest") or post.get("url") or "")
    parsed = urlparse(image_url)
    host = (parsed.hostname or "").lower()
    if host not in REDDIT_IMAGE_HOSTS and not host.endswith(".redditmedia.com"):
        return ()
    return _unique_urls([html.unescape(image_url)])


def parse_reddit_post(payload: Any, webpage_url: str) -> MediaSourcePost:
    post = _reddit_post_data(payload)
    media_post = _reddit_media_data(post)
    post_id = str(post.get("id") or reddit_post_id_from_url(webpage_url))
    username = str(post.get("author") or "reddit").strip().lstrip("@")
    title = str(post.get("title") or "").strip()
    selftext = str(post.get("selftext") or "").strip()
    description = "\n\n".join(
        part for index, part in enumerate((title, selftext)) if part and (index == 0 or part != title)
    )
    permalink = str(post.get("permalink") or "")
    canonical_url = (
        f"https://www.reddit.com{permalink}"
        if permalink.startswith("/")
        else webpage_url
    )
    image_urls = _reddit_gallery_image_urls(media_post) or _reddit_single_image_url(media_post)
    media_type = (
        "video"
        if _reddit_video_present(media_post)
        else ("image" if image_urls else "text")
    )
    author_url = (
        f"https://www.reddit.com/user/{username}/" if username != "reddit" else canonical_url
    )
    return MediaSourcePost(
        post_id=post_id,
        username=username,
        description=description,
        webpage_url=canonical_url,
        timestamp=_timestamp(post.get("created_utc")),
        platform="reddit",
        author_url=author_url,
        media_type=media_type,
        image_urls=image_urls,
    )


def _reddit_needs_resolution(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host in {"redd.it", "www.redd.it"} or bool(
        re.search(r"/(?:r/[^/]+/)?s/[^/]+", parsed.path, flags=re.IGNORECASE)
    )


def fetch_reddit_post(
    session: Any,
    webpage_url: str,
    *,
    timeout: int = 60,
) -> MediaSourcePost:
    validated = validate_reddit_url(webpage_url)
    resolved = validated
    if _reddit_needs_resolution(validated):
        redirect_response = session.get(validated, allow_redirects=True, timeout=timeout)
        redirect_response.raise_for_status()
        resolved = validate_reddit_url(str(redirect_response.url))
    parsed = urlparse(resolved)
    json_path = parsed.path.rstrip("/") + ".json"
    json_url = urlunparse(parsed._replace(path=json_path, query="", fragment=""))
    response = session.get(
        json_url,
        params={"raw_json": 1},
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_reddit_post(response.json(), resolved)
