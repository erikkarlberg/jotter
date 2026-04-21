"""SQLite-backed data models for Jotter."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class Folder:
    id: int
    name: str
    imap_name: str


@dataclass
class Note:
    id: int = 0
    folder_id: int = 0
    imap_uid: Optional[int] = None
    subject: str = ""
    body_html: str = ""
    body_text: str = ""
    created_at: str = field(default_factory=lambda: _now())
    modified_at: str = field(default_factory=lambda: _now())
    synced_at: Optional[str] = None
    imap_message_id: Optional[str] = None
    deleted: int = 0
    # Apple Notes' X-Universally-Unique-Identifier — stable across edits, unlike Message-ID.
    # We also assign this UUID ourselves when pushing notes so Apple Notes can track them.
    apple_uuid: Optional[str] = None

    @property
    def is_dirty(self) -> bool:
        """Return True if the note has local changes not yet pushed to IMAP."""
        return self.synced_at is None or self.modified_at > (self.synced_at or "")

    @property
    def preview(self) -> str:
        """Body text after the first line, shown in note list cards."""
        lines = self.body_text.split("\n", 1)
        rest = lines[1].strip() if len(lines) > 1 else ""
        if len(rest) <= 80:
            return rest
        return rest[:80].rsplit(" ", 1)[0] + "…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    imap_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS notes (
    id               INTEGER PRIMARY KEY,
    folder_id        INTEGER NOT NULL,
    imap_uid         INTEGER,
    subject          TEXT    DEFAULT '',
    body_html        TEXT    DEFAULT '',
    body_text        TEXT    DEFAULT '',
    created_at       TEXT,
    modified_at      TEXT,
    synced_at        TEXT,
    imap_message_id  TEXT    UNIQUE,
    deleted          INTEGER DEFAULT 0,
    apple_uuid       TEXT,
    FOREIGN KEY (folder_id) REFERENCES folders(id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    subject,
    body_text,
    content=notes,
    content_rowid=id
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- FTS triggers to keep the virtual table in sync
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, subject, body_text)
    VALUES (new.id, new.subject, new.body_text);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, subject, body_text)
    VALUES ('delete', old.id, old.subject, old.body_text);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, subject, body_text)
    VALUES ('delete', old.id, old.subject, old.body_text);
    INSERT INTO notes_fts(rowid, subject, body_text)
    VALUES (new.id, new.subject, new.body_text);
END;
"""


