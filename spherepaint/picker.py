"""Mouse control for the view direction: a thumbnail of the panorama to click and drag on."""
from . import projection as P
from .qt import (
    QColor, QImage, QPainter, QPainterPath, QPen, QPointF, QSize, QSizePolicy, Qt, QWidget,
    event_pos, pyqtSignal,
)


class DirectionPicker(QWidget):
    """Shows the equirectangular image with the current view's outline.

    Click or drag sets yaw (horizontal) and pitch (vertical); the wheel changes
    the field of view. ``directionChanged`` fires while dragging,
    ``directionPicked`` when the button is released.
    """

    directionChanged = pyqtSignal(float, float)
    directionPicked = pyqtSignal(float, float)
    fovStep = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = None
        self._yaw = 0.0
        self._pitch = 0.0
        self._fov = 90.0
        self._aspect = 1.0
        self._dragging = False
        self.setMouseTracking(False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumSize(160, 80)

    # --- state --------------------------------------------------------------

    def setImage(self, image):
        self._image = image if isinstance(image, QImage) and not image.isNull() else None
        self.update()

    def setView(self, yaw, pitch, fov, aspect=1.0):
        self._yaw, self._pitch, self._fov, self._aspect = yaw, pitch, fov, aspect
        self.update()

    # --- geometry -----------------------------------------------------------

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return max(80, width // 2)

    def sizeHint(self):
        return QSize(320, 160)

    def _rect(self):
        """The 2:1 area the panorama is drawn in, centred in the widget."""
        w, h = self.width(), self.height()
        rw = min(w, 2 * h)
        rh = rw // 2
        return (w - rw) // 2, (h - rh) // 2, rw, rh

    def _to_widget(self, lon, lat):
        x0, y0, rw, rh = self._rect()
        return x0 + (lon + 180.0) / 360.0 * rw, y0 + (90.0 - lat) / 180.0 * rh

    def _from_widget(self, pos):
        x0, y0, rw, rh = self._rect()
        yaw = (pos.x() - x0) / max(1, rw) * 360.0 - 180.0
        pitch = 90.0 - (pos.y() - y0) / max(1, rh) * 180.0
        yaw = (yaw + 180.0) % 360.0 - 180.0
        pitch = max(-90.0, min(90.0, pitch))
        return round(yaw, 1), round(pitch, 1)

    # --- painting -----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x0, y0, rw, rh = self._rect()
        if self._image is not None:
            p.drawImage(x0, y0, self._image.scaled(rw, rh, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation))
        else:
            p.fillRect(x0, y0, rw, rh, QColor(40, 40, 40))
        grid = QPen(QColor(255, 255, 255, 50), 1, Qt.PenStyle.DotLine)
        p.setPen(grid)
        for lon in (-90, 0, 90):
            x, _ = self._to_widget(lon, 0)
            p.drawLine(int(x), y0, int(x), y0 + rh)
        _, y = self._to_widget(0, 0)
        p.drawLine(x0, int(y), x0 + rw, int(y))

        # View outline; split where it wraps across the ±180° seam.
        lon, lat = P.view_outline(P.View(self._yaw, self._pitch, self._fov, 1000,
                                                   P.view_height(1000, self._aspect)))
        path = QPainterPath()
        pts = [self._to_widget(a, b) for a, b in zip(lon, lat)]
        pts.append(pts[0])
        prev_lon = lon[0]
        path.moveTo(*pts[0])
        for (x, y), l in zip(pts[1:], list(lon[1:]) + [lon[0]]):
            if abs(l - prev_lon) > 180.0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
            prev_lon = l
        p.setPen(QPen(QColor(0, 0, 0, 160), 3))
        p.drawPath(path)
        p.setPen(QPen(QColor(255, 210, 60), 1.5))
        p.drawPath(path)

        cx, cy = self._to_widget(self._yaw, self._pitch)
        p.setPen(QPen(QColor(255, 210, 60), 2))
        p.drawEllipse(QPointF(cx, cy), 4, 4)
        p.end()

    # --- input --------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._emit_change(event_pos(event))

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._emit_change(event_pos(event))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            yaw, pitch = self._from_widget(event_pos(event))
            self.directionPicked.emit(yaw, pitch)

    def wheelEvent(self, event):
        steps = event.angleDelta().y() // 120
        if steps:
            self.fovStep.emit(-steps)  # wheel up = zoom in = narrower field of view
        event.accept()

    def _emit_change(self, pos):
        yaw, pitch = self._from_widget(pos)
        self._yaw, self._pitch = yaw, pitch
        self.update()
        self.directionChanged.emit(yaw, pitch)
