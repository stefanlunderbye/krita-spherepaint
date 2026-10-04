"""Guide grid: labelled cube faces (front, right, back, left, top, bottom) as an equirectangular layer."""
import numpy as np
from . import projection as P
from .i18n import tr
from .qt import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QRectF, Qt

LABELS = {"front": "FRONT", "right": "RIGHT", "back": "BACK", "left": "LEFT", "top": "TOP", "bottom": "BOTTOM"}
COLOURS = {
    "front": QColor(255, 90, 90), "right": QColor(90, 210, 110), "back": QColor(90, 150, 255),
    "left": QColor(255, 200, 60), "top": QColor(80, 220, 230), "bottom": QColor(230, 110, 230),
}
SUPPORTED_DEPTHS = {"U8": (np.uint8, 1), "U16": (np.uint16, 257)}


def render_face(name, size):
    """One cube face as BGRA uint8 (size, size, 4): border, 4×4 grid, centre cross and label."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    colour = COLOURS[name]
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    thin = QPen(QColor(colour.red(), colour.green(), colour.blue(), 150), max(1.0, size / 600))
    p.setPen(thin)
    for i in range(1, 4):
        pos = size * i / 4
        p.drawLine(int(pos), 0, int(pos), size)
        p.drawLine(0, int(pos), size, int(pos))

    p.setPen(QPen(colour, max(2.0, size / 150)))
    p.drawRect(QRectF(0, 0, size, size).adjusted(1, 1, -1, -1))
    arm = size / 16
    c = size / 2
    p.drawLine(int(c - arm), int(c), int(c + arm), int(c))
    p.drawLine(int(c), int(c - arm), int(c), int(c + arm))

    font = QFont()  # the application font, so the label renders with whatever Krita uses
    font.setBold(True)
    font.setPixelSize(max(12, int(size / 9)))
    text = tr(LABELS[name])
    path = QPainterPath()
    metrics_rect = QRectF(0, 0, size, size)
    path.addText(0, 0, font, text)
    bounds = path.boundingRect()
    # Labels sit towards the front: the top face's image "up" points backwards, so its
    # label goes below the centre to land above FRONT, upright in the flat image.
    label_y = 0.64 if name == "top" else 0.36
    path.translate(metrics_rect.center().x() - bounds.center().x(), size * label_y - bounds.center().y())
    p.setPen(QPen(QColor(0, 0, 0, 200), max(2.0, size / 200), Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(colour)
    p.drawPath(path)
    p.end()

    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    return np.frombuffer(ptr, dtype=np.uint8).reshape(size, size, 4).copy()


def build_guide(width, height, depth):
    """The whole guide as an equirectangular array matching Krita's RGBA layout for ``depth``."""
    dtype, scale = SUPPORTED_DEPTHS[depth]
    face_size = P.matching_view_size(width, 90)
    faces = [(P.View(yaw, pitch, 90, face_size), render_face(name, face_size))
             for name, yaw, pitch in P.CUBE_FACES]
    guide = P.cube_to_equirect(faces, width, height)  # uint8 BGRA, same order as Krita's RGBA U8/U16
    return guide if scale == 1 else guide.astype(dtype) * dtype(scale)
