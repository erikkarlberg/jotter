"""HTML <-> GtkTextBuffer serialization utilities."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk


# ---------------------------------------------------------------------------
# Tag name constants (used as Gtk.TextTag names)
# ---------------------------------------------------------------------------

TAG_BOLD = "bold"
TAG_ITALIC = "italic"
TAG_UNDERLINE = "underline"
TAG_STRIKETHROUGH = "strikethrough"
TAG_HEADING1 = "heading1"
TAG_HEADING2 = "heading2"
TAG_HEADING3 = "heading3"
TAG_MONOSPACE = "monospace"

# Maps HTML tag -> TextTag name (and vice-versa)
_HTML_TO_TAG: dict[str, str] = {
    "b": TAG_BOLD,
    "strong": TAG_BOLD,
    "i": TAG_ITALIC,
    "em": TAG_ITALIC,
    "u": TAG_UNDERLINE,
    "s": TAG_STRIKETHROUGH,
    "del": TAG_STRIKETHROUGH,
    "strike": TAG_STRIKETHROUGH,
    "h1": TAG_HEADING1,
    "h2": TAG_HEADING2,
    "h3": TAG_HEADING3,
    "code": TAG_MONOSPACE,
    "tt": TAG_MONOSPACE,
}

_TAG_TO_HTML: dict[str, str] = {
    TAG_BOLD: "b",
    TAG_ITALIC: "i",
    TAG_UNDERLINE: "u",
    TAG_STRIKETHROUGH: "s",
    TAG_HEADING1: "h1",
    TAG_HEADING2: "h2",
    TAG_HEADING3: "h3",
    TAG_MONOSPACE: "code",
}

# Block-level tags that introduce line breaks
_BLOCK_TAGS = {"div", "p", "h1", "h2", "h3", "li", "br"}


def setup_tags(tag_table: "Gtk.TextTagTable") -> None:
    """Create all formatting tags in the given TextTagTable."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Pango

    def _make(name: str, **props):
        tag = Gtk.TextTag.new(name)
        for k, v in props.items():
            tag.set_property(k, v)
        tag_table.add(tag)

    _make(TAG_BOLD, weight=Pango.Weight.BOLD)
    _make(TAG_ITALIC, style=Pango.Style.ITALIC)
    _make(TAG_UNDERLINE, underline=Pango.Underline.SINGLE)
    _make(TAG_STRIKETHROUGH, strikethrough=True)
    _make(TAG_HEADING1, weight=Pango.Weight.BOLD, scale=1.8)
    _make(TAG_HEADING2, weight=Pango.Weight.BOLD, scale=1.4)
    _make(TAG_HEADING3, weight=Pango.Weight.BOLD, scale=1.2)
    _make(TAG_MONOSPACE, family="Monospace")


# ---------------------------------------------------------------------------
# HTML -> TextBuffer
# ---------------------------------------------------------------------------

