"""Import and export helpers for Jotter notes."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from .utils import html_to_markdown, markdown_to_html, strip_html

if TYPE_CHECKING:
    from .models import Database, Note

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File chooser helpers
# ---------------------------------------------------------------------------

def _save_dialog(parent, title: str, filename: str, mime: str, suffix: str,
                 on_save) -> None:
    """Show a native save-file dialog; call on_save(path: Path) on confirm."""
    dialog = Gtk.FileDialog()
    dialog.set_title(title)
    dialog.set_initial_name(filename)

    filter_ = Gtk.FileFilter()
    filter_.set_name(f"{suffix.upper()} files")
    filter_.add_mime_type(mime)
    filters = Gio_ListStore(filter_)
    dialog.set_filters(filters)
    dialog.set_default_filter(filter_)

    def _cb(dlg, result):
        try:
            gfile = dlg.save_finish(result)
            path = Path(gfile.get_path())
            on_save(path)
        except Exception as exc:
            if "dismissed" not in str(exc).lower():
                logger.warning("Save dialog error: %s", exc)

    dialog.save(parent, None, _cb)


def _open_dialog(parent, title: str, mime: str, on_open) -> None:
    """Show a native open-file dialog; call on_open(path: Path) on confirm."""
    dialog = Gtk.FileDialog()
    dialog.set_title(title)

    filter_ = Gtk.FileFilter()
    filter_.add_mime_type(mime)
    filter_.add_mime_type("text/plain")

    def _cb(dlg, result):
        try:
            gfile = dlg.open_finish(result)
            path = Path(gfile.get_path())
            on_open(path)
        except Exception as exc:
            if "dismissed" not in str(exc).lower():
                logger.warning("Open dialog error: %s", exc)

    dialog.open(parent, None, _cb)


def Gio_ListStore(filter_):
    from gi.repository import Gio
    ls = Gio.ListStore.new(Gtk.FileFilter)
    ls.append(filter_)
    return ls


# ---------------------------------------------------------------------------
# Export: individual note
# ---------------------------------------------------------------------------

def export_note_markdown(note: "Note", parent: Gtk.Window) -> None:
    safe_name = _safe_filename(note.subject or "note") + ".md"

    def _save(path: Path) -> None:
        md = html_to_markdown(note.body_html) if note.body_html else (note.body_text or "")
        path.write_text(md, encoding="utf-8")
        _toast(parent, f"Exported to {path.name}")

    _save_dialog(parent, "Export as Markdown", safe_name, "text/markdown", "md", _save)


def export_note_text(note: "Note", parent: Gtk.Window) -> None:
    safe_name = _safe_filename(note.subject or "note") + ".txt"

    def _save(path: Path) -> None:
        text = strip_html(note.body_html) if note.body_html else (note.body_text or "")
        path.write_text(text, encoding="utf-8")
        _toast(parent, f"Exported to {path.name}")

    _save_dialog(parent, "Export as Plain Text", safe_name, "text/plain", "txt", _save)


def export_note_html(note: "Note", parent: Gtk.Window) -> None:
    safe_name = _safe_filename(note.subject or "note") + ".html"

    def _save(path: Path) -> None:
        html = note.body_html or ""
        if not html.lower().strip().startswith("<html"):
            title = note.subject or "Note"
            html = (
                f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>{title}</title></head><body>{html}</body></html>"
            )
        path.write_text(html, encoding="utf-8")
        _toast(parent, f"Exported to {path.name}")

    _save_dialog(parent, "Export as HTML", safe_name, "text/html", "html", _save)


def export_note_pdf(note: "Note", parent: Gtk.Window) -> None:
    """Print note to PDF via GTK's print API."""
    safe_name = _safe_filename(note.subject or "note") + ".pdf"

    op = Gtk.PrintOperation()
    op.set_job_name(note.subject or "Note")
    op.set_n_pages(1)

    text = strip_html(note.body_html) if note.body_html else (note.body_text or "")

    def _draw_page(operation, context, page_nr):
        cr = context.get_cairo_context()
        width = context.get_width()
        cr.set_source_rgb(0, 0, 0)
        import gi
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Pango, PangoCairo
        layout = PangoCairo.create_layout(cr)
        layout.set_width(int(width * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(text, -1)
        PangoCairo.show_layout(cr, layout)

    op.connect("draw-page", _draw_page)

    settings = Gtk.PrintSettings()
    settings.set(Gtk.PRINT_SETTINGS_OUTPUT_FILE_FORMAT, "pdf")
    settings.set(Gtk.PRINT_SETTINGS_OUTPUT_URI, f"file://{GLib.get_home_dir()}/{safe_name}")
    op.set_print_settings(settings)

    try:
        op.run(Gtk.PrintOperationAction.PRINT_DIALOG, parent)
    except Exception as exc:
        logger.warning("PDF export: %s", exc)


# ---------------------------------------------------------------------------
# Import: individual note
# ---------------------------------------------------------------------------

def import_note_markdown(db: "Database", folder_id: int, parent: Gtk.Window,
                         on_done) -> None:
    def _open(path: Path) -> None:
        md = path.read_text(encoding="utf-8", errors="replace")
        body_html = markdown_to_html(md)
        body_text = strip_html(body_html)
        subject = path.stem[:100]
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        from .models import Note
        note = Note(folder_id=folder_id, subject=subject,
                    body_html=body_html, body_text=body_text,
                    created_at=now, modified_at=now)
        db.save_note(note)
        _toast(parent, f"Imported “{subject}”")
        on_done(note)

    _open_dialog(parent, "Import Markdown", "text/markdown", _open)


def import_note_text(db: "Database", folder_id: int, parent: Gtk.Window,
                     on_done) -> None:
    def _open(path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        body_html = "".join(
            f"<div>{_esc(line)}</div>" if line.strip() else "<div><br></div>"
            for line in lines
        )
        subject = (lines[0].strip() if lines else path.stem)[:100]
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        from .models import Note
        note = Note(folder_id=folder_id, subject=subject,
                    body_html=body_html, body_text=text,
                    created_at=now, modified_at=now)
        db.save_note(note)
        _toast(parent, f"Imported “{subject}”")
        on_done(note)

    _open_dialog(parent, "Import Text File", "text/plain", _open)


# ---------------------------------------------------------------------------
# Batch export
# ---------------------------------------------------------------------------

def export_folder_zip(db: "Database", folder_id: int, folder_name: str,
                      parent: Gtk.Window) -> None:
    """Export all notes in *folder_id* as a zip of Markdown files."""
    safe_folder = _safe_filename(folder_name or "notes")
    zip_name = f"{safe_folder}.zip"

    def _save(path: Path) -> None:
        notes = db.get_notes(folder_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            seen: dict[str, int] = {}
            for note in notes:
                base = _safe_filename(note.subject or "note")
                count = seen.get(base, 0)
                seen[base] = count + 1
                fname = f"{base}{'_' + str(count) if count else ''}.md"
                md = html_to_markdown(note.body_html) if note.body_html else (note.body_text or "")
                zf.writestr(fname, md.encode("utf-8"))
        path.write_bytes(buf.getvalue())
        _toast(parent, f"Exported {len(notes)} note(s) to {path.name}")

    _save_dialog(parent, "Export Folder as Zip", zip_name, "application/zip", "zip", _save)


def export_all_zip(db: "Database", parent: Gtk.Window) -> None:
    """Export every note across all folders as a zip organised by folder."""
    def _save(path: Path) -> None:
        folders = db.get_folders()
        folder_map = {f.id: f for f in folders}
        all_notes = db.get_all_notes()
        buf = io.BytesIO()
        total = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            seen: dict[str, int] = {}
            for note in all_notes:
                folder_name = folder_map.get(note.folder_id, None)
                folder_part = _safe_filename(folder_name.name if folder_name else "Notes")
                base = _safe_filename(note.subject or "note")
                key = f"{folder_part}/{base}"
                count = seen.get(key, 0)
                seen[key] = count + 1
                fname = f"{folder_part}/{base}{'_' + str(count) if count else ''}.md"
                md = html_to_markdown(note.body_html) if note.body_html else (note.body_text or "")
                zf.writestr(fname, md.encode("utf-8"))
                total += 1
        path.write_bytes(buf.getvalue())
        _toast(parent, f"Exported {total} note(s) to {path.name}")

    _save_dialog(parent, "Export All Notes", "jotter_backup.zip", "application/zip", "zip", _save)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Strip characters that are unsafe in file names."""
    import re
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name.strip(". ")[:80] or "note"


def _esc(text: str) -> str:
    import html
    return html.escape(text, quote=False)


def _toast(parent: Gtk.Window, message: str) -> None:
    overlay = _find_toast_overlay(parent)
    if overlay:
        toast = Adw.Toast(title=message)
        toast.set_timeout(3)
        overlay.add_toast(toast)


def _find_toast_overlay(widget) -> "Adw.ToastOverlay | None":
    """Walk up the widget tree looking for an Adw.ToastOverlay."""
    w = widget
    while w:
        if isinstance(w, Adw.ToastOverlay):
            return w
        w = w.get_parent()
    return None
