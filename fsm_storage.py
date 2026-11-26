"""Персистентное FSM-хранилище на SQLite (переживает рестарт/деплой бота).

Интерфейс aiogram 3 BaseStorage. Данные — JSON, поэтому всё, что кладётся
в state.update_data(), должно быть JSON-сериализуемым (у нас так и есть:
строки, числа, списки, словари из JSON API).
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.fsm.storage.base import BaseStorage, StorageKey

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm_state (
    key TEXT PRIMARY KEY,
    state TEXT
);
CREATE TABLE IF NOT EXISTS fsm_data (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""


def _pack(key: StorageKey) -> str:
    return ":".join(str(v) for v in (
        key.bot_id, key.chat_id, key.user_id,
        key.thread_id, key.business_connection_id, key.destiny,
    ))


class SQLiteStorage(BaseStorage):
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    async def set_state(self, bot: Bot, key: StorageKey, state: Optional[str] = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO fsm_state (key, state) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET state = excluded.state",
                (_pack(key), state),
            )
            self._conn.commit()

    async def get_state(self, bot: Bot, key: StorageKey) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM fsm_state WHERE key = ?", (_pack(key),)
            ).fetchone()
        return row[0] if row else None

    async def set_data(self, bot: Bot, key: StorageKey, data: Dict[str, Any]) -> None:
        with self._lock:
            if data:
                self._conn.execute(
                    "INSERT INTO fsm_data (key, data) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                    (_pack(key), json.dumps(data, ensure_ascii=False)),
                )
            else:
                self._conn.execute("DELETE FROM fsm_data WHERE key = ?", (_pack(key),))
            self._conn.commit()

    async def get_data(self, bot: Bot, key: StorageKey) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM fsm_data WHERE key = ?", (_pack(key),)
            ).fetchone()
        return json.loads(row[0]) if row else {}

    async def close(self) -> None:
        with self._lock:
            self._conn.close()
