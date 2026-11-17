"""SQLite storage for notes, todos, long-term memories and conversations."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai import ModelMessage, ModelMessagesTypeAdapter

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    done_at TEXT
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    fact TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    title TEXT,
    channel TEXT NOT NULL DEFAULT 'cli',
    messages TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Note:
    id: int
    title: str
    body: str
    tags: str
    updated_at: str


@dataclass
class Todo:
    id: int
    text: str
    due: str | None
    done: bool


@dataclass
class Memory:
    id: int
    fact: str


@dataclass
class ConversationInfo:
    id: int
    title: str | None
    updated_at: str


class Store:
    def __init__(self, path: Path):
        # Sync tools can run concurrently in worker threads, so all access goes through a lock.
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def _read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self._read(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---- notes -------------------------------------------------------------
    def add_note(self, title: str, body: str, tags: list[str]) -> int:
        now = _now()
        cur = self._write(
            "INSERT INTO notes (title, body, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (title, body, ",".join(tags), now, now),
        )
        return cur.lastrowid  # type: ignore[return-value]

    def update_note(self, note_id: int, body: str) -> bool:
        cur = self._write("UPDATE notes SET body = ?, updated_at = ? WHERE id = ?", (body, _now(), note_id))
        return cur.rowcount > 0

    def search_notes(self, query: str | None, limit: int = 20) -> list[Note]:
        if query:
            like = f"%{query}%"
            rows = self._read(
                "SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (like, like, like, limit),
            )
        else:
            rows = self._read("SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [Note(r["id"], r["title"], r["body"], r["tags"], r["updated_at"]) for r in rows]

    def delete_note(self, note_id: int) -> bool:
        return self._write("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount > 0

    # ---- todos -------------------------------------------------------------
    def add_todo(self, text: str, due: str | None) -> int:
        cur = self._write("INSERT INTO todos (text, due, created_at) VALUES (?, ?, ?)", (text, due, _now()))
        return cur.lastrowid  # type: ignore[return-value]

    def list_todos(self, include_done: bool = False) -> list[Todo]:
        sql = "SELECT * FROM todos"
        if not include_done:
            sql += " WHERE done = 0"
        sql += " ORDER BY done, due IS NULL, due, id"
        return [Todo(r["id"], r["text"], r["due"], bool(r["done"])) for r in self._read(sql)]

    def complete_todo(self, todo_id: int) -> bool:
        return self._write("UPDATE todos SET done = 1, done_at = ? WHERE id = ?", (_now(), todo_id)).rowcount > 0

    def delete_todo(self, todo_id: int) -> bool:
        return self._write("DELETE FROM todos WHERE id = ?", (todo_id,)).rowcount > 0

    # ---- long-term memory --------------------------------------------------
    def add_memory(self, fact: str) -> int:
        cur = self._write("INSERT INTO memories (fact, created_at) VALUES (?, ?)", (fact, _now()))
        return cur.lastrowid  # type: ignore[return-value]

    def list_memories(self) -> list[Memory]:
        return [Memory(r["id"], r["fact"]) for r in self._read("SELECT * FROM memories ORDER BY id")]

    def delete_memory(self, memory_id: int) -> bool:
        return self._write("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    # ---- conversations -----------------------------------------------------
    def new_conversation(self, channel: str = "cli") -> int:
        now = _now()
        cur = self._write(
            "INSERT INTO conversations (channel, created_at, updated_at) VALUES (?, ?, ?)", (channel, now, now)
        )
        return cur.lastrowid  # type: ignore[return-value]

    def latest_conversation(self, channel: str = "cli") -> int | None:
        row = self._one(
            "SELECT id FROM conversations WHERE channel = ? ORDER BY updated_at DESC, id DESC LIMIT 1", (channel,)
        )
        return row["id"] if row else None

    def list_conversations(self, channel: str = "cli", limit: int = 20) -> list[ConversationInfo]:
        rows = self._read(
            "SELECT id, title, updated_at FROM conversations WHERE channel = ? ORDER BY updated_at DESC LIMIT ?",
            (channel, limit),
        )
        return [ConversationInfo(r["id"], r["title"], r["updated_at"]) for r in rows]

    def conversation_exists(self, conv_id: int) -> bool:
        return self._one("SELECT 1 FROM conversations WHERE id = ?", (conv_id,)) is not None

    def load_messages(self, conv_id: int) -> list[ModelMessage]:
        row = self._one("SELECT messages FROM conversations WHERE id = ?", (conv_id,))
        if not row:
            return []
        return list(ModelMessagesTypeAdapter.validate_json(row["messages"]))

    def save_messages(self, conv_id: int, messages: list[ModelMessage], title: str | None = None) -> None:
        data = ModelMessagesTypeAdapter.dump_json(messages).decode()
        self._write(
            "UPDATE conversations SET messages = ?, updated_at = ?, title = COALESCE(title, ?) WHERE id = ?",
            (data, _now(), title, conv_id),
        )
