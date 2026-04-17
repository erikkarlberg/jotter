"""Rich text editor widget."""

from __future__ import annotations

import logging
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk, Pango

from .models import Note
from .utils import (
    TAG_BOLD, TAG_HEADING1, TAG_HEADING2, TAG_HEADING3,
    TAG_ITALIC, TAG_MONOSPACE, TAG_STRIKETHROUGH, TAG_UNDERLINE,
    buffer_to_html, html_to_buffer, setup_tags, strip_html,
)

logger = logging.getLogger(__name__)

AUTOSAVE_DELAY_MS = 500


class EditorWidget(Gtk.Box):
    """
    A self-contained rich-text editor with a formatting toolbar.

    Emits ``note-changed(Note)`` after the autosave debounce fires.
    """

    __gtype_name__ = "EditorWidget"

    # Signal: note-changed(Note)
    __gsignals__ = {
        "note-changed": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._note: Optional[Note] = None
        self._autosave_id: Optional[int] = None
        self._updating_buffer = False

        # --- TextBuffer + tags ---
        self._tag_table = Gtk.TextTagTable()
        setup_tags(self._tag_table)
        self._buffer = Gtk.TextBuffer(tag_table=self._tag_table)

        # --- TextView ---
        self._view = Gtk.TextView(buffer=self._buffer)
        self._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._view.set_left_margin(16)
        self._view.set_right_margin(16)
        self._view.set_top_margin(12)
        self._view.set_bottom_margin(12)
        self._view.add_css_class("editor-view")

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self._view)

        # --- Formatting toolbar ---
        toolbar = self._build_toolbar()

        self.append(toolbar)
        self.append(scroll)

        # --- Signals ---
        self._buffer.connect("changed", self._on_buffer_changed)
        self._buffer.connect("notify::cursor-position", self._on_cursor_moved)

        # Keyboard shortcuts
        ctrl = Gtk.ShortcutController()
        ctrl.set_scope(Gtk.ShortcutScope.LOCAL)
        for key, tag in [
            ("b", TAG_BOLD),
            ("i", TAG_ITALIC),
            ("u", TAG_UNDERLINE),
        ]:
            shortcut = Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(f"<Control>{key}"),
                Gtk.CallbackAction.new(lambda *_, t=tag: self._toggle_tag(t)),
            )
            ctrl.add_shortcut(shortcut)
        self._view.add_controller(ctrl)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_note(self, note: Note) -> None:
        """Populate the editor with *note*'s content."""
        logger.debug("load_note id=%s", note.id)
        self._cancel_autosave()
        self._note = note
        self._updating_buffer = True
        try:
            if note.body_html:
                html_to_buffer(note.body_html, self._buffer)
            else:
                self._buffer.set_text(note.body_text or "")
        finally:
            self._updating_buffer = False
        self._view.grab_focus()

    def clear(self) -> None:
        """Clear editor and unset current note."""
        logger.debug("clear()")
        self._cancel_autosave()
        self._note = None
        self._updating_buffer = True
        self._buffer.set_text("")
        self._updating_buffer = False
        # Keep the view sensitive so it can still be scrolled; editable=False is enough
        self._view.set_editable(False)
        self._view.set_cursor_visible(False)

    def set_editable(self, editable: bool) -> None:
        self._view.set_editable(editable)
        self._view.set_cursor_visible(editable)
        self._view.set_sensitive(editable)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)
        bar.add_css_class("toolbar")

        self._fmt_buttons: dict[str, Gtk.ToggleButton] = {}

        # Linked inline formatting group
        inline_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        inline_box.add_css_class("linked")

        for tag_name, label, tooltip in [
            (TAG_BOLD, "<b>B</b>", "Bold (Ctrl+B)"),
            (TAG_ITALIC, "<i>I</i>", "Italic (Ctrl+I)"),
            (TAG_UNDERLINE, "<u>U</u>", "Underline (Ctrl+U)"),
            (TAG_STRIKETHROUGH, "<s>S</s>", "Strikethrough"),
        ]:
            btn = Gtk.ToggleButton()
            lbl = Gtk.Label()
            lbl.set_markup(label)
            btn.set_child(lbl)
            btn.set_tooltip_text(tooltip)
            btn.connect("toggled", self._on_format_toggle, tag_name)
            inline_box.append(btn)
            self._fmt_buttons[tag_name] = btn

        bar.append(inline_box)

        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_start(4)
        sep.set_margin_end(4)
        bar.append(sep)

        # Heading group
        heading_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        heading_box.add_css_class("linked")

        for tag_name, label, tooltip in [
            (TAG_HEADING1, "H1", "Heading 1"),
            (TAG_HEADING2, "H2", "Heading 2"),
            (TAG_HEADING3, "H3", "Heading 3"),
        ]:
            btn = Gtk.ToggleButton(label=label)
            btn.set_tooltip_text(tooltip)
            btn.connect("toggled", self._on_format_toggle, tag_name)
            heading_box.append(btn)
            self._fmt_buttons[tag_name] = btn

        bar.append(heading_box)

        # Separator
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep2.set_margin_start(4)
        sep2.set_margin_end(4)
        bar.append(sep2)

        # Monospace
        mono_btn = Gtk.ToggleButton(label="</>")
        mono_btn.set_tooltip_text("Monospace / Code")
        mono_btn.connect("toggled", self._on_format_toggle, TAG_MONOSPACE)
        bar.append(mono_btn)
        self._fmt_buttons[TAG_MONOSPACE] = mono_btn

        return bar

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _toggle_tag(self, tag_name: str) -> None:
        bounds = self._buffer.get_selection_bounds()
        if not bounds:
            return
        start, end = bounds
        tag = self._tag_table.lookup(tag_name)
        if tag is None:
            return
        if start.has_tag(tag):
            self._buffer.remove_tag(tag, start, end)
        else:
            self._buffer.apply_tag(tag, start, end)

    def _on_format_toggle(self, btn: Gtk.ToggleButton, tag_name: str) -> None:
        """Called when the user clicks a formatting button."""
        if self._updating_buffer:
            return
        self._toggle_tag(tag_name)

    def _on_cursor_moved(self, *_) -> None:
        """Update toolbar button state to reflect tags at cursor."""
        self._updating_buffer = True
        try:
            it = self._buffer.get_iter_at_mark(self._buffer.get_insert())
            for tag_name, btn in self._fmt_buttons.items():
                tag = self._tag_table.lookup(tag_name)
                if tag:
                    # Check tag at the character just before cursor (more intuitive)
                    check = it.copy()
                    if not check.is_start():
                        check.backward_char()
                    active = check.has_tag(tag)
                    btn.set_active(active)
        finally:
            self._updating_buffer = False

    # ------------------------------------------------------------------
    # Auto-save
    # ------------------------------------------------------------------

    def _on_buffer_changed(self, *_) -> None:
        if self._updating_buffer or self._note is None:
            return
        self._cancel_autosave()
        self._autosave_id = GLib.timeout_add(AUTOSAVE_DELAY_MS, self._do_autosave)

    def _cancel_autosave(self) -> None:
        if self._autosave_id is not None:
            GLib.source_remove(self._autosave_id)
            self._autosave_id = None

    def _do_autosave(self) -> bool:
        self._autosave_id = None
        if self._note is None:
            return GLib.SOURCE_REMOVE

        html = buffer_to_html(self._buffer)
        plain = strip_html(html)

        # Derive subject from first non-empty line
        first_line = plain.split("\n", 1)[0].strip()[:100]

        from datetime import datetime, timezone  # noqa: PLC0415
        self._note.body_html = html
        self._note.body_text = plain
        self._note.subject = first_line
        self._note.modified_at = datetime.now(timezone.utc).isoformat()

        self.emit("note-changed", self._note)
        return GLib.SOURCE_REMOVE
