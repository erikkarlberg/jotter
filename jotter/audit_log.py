"""Rotating audit log — tracks the last MAX_ENTRIES note actions and their IMAP sync status."""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_ENTRIES = 10

# Status values
ST_PENDING  = "pending"   # recorded locally, not yet confirmed in IMAP
ST_OK       = "ok"        # successfully pushed to / verified in IMAP
ST_CONFLICT = "conflict"  # remote change existed; local edit was kept
ST_ERROR    = "error"     # push or verification failed


@dataclass
class AuditEntry:
    timestamp: str
    action: str              # "create" | "edit" | "delete" | "conflict" | "sync"
    note_id: int
    subject: str
    status: str
    imap_uid: Optional[int] = None
    apple_uuid: Optional[str] = None
    message_id: Optional[str] = None
    folder: Optional[str] = None
    detail: Optional[str] = None   # error message, conflict reason, or confirmation info


class AuditLog:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._entries: deque[AuditEntry] = deque(maxlen=MAX_ENTRIES)
        self._load()

    # ------------------------------------------------------------------
    # Record actions (UI thread)
    # ------------------------------------------------------------------

    def record(self, action: str, note_id: int, subject: str) -> AuditEntry:
        """Record a note action.  If a pending entry already exists for this
        note, update it in-place (prevents duplicate rows during rapid edits)."""
        subject = (subject or "(untitled)")[:60]
        with self._lock:
            if note_id:
                for entry in reversed(list(self._entries)):
                    if entry.note_id == note_id and entry.status == ST_PENDING:
                        entry.timestamp = _now()
                        entry.action = action
                        entry.subject = subject
                        self._save()
                        return entry
            entry = AuditEntry(
                timestamp=_now(),
                action=action,
                note_id=note_id,
                subject=subject,
                status=ST_PENDING,
            )
            self._entries.append(entry)
            self._save()
        return entry

    # ------------------------------------------------------------------
    # Update status (sync thread)
    # ------------------------------------------------------------------

    def mark_ok(
        self,
        note_id: int,
        imap_uid: Optional[int] = None,
        apple_uuid: Optional[str] = None,
        message_id: Optional[str] = None,
        folder: Optional[str] = None,
    ) -> None:
        with self._lock:
            for entry in reversed(list(self._entries)):
                if entry.note_id == note_id and entry.status == ST_PENDING:
                    entry.status = ST_OK
                    if imap_uid is not None:
                        entry.imap_uid = imap_uid
                    if apple_uuid:
                        entry.apple_uuid = apple_uuid
                    if message_id:
                        entry.message_id = message_id
                    if folder:
                        entry.folder = folder
                    parts = []
                    if imap_uid:
                        parts.append(f"IMAP UID {imap_uid}")
                    if folder:
                        parts.append(f"folder={folder}")
                    if message_id:
                        parts.append(f"msg-id={message_id}")
                    entry.detail = "Pushed: " + ", ".join(parts) if parts else "Pushed OK"
                    break
            self._save()

    def mark_error(self, note_id: int, error: str) -> None:
        with self._lock:
            updated = False
            for entry in reversed(list(self._entries)):
                if entry.note_id == note_id and entry.status in (ST_PENDING, ST_OK):
                    entry.status = ST_ERROR
                    entry.detail = error
                    updated = True
                    break
            if not updated:
                self._entries.append(AuditEntry(
                    timestamp=_now(),
                    action="sync",
                    note_id=note_id,
                    subject="(unknown)",
                    status=ST_ERROR,
                    detail=error,
                ))
            self._save()

    def mark_conflict(self, note_id: int, subject: str, detail: str) -> None:
        with self._lock:
            for entry in reversed(list(self._entries)):
                if entry.note_id == note_id and entry.status == ST_PENDING:
                    entry.status = ST_CONFLICT
                    entry.detail = detail
                    break
            else:
                # No pending entry for this note — add one
                self._entries.append(AuditEntry(
                    timestamp=_now(),
                    action="conflict",
                    note_id=note_id,
                    subject=(subject or "(untitled)")[:60],
                    status=ST_CONFLICT,
                    detail=detail,
                ))
            self._save()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def has_errors(self) -> bool:
        return any(e.status in (ST_ERROR, ST_CONFLICT) for e in self._entries)

    def format_text(self) -> str:
        """Human-readable log of all entries, newest last."""
        if not self._entries:
            return "(no recent note actions logged)"
        lines = ["Recent note actions (newest last):\n"]
        icons = {ST_OK: "✓", ST_ERROR: "✗", ST_PENDING: "…", ST_CONFLICT: "⚠"}
        for e in self._entries:
            ts = e.timestamp[:19].replace("T", " ") + "Z"
            icon = icons.get(e.status, "?")
            lines.append(f"  {icon}  [{e.status}]  {ts}  {e.action}")
            lines.append(f"       note_id={e.note_id}  subject={e.subject!r}")
            meta = []
            if e.imap_uid:
                meta.append(f"imap_uid={e.imap_uid}")
            if e.folder:
                meta.append(f"folder={e.folder!r}")
            if e.apple_uuid:
                meta.append(f"apple_uuid={e.apple_uuid}")
            if e.message_id:
                meta.append(f"message_id={e.message_id}")
            if meta:
                lines.append("       " + "  ".join(meta))
            if e.detail:
                lines.append(f"       ↳ {e.detail}")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            text = "\n".join(json.dumps(asdict(e)) for e in self._entries)
            self._path.write_text(text + "\n" if text else "")
        except Exception:
            logger.exception("Failed to write audit log to %s", self._path)

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            for line in self._path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Tolerate entries written by older versions missing new fields
                    data.setdefault("imap_uid", None)
                    data.setdefault("apple_uuid", None)
                    data.setdefault("message_id", None)
                    data.setdefault("folder", None)
                    data.setdefault("detail", None)
                    self._entries.append(AuditEntry(**data))
                except Exception:
                    pass
        except Exception:
            logger.exception("Failed to read audit log from %s", self._path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
