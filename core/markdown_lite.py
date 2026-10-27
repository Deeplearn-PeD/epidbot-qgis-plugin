"""Markdown-to-HTML converter (safe subset) for the chat UI.

Converts the Markdown subset that EpidBot answers typically use (headings,
bold/italic, inline code, fenced code blocks, lists, links, paragraphs) into
HTML suitable for a ``QTextBrowser``. All input is HTML-escaped first, so
agent output can never inject markup. Pure Python, no Qt.
"""

import html
import re
from typing import Dict, List

_FENCED_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)(?:```|\Z)",
                        re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ULIST_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OLIST_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.*)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _render_inline(text: str) -> str:
    """Render inline markdown (on already-escaped text)."""
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
    return text


def markdown_blocks(text: str) -> List[Dict[str, str]]:
    """Convert a Markdown subset to a list of rendered blocks.

    :param text: raw markdown (may be empty or None).
    :returns: blocks in document order. ``{"kind": "html", "html": ...}`` for
        flowing content and ``{"kind": "code", "lang": ..., "code": <raw
        source>, "html": ...}`` for fenced code. The ``code`` key carries the
        unescaped source so UIs can offer "copy to clipboard".
    """
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Split out fenced code blocks first so their content is never touched
    # by inline processing.
    parts: List[tuple] = []
    cursor = 0
    for match in _FENCED_RE.finditer(text):
        before = text[cursor:match.start()]
        if before:
            parts.append(("html", before))
        lang = match.group(1) or ""
        code = html.escape(match.group(2).rstrip("\n"))
        parts.append(("pre", (lang, code)))
        cursor = match.end()
    tail = text[cursor:]
    if tail:
        parts.append(("html", tail))

    blocks: List[Dict[str, str]] = []
    for kind, payload in parts:
        if kind == "pre":
            lang, code = payload
            if lang:
                block_html = ('<pre><code class="language-{}">{}</code></pre>'
                              .format(html.escape(lang), code))
            else:
                block_html = "<pre><code>{}</code></pre>".format(code)
            blocks.append({"kind": "code", "lang": lang,
                           "code": html.unescape(code), "html": block_html})
        else:
            blocks.append({"kind": "html",
                           "html": "\n".join(_render_block(payload))})
    return blocks


def markdown_to_html(text: str) -> str:
    """Convert a Markdown subset to HTML.

    :param text: raw markdown (may be empty or None).
    :returns: HTML string; fenced code becomes ``<pre><code>`` blocks, other
        content is escaped and grouped into paragraphs, lists and tables.
    """
    return "\n".join(block["html"] for block in markdown_blocks(text))


def _render_block(block: str) -> List[str]:
    """Render one non-code chunk: paragraphs, headings, lists and tables."""
    fragments: List[str] = []
    paragraph: List[str] = []
    list_tag: List[str] = []  # stack with single entry: "ul" or "ol" or []

    def close_paragraph():
        if paragraph:
            fragments.append("<p>{}</p>".format(
                "<br/>".join(_render_inline(line) for line in paragraph)))
            paragraph.clear()

    def close_list():
        if list_tag:
            fragments.append("</{}>".format(list_tag.pop()))

    lines = block.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            close_paragraph()
            close_list()
            i += 1
            continue
        table = _match_table(lines, i)
        if table is not None:
            close_paragraph()
            close_list()
            header_cells, row_cells, next_index = table
            fragments.append(_render_table(header_cells, row_cells))
            i = next_index  # first line after the table
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            close_paragraph()
            close_list()
            level = min(len(heading.group(1)) + 2, 6)  # h3..h6 in chat bubbles
            fragments.append("<h{1}>{0}</h{1}>".format(
                _render_inline(html.escape(heading.group(2))), level))
            i += 1
            continue
        ulist = _ULIST_RE.match(line)
        olist = _OLIST_RE.match(line)
        if ulist or olist:
            close_paragraph()
            wanted = "ul" if ulist else "ol"
            if not list_tag:
                list_tag.append(wanted)
                fragments.append("<{}>".format(wanted))
            elif list_tag[-1] != wanted:
                close_list()
                list_tag.append(wanted)
                fragments.append("<{}>".format(wanted))
            item = (ulist or olist).group(1)
            fragments.append("<li>{}</li>".format(
                _render_inline(html.escape(item))))
            i += 1
            continue
        paragraph.append(html.escape(line))
        i += 1
    close_paragraph()
    close_list()
    return fragments


def _match_table(lines: List[str], start: int):
    """Detect a pipe table beginning at ``lines[start]``.

    :returns: ``(header_cells, row_cells, next_index)`` or None.
    """
    if start + 1 >= len(lines):
        return None
    header = _TABLE_ROW_RE.match(lines[start])
    if not header or not _TABLE_SEP_RE.match(lines[start + 1]):
        return None
    rows: List[List[str]] = []
    i = start + 2
    while i < len(lines):
        row = _TABLE_ROW_RE.match(lines[i])
        if not row:
            break
        rows.append(_table_cells(row.group(1)))
        i += 1
    return _table_cells(header.group(1)), rows, i


def _table_cells(inner: str) -> List[str]:
    """Split a table row's inner text into trimmed cells."""
    return [cell.strip() for cell in inner.split("|")]


def _render_table(header: List[str], rows: List[List[str]]) -> str:
    """Render a pipe table as a simple bordered HTML table."""

    def row_html(cells: List[str], tag: str) -> str:
        cells_html = "".join(
            "<{1}>{0}</{1}>".format(_render_inline(html.escape(cell)), tag)
            for cell in cells)
        return "<tr>{}</tr>".format(cells_html)

    out = ['<table border="1" cellspacing="0" cellpadding="3">',
           row_html(header, "th")]
    for row in rows:
        out.append(row_html(row, "td"))
    out.append("</table>")
    return "".join(out)
