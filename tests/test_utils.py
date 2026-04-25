"""Unit tests for jotter.utils (pure-Python functions only)."""

from __future__ import annotations

import pytest

from jotter.utils import html_to_markdown, markdown_to_html, strip_html


class TestStripHtml:
    def test_removes_tags(self):
        assert strip_html("<div>Hello</div>") == "Hello"

    def test_empty_string(self):
        assert strip_html("") == ""

    def test_preserves_plain_text(self):
        assert strip_html("Just text") == "Just text"

    def test_block_tags_become_newlines(self):
        result = strip_html("<div>Line 1</div><div>Line 2</div>")
        assert "Line 1" in result
        assert "Line 2" in result
        assert "\n" in result

    def test_strips_nested_tags(self):
        assert strip_html("<div><b>Bold</b> and <i>italic</i></div>") == "Bold and italic"

    def test_collapses_excess_newlines(self):
        result = strip_html("<div>A</div><div><br></div><div><br></div><div>B</div>")
        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in result


class TestHtmlToMarkdown:
    def test_empty(self):
        assert html_to_markdown("") == ""

    def test_plain_div(self):
        assert html_to_markdown("<div>Hello world</div>") == "Hello world"

    def test_bold(self):
        result = html_to_markdown("<div><b>Bold</b> text</div>")
        assert "**Bold**" in result

    def test_italic(self):
        result = html_to_markdown("<div><i>Italic</i> text</div>")
        assert "*Italic*" in result

    def test_strikethrough(self):
        result = html_to_markdown("<div><s>Strike</s></div>")
        assert "~~Strike~~" in result

    def test_underline_preserved_as_html(self):
        result = html_to_markdown("<div><u>Underline</u></div>")
        assert "<u>Underline</u>" in result

    def test_heading1(self):
        result = html_to_markdown("<div><h1>Title</h1></div>")
        assert result.startswith("# Title")

    def test_heading2(self):
        result = html_to_markdown("<div><h2>Section</h2></div>")
        assert result.startswith("## Section")

    def test_heading3(self):
        result = html_to_markdown("<div><h3>Sub</h3></div>")
        assert result.startswith("### Sub")

    def test_blank_div_becomes_empty_line(self):
        result = html_to_markdown("<div>A</div><div><br></div><div>B</div>")
        assert "\n\n" in result

    def test_multiple_lines(self):
        html = "<div>First line</div><div>Second line</div>"
        result = html_to_markdown(html)
        assert "First line" in result
        assert "Second line" in result

    def test_strips_outer_html_wrapper(self):
        html = (
            '<html><head></head><body>'
            '<div>Content</div>'
            '</body></html>'
        )
        result = html_to_markdown(html)
        assert result == "Content"


class TestMarkdownToHtml:
    def test_empty(self):
        assert markdown_to_html("") == ""

    def test_plain_line(self):
        result = markdown_to_html("Hello world")
        assert "<div>" in result
        assert "Hello world" in result

    def test_bold(self):
        result = markdown_to_html("**Bold** text")
        assert "<b>Bold</b>" in result

    def test_italic(self):
        result = markdown_to_html("*Italic* text")
        assert "<i>Italic</i>" in result

    def test_strikethrough(self):
        result = markdown_to_html("~~Strike~~")
        assert "<s>Strike</s>" in result

    def test_heading1(self):
        result = markdown_to_html("# Title")
        assert "<h1>Title</h1>" in result

    def test_heading2(self):
        result = markdown_to_html("## Section")
        assert "<h2>Section</h2>" in result

    def test_heading3(self):
        result = markdown_to_html("### Sub")
        assert "<h3>Sub</h3>" in result

    def test_blank_line_becomes_br_div(self):
        result = markdown_to_html("Line one\n\nLine two")
        assert "<br>" in result

    def test_html_entities_escaped(self):
        result = markdown_to_html("a < b & c > d")
        assert "<" not in result.replace("<div>", "").replace("</div>", "").replace("<br>", "")
        assert "&amp;" in result or "&lt;" in result

    def test_roundtrip_plain_text(self):
        md = "Hello world"
        html = markdown_to_html(md)
        back = html_to_markdown(html)
        assert back == md

    def test_roundtrip_bold(self):
        md = "**Bold** word"
        html = markdown_to_html(md)
        back = html_to_markdown(html)
        assert back == md
