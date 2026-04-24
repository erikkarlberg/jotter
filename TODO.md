# Jotter — Roadmap to v1.0

A prioritised task list for taking Jotter from its current state to a polished, finished product.

---

## Status key
- [ ] Not started
- [~] In progress / partial
- [x] Done

---

## 1. Core Features

### 1.1 Organisation
- 1.1.1 [~] **Drag-and-drop notes between folders** — drag source on `NoteRow` and drop target on `FolderRow` exist; validate that the IMAP move is triggered and reflected correctly after a drop.
- 1.1.2 [ ] **Subfolder support** — display nested IMAP folders (e.g. `Notes/Work/Projects`) as an indented tree in the sidebar, create/rename/delete subfolders, and move notes into them.
- 1.1.3 [ ] **Sort options** — sort note list by: last modified (default), created date, title A–Z, title Z–A.

### 1.2 Import & Export
- 1.2.1 [ ] **Export note as Markdown** — convert the HTML body to Markdown and write to a `.md` file via a native save dialog.
- 1.2.2 [ ] **Export note as plain text** — strip formatting, write `.txt`.
- 1.2.3 [ ] **Export note as HTML** — save the raw `body_html` as a standalone `.html` file.
- 1.2.4 [ ] **Export note as PDF** — render the note through GTK's print-to-PDF path.
- 1.2.5 [ ] **Import from Markdown** — parse a `.md` file, convert to HTML, create a new note in the selected folder.
- 1.2.6 [ ] **Import from plain text** — create a new note from a `.txt` file.
- 1.2.7 [ ] **Import from Apple Notes export (.enex / HTML zip)** — bulk-import an Apple Notes backup archive.
- 1.2.8 [ ] **Batch export folder** — export all notes in a folder as a zip of Markdown files.

---

## 2. Editor Improvements

- 2.1 [ ] **Image attachments** — Apple Notes stores images as MIME multipart attachments with `Content-ID` references in the HTML body (not Base64 embedded in the HTML). Jotter must construct and parse the same `multipart/mixed` structure to correctly display images synced from Apple Notes and to have images created in Jotter appear on Apple devices.
- 2.2 [ ] **Word and character count** — displayed in the editor footer.
- 2.3 [ ] **Spell check** — integrate GtkSourceView or `enchant` for on-the-fly spell checking.
- 2.4 [ ] **Find & replace within note** — Ctrl+H opens a find-and-replace bar inside the editor.
- 2.5 [ ] **Undo/redo beyond default** — ensure undo history survives autosave cycles.
- 2.6 [ ] **Focus / distraction-free mode** — hide sidebars to full-screen the editor with a keyboard shortcut (e.g. F11).

---

## 3. Sync

- 3.1 [ ] **Sync status indicator** — show a spinner/icon in the header that distinguishes: idle, syncing, error, offline.
- 3.2 [ ] **Manual sync button** — force a full pull+push from the toolbar (useful when offline for a while).
- 3.3 [ ] **Selective folder sync** — let the user choose which IMAP folders are synced vs. local-only.
- 3.4 [ ] **Conflict UI** — when a conflict is detected, show a diff and let the user pick which version to keep rather than silently preferring local.
- 3.5 [ ] **Desktop notification on sync errors** — use `GLib.Notification` to surface sync failures without the user having to open the error log.

---

## 4. Search & Discovery

- 4.1 [ ] **Advanced search filters** — filter by folder, date range, or tag from the search bar.
- 4.2 [ ] **Search result highlighting** — highlight matched terms in both the note list snippet and the editor body.
- 4.3 [ ] **Recents / jump-to** — Ctrl+P command palette that fuzzy-searches note titles for quick navigation.
- 4.4 [ ] **Note tags** — Apple Notes tags (iOS 15+) are plain `#hashtag` text in the note body and are present in the IMAP message. Jotter should parse these and surface them as filterable labels; tags added in Jotter must be written as `#hashtag` text so they sync to Apple devices.

---

## 5. Backup & Safety

- 5.1 [ ] **Automatic local backups** — periodically snapshot the SQLite database to `~/.local/share/jotter/backups/` (rolling, keep last 7).
- 5.2 [ ] **Export all notes on demand** — one-click "backup everything" to a zip of Markdown files.
- 5.3 [ ] **Trash / recently deleted** — soft-deleted notes go to a "Trash" pseudo-folder and are permanently removed after 30 days (matching Apple Notes behaviour).

---

## 6. Onboarding & Setup

- 6.1 [ ] **Welcome screen** — shown on first launch before any account is connected; explains what Jotter is and how to connect an account.
- 6.2 [ ] **Setup wizard** — step-by-step Adw dialog for connecting a Google account (GOA) or entering IMAP credentials manually.
- 6.3 [ ] **Empty-state illustrations** — meaningful placeholder content when a folder has no notes or search returns no results.

---

## 7. Accessibility & Polish

- 7.1 [ ] **Full keyboard navigation** — every action reachable without a mouse; focus ring visible on all interactive elements.
- 7.2 [ ] **Screen-reader labels** — `accessible-label` / `accessible-description` on all custom widgets.
- 7.3 [ ] **High-contrast support** — test under GNOME's high-contrast theme and fix any hard-coded colours.
- 7.4 [ ] **RTL language support** — ensure the three-column layout mirrors correctly for Arabic, Hebrew, etc.
- 7.5 [ ] **Responsive layout review** — test and polish the single-column (mobile/small-window) layout.

---

## 8. Packaging & Distribution

- 8.1 [ ] **Flatpak manifest** — `com.example.Jotter.json` with the correct GNOME runtime, permissions (`--share=network`, `--talk-name=org.gnome.OnlineAccounts`), and finish-args.
- 8.2 [ ] **Flathub submission** — meet Flathub quality requirements (metadata, screenshots, appstream XML).
- 8.3 [ ] **`.desktop` file** — proper `StartupNotify=true`, `Categories`, and `Keywords`.
- 8.4 [ ] **AppStream / metainfo XML** — release notes, screenshots, developer name, OARS rating.
- 8.5 [ ] **GNOME Shell search provider** — register Jotter so notes are surfaced directly in the Activities search.
- 8.6 [ ] **Autostart / background sync** — optional D-Bus activation so the sync daemon can run without the window open.

---

## 9. Code Quality & Testing

- 9.1 [ ] **Automated unit tests** — pytest suite for `models.py` (DB CRUD), `utils.py` (HTML↔TextBuffer round-trips), and `auth.py` (token refresh logic).
- 9.2 [ ] **Integration tests** — mock IMAP server (e.g. `IMAPStub`) to test the full sync loop without a live Gmail account.
- 9.3 [ ] **CI pipeline** — GitHub Actions workflow: lint (`ruff`), type-check (`mypy`), run tests, build Flatpak on every PR.
- 9.4 [ ] **Type annotations** — add `mypy`-clean type hints throughout; especially `window.py` and `imap_backend.py`.
- 9.5 [ ] **Refactor `window.py`** — at 1160+ lines it handles too much; split into sub-controllers for the sidebar, note list, and header bar.
- 9.6 [ ] **Logging cleanup** — replace bare `print()` calls with structured `logging` at appropriate levels; make log verbosity configurable.
