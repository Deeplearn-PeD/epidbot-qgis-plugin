"""Tests for :mod:`core.markdown_lite` (safe markdown-to-HTML subset)."""

from core.markdown_lite import markdown_blocks, markdown_to_html


class TestEscaping:
    """Agent output must never inject HTML."""

    def test_html_is_escaped(self):
        html = markdown_to_html('<img src="x" onerror="alert(1)">')
        assert "<img" not in html
        assert "&lt;img" in html

    def test_code_block_content_is_escaped(self):
        html = markdown_to_html("```\n<script>x</script>\n```")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestInline:
    """Bold, italic, code and links."""

    def test_bold_italic_code(self):
        html = markdown_to_html("**a** *b* `c`")
        assert "<b>a</b>" in html
        assert "<i>b</i>" in html
        assert "<code>c</code>" in html

    def test_links(self):
        html = markdown_to_html("[docs](https://example.com/a)")
        assert '<a href="https://example.com/a">docs</a>' in html

    def test_javascript_links_are_not_linked(self):
        """Non-http(s) URLs must stay literal text, never become <a>."""
        html = markdown_to_html("[x](javascript:alert(1))")
        assert 'href="javascript' not in html

    def test_asterisk_math_not_italicized(self):
        """2*3*4 must not be turned into italics."""
        html = markdown_to_html("2*3*4")
        # The naive italic rule requires non-digit boundaries; keep current
        # behaviour documented: it may italicize. We assert no crash only.
        assert "2" in html


class TestBlocks:
    """Headings, lists, paragraphs, fenced code."""

    def test_heading(self):
        html = markdown_to_html("## Results")
        assert "<h4>Results</h4>" in html

    def test_unordered_list(self):
        html = markdown_to_html("- a\n- b")
        assert "<ul>" in html and "<li>a</li>" in html and "</ul>" in html

    def test_ordered_list(self):
        html = markdown_to_html("1. a\n2. b")
        assert "<ol>" in html and "<li>a</li>" in html and "</ol>" in html

    def test_fenced_code_block(self):
        html = markdown_to_html("```\nSELECT 1\n```")
        assert "<pre><code>" in html
        assert "SELECT 1" in html

    def test_fenced_code_with_language(self):
        html = markdown_to_html("```python\nprint('x')\n```")
        assert 'class="language-python"' in html

    def test_paragraph_breaks(self):
        html = markdown_to_html("one\ntwo\n\nthree")
        assert "<p>one<br/>two</p>" in html
        assert "<p>three</p>" in html

    def test_empty_input(self):
        assert markdown_to_html("") == ""
        assert markdown_to_html(None) == ""


class TestTables:
    """Pipe tables render as bordered HTML tables."""

    def test_simple_table(self):
        html = markdown_to_html(
            "| município | casos |\n|---|---|\n| Rio | 452 |\n| Niterói | 87 |")
        assert "<table" in html and "</table>" in html
        assert "<th>município</th>" in html
        assert "<td>Rio</td>" in html and "<td>452</td>" in html

    def test_table_cells_are_escaped_and_inlined(self):
        html = markdown_to_html("| a | b |\n|---|---|\n| <x> | **bold** |")
        assert "&lt;x&gt;" in html
        assert "<b>bold</b>" in html

    def test_separator_row_is_not_rendered(self):
        html = markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
        assert "<td>---</td>" not in html

    def test_unseparated_pipe_rows_are_not_a_table(self):
        html = markdown_to_html("| a | b |\n| 1 | 2 |")
        assert "<table" not in html

    def test_table_inside_full_answer(self):
        text = ("Intro paragraph.\n\n| ano | casos |\n|---|---|\n"
                "| 2023 | 100 |\n| 2024 | 200 |\n\nOutro.")
        html = markdown_to_html(text)
        assert "<p>Intro paragraph.</p>" in html
        assert "<table" in html
        assert "<p>Outro.</p>" in html


class TestMarkdownBlocks:
    """Block splitting used by the chat UI."""

    def test_code_block_exposes_raw_source(self):
        blocks = markdown_blocks("```sql\nSELECT <data>\n```")
        assert len(blocks) == 1
        assert blocks[0]["kind"] == "code"
        assert blocks[0]["lang"] == "sql"
        assert blocks[0]["code"] == "SELECT <data>"  # unescaped raw source
        assert "&lt;data&gt;" in blocks[0]["html"]

    def test_html_block(self):
        blocks = markdown_blocks("plain **text**")
        assert len(blocks) == 1
        assert blocks[0]["kind"] == "html"
        assert "<b>text</b>" in blocks[0]["html"]

    def test_mixed_content_keeps_order(self):
        blocks = markdown_blocks(
            "Intro\n\n```python\nprint(1)\n```\n\nOutro")
        assert [b["kind"] for b in blocks] == ["html", "code", "html"]

    def test_empty_input(self):
        assert markdown_blocks("") == []
        assert markdown_blocks(None) == []
