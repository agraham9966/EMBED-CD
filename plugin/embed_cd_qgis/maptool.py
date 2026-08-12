"""Drag a rectangle on the map to define the area.

The tool owns only a transient preview band and removes it on release. The persistent
"this is your area" outline belongs to the dock, so there is exactly one of each and neither
can pile up when the tool is toggled on and off.
"""
from qgis.PyQt.QtGui import QColor
from qgis.core import QgsGeometry, QgsRectangle, Qgis
from qgis.gui import QgsMapTool, QgsRubberBand


def polygon_geomtype():
    try:
        return Qgis.GeometryType.Polygon
    except AttributeError:
        from qgis.core import QgsWkbTypes
        return QgsWkbTypes.PolygonGeometry


class RectangleTool(QgsMapTool):
    """Press, drag, release -> reports a QgsRectangle in project CRS."""

    def __init__(self, canvas, on_area):
        super().__init__(canvas)
        self._canvas = canvas
        self._on_area = on_area
        self._start = None
        self._preview = None

    def _drop_preview(self):
        if self._preview is not None:
            self._canvas.scene().removeItem(self._preview)
            self._preview = None

    def canvasPressEvent(self, event):
        self._start = event.mapPoint()
        self._drop_preview()
        self._preview = QgsRubberBand(self._canvas, polygon_geomtype())
        self._preview.setColor(QColor(255, 140, 0))
        self._preview.setFillColor(QColor(255, 140, 0, 40))
        self._preview.setWidth(2)

    def canvasMoveEvent(self, event):
        if self._start is None or self._preview is None:
            return
        self._preview.setToGeometry(
            QgsGeometry.fromRect(QgsRectangle(self._start, event.mapPoint())), None)

    def canvasReleaseEvent(self, event):
        if self._start is None:
            return
        rect = QgsRectangle(self._start, event.mapPoint())
        self._start = None
        self._drop_preview()                  # the dock draws the lasting outline
        if rect.width() > 0 and rect.height() > 0:
            self._on_area(rect)

    def deactivate(self):
        self._start = None
        self._drop_preview()
        super().deactivate()

    def clear(self):
        self._drop_preview()
