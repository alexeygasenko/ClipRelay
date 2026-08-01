from __future__ import annotations

from typing import Any


SERVICE_META: dict[str, dict[str, str]] = {
    "tiktok": {
        "label": "TikTok",
        "description": "Видео, фотопосты и мониторинг каналов",
    },
    "instagram": {
        "label": "Instagram",
        "description": "Reels, видео и карусели изображений",
    },
    "twitter": {
        "label": "X (Twitter)",
        "description": "Видео, изображения и текстовые посты",
    },
    "reddit": {
        "label": "Reddit",
        "description": "Видео, галереи и текстовые публикации",
    },
    "youtube": {
        "label": "YouTube",
        "description": "Видео, превью и Telegram-посты",
    },
    "spotify": {
        "label": "Spotify",
        "description": "MP3 и Telegram-посты",
    },
}


def user_payload(user: Any | None) -> dict[str, Any] | None:
    if user is None:
        return None
    permissions = {
        service: bool(getattr(user, f"allow_{service}", False))
        for service in SERVICE_META
    }
    return {
        "id": int(user.id),
        "username": str(user.username),
        "is_admin": bool(getattr(user, "is_admin", False)),
        "is_disabled": bool(getattr(user, "is_disabled", False)),
        "must_set_password": bool(getattr(user, "must_set_password", False)),
        "created_at": str(getattr(user, "created_at", "") or ""),
        "permissions": permissions,
        **{f"allow_{service}": allowed for service, allowed in permissions.items()},
    }


def channel_payload(channel: Any) -> dict[str, str]:
    chat_id = str(channel.chat_id)
    destination_type = str(
        getattr(channel, "destination_type", "channel") or "channel"
    )
    private_id = chat_id.lstrip("-") if chat_id.lstrip("-").isdigit() else ""
    return {
        "name": str(channel.name),
        "chat_id": chat_id,
        "destination_type": destination_type,
        "display": (
            f"ID {private_id}"
            if private_id
            else chat_id
        ),
        "kind_label": (
            "чат"
            if destination_type in {"group", "supergroup", "private"}
            else "канал"
        ),
    }


def video_payload(video: Any) -> dict[str, Any]:
    username = str(video.username)
    return {
        "id": str(video.video_id),
        "platform": str(video.platform),
        "media_type": str(video.media_type),
        "username": username,
        "author": f"@{username}" if username else "Автор не указан",
        "author_url": str(video.author_url or ""),
        "description": str(getattr(video, "description", "") or ""),
        "url": str(video.url),
        "timestamp": int(video.timestamp or 0),
    }


def youtube_payload(video: Any) -> dict[str, Any]:
    return {
        "id": str(video.video_id),
        "title": str(video.title),
        "channel": str(video.channel),
        "url": str(video.url),
        "thumbnail_url": str(video.thumbnail_url or ""),
        "description": str(getattr(video, "description", "") or ""),
    }


def spotify_payload(track: Any) -> dict[str, Any]:
    return {
        "id": str(track.track_id),
        "title": str(track.title),
        "artist": str(track.artist),
        "url": str(track.url),
        "thumbnail_url": str(track.thumbnail_url or ""),
    }