class _HtmlParser(HTMLParser):
    def __init__(self, buffer):
        super().__init__(convert_charrefs=True)
        self._buf = buffer
        self._active_tags: list[str] = []   # stack of TextTag names
        self._list_stack: list[str] = []    # 'ul' or 'ol'
        self._list_counters: list[int] = []
        self._pending_newline = False

    def _insert(self, text: str) -> None:
        if not text:
            return
        end_iter = self._buf.get_end_iter()
        self._buf.insert(end_iter, text)
        # Apply all currently active tags to the inserted text
        if self._active_tags:
            new_end = self._buf.get_end_iter()
            start = new_end.copy()
            start.backward_chars(len(text))
            for tag_name in self._active_tags:
                tag = self._buf.get_tag_table().lookup(tag_name)
                if tag:
                    self._buf.apply_tag(tag, start, new_end)

    def _newline(self) -> None:
        self._insert("\n")

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "br":
            self._newline()
            return
        if tag in ("ul", "ol"):
            self._list_stack.append(tag)
            self._list_counters.append(0)
            return
        if tag == "li":
            self._newline()
            if self._list_stack:
                list_type = self._list_stack[-1]
                if list_type == "ol":
                    self._list_counters[-1] += 1
                    self._insert(f"{self._list_counters[-1]}. ")
                else:
                    self._insert("• ")
            return
        if tag in _BLOCK_TAGS:
            # Ensure we're on a new line before block content
            end = self._buf.get_end_iter()
            if end.get_offset() > 0:
                prev = end.copy()
                prev.backward_char()
                ch = self._buf.get_text(prev, end, False)
                if ch != "\n":
                    self._newline()
        text_tag = _HTML_TO_TAG.get(tag)
        if text_tag:
            self._active_tags.append(text_tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
                self._list_counters.pop()
            return
        if tag in _BLOCK_TAGS and tag != "br":
            self._newline()
        text_tag = _HTML_TO_TAG.get(tag)
        if text_tag and text_tag in self._active_tags:
            # Pop only the most recent occurrence
            for i in range(len(self._active_tags) - 1, -1, -1):
                if self._active_tags[i] == text_tag:
                    self._active_tags.pop(i)
                    break

    def handle_data(self, data: str) -> None:
        self._insert(data)


def html_to_buffer(html: str, buffer) -> None:
    """Parse *html* and populate *buffer* with formatted text."""
    buffer.set_text("")
    if not html:
        return
    parser = _HtmlParser(buffer)
    parser.feed(html)
    # Remove leading/trailing blank lines
    start = buffer.get_start_iter()
    end = buffer.get_end_iter()
    text = buffer.get_text(start, end, False)
    stripped = text.strip("\n")
    if stripped != text:
        buffer.set_text("")
        # Re-parse with stripped version — simpler than fixing iterators
        parser2 = _HtmlParser(buffer)
        parser2.feed(html)
        # Just strip trailing newlines
        end2 = buffer.get_end_iter()
        while end2.get_offset() > 0:
            prev = end2.copy()
            prev.backward_char()
            ch = buffer.get_text(prev, end2, False)
            if ch == "\n":
                buffer.delete(prev, end2)
                end2 = buffer.get_end_iter()
            else:
                break


# ---------------------------------------------------------------------------
# TextBuffer -> HTML
# ---------------------------------------------------------------------------

def buffer_to_html(buffer) -> str:
    """Serialize *buffer* contents to HTML."""
    start = buffer.get_start_iter()
    end = buffer.get_end_iter()
    if start.equal(end):
        return ""

    result: list[str] = []
    it = start.copy()
    open_tags: list[str] = []

    while not it.equal(end):
        # Determine which TextTag names are active at this iterator
        active = {
            tag.get_property("name")
            for tag in it.get_tags()
            if tag.get_property("name") in _TAG_TO_HTML
        }

        # Close tags that are no longer active (in reverse open order)
        to_close = [t for t in reversed(open_tags) if t not in active]
        to_reopen = []
        for tag_name in to_close:
            result.append(f"</{_TAG_TO_HTML[tag_name]}>")
            open_tags.remove(tag_name)
            # Any tags still open that came after this one need reopening
            # (handled by the loop below)

        # Open new tags
        for tag_name in active:
            if tag_name not in open_tags:
                result.append(f"<{_TAG_TO_HTML[tag_name]}>")
                open_tags.append(tag_name)

        # Get the character
        next_it = it.copy()
        next_it.forward_char()
        ch = buffer.get_text(it, next_it, False)

        if ch == "\n":
            result.append("<br>")
        else:
            result.append(_escape(ch))

        it = next_it

    # Close any remaining open tags
    for tag_name in reversed(open_tags):
        result.append(f"</{_TAG_TO_HTML[tag_name]}>")

    return "".join(result)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Plain-text extraction
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(html: str) -> str:
    """Return plain text with HTML tags removed."""
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text
