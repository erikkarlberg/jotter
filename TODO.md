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
- 1.1.3 [ ] **Note pinning / favourites** — pin a note to the top of any folder list; stored as a local flag in SQLite (no IMAP side-effect needed).
- 1.1.4 [ ] **Note reordering** — manual drag-to-reorder within a folder list (local order stored in DB, not synced to IMAP).
- 1.1.5 [ ] **Sort options** — sort note list by: last modified (default), created date, title A–Z, title Z–A.

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

- 2.1 [ ] **Checklists** — insert a `☐ / ☑` checklist item; toggling the checkbox updates the HTML and re-saves.
- 2.2 [ ] **Bulleted and numbered lists** — `<ul>` / `<ol>` support in the HTML ↔ TextBuffer converter and a toolbar button for each.
- 2.3 [ ] **Horizontal rule** — insert `<hr>` as a visual separator.
- 2.4 [ ] **Inline code and code blocks** — already have monospace span; add a full fenced-code-block style (background, no wrap).
- 2.5 [ ] **Hyperlinks** — insert/edit clickable `<a href>` links; Ctrl+click to open in browser.
- 2.6 [ ] **Image attachments** — embed images inline (stored as Base64 in HTML or as separate files referenced from the note body).
- 2.7 [ ] **Table support** — insert a simple `<table>` with add/remove row/column actions.
- 2.8 [ ] **Word and character count** — displayed in the editor footer.
- 2.9 [ ] **Spell check** — integrate GtkSourceView or `enchant` for on-the-fly spell checking.
- 2.10 [ ] **Find & replace within note** — Ctrl+H opens a find-and-replace bar inside the editor.
- 2.11 [ ] **Undo/redo beyond default** — ensure undo history survives autosave cycles.
- 2.12 [ ] **Focus / distraction-free mode** — hide sidebars to full-screen the editor with a keyboard shortcut (e.g. F11).

---

## 3. Sync

- 3.1 [ ] **Sync status indicator** — show a spinner/icon in the header that distinguishes: idle, syncing, error, offline.
- 3.2 [ ] **Manual sync button** — force a full pull+push from the toolbar (useful when offline for a while).
- 3.3 [ ] **Selective folder sync** — let the user choose which IMAP folders are synced vs. local-only.
- 3.4 [ ] **Conflict UI** — when a conflict is detected, show a diff and let the user pick which version to keep rather than silently preferring local.
- 3.5 [ ] **Desktop notification on sync errors** — use `GLib.Notification` to surface sync failures without the user having to open the error log.

---

## 4. Search & Discovery

- 4.1 [ ] **Advanced search filters** — filter by folder, date range, tag, or has-attachments from the search bar.
- 4.2 [ ] **Search result highlighting** — highlight matched terms in both the note list snippet and the editor body.
- 4.3 [ ] **Recents / jump-to** — Ctrl+P command palette that fuzzy-searches note titles for quick navigation.
- 4.4 [ ] **Note tags / labels** — add arbitrary tags to notes (stored locally); filter by tag in the sidebar.

---

## 5. Preferences & Personalisation

- 5.1 [ ] **Preferences dialog** — `Adw.PreferencesWindow` covering: default font, font size, autosave interval, sync interval, keyboard shortcuts reference.
- 5.2 [ ] **Font size control** — zoom in/out in the editor (Ctrl+= / Ctrl+-).
- 5.3 [ ] **Colour accent for folders** — let the user assign a colour dot to folders for visual scanning.
- 5.4 [ ] **Note icon / emoji** — allow setting a per-note emoji icon shown in the note list.
- 5.5 [ ] **Dark/light/auto theme** — explicit toggle in preferences (currently follows system; make it overridable).
- 5.6 [ ] **Editor line spacing and width** — configurable max content width for comfortable long-form writing.

---

## 6. Backup & Safety

- 6.1 [ ] **Automatic local backups** — periodically snapshot the SQLite database to `~/.local/share/jotter/backups/` (rolling, keep last 7).
- 6.2 [ ] **Export all notes on demand** — one-click "backup everything" to a zip of Markdown files.
- 6.3 [ ] **Trash / recently deleted** — soft-deleted notes go to a "Trash" pseudo-folder and are permanently removed after 30 days (matching Apple Notes behaviour).

---

## 7. Onboarding & Setup

- 7.1 [ ] **Welcome screen** — shown on first launch before any account is connected; explains what Jotter is and how to connect an account.
- 7.2 [ ] **Setup wizard** — step-by-step Adw dialog for connecting a Google account (GOA) or entering IMAP credentials manually.
- 7.3 [ ] **Empty-state illustrations** — meaningful placeholder content when a folder has no notes or search returns no results.

---

## 8. Accessibility & Polish

- 8.1 [ ] **Full keyboard navigation** — every action reachable without a mouse; focus ring visible on all interactive elements.
- 8.2 [ ] **Screen-reader labels** — `accessible-label` / `accessible-description` on all custom widgets.
- 8.3 [ ] **High-contrast support** — test under GNOME's high-contrast theme and fix any hard-coded colours.
- 8.4 [ ] **RTL language support** — ensure the three-column layout mirrors correctly for Arabic, Hebrew, etc.
- 8.5 [ ] **Responsive layout review** — test and polish the single-column (mobile/small-window) layout.

---

## 9. Packaging & Distribution

- 9.1 [ ] **Flatpak manifest** — `com.example.Jotter.json` with the correct GNOME runtime, permissions (`--share=network`, `--talk-name=org.gnome.OnlineAccounts`), and finish-args.
- 9.2 [ ] **Flathub submission** — meet Flathub quality requirements (metadata, screenshots, appstream XML).
- 9.3 [ ] **`.desktop` file** — proper `StartupNotify=true`, `Categories`, and `Keywords`.
- 9.4 [ ] **AppStream / metainfo XML** — release notes, screenshots, developer name, OARS rating.
- 9.5 [ ] **GNOME Shell search provider** — register Jotter so notes are surfaced directly in the Activities search.
- 9.6 [ ] **Autostart / background sync** — optional D-Bus activation so the sync daemon can run without the window open.

---

## 10. Code Quality & Testing

- 10.1 [ ] **Automated unit tests** — pytest suite for `models.py` (DB CRUD), `utils.py` (HTML↔TextBuffer round-trips), and `auth.py` (token refresh logic).
- 10.2 [ ] **Integration tests** — mock IMAP server (e.g. `IMAPStub`) to test the full sync loop without a live Gmail account.
- 10.3 [ ] **CI pipeline** — GitHub Actions workflow: lint (`ruff`), type-check (`mypy`), run tests, build Flatpak on every PR.
- 10.4 [ ] **Type annotations** — add `mypy`-clean type hints throughout; especially `window.py` and `imap_backend.py`.
- 10.5 [ ] **Refactor `window.py`** — at 1160+ lines it handles too much; split into sub-controllers for the sidebar, note list, and header bar.
- 10.6 [ ] **Logging cleanup** — replace bare `print()` calls with structured `logging` at appropriate levels; make log verbosity configurable.
