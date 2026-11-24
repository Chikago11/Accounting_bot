"""Durable reservations and at-most-once attempts across restarts.

Sheets and SQLite cannot commit atomically. An interrupted write is reported as
uncertain and NEVER retried automatically, including after process restart.
"""

import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager


class PendingStore:
    def __init__(self, path, ttl=900):
        self.path, self.ttl = path, ttl
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, source TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL, expires REAL NOT NULL,
                    transcript TEXT NOT NULL DEFAULT '',
                    operations TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    batch_id TEXT NOT NULL, position INTEGER NOT NULL,
                    status TEXT NOT NULL, PRIMARY KEY (batch_id, position),
                    FOREIGN KEY (batch_id) REFERENCES batches(id)
                );
            """)
        os.chmod(path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        """Call only while holding the application's exclusive lifetime lock."""
        with self.connect() as db:
            db.execute("UPDATE attempts SET status='uncertain' WHERE status='executing'")
            db.execute(
                "UPDATE batches SET status='failed' WHERE status IN ('processing','analyzing')"
            )

    def reserve(self, source, user_id, chat_id):
        key = secrets.token_urlsafe(12)
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO batches(id,source,user_id,chat_id,status,expires) VALUES(?,?,?,?,?,?)",
                (key, source, user_id, chat_id, "analyzing", time.time() + self.ttl),
            )
            row = dict(db.execute("SELECT * FROM batches WHERE source=?", (source,)).fetchone())
            return row, bool(cursor.rowcount)

    def pending(self, key, transcript, operations):
        with self.connect() as db:
            db.execute(
                "UPDATE batches SET status='pending', transcript=?, operations=?, expires=? WHERE id=? AND status='analyzing'",
                (
                    transcript,
                    json.dumps([op.to_dict() for op in operations], ensure_ascii=False),
                    time.time() + self.ttl,
                    key,
                ),
            )

    def get(self, key, user_id, chat_id):
        with self.connect() as db:
            db.execute(
                "UPDATE batches SET status='expired', transcript='' WHERE id=? AND status='pending' AND expires<=?",
                (key, time.time()),
            )
            row = db.execute(
                "SELECT * FROM batches WHERE id=? AND user_id=? AND chat_id=?",
                (key, user_id, chat_id),
            ).fetchone()
            return dict(row) if row else None

    def claim(self, key, user_id, chat_id):
        with self.connect() as db:
            return bool(
                db.execute(
                    "UPDATE batches SET status='processing' WHERE id=? AND user_id=? AND chat_id=? AND status='pending' AND expires>?",
                    (key, user_id, chat_id, time.time()),
                ).rowcount
            )

    def cancel(self, key, user_id, chat_id):
        with self.connect() as db:
            return bool(
                db.execute(
                    "UPDATE batches SET status='cancelled', transcript='' WHERE id=? AND user_id=? AND chat_id=? AND status='pending'",
                    (key, user_id, chat_id),
                ).rowcount
            )

    def mark_attempt(self, key, position, status):
        with self.connect() as db:
            if status == "executing":
                db.execute("INSERT INTO attempts VALUES(?,?,?)", (key, position, status))
            else:
                db.execute(
                    "UPDATE attempts SET status=? WHERE batch_id=? AND position=?",
                    (status, key, position),
                )

    def finish(self, key, status):
        with self.connect() as db:
            db.execute("UPDATE batches SET status=?, transcript='' WHERE id=?", (status, key))

    def attempts(self, key):
        with self.connect() as db:
            return {
                row["position"]: row["status"]
                for row in db.execute("SELECT * FROM attempts WHERE batch_id=?", (key,))
            }

    def purge_transcripts(self):
        with self.connect() as db:
            db.execute(
                "UPDATE batches SET status='expired', transcript='' WHERE status='pending' AND expires<=?",
                (time.time(),),
            )
