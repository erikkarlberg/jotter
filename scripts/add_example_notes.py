#!/usr/bin/env python3
"""Push 10 example notes directly to IMAP (bypasses the local DB).

Uses the same credential path as the app: GNOME Online Accounts first,
then ~/.config/jotter/client_secrets.json as fallback.

Usage:
    python3 scripts/add_example_notes.py
    python3 scripts/add_example_notes.py --folder "Notes/Work"
    python3 scripts/add_example_notes.py --count 5
"""

from __future__ import annotations

import argparse
import email.message
import email.utils
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

EXAMPLE_NOTES = [
    (
        "Meeting agenda — Q2 planning",
        "<div>Meeting agenda — Q2 planning</div>"
        "<div><br></div>"
        "<div>1. Review Q1 results</div>"
        "<div>2. Set OKRs for Q2</div>"
        "<div>3. Headcount discussion</div>"
        "<div>4. Any other business</div>",
    ),
    (
        "Book list",
        "<div>Book list</div>"
        "<div><br></div>"
        "<div>- The Pragmatic Programmer</div>"
        "<div>- Designing Data-Intensive Applications</div>"
        "<div>- Clean Architecture</div>"
        "<div>- A Philosophy of Software Design</div>",
    ),
    (
        "Grocery shopping",
        "<div>Grocery shopping</div>"
        "<div><br></div>"
        "<div>Milk, eggs, bread</div>"
        "<div>Pasta, tomato sauce</div>"
        "<div>Olive oil, garlic</div>"
        "<div>Apples, bananas</div>",
    ),
    (
        "Workout routine",
        "<div>Workout routine</div>"
        "<div><br></div>"
        "<div>Monday — chest &amp; triceps</div>"
        "<div>Wednesday — back &amp; biceps</div>"
        "<div>Friday — legs &amp; shoulders</div>"
        "<div>Weekend — rest or cardio</div>",
    ),
    (
        "Ideas for the weekend",
        "<div>Ideas for the weekend</div>"
        "<div><br></div>"
        "<div>Visit the botanical garden</div>"
        "<div>Try the new ramen place downtown</div>"
        "<div>Finish reading chapter 7</div>",
    ),
    (
        "Passwords hint (not real)",
        "<div>Passwords hint (not real)</div>"
        "<div><br></div>"
        "<div>Email — childhood pet + birth year</div>"
        "<div>Bank — first car model + !</div>"
        "<div>Work VPN — see IT portal</div>",
    ),
    (
        "Travel packing list",
        "<div>Travel packing list</div>"
        "<div><br></div>"
        "<div>Passport, tickets, hotel confirmation</div>"
        "<div>Chargers &amp; adapters</div>"
        "<div>Toiletries bag</div>"
        "<div>Two changes of clothes per day</div>"
        "<div>Noise-cancelling headphones</div>",
    ),
    (
        "Recipe — quick tomato soup",
        "<div>Recipe — quick tomato soup</div>"
        "<div><br></div>"
        "<div>1 can crushed tomatoes</div>"
        "<div>1 onion, diced</div>"
        "<div>2 cloves garlic</div>"
        "<div>200 ml cream</div>"
        "<div>Salt, pepper, basil</div>"
        "<div><br></div>"
        "<div>Sauté onion &amp; garlic, add tomatoes, simmer 15 min, blend, add cream.</div>",
    ),
    (
        "Home improvement tasks",
        "<div>Home improvement tasks</div>"
        "<div><br></div>"
        "<div>Fix leaky kitchen tap</div>"
        "<div>Replace bathroom light bulb</div>"
        "<div>Repaint hallway wall</div>"
        "<div>Buy a new doormat</div>",
    ),
    (
        "Learning goals 2026",
        "<div>Learning goals 2026</div>"
        "<div><br></div>"
        "<div>- Finish the Rust book</div>"
        "<div>- Build a small CLI tool in Go</div>"
        "<div>- Complete one ML course</div>"
        "<div>- Contribute to an open-source project</div>",
    ),
]


