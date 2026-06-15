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
    destination_type: str = "channel"
    sort_order: int = 0


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
        columns = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA table_info(telegram_destinations)"
            ).fetchall()
        }
        if "destination_type" not in columns:
            self.connection.execute(
                "ALTER TABLE telegram_destinations ADD COLUMN destination_type TEXT NOT NULL DEFAULT 'channel'"
            )
        if "sort_order" not in columns:
            self.connection.execute(
                "ALTER TABLE telegram_destinations ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deleted_telegram_destinations (
                chat_id TEXT PRIMARY KEY,
                deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS monitored_tiktok_channels (
                channel TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
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
        destination_type: str = "channel",
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
                INSERT INTO telegram_destinations(name, chat_id, bot_token, is_default, destination_type, sort_order)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT MAX(sort_order) + 1 FROM telegram_destinations), 0))
                ON CONFLICT(chat_id) DO UPDATE SET
                    name = excluded.name,
                    bot_token = excluded.bot_token,
                    destination_type = excluded.destination_type,
                    is_default = CASE
                        WHEN excluded.is_default = 1 THEN 1
                        ELSE telegram_destinations.is_default
                    END
                """
                if replace
                else """
                INSERT OR IGNORE INTO telegram_destinations(name, chat_id, bot_token, is_default, destination_type, sort_order)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT MAX(sort_order) + 1 FROM telegram_destinations), 0))
                """
            )
            self.connection.execute(
                query, (name, chat_id, bot_token, int(is_default), destination_type)
            )
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
                    "SELECT chat_id FROM telegram_destinations ORDER BY sort_order, created_at LIMIT 1"
                ).fetchone()[0]
                self.connection.execute(
                    "UPDATE telegram_destinations SET is_default = 1 WHERE chat_id = ?",
                    (next_chat_id,),
                )
            self.connection.commit()

    def canonicalize_telegram_destination(
        self,
        previous_chat_id: str,
        name: str,
        chat_id: str,
        bot_token: str,
        destination_type: str = "channel",
    ) -> None:
        with self.lock:
            previous = self.connection.execute(
                "SELECT is_default, sort_order FROM telegram_destinations WHERE chat_id = ?",
                (previous_chat_id,),
            ).fetchone()
            existing = self.connection.execute(
                "SELECT is_default, sort_order FROM telegram_destinations WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            is_default = bool((previous and previous[0]) or (existing and existing[0]))
            sort_order = min(
                [row[1] for row in (previous, existing) if row is not None] or [0]
            )
            self.connection.execute(
                "DELETE FROM telegram_destinations WHERE chat_id IN (?, ?)",
                (previous_chat_id, chat_id),
            )
            self.connection.execute(
                """
                INSERT INTO telegram_destinations(name, chat_id, bot_token, is_default, destination_type, sort_order)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, chat_id, bot_token, int(is_default), destination_type, sort_order),
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
                SELECT name, chat_id, bot_token, is_default, destination_type, sort_order
                FROM telegram_destinations
                ORDER BY sort_order, is_default DESC, name COLLATE NOCASE, chat_id
                """
            ).fetchall()
        return tuple(
            TelegramDestination(
                name, chat_id, bot_token, bool(is_default), destination_type, sort_order
            )
            for name, chat_id, bot_token, is_default, destination_type, sort_order in rows
        )

    def telegram_destination(self, chat_id: str | None = None) -> TelegramDestination:
        with self.lock:
            if chat_id:
                row = self.connection.execute(
                    """
                    SELECT name, chat_id, bot_token, is_default, destination_type, sort_order
                    FROM telegram_destinations WHERE chat_id = ?
                    """,
                    (chat_id,),
                ).fetchone()
            else:
                row = self.connection.execute(
                    """
                    SELECT name, chat_id, bot_token, is_default, destination_type, sort_order
                    FROM telegram_destinations
                    ORDER BY sort_order, is_default DESC, created_at
                    LIMIT 1
                    """
                ).fetchone()
        if not row:
            raise ValueError("Выбран неизвестный Telegram-канал")
        return TelegramDestination(row[0], row[1], row[2], bool(row[3]), row[4], row[5])

    def move_telegram_destination(self, chat_id: str, direction: int) -> None:
        destinations = list(self.telegram_destinations())
        index = next(
            (position for position, item in enumerate(destinations) if item.chat_id == chat_id),
            None,
        )
        if index is None:
            raise ValueError("Telegram-направление не найдено")
        target = index + direction
        if target < 0 or target >= len(destinations):
            return
        destinations[index], destinations[target] = destinations[target], destinations[index]
        with self.lock:
            self.connection.executemany(
                "UPDATE telegram_destinations SET sort_order = ? WHERE chat_id = ?",
                [(position, item.chat_id) for position, item in enumerate(destinations)],
            )
            self.connection.commit()

    def add_monitored_tiktok_channel(self, channel: str) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO monitored_tiktok_channels(channel) VALUES (?)",
                (channel,),
            )
            self.connection.commit()

    def delete_monitored_tiktok_channel(self, channel: str) -> None:
        with self.lock:
            self.connection.execute(
                "DELETE FROM monitored_tiktok_channels WHERE channel = ?", (channel,)
            )
            self.connection.commit()

    def monitored_tiktok_channels(self) -> tuple[str, ...]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT channel FROM monitored_tiktok_channels ORDER BY created_at, channel"
            ).fetchall()
        return tuple(row[0] for row in rows)

    def setting(self, key: str, default: str) -> str:
        with self.lock:
            row = self.connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str, *, only_if_missing: bool = False) -> None:
        query = (
            "INSERT OR IGNORE INTO app_settings(key, value) VALUES (?, ?)"
            if only_if_missing
            else """
            INSERT INTO app_settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        with self.lock:
            self.connection.execute(query, (key, value))
            self.connection.commit()
