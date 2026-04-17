# Jotter

A native GNOME notes app that syncs with Apple Notes and Gmail.

Jotter stores notes in Gmail's Notes IMAP mailbox — the exact same mailbox Apple Notes uses when configured with a Google account. Notes you write in Jotter appear in Apple Notes on your iPhone and Mac, and vice versa, within one sync cycle.

## Features

- **Apple Notes sync** — full two-way sync via Gmail IMAP; notes appear on all your Apple devices
- **Native GNOME UI** — three-column layout built with GTK4 and Libadwaita, following GNOME HIG
- **Rich text** — bold, italic, underline, strikethrough, three heading levels, monospace/code
- **Folders** — all Apple Notes folders are synced and shown in the sidebar; drag notes between folders
- **Full-text search** — instant search powered by SQLite FTS5
- **GNOME Online Accounts** — sign in once in GNOME Settings, no OAuth setup required
- **Offline mode** — works as a local notes manager without a Google account

## Requirements

- GNOME on Ubuntu 22.04+ or any distribution with GTK 4.12+ and Libadwaita 1.4+
- A Google account added in **GNOME Settings → Online Accounts** (for sync)
- Python 3.10+

## Installation

### 1. Install system dependencies

```bash
sudo apt install python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-goa-1.0 \
    python3-imapclient python3-google-auth-oauthlib \
    python3-secretstorage
```

### 2. Install Jotter

```bash
pip install --user -e .
```

### 3. Add your Google Account

Open **GNOME Settings → Online Accounts → +** and sign in with your Google account. Jotter detects it automatically — no OAuth credentials file or developer console setup needed.

### 4. Run

```bash
jotter
```

## How it works

Apple Notes, when configured with a Google account, stores notes as RFC 2822 email messages in a dedicated IMAP mailbox on Gmail. Jotter reads and writes that same mailbox using the Gmail IMAP API with an OAuth2 token from GNOME Online Accounts. Each note is identified by its `X-Universally-Unique-Identifier` header, which stays stable across edits, so changes from Apple Notes and from Jotter are matched correctly without creating duplicates.

Sync runs in a background thread every 60 seconds, or immediately after you save a note.

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+N | New note |
| Ctrl+F | Toggle search |
| Ctrl+B | Bold |
| Ctrl+I | Italic |
| Ctrl+U | Underline |

## Data

- **Notes cache:** `~/.local/share/jotter/cache.db` (SQLite, rebuilt automatically from IMAP)
- **OAuth token:** GNOME Secret Service (Keyring) — never stored as a plain-text file

## Fallback: manual OAuth credentials

If you prefer not to use GNOME Online Accounts, place a `client_secrets.json` file (Desktop app type, downloaded from Google Cloud Console) at:

```
~/.config/jotter/client_secrets.json
```

This triggers a one-time browser-based OAuth flow on first launch.

## License

MIT
