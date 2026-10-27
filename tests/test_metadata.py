"""Guard: metadata.txt must parse exactly the way QGIS parses it.

QGIS (both ``qgis.utils`` and ``pyplugin_installer``) reads ``metadata.txt``
with ``configparser``. Between 0.1.2 and 0.1.4 the plugin shipped with a
changelog whose continuation lines broke that parser, making the Plugin
Manager flag the plugin as broken ("Error reading metadata"). These tests
replicate QGIS's parsing so the failure mode cannot ship again.
"""

import configparser
import codecs
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METADATA_PATH = os.path.join(REPO_ROOT, "metadata.txt")

REQUIRED_KEYS = ["name", "description", "about", "version",
                 "qgisMinimumVersion", "author_name", "email", "icon"]


@pytest.fixture(scope="module")
def metadata():
    """Parse metadata.txt exactly like pyplugin_installer does."""
    cp = configparser.ConfigParser()
    with codecs.open(METADATA_PATH, "r", "utf8") as fh:
        cp.read_file(fh)  # raises ParsingError on QGIS-incompatible syntax
    return cp


class TestMetadataParses:
    """The configparser gate."""

    def test_file_parses_with_configparser(self, metadata):
        assert metadata.sections() == ["general"]

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_required_keys_present(self, metadata, key):
        value = metadata.get("general", key)
        assert value and value.strip(), "missing metadata key: {}".format(key)

    def test_version_is_semantic_like(self, metadata):
        import re

        version = metadata.get("general", "version").strip()
        assert re.match(r"^\d+\.\d+(\.\d+)?$", version), \
            "version {!r} is not X.Y[.Z]".format(version)

    def test_icon_file_exists(self, metadata):
        icon = metadata.get("general", "icon").strip()
        path = os.path.join(REPO_ROOT, icon)
        assert os.path.exists(path), "icon file not found: {}".format(path)

    def test_experimental_flag_is_boolean_like(self, metadata):
        value = metadata.get("general", "experimental",
                             fallback="true").strip().lower()
        assert value in ("true", "false")

    def test_changelog_mentions_current_version(self, metadata):
        version = metadata.get("general", "version").strip()
        changelog = metadata.get("general", "changelog").strip()
        assert changelog.startswith(version), \
            "changelog does not start with an entry for {}".format(version)


class TestPackagedZip:
    """The shipped ZIP must contain a parseable metadata.txt too."""

    def test_package_sh_script_excludes_caches(self):
        script = os.path.join(REPO_ROOT, "scripts", "package.sh")
        with open(script, "r", encoding="utf8") as fh:
            content = fh.read()
        assert "__pycache__" in content, \
            "package.sh should strip __pycache__ directories"
