"""Session persistence: PostgreSQL when DATABASE_URL is set, memory otherwise.

A session is stored as one JSONB document (conversation history, UI events,
pending plan, token usage). Sessions survive backend restarts with Postgres.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from typing import Any, Protocol

from app.config import settings

log = logging.getLogger(__name__)


class SessionStore(Protocol):
    def save(self, session: dict[str, Any]) -> None: ...
    def get(self, session_id: str) -> dict[str, Any] | None: ...
    def list(self, limit: int = 50) -> list[dict[str, Any]]: ...
    def delete(self, session_id: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def save(self, session: dict[str, Any]) -> None:
        with self._lock:
            self._data[session["id"]] = copy.deepcopy(session)

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._data.get(session_id)
            return copy.deepcopy(found) if found else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self._data.values(), key=lambda s: s["updated_at"], reverse=True)
            return [_summary(s) for s in items[:limit]]

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(dsn, min_size=1, max_size=5, open=True)
        with self._pool.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    id TEXT PRIMARY KEY,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS agent_sessions_updated ON agent_sessions (updated_at DESC)"
            )

    def save(self, session: dict[str, Any]) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO agent_sessions (id, data, updated_at) VALUES (%s, %s::jsonb, now())
                ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()
                """,
                (session["id"], json.dumps(session, default=str)),
            )

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT data FROM agent_sessions WHERE id = %s", (session_id,)).fetchone()
        return row[0] if row else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT jsonb_build_object(
                    'id', data->'id', 'title', data->'title', 'status', data->'status',
                    'created_at', data->'created_at', 'updated_at', data->'updated_at',
                    'usage', data->'usage')
                FROM agent_sessions ORDER BY updated_at DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [r[0] for r in rows]

    def delete(self, session_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM agent_sessions WHERE id = %s", (session_id,))


def _summary(session: dict[str, Any]) -> dict[str, Any]:
    return {k: session.get(k) for k in ("id", "title", "status", "created_at", "updated_at", "usage")}


def build_store() -> SessionStore:
    if settings.database_url:
        # Postgres may still be starting when the backend boots; wait ~60s.
        for attempt in range(30):
            try:
                store = PostgresStore(settings.database_url)
                log.info("session store: postgres")
                return store
            except Exception as exc:  # noqa: BLE001 - fall back so the agent still works
                log.warning("postgres not ready (attempt %d): %s", attempt + 1, exc)
                time.sleep(2)
        log.error("postgres unavailable, falling back to in-memory sessions")
    log.warning("session store: in-memory (sessions are lost on restart)")
    return MemoryStore()
