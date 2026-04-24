"""Background IMAP sync engine for Jotter."""

from __future__ import annotations

import email
import email.header
import email.message
import email.utils
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
NOTES_FOLDER_CANDIDATES = ["Notes", "[Gmail]/Notes"]
SYNC_INTERVAL = 30          # fallback polling interval when IDLE unavailable (seconds)
IDLE_REFRESH_SECS = 29 * 60 # RFC 2177: re-issue IDLE every 29 min to avoid server cutoff
RECONNECT_DELAY = 20        # seconds to wait before reconnecting after a connection error


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
        audit_log=None,              # audit_log.AuditLog | None
    ):
        super().__init__(daemon=True, name="imap-sync")
        self._db = db
        self._get_creds = get_credentials_cb
        self._event_cb = event_cb
        self._audit = audit_log
        self.cmd_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._idle_folder: Optional[str] = None  # set after first _run_folders
        self._idle_mode: Optional[bool] = None   # True=IDLE, False=polling, None=unknown

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._persistent_sync_loop()
            except Exception as exc:
                logger.exception("Sync connection lost: %s", exc)
                self._emit(SyncEventType.DISCONNECTED)
                # Wait before reconnecting; wake early on STOP or SYNC_NOW
                try:
                    cmd = self.cmd_queue.get(timeout=RECONNECT_DELAY)
                    if cmd.type == CmdType.STOP:
                        return
                except queue.Empty:
                    pass

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

    def _persistent_sync_loop(self) -> None:
        """Connect once; sync in a loop with IMAP IDLE between cycles."""
        try:
            import imapclient
            import imapclient.exceptions as _imap_exc
        except ImportError:
            logger.error("imapclient not installed")
            self._emit(SyncEventType.SYNC_ERROR, error="imapclient not installed")
            time.sleep(RECONNECT_DELAY)
            return

        creds = self._get_creds()
        if creds is None:
            self._emit(SyncEventType.AUTH_REQUIRED)
            time.sleep(RECONNECT_DELAY)
            return

        email_addr = creds.email
        if email_addr and not self._db.get_meta("email"):
            self._db.set_meta("email", email_addr)

        try:
            client = imapclient.IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True)
        except Exception as exc:
            raise RuntimeError(f"IMAP connect failed: {exc}") from exc

        try:
            try:
                client.oauth2_login(email_addr, creds.access_token)
            except _imap_exc.LoginError:
                logger.warning("IMAP login failed — account may need re-authorisation")
                self._emit(SyncEventType.AUTH_REQUIRED)
                return

            self._emit(SyncEventType.CONNECTED)
            self._idle_mode = None  # re-announce mode after each reconnect

            while not self._stop_event.is_set():
                self._run_folders(client, email_addr)
                self._emit(SyncEventType.SYNC_COMPLETE)

                # Drain any commands that arrived while syncing
                try:
                    while True:
                        cmd = self.cmd_queue.get_nowait()
                        if cmd.type == CmdType.STOP:
                            return
                except queue.Empty:
                    pass

                # Block until the server signals a change, a command arrives,
                # or the fallback polling interval elapses.
                if not self._wait_for_changes(client):
                    return  # STOP received

        finally:
            try:
                client.logout()
            except Exception:
                pass
            self._emit(SyncEventType.DISCONNECTED)

    def _wait_for_changes(self, client) -> bool:
        """IDLE on the primary Notes folder; return False if STOP received."""
        if not self._idle_folder:
            # No folder known yet — plain sleep
            try:
                cmd = self.cmd_queue.get(timeout=SYNC_INTERVAL)
                return cmd.type != CmdType.STOP
            except queue.Empty:
                return True

        try:
            client.select_folder(self._idle_folder, readonly=True)
            client.idle()
            if self._idle_mode is not True:
                self._idle_mode = True
                logger.info("IMAP IDLE active on %s — changes will sync in real time",
                            self._idle_folder)

            deadline = time.monotonic() + IDLE_REFRESH_SECS
            while time.monotonic() < deadline and not self._stop_event.is_set():
                responses = client.idle_check(timeout=5)
                if responses:
                    logger.debug("IDLE woke on server notification: %s", responses)
                    client.idle_done()
                    return True
                try:
                    cmd = self.cmd_queue.get_nowait()
                    client.idle_done()
                    return cmd.type != CmdType.STOP
                except queue.Empty:
                    pass

            # Refresh IDLE before the 30-minute server cutoff
            client.idle_done()
            return not self._stop_event.is_set()

        except Exception as exc:
            if self._idle_mode is not False:
                self._idle_mode = False
                logger.info("IMAP IDLE not available (%s) — polling every %ds",
                            exc, SYNC_INTERVAL)
            # Fall back to polling
            try:
                cmd = self.cmd_queue.get(timeout=SYNC_INTERVAL)
                return cmd.type != CmdType.STOP
            except queue.Empty:
                return True

    def _run_folders(self, client, email_addr: str) -> None:
        notes_folders = self._get_notes_folders(client)
        logger.info("Syncing %d Notes folder(s): %s", len(notes_folders),
                    [n for _, n in notes_folders])

        # Root Notes folder is last in the list; IDLE on it to catch all changes
        # (Gmail label inheritance means root folder sees messages from all subfolders).
        if notes_folders:
            self._idle_folder = notes_folders[-1][0]

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
        conflict_notes: list = []

        if new_uids:
            fetched = client.fetch(list(new_uids), ["RFC822", "ENVELOPE", "INTERNALDATE"])

            # Deduplicate by apple_uuid: if multiple new messages share the same UUID
            # (us + Apple Notes both pushed versions), keep only the highest UID and
            # collect the rest as stale duplicates to delete.
            uuid_to_highest_uid: dict[str, int] = {}
            for uid, data in fetched.items():
                raw = data.get(b"RFC822") or data.get(b"BODY[]")
                if not raw:
                    continue
                note = self._parse_message(raw, uid, folder.id)
                if note.apple_uuid:
                    prev = uuid_to_highest_uid.get(note.apple_uuid)
                    if prev is None or uid > prev:
                        uuid_to_highest_uid[note.apple_uuid] = uid

            stale_uids: list[int] = []

            for uid, data in sorted(fetched.items()):
                raw = data.get(b"RFC822") or data.get(b"BODY[]")
                if not raw:
                    continue
                note = self._parse_message(raw, uid, folder.id)

                # If this uuid has a newer sibling in the same batch, it's a stale
                # duplicate — delete it rather than processing it.
                if note.apple_uuid and uuid_to_highest_uid.get(note.apple_uuid, uid) != uid:
                    stale_uids.append(uid)
                    logger.info(
                        "Stale duplicate UID %s (apple_uuid=%s) — will delete in favour of UID %s",
                        uid, note.apple_uuid, uuid_to_highest_uid[note.apple_uuid],
                    )
                    continue

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

                        # Apple Notes uses the current wall-clock time as the Date: header
                        # when it re-pushes from iCloud, so timestamp comparison alone is
                        # unreliable.  A content match is the definitive signal that Apple
                        # is echoing our own version back.
                        #
                        # Two sub-cases:
                        #   (a) Our canonical UID is still alive in remote_uids — Apple
                        #       added a redundant copy.  Delete Apple's echo.
                        #   (b) Our canonical UID is gone — Apple replaced our message.
                        #       Accept Apple's UID as the new canonical one so the
                        #       vanished-UID pass doesn't mark the note as deleted.
                        remote_text = note.body_text.strip()
                        remote_subj = note.subject.strip()
                        local_text  = local.body_text.strip()
                        local_subj  = local.subject.strip()
                        if remote_text == local_text and remote_subj == local_subj:
                            our_uid_alive = local.imap_uid and local.imap_uid in remote_uids
                            if our_uid_alive:
                                stale_uids.append(uid)
                                logger.info(
                                    "Echo detected: remote UID %s matches note %s %r "
                                    "— deleting stale copy (our UID %s still alive)",
                                    uid, local.id, local.subject, local.imap_uid,
                                )
                            else:
                                self._db.update_imap_uid(local.id, uid)
                                logger.info(
                                    "Echo detected: remote UID %s matches note %s %r "
                                    "— accepting as replacement (our UID %s gone)",
                                    uid, local.id, local.subject, local.imap_uid,
                                )
                            continue

                        # Also fall back to the timestamp check (catches echoes where
                        # Apple sends the original note content with an old Date header).
                        remote_mtime = note.modified_at or ""
                        local_synced = local.synced_at or ""
                        if local_synced and remote_mtime and remote_mtime <= local_synced:
                            our_uid_alive = local.imap_uid and local.imap_uid in remote_uids
                            if our_uid_alive:
                                stale_uids.append(uid)
                                logger.info(
                                    "Stale Apple re-push: UID %s mtime=%s <= synced_at=%s "
                                    "(note %s %r) — deleting",
                                    uid, remote_mtime, local_synced, local.id, local.subject,
                                )
                            else:
                                self._db.update_imap_uid(local.id, uid)
                                logger.info(
                                    "Stale re-push UID %s accepted as replacement for "
                                    "note %s %r (our UID %s gone)",
                                    uid, local.id, local.subject, local.imap_uid,
                                )
                            continue

                        if local.is_dirty:
                            # Local has unsaved edits that would be overwritten by the
                            # remote version. Point imap_uid at the conflicting remote UID
                            # so _push deletes it when uploading the local version.
                            if local.imap_uid != uid:
                                self._db.update_imap_uid(local.id, uid)
                            n_conflicts += 1
                            conflict_notes.append((local.id, local.subject))
                            logger.info(
                                "Sync conflict on note %s (%r) — keeping local edits, "
                                "will delete remote UID %s on push",
                                local.id, local.subject, uid,
                            )
                            continue
                        # Local is clean — if our current imap_uid is already higher
                        # than this UID the remote message is a stale older copy.
                        if local.imap_uid and local.imap_uid > uid:
                            if local.imap_uid in remote_uids:
                                stale_uids.append(uid)
                                logger.info(
                                    "Skipping stale remote UID %s for note %s "
                                    "(local already at UID %s)",
                                    uid, local.id, local.imap_uid,
                                )
                            else:
                                self._db.update_imap_uid(local.id, uid)
                                logger.info(
                                    "Accepting UID %s for note %s (our higher UID %s is gone)",
                                    uid, local.id, local.imap_uid,
                                )
                            continue

                # Mark the note as synced at pull time so saving it doesn't
                # clear synced_at and immediately make it dirty again.
                from datetime import datetime, timezone as _tz
                note.synced_at = datetime.now(_tz.utc).isoformat()
                result = self._db.save_note(note)
                if result.id:
                    changed = True

            # Clean up stale duplicates from IMAP so they don't resurface next cycle.
            if stale_uids:
                try:
                    client.delete_messages(stale_uids)
                    client.expunge()
                    logger.info("Deleted %d stale duplicate UID(s) in %s: %s",
                                len(stale_uids), folder_name, stale_uids)
                except Exception as exc:
                    logger.warning("Could not delete stale duplicates %s: %s", stale_uids, exc)

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
            self._emit(SyncEventType.SYNC_CONFLICT,
                       data={"count": n_conflicts, "notes": conflict_notes})

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

            try:
                client.append(
                    folder_name,
                    msg_bytes,
                    flags=["\\Seen"],
                    msg_time=_parse_dt(note.modified_at),
                )
            except Exception as exc:
                err = (f"IMAP APPEND failed: {exc}  "
                       f"[note_id={note.id} folder={folder_name!r} "
                       f"apple_uuid={note.apple_uuid}]")
                logger.error(err)
                if self._audit:
                    self._audit.mark_error(note.id, err)
                self._emit(SyncEventType.SYNC_ERROR, error=err)
                continue

            # Search UIDs >= uidnext, optionally filtered by our tracking header.
            # Fall back to the raw UID range if the server doesn't support HEADER search.
            try:
                candidates = client.search(
                    ["UID", f"{uidnext}:*", "HEADER", "X-Jotter-Id", str(note.id)]
                )
            except Exception:
                candidates = client.search(["UID", f"{uidnext}:*"])
            new_uid = candidates[-1] if candidates else None

            if not new_uid:
                err = (f"Note not found in IMAP after upload  "
                       f"[note_id={note.id} subject={note.subject!r} "
                       f"folder={folder_name!r} uidnext={uidnext} "
                       f"apple_uuid={note.apple_uuid}]")
                logger.error(err)
                if self._audit:
                    self._audit.mark_error(note.id, err)
                self._emit(SyncEventType.SYNC_ERROR, error=err)
                continue

            # Delete the old IMAP message if it existed
            if note.imap_uid and note.imap_uid != new_uid:
                try:
                    client.delete_messages([note.imap_uid])
                    client.expunge()
                except Exception as exc:
                    logger.warning("Could not delete old UID %s: %s", note.imap_uid, exc)

            # Retrieve the Message-ID and verify subject from the freshly appended message
            try:
                fetched = client.fetch([new_uid], ["ENVELOPE"])
                envelope = fetched.get(new_uid, {}).get(b"ENVELOPE")
            except Exception as exc:
                err = (f"Verification fetch failed: {exc}  "
                       f"[note_id={note.id} new_uid={new_uid} "
                       f"folder={folder_name!r} apple_uuid={note.apple_uuid}]")
                logger.error(err)
                if self._audit:
                    self._audit.mark_error(note.id, err)
                self._emit(SyncEventType.SYNC_ERROR, error=err)
                continue

            if not envelope:
                err = (f"No envelope at UID {new_uid} after upload  "
                       f"[note_id={note.id} subject={note.subject!r} "
                       f"folder={folder_name!r} apple_uuid={note.apple_uuid}]")
                logger.error(err)
                if self._audit:
                    self._audit.mark_error(note.id, err)
                self._emit(SyncEventType.SYNC_ERROR, error=err)
                continue

            msg_id = ""
            if envelope.message_id:
                msg_id = envelope.message_id.decode(errors="replace")

            self._db.mark_synced(note.id, new_uid, msg_id, apple_uuid=note.apple_uuid)
            logger.debug("Pushed note %s → UID %s (folder=%s)", note.id, new_uid, folder_name)
            if self._audit:
                self._audit.mark_ok(
                    note.id,
                    imap_uid=new_uid,
                    apple_uuid=note.apple_uuid,
                    message_id=msg_id or None,
                    folder=folder_name,
                )

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
