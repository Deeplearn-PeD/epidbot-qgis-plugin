"""Import and construction smoke tests under a real PyQt5 runtime.

These catch import-time and construction-time errors (wrong Qt module for a
name, missing attributes like ``tr`` on non-QObject classes) that compile
checks cannot see — the exact failure class that broke releases 0.1.0 and
0.1.1 on real QGIS. Skipped automatically when PyQt5 is not installed.

The plugin imports Qt through QGIS's ``qgis.PyQt`` alias module; outside a
QGIS runtime we install a minimal shim that re-exports PyQt5.
"""

import importlib
import os
import sys
import tempfile
import types

import pytest

pytest.importorskip("PyQt5.QtCore")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Repo root (parent of tests/), imported as an installed plugin would be.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _install_qgis_pyqt_shim():
    """Create a minimal ``qgis.PyQt.<module>`` re-exporting PyQt5."""
    if "qgis" in sys.modules:
        return
    qgis_mod = types.ModuleType("qgis")
    pyqt_mod = types.ModuleType("qgis.PyQt")
    qgis_mod.PyQt = pyqt_mod
    sys.modules["qgis"] = qgis_mod
    sys.modules["qgis.PyQt"] = pyqt_mod
    for sub in ("QtCore", "QtGui", "QtWidgets"):
        src = importlib.import_module("PyQt5." + sub)
        shim = types.ModuleType("qgis.PyQt." + sub)
        for name in dir(src):
            if not name.startswith("_"):
                setattr(shim, name, getattr(src, name))
        sys.modules["qgis.PyQt." + sub] = shim
        setattr(pyqt_mod, sub, shim)


def _import_plugin_package():
    """Import the repo as a package named ``epidbot`` (like QGIS does)."""
    _install_qgis_pyqt_shim()
    parent = tempfile.mkdtemp(prefix="epidbot-pkg-")
    link = os.path.join(parent, "epidbot")
    os.symlink(REPO_ROOT, link)
    sys.path.insert(0, parent)
    return importlib.import_module("epidbot")


class FakeBackend:
    """Dict-based QSettings double."""

    def __init__(self):
        self.data = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.data.pop(key, None)

    def sync(self):
        pass


@pytest.fixture(scope="module")
def qt_app():
    from qgis.PyQt.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def plugin_pkg():
    return _import_plugin_package()


class TestImports:
    """Every plugin module must import cleanly under real Qt bindings."""

    def test_plugin_module_imports(self):
        """The package root plus every submodule must import cleanly."""
        plugin_pkg = _import_plugin_package()
        assert hasattr(plugin_pkg, "classFactory")

    def test_all_ui_and_core_modules_import(self):
        for name in ("plugin", "core.api_client", "core.auth_store",
                     "core.chat_worker", "core.map_context",
                     "core.map_context_qgis", "core.markdown_lite",
                     "ui.async_call", "ui.chat_dock", "ui.message_widget",
                     "ui.settings_dialog"):
            importlib.import_module("epidbot." + name)

    def test_plugin_class_is_qobject(self):
        from qgis.PyQt.QtCore import QObject

        plugin_module = importlib.import_module("epidbot.plugin")
        assert issubclass(plugin_module.EpidbotPlugin, QObject)

    def test_classfactory_returns_instance(self):
        class _Iface:  # unused during initGui
            pass

        plugin_module = importlib.import_module("epidbot.plugin")
        instance = plugin_module.EpidbotPlugin(_Iface())
        assert isinstance(instance, plugin_module.EpidbotPlugin)


class TestConstruction:
    """Key widgets must construct without a live QGIS interface."""

    def test_settings_dialog_construction(self, plugin_pkg, qt_app):
        from epidbot.core.api_client import EpidBotApiClient
        from epidbot.core.auth_store import AuthStore
        from epidbot.ui.settings_dialog import SettingsDialog

        store = AuthStore(backend=FakeBackend(), keyring_module="disabled")
        dialog = SettingsDialog(store, EpidBotApiClient(base_url=""))
        # Defaults loaded from the store are visible in the form.
        assert dialog.url_edit.text() != ""

    def test_message_widget_construction(self, plugin_pkg, qt_app):
        from epidbot.ui.message_widget import MessageWidget

        widget = MessageWidget("assistant", "Hello **world**")
        assert widget.role == "assistant"

    def test_chat_dock_construction(self, plugin_pkg, qt_app):
        from epidbot.core.api_client import EpidBotApiClient
        from epidbot.core.auth_store import AuthStore
        from epidbot.ui.chat_dock import ChatDock

        class _Iface:
            def mapCanvas(self):  # never called during construction
                raise AssertionError("canvas not needed at construction")

        store = AuthStore(backend=FakeBackend(), keyring_module="disabled")
        client = EpidBotApiClient(base_url="")  # no network in tests
        dock = ChatDock(_Iface(), client, store)
        assert dock.session_id is None
