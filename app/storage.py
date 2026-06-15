from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TelegramDestination:
    name: str
    chat_id: str
    bot_token: str
    is_default: bool = False


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_videos (
                video_id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS initialized_channels (
                channel TEXT PRIMARY KEY,
                initialized_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_destinations (
                chat_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                bot_token TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deleted_telegram_destinations (
                chat_id TEXT PRIMARY KEY,
                deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.commit()

    def has(self, video_id: str) -> bool:
        with self.lock:
            row = self.connection.execute(
                "SELECT 1 FROM processed_videos WHERE video_id = ?", (video_id,)
            ).fetchone()
        return row is not None

    def mark(self, video_id: str, channel: str) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO processed_videos(video_id, channel) VALUES (?, ?)",
                (video_id, channel),
            )
            self.connection.commit()

    def is_channel_initialized(self, channel: str) -> bool:
        with self.lock:
            row = self.connection.execute(
                "SELECT 1 FROM initialized_channels WHERE channel = ?", (channel,)
            ).fetchone()
        return row is not None

    def mark_channel_initialized(self, channel: str) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO initialized_channels(channel) VALUES (?)", (channel,)
            )
            self.connection.commit()

    def add_telegram_destination(
        self,
        name: str,
        chat_id: str,
        bot_token: str,
        *,
        is_default: bool = False,
        replace: bool = True,
    ) -> None:
        with self.lock:
            if not replace:
                deleted = self.connection.execute(
                    "SELECT 1 FROM deleted_telegram_destinations WHERE chat_id = ?",
                    (chat_id,),
                ).fetchone()
                if deleted:
                    return
                existing = self.connection.execute(
                    "SELECT 1 FROM telegram_destinations WHERE chat_id = ?",
                    (chat_id,),
                ).fetchone()
                if existing:
                    has_default = self.connection.execute(
                        "SELECT 1 FROM telegram_destinations WHERE is_default = 1 LIMIT 1"
                    ).fetchone()
                    if is_default and not has_default:
                        self.connection.execute(
                            "UPDATE telegram_destinations SET is_default = 1 WHERE chat_id = ?",
                            (chat_id,),
                        )
                        self.connection.commit()
                    return
            else:
                self.connection.execute(
                    "DELETE FROM deleted_telegram_destinations WHERE chat_id = ?",
                    (chat_id,),
                )
            if is_default:
                self.connection.execute(
                    "UPDATE telegram_destinations SET is_default = 0"
                )
            query = (
                """
                INSERT INTO telegram_destinations(name, chat_id, bot_token, is_default)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    name = excluded.name,
                    bot_token = excluded.bot_token,
                    is_default = CASE
                        WHEN excluded.is_default = 1 THEN 1
                        ELSE telegram_destinations.is_default
                    END
                """
                if replace
                else """
                INSERT OR IGNORE INTO telegram_destinations(name, chat_id, bot_token, is_default)
                VALUES (?, ?, ?, ?)
                """
            )
            self.connection.execute(query, (name, chat_id, bot_token, int(is_default)))
            self.connection.commit()

    def delete_telegram_destination(
        self, chat_id: str, *, remember: bool = True
    ) -> None:
        with self.lock:
            count = self.connection.execute(
                "SELECT COUNT(*) FROM telegram_destinations"
            ).fetchone()[0]
            if count <= 1:
                raise ValueError("Нельзя удалить единственный Telegram-канал")
            row = self.connection.execute(
                "SELECT is_default FROM telegram_destinations WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            if not row:
                raise ValueError("Telegram-канал уже удалён")
            self.connection.execute(
                "DELETE FROM telegram_destinations WHERE chat_id = ?", (chat_id,)
            )
            if remember:
                self.connection.execute(
                    "INSERT OR REPLACE INTO deleted_telegram_destinations(chat_id) VALUES (?)",
                    (chat_id,),
                )
            if row[0]:
                next_chat_id = self.connection.execute(
                    "SELECT chat_id FROM telegram_destinations ORDER BY created_at LIMIT 1"
                ).fetchone()[0]
                self.connection.execute(
                    "UPDATE telegram_destinations SET is_default = 1 WHERE chat_id = ?",
                    (next_chat_id,),
                )
            self.connection.commit()

    def canonicalize_telegram_destination(
        self, previous_chat_id: str, name: str, chat_id: str, bot_token: str
    ) -> None:
        with self.lock:
            previous = self.connection.execute(
                "SELECT is_default FROM telegram_destinations WHERE chat_id = ?",
                (previous_chat_id,),
            ).fetchone()
            existing = self.connection.execute(
                "SELECT is_default FROM telegram_destinations WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            is_default = bool((previous and previous[0]) or (existing and existing[0]))
            self.connection.execute(
                "DELETE FROM telegram_destinations WHERE chat_id IN (?, ?)",
                (previous_chat_id, chat_id),
            )
            self.connection.execute(
                """
                INSERT INTO telegram_destinations(name, chat_id, bot_token, is_default)
                VALUES (?, ?, ?, ?)
                """,
                (name, chat_id, bot_token, int(is_default)),
            )
            self.connection.execute(
                "DELETE FROM deleted_telegram_destinations WHERE chat_id = ?",
                (chat_id,),
            )
            self.connection.commit()

    def telegram_destinations(self) -> tuple[TelegramDestination, ...]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT name, chat_id, bot_token, is_default
                FROM telegram_destinations
                ORDER BY is_default DESC, name COLLATE NOCASE, chat_id
                """
            ).fetchall()
        return tuple(
            TelegramDestination(name, chat_id, bot_token, bool(is_default))
            for name, chat_id, bot_token, is_default in rows
        )

    def telegram_destination(self, chat_id: str | None = None) -> TelegramDestination:
        with self.lock:
            if chat_id:
                row = self.connection.execute(
                    """
                    SELECT name, chat_id, bot_token, is_default
                    FROM telegram_destinations WHERE chat_id = ?
                    """,
                    (chat_id,),
                ).fetchone()
            else:
                row = self.connection.execute(
                    """
                    SELECT name, chat_id, bot_token, is_default
                    FROM telegram_destinations
                    ORDER BY is_default DESC, created_at
                    LIMIT 1
                    """
                ).fetchone()
        if not row:
            raise ValueError("Выбран неизвестный Telegram-канал")
        return TelegramDestination(row[0], row[1], row[2], bool(row[3]))
