# Jotter — Roadmap to v1.0

A prioritised task list for taking Jotter from its current state to a polished, finished product.

---

## Status key
- [ ] Not started
- [~] In progress / partial
- [x] Done

---

## 1. Core Features

### Organisation
- [~] **Drag-and-drop notes between folders** — drag source on `NoteRow` and drop target on `FolderRow` exist; validate that the IMAP move is triggered and reflected correctly after a drop.
- [ ] **Subfolder support** — display nested IMAP folders (e.g. `Notes/Work/Projects`) as an indented tree in the sidebar, create/rename/delete subfolders, and move notes into them.
- [ ] **Note pinning / favourites** — pin a note to the top of any folder list; stored as a local flag in SQLite (no IMAP side-effect needed).
- [ ] **Note reordering** — manual drag-to-reorder within a folder list (local order stored in DB, not synced to IMAP).
- [ ] **Sort options** — sort note list by: last modified (default), created date, title A–Z, title Z–A.

### Import & Export
- [ ] **Export note as Markdown** — convert the HTML body to Markdown and write to a `.md` file via a native save dialog.
- [ ] **Export note as plain text** — strip formatting, write `.txt`.
- [ ] **Export note as HTML** — save the raw `body_html` as a standalone `.html` file.
- [ ] **Export note as PDF** — render the note through GTK's print-to-PDF path.
- [ ] **Import from Markdown** — parse a `.md` file, convert to HTML, create a new note in the selected folder.
- [ ] **Import from plain text** — create a new note from a `.txt` file.
- [ ] **Import from Apple Notes export (.enex / HTML zip)** — bulk-import an Apple Notes backup archive.
- [ ] **Batch export folder** — export all notes in a folder as a zip of Markdown files.

---

## 2. Editor Improvements

- [ ] **Checklists** — insert a `☐ / ☑` checklist item; toggling the checkbox updates the HTML and re-saves.
- [ ] **Bulleted and numbered lists** — `<ul>` / `<ol>` support in the HTML ↔ TextBuffer converter and a toolbar button for each.
- [ ] **Horizontal rule** — insert `<hr>` as a visual separator.
- [ ] **Inline code and code blocks** — already have monospace span; add a full fenced-code-block style (background, no wrap).
- [ ] **Hyperlinks** — insert/edit clickable `<a href>` links; Ctrl+click to open in browser.
- [ ] **Image attachments** — embed images inline (stored as Base64 in HTML or as separate files referenced from the note body).
- [ ] **Table support** — insert a simple `<table>` with add/remove row/column actions.
- [ ] **Word and character count** — displayed in the editor footer.
- [ ] **Spell check** — integrate GtkSourceView or `enchant` for on-the-fly spell checking.
- [ ] **Find & replace within note** — Ctrl+H opens a find-and-replace bar inside the editor.
- [ ] **Undo/redo beyond default** — ensure undo history survives autosave cycles.
- [ ] **Focus / distraction-free mode** — hide sidebars to full-screen the editor with a keyboard shortcut (e.g. F11).

---

## 3. Sync

- [ ] **Sync status indicator** — show a spinner/icon in the header that distinguishes: idle, syncing, error, offline.
- [ ] **Manual sync button** — force a full pull+push from the toolbar (useful when offline for a while).
- [ ] **Selective folder sync** — let the user choose which IMAP folders are synced vs. local-only.
- [ ] **Conflict UI** — when a conflict is detected, show a diff and let the user pick which version to keep rather than silently preferring local.
- [ ] **Desktop notification on sync errors** — use `GLib.Notification` to surface sync failures without the user having to open the error log.

---

## 4. Search & Discovery

- [ ] **Advanced search filters** — filter by folder, date range, tag, or has-attachments from the search bar.
- [ ] **Search result highlighting** — highlight matched terms in both the note list snippet and the editor body.
- [ ] **Recents / jump-to** — Ctrl+P command palette that fuzzy-searches note titles for quick navigation.
- [ ] **Note tags / labels** — add arbitrary tags to notes (stored locally); filter by tag in the sidebar.

