"""Background IMAP sync engine for Jotter."""

from __future__ import annotations

import email
import email.header
import email.message
import email.utils
import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
NOTES_FOLDER_CANDIDATES = ["Notes", "[Gmail]/Notes"]
SYNC_INTERVAL = 60  # seconds


# ---------------------------------------------------------------------------
# Events emitted to the UI via GLib.idle_add
# ---------------------------------------------------------------------------

class SyncEventType(Enum):
    NOTES_UPDATED = auto()
    SYNC_COMPLETE = auto()
    SYNC_ERROR = auto()
    AUTH_REQUIRED = auto()
    CONNECTED = auto()
    DISCONNECTED = auto()
    SYNC_CONFLICT = auto()   # data = number of conflicting notes skipped this cycle


@dataclass
class SyncEvent:
    type: SyncEventType
    data: Any = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Commands sent from UI to sync thread
# ---------------------------------------------------------------------------

class CmdType(Enum):
    SYNC_NOW = auto()
    STOP = auto()
    NOTE_SAVED = auto()   # data = note_id


# ---------------------------------------------------------------------------
# IMAP sync engine
# ---------------------------------------------------------------------------

class ImapSyncEngine(threading.Thread):
    """
    Background thread that keeps a local SQLite database in sync with Gmail's
    Notes IMAP mailbox.

    UI → thread communication: put CmdType items on ``cmd_queue``.
    Thread → UI communication: ``event_cb`` is called via GLib.idle_add.
    """

    def __init__(
        self,
        db,                          # models.Database instance
        get_credentials_cb: Callable,  # () -> ImapCredentials | None
        event_cb: Callable[[SyncEvent], None],
    ):
        super().__init__(daemon=True, name="imap-sync")
        self._db = db
        self._get_creds = get_credentials_cb
        self._event_cb = event_cb
        self.cmd_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sync_cycle()
            except Exception as exc:
                logger.exception("Sync cycle failed")
                self._emit(SyncEventType.SYNC_ERROR, error=str(exc))

            # Wait for next cycle or a command
            try:
                cmd = self.cmd_queue.get(timeout=SYNC_INTERVAL)
                if cmd.type == CmdType.STOP:
                    break
                # SYNC_NOW or NOTE_SAVED → fall through to next iteration
            except queue.Empty:
                pass  # timeout → run another cycle

    def stop(self) -> None:
        self._stop_event.set()
        self.cmd_queue.put(_Cmd(CmdType.STOP))

    def request_sync(self) -> None:
        self.cmd_queue.put(_Cmd(CmdType.SYNC_NOW))

    def request_full_sync(self) -> None:
        """Drop all cached IMAP UIDs then run a sync cycle.

        Clearing the UIDs (without touching synced_at) means the next _pull
        treats every remote message as new, re-fetches its headers and body,
        and reconciles against the local DB via UUID dedup.  Notes that have
        vanished from IMAP are caught because _pull sees an empty known_uids
        set — but we also do a separate pass to mark as deleted any local
        note whose imap_uid is no longer in the remote set after re-population.
        """
        self._db.clear_imap_uids()
        self.cmd_queue.put(_Cmd(CmdType.SYNC_NOW))

    # ------------------------------------------------------------------
    # Core sync logic
    # ------------------------------------------------------------------

    def _sync_cycle(self) -> None:
        creds = self._get_creds()
        if creds is None:
            self._emit(SyncEventType.AUTH_REQUIRED)
            return

        try:
            import imapclient
            import imapclient.exceptions as _imap_exc
        except ImportError:
            logger.error("imapclient not installed")
            self._emit(SyncEventType.SYNC_ERROR, error="imapclient not installed")
            return

        email_addr = creds.email
        if email_addr and not self._db.get_meta("email"):
            self._db.set_meta("email", email_addr)

        try:
            with imapclient.IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True) as client:
                client.oauth2_login(email_addr, creds.access_token)
                self._emit(SyncEventType.CONNECTED)
                self._run_folders(client, email_addr)
        except _imap_exc.LoginError:
            # GOA always refreshes via call_get_access_token_sync; a LoginError here
            # means the account itself needs re-authorisation, not just a token refresh.
            logger.warning("IMAP login failed — account may need re-authorisation")
            self._emit(SyncEventType.AUTH_REQUIRED)
            return

        self._emit(SyncEventType.SYNC_COMPLETE)

    def _run_folders(self, client, email_addr: str) -> None:
        notes_folders = self._get_notes_folders(client)
        logger.info("Syncing %d Notes folder(s): %s", len(notes_folders),
                    [n for _, n in notes_folders])

        for imap_name, display_name in notes_folders:
            folder = self._db.ensure_folder(display_name, imap_name)

            # Check UIDVALIDITY per folder
            status = client.select_folder(imap_name, readonly=False)
            uid_validity = str(status.get(b"UIDVALIDITY", ""))
            stored_validity = self._db.get_meta(f"uidvalidity:{imap_name}")
            if stored_validity and stored_validity != uid_validity:
                logger.warning("UIDVALIDITY changed for %s — clearing cached UIDs", imap_name)
                self._clear_uids(folder.id)
            self._db.set_meta(f"uidvalidity:{imap_name}", uid_validity)

            # 1. Pull remote → local
            self._pull(client, folder, imap_name)

            # 2. Push local dirty → remote
            self._push(client, folder, imap_name, email_addr)

    def _get_notes_folders(self, client) -> list[tuple[str, str]]:
        """Return [(imap_name, display_name)] for the root Notes folder and all subfolders."""
        all_folders = client.list_folders()

        # Read the path separator from the server rather than assuming '/'.
        sep = "/"
        if all_folders:
            raw_sep = all_folders[0][1] or b"/"
            sep = raw_sep.decode() if isinstance(raw_sep, bytes) else raw_sep

        all_folder_names = [f[2] for f in all_folders]

        # Find root Notes folder
        root = None
        for candidate in NOTES_FOLDER_CANDIDATES:
            if candidate in all_folder_names:
                root = candidate
                break
        if root is None:
            client.create_folder("Notes")
            root = "Notes"

        # Collect subfolders BEFORE root so notes get the most specific folder_id first.
        # Gmail label inheritance makes subfolder notes also appear in the root IMAP folder;
        # syncing subfolders first means the root pass sees them as duplicates and skips them.
        result = []
        prefix = root + sep
        for name in all_folder_names:
            if name.startswith(prefix):
                display = name[len(prefix):]
                if display:
                    result.append((name, display))
        result.append((root, "Notes"))  # root last

        return result

    def _pull(self, client, folder, folder_name: str) -> None:
        """Fetch new/changed messages from IMAP and store in DB."""
        remote_uids: set[int] = set(client.search("ALL"))

        # UIDs we already have for this folder — include deleted notes so a locally-deleted
        # note isn't re-fetched from IMAP before _push has a chance to remove it remotely.
        known_rows = self._db._conn().execute(
            "SELECT imap_uid FROM notes WHERE folder_id=? AND imap_uid IS NOT NULL",
            (folder.id,),
        ).fetchall()
        known_uids = {r[0] for r in known_rows}

        new_uids = remote_uids - known_uids
        changed = False
        n_conflicts = 0

        if new_uids:
            fetched = client.fetch(list(new_uids), ["RFC822", "ENVELOPE", "INTERNALDATE"])
            for uid, data in fetched.items():
                raw = data.get(b"RFC822") or data.get(b"BODY[]")
                if not raw:
                    continue
                note = self._parse_message(raw, uid, folder.id)

                # Before saving, check for conflict or deleted-revival by apple_uuid.
                # This is the stable identifier Apple Notes keeps across all edits.
                if note.apple_uuid:
                    local = self._db.get_note_by_apple_uuid(note.apple_uuid)
                    if local and local.folder_id == folder.id:
                        if local.deleted:
                            # Note was deleted locally. Update the imap_uid to the new
                            # remote UID so _push can issue the remote delete, but don't
                            # revive the note.
                            self._db.update_imap_uid(local.id, uid)
                            continue
                        if local.is_dirty:
                            # Local has unsaved edits that would be overwritten by the
                            # remote version. Skip; _push will upload the local version
                            # and win the conflict with a new UID.
                            n_conflicts += 1
                            logger.info(
                                "Sync conflict on note %s (%r) — keeping local edits",
                                local.id, local.subject,
                            )
                            continue

                result = self._db.save_note(note)
                if result.id:
                    changed = True

        # Detect remotely deleted notes.  Two cases:
        #   Normal sync: UIDs we had before are no longer on the server.
        #   Full reload:  known_uids was empty (UIDs were cleared), so we detect
        #                 deletions by finding synced notes whose imap_uid is still
        #                 NULL after the re-population pass above — they were never
        #                 matched to a remote UID and must have been deleted remotely.
        if known_uids:
            vanished_uids = known_uids - remote_uids
        else:
            vanished_uids = set()  # handled below via delete_notes_missing_from_imap
        if vanished_uids:
            n_deleted = self._db.delete_notes_by_uids(folder.id, vanished_uids)
            if n_deleted:
                logger.info("Marked %d note(s) deleted (remote removal) in %s", n_deleted, folder_name)
                changed = True
        elif not known_uids:
            # Full-reload path: notes that were previously synced but whose
            # imap_uid is still NULL after the fetch loop were not found on the
            # server — they have been deleted remotely.
            n_deleted = self._db.delete_notes_missing_from_imap(folder.id)
            if n_deleted:
                logger.info("Marked %d note(s) deleted (missing from IMAP) in %s", n_deleted, folder_name)
                changed = True

        if n_conflicts:
            self._emit(SyncEventType.SYNC_CONFLICT, data=n_conflicts)

        if changed:
            self._emit(SyncEventType.NOTES_UPDATED, data=folder.id)

    def _push(self, client, folder, folder_name: str, email_addr: str) -> None:
        """Upload local dirty notes to IMAP and push deletions."""
        import uuid as _uuid_mod

        # Batch-delete all notes that were deleted locally in one round-trip.
        pending_deletes = self._db.get_notes_pending_imap_delete(folder.id)
        if pending_deletes:
            uids_to_delete = [n.imap_uid for n in pending_deletes]
            try:
                client.delete_messages(uids_to_delete)
                client.expunge()
                logger.info("Deleted IMAP UIDs %s in %s", uids_to_delete, folder_name)
            except Exception as exc:
                logger.warning("Could not delete IMAP UIDs %s: %s", uids_to_delete, exc)
            for note in pending_deletes:
                self._db.mark_imap_deleted(note.id)

        dirty = self._db.get_dirty_notes(folder.id)
        if not dirty and not pending_deletes:
            return
        if not dirty:
            self._emit(SyncEventType.NOTES_UPDATED, data=folder.id)
            return

        for note in dirty:
            # Don't push placeholder notes that the user never typed anything into.
            if not note.body_text.strip() and note.imap_uid is None:
                continue

            # Assign a stable UUID if this note doesn't have one yet.
            # Apple Notes uses X-Universally-Unique-Identifier to track notes
            # across edits, so we must include this on every message we push.
            if not note.apple_uuid:
                note.apple_uuid = str(_uuid_mod.uuid4()).upper()

            msg_bytes = self._note_to_rfc822(note, email_addr)

            # Capture UIDNEXT *before* APPEND so we can locate the new message
            # reliably without depending on a header search over the whole folder.
            folder_status = client.folder_status(folder_name, [b"UIDNEXT"])
            uidnext = folder_status.get(b"UIDNEXT", 1)

            client.append(
                folder_name,
                msg_bytes,
                flags=["\\Seen"],
                msg_time=_parse_dt(note.modified_at),
            )

            # Search UIDs >= uidnext, optionally filtered by our tracking header.
            # Fall back to the raw UID range if the server doesn't support HEADER search.
            try:
                candidates = client.search(
                    ["UID", f"{uidnext}:*", "HEADER", "X-Jotter-Id", str(note.id)]
                )
            except Exception:
                candidates = client.search(["UID", f"{uidnext}:*"])
            new_uid = candidates[-1] if candidates else None

            if new_uid:
                # Delete the old IMAP message if it existed
                if note.imap_uid and note.imap_uid != new_uid:
                    try:
                        client.delete_messages([note.imap_uid])
                        client.expunge()
                    except Exception as exc:
                        logger.warning("Could not delete old UID %s: %s", note.imap_uid, exc)

                # Retrieve the Message-ID from the freshly appended message
                fetched = client.fetch([new_uid], ["ENVELOPE"])
                envelope = fetched.get(new_uid, {}).get(b"ENVELOPE")
                msg_id = ""
                if envelope and envelope.message_id:
                    msg_id = envelope.message_id.decode(errors="replace")

                self._db.mark_synced(note.id, new_uid, msg_id, apple_uuid=note.apple_uuid)

        self._emit(SyncEventType.NOTES_UPDATED, data=folder.id)

    def _clear_uids(self, folder_id: int) -> None:
        """Reset all IMAP UIDs for a folder (triggered by UIDVALIDITY change)."""
        with self._db._lock:
            self._db._conn().execute(
                "UPDATE notes SET imap_uid=NULL, synced_at=NULL WHERE folder_id=?",
                (folder_id,),
            )
            self._db._conn().commit()

    # ------------------------------------------------------------------
    # Message parsing / serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_message(raw: bytes, uid: int, folder_id: int):
        from .models import Note
        from .utils import strip_html

        msg = email.message_from_bytes(raw)

        # Subject — handle unknown charsets gracefully
        subject = ""
        raw_subject = msg.get("Subject", "")
        if raw_subject:
            parts = email.header.decode_header(raw_subject)
            decoded = []
            for p, enc in parts:
                if isinstance(p, bytes):
                    try:
                        decoded.append(p.decode(enc or "utf-8"))
                    except (LookupError, UnicodeDecodeError):
                        decoded.append(p.decode("utf-8", errors="replace"))
                else:
                    decoded.append(p)
            subject = "".join(decoded)

        # Message-ID — store None rather than "" so UNIQUE constraint allows multiple missing IDs
        message_id = (msg.get("Message-ID") or "").strip() or None

        # Apple Notes' stable UUID — unchanged across all edits, unlike Message-ID
        apple_uuid = (msg.get("X-Universally-Unique-Identifier") or "").strip().upper() or None

        # Apple Notes sets Date: to the last-edit time and X-Mail-Created-Date: to the
        # original creation time.  Map these correctly so round-trips preserve both dates.
        date_str = msg.get("Date", "")
        x_created_str = msg.get("X-Mail-Created-Date", "")
        try:
            modified_at = email.utils.parsedate_to_datetime(date_str).isoformat()
        except Exception:
            modified_at = _now()
        try:
            created_at = email.utils.parsedate_to_datetime(x_created_str).isoformat()
        except Exception:
            created_at = modified_at  # fall back to modified time if header absent

        # Body — prefer HTML, fall back to plain
        body_html = ""
        body_text = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/html" and not body_html:
                    body_html = _decode_payload(part)
                elif ct == "text/plain" and not body_text:
                    body_text = _decode_payload(part)
        else:
            ct = msg.get_content_type()
            if ct == "text/html":
                body_html = _decode_payload(msg)
            else:
                body_text = _decode_payload(msg)

        if body_html and not body_text:
            body_text = strip_html(body_html)
        elif body_text and not body_html:
            # Wrap plain text in a minimal HTML body
            body_html = f"<div>{body_text}</div>"

        return Note(
            folder_id=folder_id,
            imap_uid=uid,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            created_at=created_at,
            modified_at=modified_at,
            synced_at=_now(),
            imap_message_id=message_id,
            apple_uuid=apple_uuid,
        )

    @staticmethod
    def _note_to_rfc822(note, from_addr: str) -> bytes:
        """Serialize a Note to an RFC2822 message compatible with Apple Notes."""
        msg = email.message.EmailMessage()
        msg["From"] = from_addr
        msg["To"] = from_addr
        msg["Subject"] = note.subject or "(no subject)"
        msg["Date"] = email.utils.formatdate(
            _parse_dt(note.modified_at).timestamp() if note.modified_at else None,
            localtime=False,
        )
        msg["MIME-Version"] = "1.0"
        msg["X-Uniform-Type-Identifier"] = "com.apple.mail-note"
        msg["X-Mail-Created-Date"] = email.utils.formatdate(
            _parse_dt(note.created_at).timestamp() if note.created_at else None,
            localtime=False,
        )
        msg["X-Jotter-Id"] = str(note.id)
        if note.apple_uuid:
            msg["X-Universally-Unique-Identifier"] = note.apple_uuid

        html_body = note.body_html or "<div><br></div>"

        # Wrap bare content in Apple Notes' own HTML envelope so the format is
        # identical to what Apple Notes writes.  This prevents Apple from rewriting
        # the structure on every edit and stops the DOCTYPE / meta-charset oscillation
        # we observed in interop testing.
        if not html_body.lower().lstrip().startswith("<html"):
            html_body = (
                "<html><head></head>"
                '<body style="overflow-wrap: break-word; -webkit-nbsp-mode: space;'
                ' line-break: after-white-space;">'
                f"{html_body}"
                "</body></html>"
            )

        msg.set_content(note.body_text or "", subtype="plain", charset="utf-8")
        msg.add_alternative(html_body, subtype="html", charset="utf-8")

        return msg.as_bytes()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, event_type: SyncEventType, data=None, error: str | None = None) -> None:
        """Send an event to the UI thread via GLib.idle_add."""
        try:
            from gi.repository import GLib
            GLib.idle_add(self._event_cb, SyncEvent(event_type, data=data, error=error))
        except Exception:
            # GLib may not be available in tests
            self._event_cb(SyncEvent(event_type, data=data, error=error))


@dataclass
class _Cmd:
    type: CmdType
    data: Any = None


def _decode_payload(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset("utf-8") or "utf-8"
    return payload.decode(charset, errors="replace")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(dt_str: str | None) -> datetime:
    if not dt_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        return datetime.now(timezone.utc)
