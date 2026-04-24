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
        self._find_tag: Optional[Gtk.TextTag] = None

        # --- TextBuffer + tags ---
        self._tag_table = Gtk.TextTagTable()
        setup_tags(self._tag_table)
        self._buffer = Gtk.TextBuffer(tag_table=self._tag_table)
        self._buffer.set_enable_undo(True)

        # Search-highlight tag
        self._find_tag = Gtk.TextTag.new("find-highlight")
        self._find_tag.set_property("background", "#ffee00")
        self._find_tag.set_property("foreground", "#000000")
        self._tag_table.add(self._find_tag)

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

        # --- Find & Replace bar ---
        self._find_bar = self._build_find_bar()

        self.append(toolbar)
        self.append(self._find_bar)
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

        ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control>h"),
            Gtk.CallbackAction.new(lambda *_: self._toggle_find_bar() or True),
        ))
        ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control>z"),
            Gtk.CallbackAction.new(lambda *_: self._buffer.undo() or True),
        ))
        ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control><Shift>z"),
            Gtk.CallbackAction.new(lambda *_: self._buffer.redo() or True),
        ))
        ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("Escape"),
            Gtk.CallbackAction.new(lambda *_: self._hide_find_bar() or True),
        ))
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
        # Wrap the load in begin/end_irreversible_action so switching notes
        # doesn't pollute the undo stack with the previous note's content.
        self._buffer.begin_irreversible_action()
        try:
            if note.body_html:
                html_to_buffer(note.body_html, self._buffer)
            else:
                self._buffer.set_text(note.body_text or "")
        finally:
            self._buffer.end_irreversible_action()
            self._updating_buffer = False
        self._view.grab_focus()

    def clear(self) -> None:
        """Clear editor and unset current note."""
        logger.debug("clear()")
        self._cancel_autosave()
        self._note = None
        self._updating_buffer = True
        self._buffer.begin_irreversible_action()
        self._buffer.set_text("")
        self._buffer.end_irreversible_action()
        self._updating_buffer = False
        # Keep the view sensitive so it can still be scrolled; editable=False is enough
        self._view.set_editable(False)
        self._view.set_cursor_visible(False)

    def focus(self) -> None:
        """Move keyboard focus into the text view."""
        self._view.grab_focus()

    @property
    def has_content(self) -> bool:
        """True if the buffer contains any non-whitespace text."""
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        return bool(self._buffer.get_text(start, end, False).strip())

    def set_editable(self, editable: bool) -> None:
        self._view.set_editable(editable)
        self._view.set_cursor_visible(editable)
        self._view.set_sensitive(editable)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> Gtk.Box:
        bar = Gtk.CenterBox()
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)
        bar.add_css_class("toolbar")

        self._fmt_buttons: dict[str, Gtk.ToggleButton] = {}

        # Linked inline formatting group (centered)
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

        bar.set_center_widget(inline_box)

        return bar

    # ------------------------------------------------------------------
    # Find & Replace bar
    # ------------------------------------------------------------------

    def _build_find_bar(self) -> Gtk.Revealer:
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        revealer.set_reveal_child(False)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)

        self._find_entry = Gtk.SearchEntry()
        self._find_entry.set_placeholder_text("Find…")
        self._find_entry.set_hexpand(True)
        self._find_entry.connect("search-changed", self._on_find_changed)
        self._find_entry.connect("activate", self._find_next)

        self._replace_entry = Gtk.Entry()
        self._replace_entry.set_placeholder_text("Replace…")
        self._replace_entry.set_hexpand(True)

        find_next_btn = Gtk.Button(label="Next")
        find_next_btn.connect("clicked", self._find_next)

        replace_btn = Gtk.Button(label="Replace")
        replace_btn.connect("clicked", self._replace_current)

        replace_all_btn = Gtk.Button(label="All")
        replace_all_btn.connect("clicked", self._replace_all)

        close_btn = Gtk.Button()
        close_btn.set_icon_name("window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.connect("clicked", lambda _: self._hide_find_bar())

        bar.append(self._find_entry)
        bar.append(find_next_btn)
        bar.append(self._replace_entry)
        bar.append(replace_btn)
        bar.append(replace_all_btn)
        bar.append(close_btn)

        revealer.set_child(bar)
        self._find_revealer = revealer
        return revealer

    def _toggle_find_bar(self) -> None:
        visible = self._find_revealer.get_reveal_child()
        if visible:
            self._hide_find_bar()
        else:
            self._find_revealer.set_reveal_child(True)
            self._find_entry.grab_focus()

    def _hide_find_bar(self) -> None:
        self._find_revealer.set_reveal_child(False)
        self._clear_find_highlights()
        self._view.grab_focus()

    def _on_find_changed(self, entry: Gtk.SearchEntry) -> None:
        self._clear_find_highlights()
        query = entry.get_text()
        if not query:
            return
        start = self._buffer.get_start_iter()
        while True:
            found = start.forward_search(query, 0, None)
            if not found:
                break
            match_start, match_end = found
            self._buffer.apply_tag(self._find_tag, match_start, match_end)
            start = match_end

    def _find_next(self, *_) -> None:
        query = self._find_entry.get_text()
        if not query:
            return
        cursor = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        found = cursor.forward_search(query, 0, None)
        if not found:
            # Wrap around
            found = self._buffer.get_start_iter().forward_search(query, 0, None)
        if found:
            match_start, match_end = found
            self._buffer.select_range(match_start, match_end)
            self._view.scroll_to_mark(self._buffer.get_insert(), 0.1, False, 0, 0)

    def _replace_current(self, *_) -> None:
        query = self._find_entry.get_text()
        replacement = self._replace_entry.get_text()
        if not query:
            return
        bounds = self._buffer.get_selection_bounds()
        if bounds:
            sel_start, sel_end = bounds
            selected = self._buffer.get_text(sel_start, sel_end, False)
            if selected == query:
                self._buffer.delete(sel_start, sel_end)
                self._buffer.insert(sel_start, replacement)
        self._find_next()

    def _replace_all(self, *_) -> None:
        query = self._find_entry.get_text()
        replacement = self._replace_entry.get_text()
        if not query:
            return
        self._clear_find_highlights()
        start = self._buffer.get_start_iter()
        count = 0
        while True:
            found = start.forward_search(query, 0, None)
            if not found:
                break
            match_start, match_end = found
            self._buffer.delete(match_start, match_end)
            self._buffer.insert(match_start, replacement)
            start = self._buffer.get_iter_at_offset(match_start.get_offset() + len(replacement))
            count += 1
        if count:
            self._on_find_changed(self._find_entry)

    def _clear_find_highlights(self) -> None:
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        self._buffer.remove_tag(self._find_tag, start, end)

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
        prev = self._updating_buffer
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
            self._updating_buffer = prev

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