---

## 5. Preferences & Personalisation

- [ ] **Preferences dialog** — `Adw.PreferencesWindow` covering: default font, font size, autosave interval, sync interval, keyboard shortcuts reference.
- [ ] **Font size control** — zoom in/out in the editor (Ctrl+= / Ctrl+-).
- [ ] **Colour accent for folders** — let the user assign a colour dot to folders for visual scanning.
- [ ] **Note icon / emoji** — allow setting a per-note emoji icon shown in the note list.
- [ ] **Dark/light/auto theme** — explicit toggle in preferences (currently follows system; make it overridable).
- [ ] **Editor line spacing and width** — configurable max content width for comfortable long-form writing.

---

## 6. Backup & Safety

- [ ] **Automatic local backups** — periodically snapshot the SQLite database to `~/.local/share/jotter/backups/` (rolling, keep last 7).
- [ ] **Export all notes on demand** — one-click "backup everything" to a zip of Markdown files.
- [ ] **Trash / recently deleted** — soft-deleted notes go to a "Trash" pseudo-folder and are permanently removed after 30 days (matching Apple Notes behaviour).

---

## 7. Onboarding & Setup

- [ ] **Welcome screen** — shown on first launch before any account is connected; explains what Jotter is and how to connect an account.
- [ ] **Setup wizard** — step-by-step Adw dialog for connecting a Google account (GOA) or entering IMAP credentials manually.
- [ ] **Empty-state illustrations** — meaningful placeholder content when a folder has no notes or search returns no results.

---

## 8. Accessibility & Polish

- [ ] **Full keyboard navigation** — every action reachable without a mouse; focus ring visible on all interactive elements.
- [ ] **Screen-reader labels** — `accessible-label` / `accessible-description` on all custom widgets.
- [ ] **High-contrast support** — test under GNOME's high-contrast theme and fix any hard-coded colours.
- [ ] **RTL language support** — ensure the three-column layout mirrors correctly for Arabic, Hebrew, etc.
- [ ] **Responsive layout review** — test and polish the single-column (mobile/small-window) layout.

---

## 9. Packaging & Distribution

- [ ] **Flatpak manifest** — `com.example.Jotter.json` with the correct GNOME runtime, permissions (`--share=network`, `--talk-name=org.gnome.OnlineAccounts`), and finish-args.
- [ ] **Flathub submission** — meet Flathub quality requirements (metadata, screenshots, appstream XML).
- [ ] **`.desktop` file** — proper `StartupNotify=true`, `Categories`, and `Keywords`.
- [ ] **AppStream / metainfo XML** — release notes, screenshots, developer name, OARS rating.
- [ ] **GNOME Shell search provider** — register Jotter so notes are surfaced directly in the Activities search.
- [ ] **Autostart / background sync** — optional D-Bus activation so the sync daemon can run without the window open.

---

## 10. Code Quality & Testing

- [ ] **Automated unit tests** — pytest suite for `models.py` (DB CRUD), `utils.py` (HTML↔TextBuffer round-trips), and `auth.py` (token refresh logic).
- [ ] **Integration tests** — mock IMAP server (e.g. `IMAPStub`) to test the full sync loop without a live Gmail account.
- [ ] **CI pipeline** — GitHub Actions workflow: lint (`ruff`), type-check (`mypy`), run tests, build Flatpak on every PR.
- [ ] **Type annotations** — add `mypy`-clean type hints throughout; especially `window.py` and `imap_backend.py`.
- [ ] **Refactor `window.py`** — at 1160+ lines it handles too much; split into sub-controllers for the sidebar, note list, and header bar.
- [ ] **Logging cleanup** — replace bare `print()` calls with structured `logging` at appropriate levels; make log verbosity configurable.
