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
            # Only insert a newline if the buffer doesn't already end with one.
            # Apple Notes uses <div><br></div> for empty lines; without this guard
            # both the <br> and the </div> would each emit a newline, doubling
            # blank lines on every round-trip.
            end = self._buf.get_end_iter()
            if end.get_offset() > 0:
                prev = end.copy()
                prev.backward_char()
                if self._buf.get_text(prev, end, False) != "\n":
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
    """Serialize *buffer* to Apple Notes-compatible HTML (one <div> per line).

    Each newline in the buffer becomes a <div> boundary; empty lines become
    <div><br></div>, matching exactly what Apple Notes produces. This keeps
    the HTML format stable across Jotter ↔ Apple Notes round-trips.
    """
    start = buffer.get_start_iter()
    end = buffer.get_end_iter()
    if start.equal(end):
        return ""

    # Collect lines: each line is a list of (character, frozenset_of_active_tag_names)
    lines: list[list[tuple[str, frozenset]]] = [[]]
    it = start.copy()
    while not it.equal(end):
        active = frozenset(
            tag.get_property("name")
            for tag in it.get_tags()
            if tag.get_property("name") in _TAG_TO_HTML
        )
        next_it = it.copy()
        next_it.forward_char()
        ch = buffer.get_text(it, next_it, False)
        if ch == "\n":
            lines.append([])
        else:
            lines[-1].append((ch, active))
        it = next_it

    result: list[str] = []
    for line_chars in lines:
        if not line_chars:
            result.append("<div><br></div>")
            continue
        result.append("<div>")
        open_tags: list[str] = []
        for ch, active in line_chars:
            to_close = [t for t in reversed(open_tags) if t not in active]
            for tag_name in to_close:
                result.append(f"</{_TAG_TO_HTML[tag_name]}>")
                open_tags.remove(tag_name)
            for tag_name in active:
                if tag_name not in open_tags:
                    result.append(f"<{_TAG_TO_HTML[tag_name]}>")
                    open_tags.append(tag_name)
            result.append(_escape(ch))
        for tag_name in reversed(open_tags):
            result.append(f"</{_TAG_TO_HTML[tag_name]}>")
        result.append("</div>")

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
_BLOCK_RE = re.compile(r"<(?:br\s*/?|/(?:div|p|h[1-6]|li))[^>]*>", re.IGNORECASE)


def strip_html(html: str) -> str:
    """Return plain text with HTML tags removed, preserving line structure."""
    if not html:
        return ""
    # Convert block boundaries to newlines before stripping all tags
    text = _BLOCK_RE.sub("\n", html)
    text = _TAG_RE.sub("", text)
    # Collapse runs of 3+ newlines to at most 2, clean up spaces
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip()


# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------

def html_to_markdown(html: str) -> str:
    """Convert Jotter HTML (Apple Notes format) to Markdown."""
    if not html:
        return ""

    # Strip outer <html><head>...</head><body ...> wrappers if present
    html = re.sub(r"(?i)<html[^>]*>.*?<body[^>]*>", "", html, flags=re.DOTALL)
    html = re.sub(r"(?i)</body>.*?</html>", "", html, flags=re.DOTALL)

    lines = []
    # Split on div boundaries
    div_content = re.findall(r"<div[^>]*>(.*?)</div>", html, re.DOTALL | re.IGNORECASE)
    if not div_content:
        # Fallback: treat as plain text block
        return strip_html(html)

    for chunk in div_content:
        chunk = chunk.strip()
        if chunk in ("", "<br>", "<br/>", "<br />"):
            lines.append("")
            continue

        # Heading blocks
        m = re.match(r"(?i)<h([123])>(.*?)</h\1>", chunk, re.DOTALL)
        if m:
            level, content = int(m.group(1)), m.group(2)
            lines.append("#" * level + " " + _md_inline(content))
            continue

        lines.append(_md_inline(chunk))

    return "\n".join(lines).strip()


def _md_inline(html: str) -> str:
    """Convert inline HTML formatting to Markdown."""
    # Bold
    html = re.sub(r"(?i)<b>(.*?)</b>", lambda m: f"**{m.group(1)}**", html, flags=re.DOTALL)
    html = re.sub(r"(?i)<strong>(.*?)</strong>", lambda m: f"**{m.group(1)}**", html, flags=re.DOTALL)
    # Italic
    html = re.sub(r"(?i)<i>(.*?)</i>", lambda m: f"*{m.group(1)}*", html, flags=re.DOTALL)
    html = re.sub(r"(?i)<em>(.*?)</em>", lambda m: f"*{m.group(1)}*", html, flags=re.DOTALL)
    # Strikethrough
    html = re.sub(r"(?i)<s>(.*?)</s>", lambda m: f"~~{m.group(1)}~~", html, flags=re.DOTALL)
    html = re.sub(r"(?i)<del>(.*?)</del>", lambda m: f"~~{m.group(1)}~~", html, flags=re.DOTALL)
    # Underline (no standard Markdown — preserve as HTML using placeholders so
    # the _TAG_RE pass below doesn't strip the re-emitted <u> tags)
    _U_OPEN = "\x00UOPEN\x00"
    _U_CLOSE = "\x00UCLOSE\x00"
    html = re.sub(r"(?i)<u>(.*?)</u>",
                  lambda m: f"{_U_OPEN}{m.group(1)}{_U_CLOSE}", html, flags=re.DOTALL)
    # Monospace
    html = re.sub(r"(?i)<(?:code|tt)>(.*?)</(?:code|tt)>", lambda m: f"`{m.group(1)}`", html, flags=re.DOTALL)
    # Strip remaining tags
    html = _TAG_RE.sub("", html)
    # Restore underline placeholders
    html = html.replace(_U_OPEN, "<u>").replace(_U_CLOSE, "</u>")
    # Decode HTML entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return html.strip()


# ---------------------------------------------------------------------------
# Markdown → HTML
# ---------------------------------------------------------------------------

def markdown_to_html(md: str) -> str:
    """Convert Markdown to Jotter/Apple Notes HTML format."""
    if not md:
        return ""

    lines = md.splitlines()
    result = []
    for line in lines:
        if not line.strip():
            result.append("<div><br></div>")
            continue

        # Headings
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            content = _html_inline(m.group(2))
            result.append(f"<div><h{level}>{content}</h{level}></div>")
            continue

        result.append(f"<div>{_html_inline(line)}</div>")

    return "".join(result)


def _html_inline(text: str) -> str:
    """Convert inline Markdown to HTML."""
    import html as _html_mod
    text = _html_mod.escape(text, quote=False)
    # Bold (before italic to handle ***)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"<b>{m.group(1)}</b>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", lambda m: f"<i>{m.group(1)}</i>", text)
    # Strikethrough
    text = re.sub(r"~~(.+?)~~", lambda m: f"<s>{m.group(1)}</s>", text)
    # Underline <u>
    text = re.sub(r"<u>(.+?)</u>", lambda m: f"<u>{m.group(1)}</u>", text)
    # Code
    text = re.sub(r"`(.+?)`", lambda m: f"<code>{m.group(1)}</code>", text)
    return text
