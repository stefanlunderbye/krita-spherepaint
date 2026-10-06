"""360° preview: look around the panorama by dragging, like a 360° viewer.

The framed rectangle in the middle shows exactly what the projection will show
(same direction, field of view and aspect ratio); when the window has another
shape the rest of the panorama around it is shown dimmed. Rendered from a downscaled copy of the panorama so it keeps up
with the mouse. No Krita dependency – the docker that hosts it lives in docker.py.
"""
import math

import numpy as np

from . import projection as P
from .qt import QColor, QImage, QPainter, QPen, QSize, QSizePolicy, Qt, QWidget, event_pos, pyqtSignal

RENDER_MAX = 400  # longest side of the rendered image; the widget scales it up
MAX_WIDE_FOV = 170.0  # cap for the widened field of view of non-square windows


def qimage_to_bgra(image):
    """QImage -> BGRA uint8 array (H, W, 4), the layout of Krita's 8-bit RGBA."""
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
    return rows[:, :image.width() * 4].reshape(image.height(), image.width(), 4).copy()


def projection_rect(width, height, aspect=1.0):
    """(width, height) of the largest ``aspect`` rectangle that fits in a width×height frame."""
    rect_w = min(float(width), height * aspect)
    return rect_w, rect_w / aspect


def widened_fov(fov_deg, width, height, aspect=1.0):
    """Horizontal field of view of a width×height frame whose centred projection rectangle spans ``fov_deg``."""
    rect_w = projection_rect(width, height, aspect)[0]
    focal = (rect_w / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    return min(MAX_WIDE_FOV, math.degrees(2.0 * math.atan((width / 2.0) / focal)))


class PanoramaPreview(QWidget):
    """Perspective preview of any shape. Drag to look around, scroll to zoom.

    ``directionChanged`` fires while dragging, ``directionPicked`` on release and
    ``fovStep`` on the wheel – the same signals as the thumbnail picker.
    """

    directionChanged = pyqtSignal(float, float)
    directionPicked = pyqtSignal(float, float)
    fovStep = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None  # BGRA array of the downscaled panorama
        self._yaw, self._pitch, self._fov = 0.0, 0.0, 90.0
        self._aspect = 1.0
        self._frame = None  # (QImage, backing bytes, size) cache for the current view
        self._drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(120, 120)

    def setSource(self, image):
        self._source = qimage_to_bgra(image) if isinstance(image, QImage) and not image.isNull() else None
        self._frame = None
        self.update()

    def setView(self, yaw, pitch, fov, aspect=None):
        aspect = self._aspect if aspect is None else aspect
        if (yaw, pitch, fov, aspect) != (self._yaw, self._pitch, self._fov, self._aspect):
            self._yaw, self._pitch, self._fov, self._aspect = yaw, pitch, fov, aspect
            self._frame = None
            self.update()

    def sizeHint(self):
        return QSize(400, 400)

    def resizeEvent(self, event):
        self._frame = None
        super().resizeEvent(event)

    def _render(self):
        w, h = max(1, self.width()), max(1, self.height())
        scale = min(1.0, RENDER_MAX / max(w, h))
        rw, rh = max(16, int(w * scale)), max(16, int(h * scale))
        fov = widened_fov(self._fov, rw, rh, self._aspect)
        pixels = P.render_perspective(self._source, self._yaw, self._pitch, fov, rw, rh).tobytes()
        image = QImage(pixels, rw, rh, rw * 4, QImage.Format.Format_ARGB32)
        return image, pixels  # keep the buffer alive as long as the QImage

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        if self._source is None:
            p.fillRect(0, 0, w, h, QColor(40, 40, 40))
            p.end()
            return
        if self._frame is None:
            self._frame = self._render()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(0, 0, self._frame[0].scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
        rect_w, rect_h = (int(round(v)) for v in projection_rect(w, h, self._aspect))
        if abs(rect_w - w) > 1 or abs(rect_h - h) > 1:
            # Dim what lies outside the projection and frame the projected rectangle.
            x0, y0 = (w - rect_w) // 2, (h - rect_h) // 2
            shade = QColor(0, 0, 0, 110)
            p.fillRect(0, 0, w, y0, shade)
            p.fillRect(0, y0 + rect_h, w, h - y0 - rect_h, shade)
            p.fillRect(0, y0, x0, rect_h, shade)
            p.fillRect(x0 + rect_w, y0, w - x0 - rect_w, rect_h, shade)
            p.setPen(QPen(QColor(255, 210, 60, 200), 1.5))
            p.drawRect(x0, y0, rect_w - 1, rect_h - 1)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = (event_pos(event), self._yaw, self._pitch)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_start is None:
            return
        start, yaw0, pitch0 = self._drag_start
        delta = event_pos(event) - start
        degrees_per_pixel = self._fov / max(1.0, projection_rect(self.width(), self.height(), self._aspect)[0])
        # Grab-and-drag: moving the mouse right turns the view left, like a 360° viewer.
        yaw = (yaw0 - delta.x() * degrees_per_pixel + 180.0) % 360.0 - 180.0
        pitch = max(-90.0, min(90.0, pitch0 + delta.y() * degrees_per_pixel))
        self.setView(round(yaw, 1), round(pitch, 1), self._fov)
        self.directionChanged.emit(self._yaw, self._pitch)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            moved = event_pos(event) != self._drag_start[0]
            self._drag_start = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if moved:
                self.directionPicked.emit(self._yaw, self._pitch)

    def wheelEvent(self, event):
        steps = event.angleDelta().y() // 120
        if steps:
            self.fovStep.emit(-steps)
        event.accept()