class Database:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_schema()

    # ------------------------------------------------------------------
    # Connection management — one connection per thread
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.executescript(_SCHEMA)
            # Migrate: empty-string imap_message_id → NULL so UNIQUE allows multiples
            conn.execute("UPDATE notes SET imap_message_id = NULL WHERE imap_message_id = ''")
            # Migrate: add apple_uuid column if it doesn't exist yet
            cols = {r[1] for r in conn.execute("PRAGMA table_info(notes)").fetchall()}
            if "apple_uuid" not in cols:
                conn.execute("ALTER TABLE notes ADD COLUMN apple_uuid TEXT")
            # Always ensure the index exists (safe for both new and migrated DBs)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS notes_apple_uuid "
                "ON notes(apple_uuid) WHERE apple_uuid IS NOT NULL"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    def get_folders(self) -> list[Folder]:
        rows = self._conn().execute(
            "SELECT id, name, imap_name FROM folders ORDER BY name"
        ).fetchall()
        return [Folder(id=r["id"], name=r["name"], imap_name=r["imap_name"]) for r in rows]

    def ensure_folder(self, name: str, imap_name: str) -> Folder:
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT OR IGNORE INTO folders (name, imap_name) VALUES (?, ?)",
                (name, imap_name),
            )
            conn.commit()
        row = conn.execute(
            "SELECT id, name, imap_name FROM folders WHERE imap_name = ?",
            (imap_name,),
        ).fetchone()
        return Folder(id=row["id"], name=row["name"], imap_name=row["imap_name"])

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    def get_all_notes(self, search: str = "") -> list[Note]:
        conn = self._conn()
        if search:
            rows = conn.execute(
                """
                SELECT n.* FROM notes n
                JOIN notes_fts f ON f.rowid = n.id
                WHERE n.deleted = 0 AND notes_fts MATCH ?
                ORDER BY n.modified_at DESC
                """,
                (search,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notes WHERE deleted = 0 ORDER BY modified_at DESC"
            ).fetchall()
        return [_row_to_note(r) for r in rows]

    def get_notes(self, folder_id: int, search: str = "") -> list[Note]:
        conn = self._conn()
        if search:
            rows = conn.execute(
                """
                SELECT n.* FROM notes n
                JOIN notes_fts f ON f.rowid = n.id
                WHERE n.folder_id = ? AND n.deleted = 0
                  AND notes_fts MATCH ?
                ORDER BY n.modified_at DESC
                """,
                (folder_id, search),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM notes
                WHERE folder_id = ? AND deleted = 0
                ORDER BY modified_at DESC
                """,
                (folder_id,),
            ).fetchall()
        return [_row_to_note(r) for r in rows]

    def get_note(self, note_id: int) -> Optional[Note]:
        row = self._conn().execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return _row_to_note(row) if row else None

    def save_note(self, note: Note) -> Note:
        with self._lock:
            conn = self._conn()
            # --- Dedup: match an existing row by apple_uuid (stable) or Message-ID ---
            # Gmail label inheritance means the same message appears in both a subfolder
            # and the root "Notes" IMAP folder.  When we encounter the duplicate from the
            # root pass, we must NOT overwrite the subfolder assignment.
            if not note.id:
                existing = None
                # 1. Prefer apple_uuid — stable across all Apple Notes edits
                if note.apple_uuid:
                    existing = conn.execute(
                        "SELECT id, folder_id FROM notes WHERE apple_uuid = ?",
                        (note.apple_uuid,),
                    ).fetchone()
                # 2. Fall back to Message-ID (may change on Apple Notes edit, but covers
                #    notes we pushed ourselves and notes without an apple_uuid)
                if existing is None and note.imap_message_id:
                    existing = conn.execute(
                        "SELECT id, folder_id FROM notes WHERE imap_message_id = ? AND deleted=0",
                        (note.imap_message_id,),
                    ).fetchone()
                if existing:
                    note.id = existing["id"]
                    if existing["folder_id"] != note.folder_id:
                        # Different folder → label-inheritance duplicate.
                        # Keep the existing (more specific) folder assignment.
                        return note

            if note.id:
                conn.execute(
                    """
                    UPDATE notes SET
                        folder_id=?, imap_uid=?, subject=?, body_html=?,
                        body_text=?, modified_at=?, synced_at=?,
                        imap_message_id=?, deleted=?,
                        apple_uuid=COALESCE(apple_uuid, ?)
                    WHERE id=?
                    """,
                    (
                        note.folder_id, note.imap_uid, note.subject,
                        note.body_html, note.body_text, note.modified_at,
                        note.synced_at, note.imap_message_id, note.deleted,
                        note.apple_uuid, note.id,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO notes
                        (folder_id, imap_uid, subject, body_html, body_text,
                         created_at, modified_at, synced_at, imap_message_id,
                         deleted, apple_uuid)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        note.folder_id, note.imap_uid, note.subject,
                        note.body_html, note.body_text, note.created_at,
                        note.modified_at, note.synced_at, note.imap_message_id,
                        note.deleted, note.apple_uuid,
                    ),
                )
                note.id = cursor.lastrowid
            conn.commit()
        return note

    def mark_synced(
        self, note_id: int, imap_uid: int, message_id: str,
        apple_uuid: Optional[str] = None,
    ) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute(
                """
                UPDATE notes SET imap_uid=?, imap_message_id=?, synced_at=?,
                    apple_uuid=COALESCE(apple_uuid, ?)
                WHERE id=?
                """,
                (imap_uid, message_id, _now(), apple_uuid, note_id),
            )
            conn.commit()

    def get_notes_pending_imap_delete(self, folder_id: int) -> list[Note]:
        """Return deleted notes that still have an IMAP UID (need remote deletion)."""
        rows = self._conn().execute(
            "SELECT * FROM notes WHERE folder_id=? AND deleted=1 AND imap_uid IS NOT NULL",
            (folder_id,),
        ).fetchall()
        return [_row_to_note(r) for r in rows]

    def mark_imap_deleted(self, note_id: int) -> None:
        """Clear the IMAP UID after the remote message has been deleted."""
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE notes SET imap_uid=NULL, synced_at=? WHERE id=?",
                (_now(), note_id),
            )
            conn.commit()

    def get_dirty_notes(self, folder_id: int) -> list[Note]:
        rows = self._conn().execute(
            """
            SELECT * FROM notes
            WHERE folder_id = ? AND deleted = 0
              AND (synced_at IS NULL OR modified_at > synced_at)
            """,
            (folder_id,),
        ).fetchall()
        return [_row_to_note(r) for r in rows]

    def search_notes(self, query: str) -> list[Note]:
        rows = self._conn().execute(
            """
            SELECT n.* FROM notes n
            JOIN notes_fts f ON f.rowid = n.id
            WHERE n.deleted = 0 AND notes_fts MATCH ?
            ORDER BY n.modified_at DESC
            """,
            (query,),
        ).fetchall()
        return [_row_to_note(r) for r in rows]

    def delete_note(self, note_id: int) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE notes SET deleted=1, modified_at=? WHERE id=?",
                (_now(), note_id),
            )
            conn.commit()

    def get_note_by_apple_uuid(self, apple_uuid: str) -> Optional[Note]:
        row = self._conn().execute(
            "SELECT * FROM notes WHERE apple_uuid = ?", (apple_uuid,)
        ).fetchone()
        return _row_to_note(row) if row else None

    def update_imap_uid(self, note_id: int, imap_uid: int) -> None:
        """Update only the IMAP UID of a note without touching any other field."""
        with self._lock:
            conn = self._conn()
            conn.execute("UPDATE notes SET imap_uid=? WHERE id=?", (imap_uid, note_id))
            conn.commit()

    def clear_imap_uids(self) -> None:
        """Clear imap_uid for all non-deleted notes so the next sync re-fetches
        everything from IMAP.  synced_at is intentionally preserved so notes are
        not treated as dirty and won't be re-pushed."""
        with self._lock:
            conn = self._conn()
            conn.execute("UPDATE notes SET imap_uid=NULL WHERE deleted=0")
            conn.commit()

    def delete_notes_missing_from_imap(self, folder_id: int) -> int:
        """Mark as deleted any previously-synced note in folder_id whose imap_uid
        is still NULL — meaning it was not matched during a full-reload fetch and
        must have been deleted from IMAP."""
        with self._lock:
            conn = self._conn()
            cursor = conn.execute(
                "UPDATE notes SET deleted=1, modified_at=?"
                " WHERE folder_id=? AND deleted=0"
                "   AND imap_uid IS NULL AND synced_at IS NOT NULL",
                (_now(), folder_id),
            )
            conn.commit()
        return cursor.rowcount

    def delete_notes_by_uids(self, folder_id: int, uids: set[int]) -> int:
        """Mark notes deleted if they still carry one of the given IMAP UIDs. Returns count."""
        if not uids:
            return 0
        placeholders = ",".join("?" * len(uids))
        with self._lock:
            conn = self._conn()
            cursor = conn.execute(
                f"UPDATE notes SET deleted=1, modified_at=?"
                f" WHERE folder_id=? AND imap_uid IN ({placeholders}) AND deleted=0",
                (_now(), folder_id, *uids),
            )
            conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        row = self._conn().execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                (key, value),
            )
            conn.commit()


def _row_to_note(row: sqlite3.Row) -> Note:
    return Note(
        id=row["id"],
        folder_id=row["folder_id"],
        imap_uid=row["imap_uid"],
        subject=row["subject"] or "",
        body_html=row["body_html"] or "",
        body_text=row["body_text"] or "",
        created_at=row["created_at"] or _now(),
        modified_at=row["modified_at"] or _now(),
        synced_at=row["synced_at"],
        imap_message_id=row["imap_message_id"],
        deleted=row["deleted"] or 0,
        apple_uuid=row["apple_uuid"],
    )