def _get_credentials():
    """Return ImapCredentials via GOA or client_secrets.json, or exit."""
    from jotter.auth import (
        list_goa_google_accounts,
        get_goa_credentials,
        get_imap_credentials_from_client_secrets,
    )
    from gi.repository import GLib

    accounts = list_goa_google_accounts()
    if accounts:
        creds = get_goa_credentials(accounts[0])
        if creds:
            logger.info("Using GNOME Online Accounts (%s)", creds.email)
            return creds

    secrets = Path(GLib.get_user_config_dir()) / "jotter" / "client_secrets.json"
    if secrets.exists():
        creds = get_imap_credentials_from_client_secrets(str(secrets))
        if creds:
            logger.info("Using client_secrets.json (%s)", creds.email)
            return creds

    sys.exit(
        "No credentials found.\n"
        "Add a Google account in GNOME Settings → Online Accounts, or place\n"
        f"client_secrets.json at {secrets}"
    )


def _ensure_folder(client, folder_name: str) -> None:
    folders = [f[2] for f in client.list_folders()]
    if folder_name not in folders:
        client.create_folder(folder_name)
        logger.info("Created IMAP folder %r", folder_name)


def _build_message(subject: str, body_html: str, from_addr: str, when: datetime) -> bytes:
    from jotter.utils import strip_html

    body_text = strip_html(body_html)
    apple_uuid = str(uuid.uuid4()).upper()

    msg = email.message.EmailMessage()
    msg["From"] = from_addr
    msg["To"] = from_addr
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(when.timestamp(), localtime=False)
    msg["MIME-Version"] = "1.0"
    msg["X-Uniform-Type-Identifier"] = "com.apple.mail-note"
    msg["X-Mail-Created-Date"] = email.utils.formatdate(when.timestamp(), localtime=False)
    msg["X-Universally-Unique-Identifier"] = apple_uuid

    html_body = (
        "<html><head></head>"
        '<body style="overflow-wrap: break-word; -webkit-nbsp-mode: space;'
        ' line-break: after-white-space;">'
        f"{body_html}"
        "</body></html>"
    )
    msg.set_content(body_text, subtype="plain", charset="utf-8")
    msg.add_alternative(html_body, subtype="html", charset="utf-8")
    return msg.as_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", default="Notes",
                        help="IMAP folder to write notes into (default: Notes)")
    parser.add_argument("--count", type=int, default=len(EXAMPLE_NOTES),
                        help=f"Number of notes to add (1–{len(EXAMPLE_NOTES)}, "
                             f"default: {len(EXAMPLE_NOTES)})")
    args = parser.parse_args()

    count = max(1, min(args.count, len(EXAMPLE_NOTES)))
    notes_to_add = EXAMPLE_NOTES[:count]

    try:
        import imapclient
    except ImportError:
        sys.exit("imapclient is not installed. Run: pip install imapclient")

    creds = _get_credentials()

    logger.info("Connecting to %s …", IMAP_HOST)
    with imapclient.IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True) as client:
        client.oauth2_login(creds.email, creds.access_token)
        logger.info("Logged in as %s", creds.email)

        _ensure_folder(client, args.folder)

        now = datetime.now(timezone.utc)
        for i, (subject, body_html) in enumerate(notes_to_add):
            # Spread notes over the past 10 days so they have distinct timestamps.
            when = now - timedelta(days=len(notes_to_add) - 1 - i, minutes=i * 3)
            msg_bytes = _build_message(subject, body_html, creds.email, when)
            client.append(args.folder, msg_bytes, flags=["\\Seen"], msg_time=when)
            logger.info("  [%d/%d] %s", i + 1, len(notes_to_add), subject)

    logger.info("Done — %d note(s) added to %r.", len(notes_to_add), args.folder)
    logger.info("Restart Jotter or wait for the next sync to see them.")


if __name__ == "__main__":
    main()
