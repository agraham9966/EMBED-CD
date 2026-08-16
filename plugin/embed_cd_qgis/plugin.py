"""Toolbar/menu entry that toggles the EMBED-CD dock."""
import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt

from .dock import ChangeDock

_MENU = "EMBED-CD"


def icon_path():
    """The logo, whether running from the repo or from an installed zip.

    make_release copies icons/ into the plugin folder, so the installed layout has it one level
    up from this file's package; in dev the plugin folder is a sibling of the repo's icons/.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "icons", "embed-cd-logo.png"),
                 os.path.join(here, "..", "..", "icons", "embed-cd-logo.png")):
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return None


def _scoped(owner, category, name):
    try:
        return getattr(getattr(owner, category), name)
    except AttributeError:
        return getattr(owner, name)


class EmbedCdPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None

    def initGui(self):
        path = icon_path()
        self.action = (QAction(QIcon(path), _MENU, self.iface.mainWindow()) if path
                       else QAction(_MENU, self.iface.mainWindow()))
        self.action.triggered.connect(self.toggle)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToRasterMenu(_MENU, self.action)

    def toggle(self):
        if self.dock is None:
            self.dock = ChangeDock(self.iface)
            self.iface.addDockWidget(
                _scoped(Qt, "DockWidgetArea", "RightDockWidgetArea"), self.dock)
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
