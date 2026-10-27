"""Guard: every ``tr()`` string in the source must exist in the .ts files.

Qt's ``tr()`` does byte-exact source-text lookup, so a typo or an edit to a
string without updating the translation catalogs silently falls back to the
English source. This test parses the source with :mod:`ast` (so implicit
string concatenation across lines is resolved exactly like the compiler
does) and compares against the catalogs.
"""

import ast
import os
import xml.etree.ElementTree as ET

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Source files whose tr() strings must be cataloged.
SOURCE_FILES = ["plugin.py", "ui/chat_dock.py", "ui/message_widget.py",
                "ui/settings_dialog.py"]

CATALOGS = ["i18n/epidbot_pt_BR.ts", "i18n/epidbot_en.ts"]


def _tr_sources(path: str):
    """All literal strings passed to ``self.tr(...)`` in a source file."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    sources = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            continue
        sources.add(node.args[0].value)
    return sources


def _catalog_sources(catalog_path: str):
    tree = ET.parse(catalog_path)
    return {message.findtext("source") or ""
            for message in tree.findall(".//message")}


@pytest.mark.parametrize("catalog", CATALOGS)
def test_every_tr_string_is_cataloged(catalog):
    cataloged = _catalog_sources(os.path.join(REPO_ROOT, catalog))
    missing = []
    for relative in SOURCE_FILES:
        for source in _tr_sources(os.path.join(REPO_ROOT, relative)):
            if source not in cataloged:
                missing.append("{}: {!r}".format(relative, source[:60]))
    assert not missing, "strings missing from {}: {}".format(catalog, missing)


def test_catalogs_have_same_source_sets():
    catalogs = [_catalog_sources(os.path.join(REPO_ROOT, catalog))
                for catalog in CATALOGS]
    assert catalogs[0] == catalogs[1], \
        "translation catalogs cover different source strings"
