#!/usr/bin/env python3
"""
Apple Notes / Jotter IMAP interop test harness.

Run this from the repo root:
    python tests/apple_notes_interop_test.py

What it does:
  1. Authenticates via GOA or client_secrets.json.
  2. Takes a full dump of the current Notes IMAP folder (baseline).
  3. Pushes a set of specifically-crafted test notes to IMAP.
  4. Pauses and walks you through a series of activities in Apple Notes,
     re-reading the folder after each step.
  5. Prints a detailed header-and-body diff so every question about how
     Apple Notes handles notes from Jotter is answered from real data.
"""

from __future__ import annotations

import email
import email.header
import email.utils
import os
import sys
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Make sure the repo's jotter package is importable when run from any cwd.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import imapclient
from jotter.auth import (
    ImapCredentials,
    get_goa_credentials,
    get_imap_credentials_from_client_secrets,
    list_goa_google_accounts,
)
from jotter.imap_backend import IMAP_HOST, IMAP_PORT, NOTES_FOLDER_CANDIDATES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEPARATOR = "=" * 72
THIN = "-" * 72
INTEROP_TAG = "JOTTER-INTEROP-TEST"  # Subject prefix for our test notes

_INTERESTING_HEADERS = [
    "Subject",
    "Date",
    "X-Mail-Created-Date",
    "X-Universally-Unique-Identifier",
    "X-Uniform-Type-Identifier",
    "X-Jotter-Id",
    "Message-ID",
    "Content-Type",
    "MIME-Version",
    "From",
    "To",
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return email.utils.formatdate(dt.timestamp(), localtime=False)


def _decode_subject(raw: str) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    out = []
    for p, enc in parts:
        if isinstance(p, bytes):
            out.append(p.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(p)
    return "".join(out)


def _decode_payload(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset("utf-8") or "utf-8"
    return payload.decode(charset, errors="replace")


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------

@dataclass
class TestNote:
    label: str             # human label, e.g. "NOTE-A"
    subject: str
    body_text: str
    body_html: str
    apple_uuid: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = _now_utc()
        if self.modified_at is None:
            self.modified_at = _now_utc()

    def to_rfc822(self, from_addr: str) -> bytes:
        import email.message
        msg = email.message.EmailMessage()
        msg["From"] = from_addr
        msg["To"] = from_addr
        msg["Subject"] = self.subject
        msg["Date"] = _fmt_dt(self.modified_at)
        msg["MIME-Version"] = "1.0"
        msg["X-Uniform-Type-Identifier"] = "com.apple.mail-note"
        msg["X-Mail-Created-Date"] = _fmt_dt(self.created_at)
        msg["X-Universally-Unique-Identifier"] = self.apple_uuid

        html = self.body_html
        if not html.lower().lstrip().startswith("<html"):
            html = (
                '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN"'
                ' "http://www.w3.org/TR/html4/strict.dtd">\n'
                "<html><head>"
                '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
                "</head><body>"
                f"{html}"
                "</body></html>"
            )

        msg.set_content(self.body_text, subtype="plain", charset="utf-8")
        msg.add_alternative(html, subtype="html", charset="utf-8")
        return msg.as_bytes()


# ---------------------------------------------------------------------------
# IMAP dump
# ---------------------------------------------------------------------------

@dataclass
class MessageSnapshot:
    uid: int
    subject: str
    apple_uuid: Optional[str]
    message_id: Optional[str]
    date_header: str
    x_created: str
    x_uti: str
    all_headers: dict
    body_plain: str
    body_html: str
    internal_date: str


def _snapshot_folder(client: imapclient.IMAPClient, folder_name: str) -> list[MessageSnapshot]:
    """Fetch all messages in *folder_name* and return their full snapshots."""
    try:
        client.select_folder(folder_name, readonly=True)
    except Exception as exc:
        print(f"  [!] Could not select {folder_name}: {exc}")
        return []

    uids = client.search("ALL")
    if not uids:
        return []

    fetched = client.fetch(uids, ["RFC822", "INTERNALDATE"])
    snapshots = []
    for uid, data in sorted(fetched.items()):
        raw = data.get(b"RFC822") or data.get(b"BODY[]")
        if not raw:
            continue
        internal_date = str(data.get(b"INTERNALDATE", ""))
        msg = email.message_from_bytes(raw)

        subject = _decode_subject(msg.get("Subject", ""))
        apple_uuid = (msg.get("X-Universally-Unique-Identifier") or "").strip().upper() or None
        message_id = (msg.get("Message-ID") or "").strip() or None
        date_header = msg.get("Date", "")
        x_created = msg.get("X-Mail-Created-Date", "")
        x_uti = msg.get("X-Uniform-Type-Identifier", "")

        all_headers = {}
        for name in _INTERESTING_HEADERS:
            val = msg.get(name)
            if val is not None:
                all_headers[name] = val

        body_plain = ""
        body_html = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/html" and not body_html:
                    body_html = _decode_payload(part)
                elif ct == "text/plain" and not body_plain:
                    body_plain = _decode_payload(part)
        else:
            ct = msg.get_content_type()
            if ct == "text/html":
                body_html = _decode_payload(msg)
            else:
                body_plain = _decode_payload(msg)

        snapshots.append(MessageSnapshot(
            uid=uid,
            subject=subject,
            apple_uuid=apple_uuid,
            message_id=message_id,
            date_header=date_header,
            x_created=x_created,
            x_uti=x_uti,
            all_headers=all_headers,
            body_plain=body_plain,
            body_html=body_html,
            internal_date=internal_date,
        ))

    return snapshots


def _print_snapshot(snap: MessageSnapshot) -> None:
    print(f"  UID:          {snap.uid}")
    print(f"  Subject:      {snap.subject!r}")
    print(f"  Apple UUID:   {snap.apple_uuid}")
    print(f"  Message-ID:   {snap.message_id}")
    print(f"  Date:         {snap.date_header}")
    print(f"  X-Created:    {snap.x_created}")
    print(f"  UTI:          {snap.x_uti}")
    print(f"  Internal dt:  {snap.internal_date}")
    extra = {k: v for k, v in snap.all_headers.items()
             if k not in ("Subject", "Date", "X-Mail-Created-Date",
                          "X-Universally-Unique-Identifier",
                          "X-Uniform-Type-Identifier", "Message-ID")}
    for k, v in extra.items():
        print(f"  {k+':':<14}{v}")
    if snap.body_plain:
        preview = snap.body_plain[:200].replace("\n", "↵")
        print(f"  Body(plain):  {preview!r}")
    if snap.body_html:
        preview = snap.body_html[:300].replace("\n", "")
        print(f"  Body(html):   {preview!r}")


def _print_folder_dump(label: str, folder_name: str, snapshots: list[MessageSnapshot]) -> None:
    print()
    print(SEPARATOR)
    print(f"  DUMP: {label}  |  folder: {folder_name}  |  {len(snapshots)} message(s)")
    print(SEPARATOR)
    if not snapshots:
        print("  (empty)")
        return
    for i, snap in enumerate(snapshots):
        print(f"\n  [{i+1}] -----")
        _print_snapshot(snap)


def _diff_snapshots(
    label: str,
    before: list[MessageSnapshot],
    after: list[MessageSnapshot],
) -> None:
    print()
    print(SEPARATOR)
    print(f"  DIFF: {label}")
    print(SEPARATOR)

    before_by_uuid = {s.apple_uuid: s for s in before if s.apple_uuid}
    after_by_uuid = {s.apple_uuid: s for s in after if s.apple_uuid}
    before_uids = {s.uid for s in before}
    after_uids = {s.uid for s in after}

    # New UIDs
    new_uids = after_uids - before_uids
    gone_uids = before_uids - after_uids
    before_by_uid = {s.uid: s for s in before}
    after_by_uid = {s.uid: s for s in after}

    if new_uids:
        print(f"\n  + {len(new_uids)} NEW message(s) appeared:")
        for uid in sorted(new_uids):
            snap = after_by_uid[uid]
            print(f"\n    UID {uid}:")
            for line in _format_snap_lines(snap):
                print(f"    {line}")
    if gone_uids:
        print(f"\n  - {len(gone_uids)} message(s) VANISHED:")
        for uid in sorted(gone_uids):
            snap = before_by_uid[uid]
            print(f"\n    UID {uid}: subject={snap.subject!r}  uuid={snap.apple_uuid}")

    # Changed by UUID
    common_uuids = set(before_by_uuid) & set(after_by_uuid)
    changed = 0
    for auuid in sorted(common_uuids):
        b = before_by_uuid[auuid]
        a = after_by_uuid[auuid]
        diffs = _compare_snaps(b, a)
        if diffs:
            changed += 1
            print(f"\n  ~ CHANGED (uuid={auuid}):")
            for d in diffs:
                print(f"    {d}")
    if not new_uids and not gone_uids and not changed:
        print("  (no changes detected)")


def _format_snap_lines(snap: MessageSnapshot) -> list[str]:
    return [
        f"Subject:    {snap.subject!r}",
        f"Apple UUID: {snap.apple_uuid}",
        f"Message-ID: {snap.message_id}",
        f"Date:       {snap.date_header}",
        f"X-Created:  {snap.x_created}",
        f"UTI:        {snap.x_uti}",
    ]


def _compare_snaps(b: MessageSnapshot, a: MessageSnapshot) -> list[str]:
    diffs = []
    fields = [
        ("UID", b.uid, a.uid),
        ("Subject", b.subject, a.subject),
        ("Message-ID", b.message_id, a.message_id),
        ("Date", b.date_header, a.date_header),
        ("X-Created", b.x_created, a.x_created),
        ("UTI", b.x_uti, a.x_uti),
        ("Body(plain)", b.body_plain[:80], a.body_plain[:80]),
        ("Body(html)", b.body_html[:120], a.body_html[:120]),
    ]
    for name, bval, aval in fields:
        if bval != aval:
            diffs.append(f"{name}:")
            diffs.append(f"  before: {bval!r}")
            diffs.append(f"  after:  {aval!r}")
    return diffs


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def get_credentials() -> ImapCredentials:
    print("\n[Auth] Looking for Google account via GNOME Online Accounts…")
    accounts = list_goa_google_accounts()
    if accounts:
        creds = get_goa_credentials(accounts[0])
        if creds:
            print(f"[Auth] GOA: authenticated as {creds.email}")
            return creds
        print("[Auth] GOA account found but token fetch failed.")

    secrets = os.path.expanduser("~/.config/jotter/client_secrets.json")
    if os.path.isfile(secrets):
        print(f"[Auth] Trying client_secrets.json at {secrets}…")
        creds = get_imap_credentials_from_client_secrets(secrets)
        if creds:
            print(f"[Auth] client_secrets: authenticated as {creds.email}")
            return creds

    print("\n[Auth] ERROR: No credentials found.")
    print("  Either add a Google account in GNOME Online Accounts, or place")
    print("  a client_secrets.json in ~/.config/jotter/")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Notes folder discovery
# ---------------------------------------------------------------------------

def find_notes_folder(client: imapclient.IMAPClient) -> str:
    all_folders = [f[2] for f in client.list_folders()]
    for candidate in NOTES_FOLDER_CANDIDATES:
        if candidate in all_folders:
            return candidate
    print("[IMAP] Notes folder not found; creating 'Notes'.")
    client.create_folder("Notes")
    return "Notes"


# ---------------------------------------------------------------------------
# Test note definitions
# ---------------------------------------------------------------------------

def build_test_notes() -> list[TestNote]:
    ts = _now_utc()
    tag = INTEROP_TAG

    return [
        TestNote(
            label="NOTE-A",
            subject=f"[{tag}] Plain text note",
            body_text="This is a plain text note.\nSecond line here.",
            body_html="<div>This is a plain text note.<br>Second line here.</div>",
        ),
        TestNote(
            label="NOTE-B",
            subject=f"[{tag}] Rich text note",
            body_text="Bold and italic and underline text.",
            body_html="<div><b>Bold</b> and <i>italic</i> and <u>underline</u> text.</div>",
        ),
        TestNote(
            label="NOTE-C",
            subject=f"[{tag}] Multiline note",
            body_text="Line one\nLine two\nLine three\n\nAfter blank line.",
            body_html=(
                "<div>Line one<br>Line two<br>Line three<br><br>After blank line.</div>"
            ),
        ),
        TestNote(
            label="NOTE-D",
            subject=f"[{tag}] Special chars: café naïve résumé",
            body_text="Emojis: 🎉 🍎 📝\nAccents: café, naïve, résumé\nSymbols: © ® ™",
            body_html=(
                "<div>Emojis: 🎉 🍎 📝<br>"
                "Accents: café, naïve, résumé<br>"
                "Symbols: © ® ™</div>"
            ),
        ),
        TestNote(
            label="NOTE-E",
            subject=f"[{tag}] Note to be edited in Apple Notes",
            body_text="Original content. Please edit this note in Apple Notes.",
            body_html="<div>Original content. Please edit this note in Apple Notes.</div>",
        ),
        TestNote(
            label="NOTE-F",
            subject=f"[{tag}] Note to be deleted in Apple Notes",
            body_text="This note should be deleted in Apple Notes.",
            body_html="<div>This note should be deleted in Apple Notes.</div>",
        ),
    ]


# ---------------------------------------------------------------------------
# Pause / prompt helpers
# ---------------------------------------------------------------------------

def pause(prompt: str) -> None:
    print()
    print(THIN)
    print(textwrap.fill(prompt, width=70))
    print(THIN)
    input("  Press ENTER when done… ")


# ---------------------------------------------------------------------------
# Main test flow
# ---------------------------------------------------------------------------

def main() -> None:
    print(SEPARATOR)
    print("  Jotter ↔ Apple Notes IMAP Interop Test Harness")
    print(SEPARATOR)
    print(
        "\nThis script systematically creates test notes in the IMAP Notes\n"
        "folder and walks you through Apple Notes activities so we can\n"
        "observe exactly how Apple Notes reads, edits, and stores notes.\n"
    )

    # --- Auth ---
    creds = get_credentials()

    print("\n[IMAP] Connecting…")
    with imapclient.IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True) as client:
        client.oauth2_login(creds.email, creds.access_token)
        print("[IMAP] Connected.")

        notes_folder = find_notes_folder(client)
        print(f"[IMAP] Notes folder: {notes_folder!r}")

        # ------------------------------------------------------------------
        # PHASE 0: Baseline — dump what's already there
        # ------------------------------------------------------------------
        print("\n\n[Phase 0] Taking baseline snapshot of existing notes…")
        baseline = _snapshot_folder(client, notes_folder)
        _print_folder_dump("BASELINE (before test)", notes_folder, baseline)
        baseline_uuids = {s.apple_uuid for s in baseline if s.apple_uuid}

        # ------------------------------------------------------------------
        # PHASE 1: Push test notes
        # ------------------------------------------------------------------
        print("\n\n[Phase 1] Pushing test notes to IMAP…")
        test_notes = build_test_notes()
        pushed: dict[str, tuple[TestNote, int]] = {}  # label -> (note, uid)

        for tn in test_notes:
            msg_bytes = tn.to_rfc822(creds.email)
            folder_status = client.folder_status(notes_folder, [b"UIDNEXT"])
            uidnext = folder_status.get(b"UIDNEXT", 1)

            client.select_folder(notes_folder, readonly=False)
            client.append(
                notes_folder,
                msg_bytes,
                flags=["\\Seen"],
                msg_time=tn.modified_at,
            )

            # Find the new UID
            try:
                candidates = client.search(
                    ["UID", f"{uidnext}:*",
                     "HEADER", "X-Universally-Unique-Identifier", tn.apple_uuid]
                )
            except Exception:
                candidates = client.search(["UID", f"{uidnext}:*"])
            new_uid = candidates[-1] if candidates else None
            pushed[tn.label] = (tn, new_uid)
            print(f"  {tn.label}: pushed as UID {new_uid}  uuid={tn.apple_uuid}")

        after_push = _snapshot_folder(client, notes_folder)
        _print_folder_dump("AFTER PUSH (our test notes appended)", notes_folder, after_push)

        # Show only our test notes
        our_snaps = [s for s in after_push
                     if s.apple_uuid in {tn.apple_uuid for tn in test_notes}]
        print(f"\n[Phase 1] {len(our_snaps)} of {len(test_notes)} test notes visible in folder.")

        # ------------------------------------------------------------------
        # PHASE 2: Apple Notes reads the folder
        # ------------------------------------------------------------------
        pause(
            "PHASE 2 — Open Apple Notes on your Mac or iPhone and wait for it "
            "to sync. You should see the new notes appearing with subjects like "
            f"'[{INTEROP_TAG}] …'. Once they appear, press ENTER."
        )
        after_apple_opened = _snapshot_folder(client, notes_folder)
        _print_folder_dump("AFTER APPLE NOTES OPENED", notes_folder, after_apple_opened)
        _diff_snapshots(
            "Did Apple Notes change anything just by reading?",
            after_push,
            after_apple_opened,
        )

        # ------------------------------------------------------------------
        # PHASE 3: Edit NOTE-E in Apple Notes
        # ------------------------------------------------------------------
        note_e = next(tn for tn in test_notes if tn.label == "NOTE-E")
        pause(
            f"PHASE 3 — In Apple Notes, open the note titled "
            f"'[{INTEROP_TAG}] Note to be edited in Apple Notes' and add some "
            f"text at the end (e.g. ' — edited in Apple Notes'). Save and wait "
            f"for the note to sync back. Press ENTER when the edit has synced."
        )
        after_edit = _snapshot_folder(client, notes_folder)
        _print_folder_dump("AFTER EDITING NOTE-E IN APPLE NOTES", notes_folder, after_edit)
        _diff_snapshots(
            f"What changed after Apple Notes edited NOTE-E (uuid={note_e.apple_uuid})?",
            after_apple_opened,
            after_edit,
        )

        # ------------------------------------------------------------------
        # PHASE 4: Delete NOTE-F in Apple Notes
        # ------------------------------------------------------------------
        note_f = next(tn for tn in test_notes if tn.label == "NOTE-F")
        pause(
            f"PHASE 4 — In Apple Notes, delete the note titled "
            f"'[{INTEROP_TAG}] Note to be deleted in Apple Notes'. "
            f"Wait for the deletion to sync (give it ~30 seconds), then press ENTER."
        )
        after_delete = _snapshot_folder(client, notes_folder)
        _print_folder_dump("AFTER DELETING NOTE-F IN APPLE NOTES", notes_folder, after_delete)
        _diff_snapshots(
            f"What changed after Apple Notes deleted NOTE-F (uuid={note_f.apple_uuid})?",
            after_edit,
            after_delete,
        )

        # ------------------------------------------------------------------
        # PHASE 5: Create a brand-new note in Apple Notes
        # ------------------------------------------------------------------
        pause(
            "PHASE 5 — In Apple Notes, create a brand-new note with the title "
            f"'[{INTEROP_TAG}] Created in Apple Notes' and some body text. "
            "Wait for it to sync (~30 seconds), then press ENTER."
        )
        after_create = _snapshot_folder(client, notes_folder)
        _print_folder_dump("AFTER CREATING NOTE IN APPLE NOTES", notes_folder, after_create)
        _diff_snapshots(
            "What does a note created in Apple Notes look like?",
            after_delete,
            after_create,
        )

        # ------------------------------------------------------------------
        # PHASE 6: Move a note to a subfolder in Apple Notes
        # ------------------------------------------------------------------
        pause(
            "PHASE 6 — In Apple Notes, move the note "
            f"'[{INTEROP_TAG}] Plain text note' into a folder (create one if "
            "needed). Wait for sync, then press ENTER."
        )
        # Also list subfolders
        all_folders = [f[2] for f in client.list_folders()]
        notes_subfolders = [f for f in all_folders if f.startswith(notes_folder + "/")]
        print(f"\n[Phase 6] Notes subfolders now visible: {notes_subfolders}")

        after_move = _snapshot_folder(client, notes_folder)
        _print_folder_dump(f"AFTER MOVING NOTE (root folder {notes_folder})", notes_folder, after_move)
        _diff_snapshots(
            "What happened to NOTE-A in the root after moving to a subfolder?",
            after_create,
            after_move,
        )

        for subfolder in notes_subfolders:
            sub_snaps = _snapshot_folder(client, subfolder)
            _print_folder_dump(f"SUBFOLDER: {subfolder}", subfolder, sub_snaps)

        # ------------------------------------------------------------------
        # PHASE 7: Edit NOTE-A (rich text) in Apple Notes
        # ------------------------------------------------------------------
        note_b = next(tn for tn in test_notes if tn.label == "NOTE-B")
        pause(
            "PHASE 7 — In Apple Notes, open "
            f"'[{INTEROP_TAG}] Rich text note' and change the formatting "
            "(e.g. add a heading, change bold to italic). Then press ENTER."
        )
        after_rich_edit = _snapshot_folder(client, notes_folder)
        _print_folder_dump("AFTER EDITING RICH TEXT NOTE", notes_folder, after_rich_edit)
        _diff_snapshots(
            f"HTML format changes after rich-text edit (NOTE-B uuid={note_b.apple_uuid})?",
            after_move,
            after_rich_edit,
        )

        # ------------------------------------------------------------------
        # CLEANUP
        # ------------------------------------------------------------------
        print()
        print(SEPARATOR)
        print("  CLEANUP")
        print(SEPARATOR)
        cleanup = input(
            "\nDelete all test notes from IMAP? (y/N): "
        ).strip().lower()
        if cleanup == "y":
            # _snapshot_folder selects readonly=True, so we must re-select
            # readonly=False before every delete operation.
            all_snaps = _snapshot_folder(client, notes_folder)
            test_uids = [s.uid for s in all_snaps if INTEROP_TAG in s.subject]
            if test_uids:
                client.select_folder(notes_folder, readonly=False)
                client.delete_messages(test_uids)
                client.expunge()
                print(f"  Deleted {len(test_uids)} test note(s) from {notes_folder}.")
            # Subfolders
            for subfolder in notes_subfolders:
                try:
                    sub_snaps = _snapshot_folder(client, subfolder)
                    sub_uids = [s.uid for s in sub_snaps if INTEROP_TAG in s.subject]
                    if sub_uids:
                        client.select_folder(subfolder, readonly=False)
                        client.delete_messages(sub_uids)
                        client.expunge()
                        print(f"  Deleted {len(sub_uids)} test note(s) from {subfolder}.")
                except Exception as exc:
                    print(f"  Could not clean up {subfolder}: {exc}")
        else:
            print("  Skipped cleanup.")

        print()
        print(SEPARATOR)
        print("  Test complete. Review the output above for interop findings.")
        print(SEPARATOR)


if __name__ == "__main__":
    main()
