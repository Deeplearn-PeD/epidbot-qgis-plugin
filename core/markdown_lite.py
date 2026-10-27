"""Markdown-to-HTML converter (safe subset) for the chat UI.

Converts the Markdown subset that EpidBot answers typically use (headings,
bold/italic, inline code, fenced code blocks, lists, links, paragraphs) into
HTML suitable for a ``QTextBrowser``. All input is HTML-escaped first, so
agent output can never inject markup. Pure Python, no Qt.
"""

import html
import re
from typing import List

_FENCED_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)(?:```|\Z)",
                        re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ULIST_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OLIST_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`\n]+)`")


def _render_inline(text: str) -> str:
    """Render inline markdown (on already-escaped text)."""
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
    return text


def markdown_to_html(text: str) -> str:
    """Convert a Markdown subset to HTML.

    :param text: raw markdown (may be empty or None).
    :returns: HTML string; fenced code becomes ``<pre><code>`` blocks, other
        content is escaped and grouped into paragraphs and lists.
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Split out fenced code blocks first so their content is never touched
    # by inline processing.
    parts: List[str] = []
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

    out: List[str] = []
    for kind, payload in parts:
        if kind == "pre":
            lang, code = payload
            if lang:
                out.append('<pre><code class="language-{}">{}</code></pre>'
                           .format(html.escape(lang), code))
            else:
                out.append("<pre><code>{}</code></pre>".format(code))
            continue
        out.extend(_render_block(payload))
    return "\n".join(out)


def _render_block(block: str) -> List[str]:
    """Render one non-code chunk: paragraphs, headings and lists."""
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

    for line in block.split("\n"):
        if not line.strip():
            close_paragraph()
            close_list()
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            close_paragraph()
            close_list()
            level = min(len(heading.group(1)) + 2, 6)  # h3..h6 in chat bubbles
            fragments.append("<h{1}>{0}</h{1}>".format(
                _render_inline(html.escape(heading.group(2))), level))
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
            continue
        paragraph.append(html.escape(line))
    close_paragraph()
    close_list()
    return fragments
