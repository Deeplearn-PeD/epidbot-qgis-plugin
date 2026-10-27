"""Plugin entry: toolbar actions, dock registration, i18n setup."""

import os
from typing import Optional

from qgis.PyQt.QtCore import (QCoreApplication, QLocale, QObject, Qt,
                              QTranslator)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget

from .core.api_client import EpidBotApiClient
from .core.auth_store import AuthStore
from .ui.chat_dock import ChatDock
from .ui.settings_dialog import SettingsDialog


class EpidbotPlugin(QObject):
    """QGIS plugin controller (created by ``classFactory``).

    Inherits ``QObject`` so ``self.tr()`` is available for
    translatable UI strings.
    """

    DOCK_OBJECT_NAME = "EpidBotChatDock"

    def __init__(self, iface):
        """Store the QGIS interface; real work happens in ``initGui``."""
        super().__init__()
        self.iface = iface
        self.translator: Optional[QTranslator] = None
        self.auth_store: Optional[AuthStore] = None
        self.client: Optional[EpidBotApiClient] = None
        self.dock: Optional[ChatDock] = None
        self.dock_widget: Optional[QDockWidget] = None
        self.actions: list = []
        self.menu = "&EpidBot"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def initGui(self):
        """Build the UI: translator, dock, toolbar/menu actions."""
        self._init_translator()
        self.auth_store = AuthStore()
        self.client = EpidBotApiClient(
            base_url=str(self.auth_store.get_plain("base_url") or ""),
            auth_store=self.auth_store,
            poll_interval=float(self.auth_store.get_plain("poll_interval")))

        icon = QIcon(os.path.join(
            os.path.dirname(__file__), "icons", "epidbot.png"))

        show_action = QAction(icon, self.tr("Open EpidBot Chat"),
                              self.iface.mainWindow())
        show_action.setCheckable(True)
        show_action.triggered.connect(self.toggle_dock)
        self._register_action(show_action)

        settings_action = QAction(self.tr("EpidBot Settings..."),
                                  self.iface.mainWindow())
        settings_action.triggered.connect(self.show_settings)
        self._register_action(settings_action)

        extent_action = QAction(self.tr("Ask EpidBot about this extent"),
                                self.iface.mainWindow())
        extent_action.setToolTip(self.tr(
            "Start a chat prompt focused on the current map extent"))
        extent_action.triggered.connect(self._prompt_about_extent)
        self._register_action(extent_action)

        selection_action = QAction(self.tr("Ask EpidBot about selection"),
                                   self.iface.mainWindow())
        selection_action.setToolTip(self.tr(
            "Start a chat prompt analyzing the features selected on the map"))
        selection_action.triggered.connect(self._prompt_about_selection)
        self._register_action(selection_action)

        self._create_dock()

    def unload(self):
        """Remove actions and dock (called by QGIS on plugin unload)."""
        if self.dock_widget is not None:
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget.deleteLater()
            self.dock_widget = None
            self.dock = None
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
            action.deleteLater()
        self.actions.clear()

    def _register_action(self, action: QAction):
        self.iface.addPluginToMenu(self.menu, action)
        self.iface.addToolBarIcon(action)
        self.actions.append(action)

    def _init_translator(self):
        """Load the .qm translation matching the QGIS locale, if present."""
        locale = QLocale().name()  # e.g. "pt_BR"
        locale_path = os.path.join(os.path.dirname(__file__), "i18n")
        candidates = ["epidbot_{}.qm".format(locale)]
        if "_" in locale:
            candidates.append("epidbot_{}.qm".format(locale.split("_")[0]))
        for candidate in candidates:
            path = os.path.join(locale_path, candidate)
            if os.path.exists(path):
                self.translator = QTranslator()
                if self.translator.load(path):
                    QCoreApplication.installTranslator(self.translator)
                break

    # ------------------------------------------------------------------
    # Dock handling
    # ------------------------------------------------------------------
    def _create_dock(self):
        self.dock = ChatDock(self.iface, self.client, self.auth_store)
        self.dock.settingsRequested.connect(self.show_settings)
        self.dock_widget = QDockWidget(self.tr("EpidBot Chat"),
                                       self.iface.mainWindow())
        self.dock_widget.setObjectName(self.DOCK_OBJECT_NAME)
        self.dock_widget.setWidget(self.dock)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)
        self.dock_widget.hide()

        # Keep the toolbar toggle in sync when the dock is closed directly.
        self.dock_widget.visibilityChanged.connect(
            self._sync_show_action_state)

    def _sync_show_action_state(self, visible: bool):
        if self.actions:
            self.actions[0].setChecked(visible)

    def toggle_dock(self, checked: bool):
        """Show/hide the chat dock (toolbar action entry point)."""
        if self.dock_widget is not None:
            self.dock_widget.setVisible(checked)

    def _prompt_about_extent(self):
        """Start an extent-focused prompt (toolbar action entry point)."""
        if self.dock is not None:
            self.dock.prompt_about_extent()

    def _prompt_about_selection(self):
        """Start a selection-analysis prompt (toolbar action entry point)."""
        if self.dock is not None:
            self.dock.prompt_about_selection()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def show_settings(self):
        """Open the settings dialog; apply changes on accept."""
        dialog = SettingsDialog(self.auth_store, self.client,
                                parent=self.iface.mainWindow())
        if dialog.exec():
            dialog.apply()
            if self.dock is not None:
                self.dock.refresh_sessions()
                self.dock._set_status(self.tr("Settings updated."))
