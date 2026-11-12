"""SQLite persistence for observed telemetry and deterministic session summaries."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Own one database from one application process; worker threads share this store."""

    def __init__(self, path: str | Path = "data/telemetry.sqlite3", batch_size: int = 100):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                source_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
                summary_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'recording'
            );
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                time REAL NOT NULL, segment_id INTEGER, active INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS samples_session ON samples(session_id, id);
            CREATE TABLE IF NOT EXISTS events (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY(session_id, ordinal)
            );
            CREATE TABLE IF NOT EXISTS laps (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY(session_id, ordinal)
            );
        """)
        self._db.execute("UPDATE sessions SET status='interrupted',updated_at=? WHERE status='recording'", (_now(),))
        self._db.commit()
        self._pending: list[tuple] = []
        self._batch_size = max(1, batch_size)
        self._known_sessions = {row[0] for row in self._db.execute("SELECT id FROM sessions")}
        self._closed = False

    def create_session(self, source: dict | str, metadata: dict | None = None) -> str:
        with self._lock:
            session_id, now = uuid.uuid4().hex, _now()
            self._db.execute("INSERT INTO sessions(id,created_at,updated_at,source_json,metadata_json) VALUES(?,?,?,?,?)",
                             (session_id, now, now, _json(source), _json(metadata or {})))
            self._db.commit()
            self._known_sessions.add(session_id)
            return session_id

    def add_sample(self, session_id: str, sample: dict) -> None:
        with self._lock:
            if session_id not in self._known_sessions:
                raise KeyError(session_id)
            self._pending.append((session_id, sample["time"], sample.get("segment_id"), bool(sample.get("active")), _json(sample)))
            if len(self._pending) >= self._batch_size:
                self.flush()

    def update_metadata(self, session_id: str, updates: dict) -> dict:
        """Merge session metadata, replacing supplied top-level values atomically."""
        if not isinstance(updates, dict):
            raise ValueError("Session metadata updates must be an object")
        with self._lock:
            row = self._db.execute("SELECT metadata_json FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            metadata = json.loads(row[0])
            metadata.update(updates)
            encoded = _json(metadata)
            with self._db:
                self._db.execute("UPDATE sessions SET metadata_json=?,updated_at=? WHERE id=?", (encoded, _now(), session_id))
            return json.loads(encoded)

    def delete_session(self, session_id: str) -> None:
        """Remove one recording and queued observations without affecting others."""
        with self._lock:
            if session_id not in self._known_sessions:
                raise KeyError(session_id)
            with self._db:
                self._db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._pending = [row for row in self._pending if row[0] != session_id]
            self._known_sessions.remove(session_id)

    def list_session_sources(self) -> list[dict]:
        """All stored source references, without the paginated library limit."""
        with self._lock:
            return [{"id": row[0], "source": json.loads(row[1]), "status": row[2]}
                    for row in self._db.execute("SELECT id,source_json,status FROM sessions")]

    def flush(self) -> None:
        with self._lock:
            if not self._pending:
                return
            with self._db:
                self._db.executemany("INSERT INTO samples(session_id,time,segment_id,active,payload_json) VALUES(?,?,?,?,?)", self._pending)
                self._db.executemany("UPDATE sessions SET updated_at=? WHERE id=?", [(_now(), key) for key in {row[0] for row in self._pending}])
            self._pending.clear()

    def save_reviewed_lap(self, session_id: str, lap: dict, make_default: bool = True) -> dict:
        """Keep reviewed ranges separate from automatically detected lap records."""
        with self._lock:
            session = self.get_session(session_id)
            if session is None:
                raise KeyError(session_id)
            metadata = session["metadata"]
            laps = [item for item in metadata.get("reviewed_laps", []) if item["id"] != lap["id"]]
            if len(laps) >= 100:
                raise ValueError("A recording can have at most 100 saved lap ranges.")
            laps.append(lap)
            defaults = dict(metadata.get("comparison_defaults", {}))
            if make_default or defaults.get("range_id") == lap["id"]:
                defaults.update(range_id=lap["id"], revision=uuid.uuid4().hex,
                                alignment_mode="landmarks" if lap.get("boundaries_verified") and lap.get("landmarks")
                                else "lap_endpoints" if lap.get("boundaries_verified") else "raw")
            return self.update_metadata(session_id, {"reviewed_laps": laps, "comparison_defaults": defaults})

    def save_summary(self, session_id: str, summary: dict, events: list | None = None,
                     laps: list | None = None, status: str | None = None) -> None:
        with self._lock:
            if session_id not in self._known_sessions:
                raise KeyError(session_id)
            self.flush()
            with self._db:
                if status is None:
                    self._db.execute("UPDATE sessions SET summary_json=?,updated_at=? WHERE id=?", (_json(summary), _now(), session_id))
                else:
                    self._db.execute("UPDATE sessions SET summary_json=?,updated_at=?,status=? WHERE id=?", (_json(summary), _now(), status, session_id))
                for table, values in (("events", events), ("laps", laps)):
                    if values is not None:
                        self._db.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
                        self._db.executemany(f"INSERT INTO {table}(session_id,ordinal,payload_json) VALUES(?,?,?)",
                                             [(session_id, index, _json(value)) for index, value in enumerate(values)])

    @staticmethod
    def _session(row: sqlite3.Row) -> dict:
        result = dict(row)
        for field in ("source", "metadata", "summary"):
            result[field] = json.loads(result.pop(field + "_json"))
        return result

    def list_sessions(self, limit: int = 50) -> list[dict]:
        with self._lock:
            self.flush()
            rows = self._db.execute("SELECT s.*, (SELECT COUNT(*) FROM samples WHERE session_id=s.id) AS sample_count FROM sessions s ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit), 1000)),)).fetchall()
            return [self._session(row) for row in rows]

    def get_session(self, session_id: str) -> dict | None:
        with self._lock:
            self.flush()
            row = self._db.execute("SELECT s.*, (SELECT COUNT(*) FROM samples WHERE session_id=s.id) AS sample_count FROM sessions s WHERE id=?", (session_id,)).fetchone()
            if row is None:
                return None
            session = self._session(row)
            for table in ("events", "laps"):
                session[table] = [json.loads(item[0]) for item in self._db.execute(f"SELECT payload_json FROM {table} WHERE session_id=? ORDER BY ordinal", (session_id,))]
            return session

    def get_samples(self, session_id: str, limit: int | None = None) -> list[dict]:
        with self._lock:
            self.flush()
            if limit is None:
                rows = self._db.execute("SELECT payload_json FROM samples WHERE session_id=? ORDER BY id", (session_id,))
            else:
                rows = self._db.execute("SELECT payload_json FROM (SELECT id,payload_json FROM samples WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id", (session_id, max(0, int(limit))))
            return [json.loads(row[0]) for row in rows]

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.flush()
                self._db.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
