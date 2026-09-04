"""Toolbar/menu entry that toggles the EMBED-CD dock."""
import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtCore import Qt as _Qt
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtCore import Qt

from .compat import scoped as _scoped
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


def trim_pixmap(pm):
    """Crop a pixmap to its non-transparent artwork.

    The logo carries a lot of empty margin — the marks occupy about two thirds of its height and
    half its width — so scaling the whole file to a title-bar height leaves the artwork tiny.
    The bounding box is found on a small copy (a few thousand pixels) and mapped back, which
    keeps this generic if the logo is ever replaced, and costs nothing at startup.
    """
    try:
        small = pm.scaled(96, 96, _scoped(_Qt, "AspectRatioMode", "KeepAspectRatio"),
                          _scoped(_Qt, "TransformationMode", "FastTransformation"))
        img = small.toImage()
        xs, ys = [], []
        for y in range(img.height()):
            for x in range(img.width()):
                if (img.pixel(x, y) >> 24) & 0xFF > 8:      # alpha
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return pm
        fx, fy = pm.width() / img.width(), pm.height() / img.height()
        x0, x1 = int(min(xs) * fx), int((max(xs) + 1) * fx)
        y0, y1 = int(min(ys) * fy), int((max(ys) + 1) * fy)
        return pm.copy(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
    except Exception:
        return pm



class EmbedCdPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None

    def initGui(self):
        # Trim before making the icon: untrimmed, the toolbar slot is mostly the logo's empty
        # margin and the artwork ends up a fraction of the space QGIS gave it.
        path = icon_path()
        icon = None
        if path:
            pm = QPixmap(path)
            if not pm.isNull():
                icon = QIcon(trim_pixmap(pm))
        self.action = (QAction(icon, _MENU, self.iface.mainWindow()) if icon
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
