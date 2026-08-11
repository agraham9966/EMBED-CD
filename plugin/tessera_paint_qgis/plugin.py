"""Plugin shell: a toolbar button + Raster menu entry that toggles the dock."""
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtCore import Qt

from .dock import TesseraDock

_MENU = "TESSERA Paint"


class TesseraPaintPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None

    def initGui(self):
        self.action = QAction(_MENU, self.iface.mainWindow())
        self.action.triggered.connect(self.toggle)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToRasterMenu(_MENU, self.action)

    def toggle(self):
        if self.dock is None:
            self.dock = TesseraDock(self.iface)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        else:
            self.dock.setVisible(not self.dock.isVisible())

    def unload(self):
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock = None
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginRasterMenu(_MENU, self.action)
            self.action = None
