"""Docker panel: switches between the equirectangular image (flat) and a perspective view."""
import hashlib
import itertools
import json
import math
import os
import time
import weakref
import zlib

import numpy as np
from krita import DockWidget, InfoObject, Krita

from . import guide, xmp
from . import projection as P
from .i18n import tr
from .picker import DirectionPicker
from .preview import PanoramaPreview
from .qt import (
    QActionGroup, QApplication, QByteArray, QDoubleSpinBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMenu,
    QImage, QMessageBox, QPainter, QPushButton, QSizeGrip, QSizePolicy, QSpinBox, Qt, QtCore, QtGui, QToolButton,
    QVBoxLayout, QWidget,
)

YES, NO, CANCEL = (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No,
                   QMessageBox.StandardButton.Cancel)

DTYPES = {"U8": np.uint8, "U16": np.uint16, "F16": np.float16, "F32": np.float32}
TITLE = "SpherePaint"  # product name, not translated
PREVIEW_SOURCE_WIDTH = 2048  # downscaled panorama used by the thumbnail and the 360° preview
ANNOTATION = "spherepaint"  # document annotation holding the last view direction
# Projection aspect ratios (width / height) offered in the menu; labels are universal, not translated.
UNDO_STEPS = 10  # write-backs that can be undone, per panorama
UNDO_BYTES = 1 << 30  # memory the undo history may hold; the oldest steps are dropped first
WATCH_INTERVAL_MS = 1000  # how often the panorama is checked for changes, when that is switched on
WATCH_QUIET_S = 1.0  # the thumbnail is remade once the panorama has been left alone this long
WATCH_ROWS = 512  # rows sampled to notice a change
SETTINGS_GROUP = "SpherePaint"  # section in Krita's settings file (kritarc)
ASPECT_RATIOS = (("1:1", 1.0), ("4:3", 4 / 3), ("3:2", 3 / 2), ("16:9", 16 / 9), ("3:4", 3 / 4), ("9:16", 9 / 16))
REFERENCE_LAYER = tr("Reference (whole image)")
MIRROR_BATCH = 4  # panorama layers projected together: shares the geometry, bounds the memory
# Channels per colour model, for a panorama without any paint layer to ask.
CHANNELS = {"RGBA": 4, "GRAYA": 2, "CMYKA": 5, "LABA": 4, "XYZA": 4, "YCbCrA": 4}


_DOCKERS = weakref.WeakSet()  # one SpherePaint docker per Krita window
_PREVIEWS = weakref.WeakSet()  # one 360° preview docker per Krita window (floating docks keep their parent)


def docker_for_active_window():
    """The SpherePaint docker of the active Krita window (used by the shortcut actions)."""
    dockers = _alive_widgets(_DOCKERS)
    window = Krita.instance().activeWindow()
    main = window.qwindow() if window else None
    for docker in dockers:
        if main is not None and docker.parentWidget() is main:
            return docker
    return dockers[0] if dockers else None


def _menu_icon(widget):
    """Three horizontal lines in the widget's text colour (Krita has no such icon of its own)."""
    pixmap = QtGui.QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(widget.palette().color(QtGui.QPalette.ColorRole.ButtonText), 3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    for y in (8, 16, 24):
        painter.drawLine(5, y, 27, y)
    painter.end()
    pixmap.setDevicePixelRatio(2.0)  # drawn at twice the size, sharp on high-DPI screens
    return QtGui.QIcon(pixmap)


def _set_icon(button, *names):
    """Uses the first of Krita's own icons that exists, so the panel matches Krita's theme."""
    for name in names:
        icon = Krita.instance().icon(name)
        if icon is not None and not icon.isNull():
            button.setIcon(icon)
            return


def _alive_widgets(registry):
    """Members of a docker registry whose Qt object still exists."""
    alive = []
    for widget in list(registry):
        try:
            widget.objectName()
        except RuntimeError:  # the C++ object behind the wrapper is gone
            registry.discard(widget)
            continue
        alive.append(widget)
    return alive


def _same_window(registry, widget):
    """Members of ``registry`` that live in the same Krita window as ``widget``."""
    return [w for w in _alive_widgets(registry) if w.parentWidget() is widget.parentWidget()]


COMMIT_TOOL = "KritaShape/KisToolBrush"


def _commit_pending_strokes(*docs):
    """Finishes open move/transform strokes so their result is in the layer pixels.

    Krita only applies a move or transform when the stroke ends (switching tool,
    Enter, deselect); until then pixelData() returns the old pixels. Switching
    tool briefly ends the stroke; the previous tool is then restored. The active
    tool is read from the toolbox, whose buttons are named after their tool ids.
    """
    app = Krita.instance()
    window = app.activeWindow()
    if window is None:
        return
    current = None
    for button in window.qwindow().findChildren(QToolButton):
        name = button.objectName()
        if button.isCheckable() and button.isChecked() and "/" in name and app.action(name) is not None:
            current = name
            break
    commit = app.action(COMMIT_TOOL)
    if commit is None or current == COMMIT_TOOL:
        return
    commit.trigger()
    QApplication.processEvents()
    for doc in docs:
        if doc is not None:
            doc.waitForDone()
    if current is not None:
        app.action(current).trigger()


def _read(node, x, y, w, h, channels, dtype):
    data = node.pixelData(x, y, w, h)
    return np.frombuffer(bytes(data), dtype=dtype).reshape(h, w, channels).copy()


def _write(node, arr, x, y):
    h, w = arr.shape[:2]
    node.setPixelData(QByteArray(np.ascontiguousarray(arr).tobytes()), x, y, w, h)


def _spans(x0, x1, width):
    """Splits columns x0..x1, which may run past the right edge, into (image x, offset, length) pieces."""
    spans = []
    x = x0
    while x < x1:
        start = x % width
        length = min(x1 - x, width - start)
        spans.append((start, x - x0, length))
        x += length
    return spans


def _read_region(source, region, width, channels, dtype):
    """Pixels of a layer or document (its merged image) in a region from projection.view_region."""
    x0, y0, x1, y1 = region
    parts = [_read(source, start, y0, length, y1 - y0, channels, dtype) for start, _, length in _spans(x0, x1, width)]
    return parts[0] if len(parts) == 1 else np.concatenate(parts, axis=1)


def _changed_rect(mask, view, margin=1):
    """Bounding box (left, top, right, bottom) of the changed view pixels, grown by ``margin``."""
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    return (max(0, int(cols[0]) - margin), max(0, int(rows[0]) - margin),
            min(view.width, int(cols[-1]) + 1 + margin), min(view.height, int(rows[-1]) + 1 + margin))


def _changed_boxes(undo_pixels, width):
    """Panorama areas a write-back touched, as (x, y, w, h): one box per side of the ±180° seam."""
    boxes = {}
    for _, r0, c0, old in undo_pixels:
        h, w = old.shape[:2]
        key = c0 < width // 2
        box = (c0, r0, c0 + w, r0 + h)
        boxes[key] = _union(box, boxes.get(key))
    return [(x0, y0, x1 - x0, y1 - y0) for x0, y0, x1, y1 in boxes.values()]


def _quick_thumbnail(doc, width, height):
    """A downscaled copy of an 8-bit RGBA document's merged image, or None for other formats.

    Reads two rows per thumbnail row and lets Qt average them down, which takes a
    fraction of a second where Document.thumbnail(), which reads every pixel, takes
    seconds for 10K+ panoramas.
    """
    if doc.colorModel() != "RGBA" or doc.colorDepth() != "U8" or doc.height() < 2 * height:
        return None
    w, h = doc.width(), doc.height()
    rows = np.minimum(h - 1, ((np.arange(2 * height) + 0.5) * h / (2 * height)).astype(int))
    doc.waitForDone()
    data = b"".join(bytes(doc.pixelData(0, int(y), w, 1)) for y in rows)  # BGRA, the layout of ARGB32
    image = QImage(data, w, len(rows), w * 4, QImage.Format.Format_ARGB32)
    return image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _read_setting(name, default):
    """A value saved with _write_setting, converted to the type of ``default``; ``default`` if missing or invalid."""
    try:
        text = Krita.instance().readSetting(SETTINGS_GROUP, name, "")
    except (AttributeError, RuntimeError):
        return default
    if not text:
        return default
    if isinstance(default, bool):
        return text == "true"
    try:
        return type(default)(float(text))
    except ValueError:
        return default


def _write_setting(name, value):
    """Saves a panel setting in Krita's settings, so it comes back the next time Krita starts."""
    text = ("true" if value else "false") if isinstance(value, bool) else repr(value)
    try:
        Krita.instance().writeSetting(SETTINGS_GROUP, name, text)
    except (AttributeError, RuntimeError):
        pass


def _fingerprint(doc):
    """A checksum of evenly spread rows of the merged image: cheap, and changes when the image does.

    A change that falls entirely between sampled rows (a stroke only a few pixels
    high on a large image) is noticed with the next change that doesn't.
    """
    w, h = doc.width(), doc.height()
    digest = hashlib.blake2b(digest_size=16)
    for y in np.unique(np.linspace(0, h - 1, min(h, WATCH_ROWS)).astype(int)):
        digest.update(bytes(doc.pixelData(0, int(y), w, 1)))
    return digest.digest()


def _union(a, b):
    return a if b is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _reference_margin(view, width):
    """View pixels around a write-back whose reference can change: one panorama pixel
    where the view is densest (its corners), plus the bilinear neighbours."""
    half_diagonal = math.hypot(view.width, view.height) / 2.0
    stretch = 1.0 + (half_diagonal / view.focal) ** 2  # 1 / cos² of the corner angle
    return 2 + int(math.ceil(view.focal * 2.0 * math.pi / width * stretch))


def _doc_id(doc):
    """Stable identity for a document; None if it is missing or has been closed."""
    if doc is None:
        return None
    try:
        root = doc.rootNode()
    except RuntimeError:  # the underlying Qt object has already been deleted
        return None
    return root.uniqueId() if root is not None else None


def _paint_layers(doc, skip=None):
    """The paint layers of a document, bottom first, as (node, shown).

    Layers inside groups are included; ``shown`` is False when the layer or one of
    its groups is hidden. A layer named ``skip`` (the view's reference) is left out.
    """
    found = []

    def walk(parent, shown):
        for child in parent.childNodes():
            visible = shown and child.visible()
            if child.type() == "paintlayer" and child.name() != skip:
                found.append((child, visible))
            elif child.type() == "grouplayer":
                walk(child, visible)

    walk(doc.rootNode(), True)
    return found


def _find_node(doc, unique_id):
    """The node with the given unique id anywhere in the document, or None."""
    def walk(parent):
        for child in parent.childNodes():
            if child.uniqueId() == unique_id:
                return child
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(doc.rootNode())


def _digest(pixels):
    return hashlib.blake2b(np.ascontiguousarray(pixels), digest_size=16).digest()


class Link:
    """A view layer's panorama layer, and what the view layer looked like when projected or last written back.

    Only changes against that state are written back. It is kept compressed – most
    layers are largely empty – with a checksum that tells an unchanged layer
    without unpacking it.
    """

    def __init__(self, src_id, pixels):
        self.src_id = src_id
        self.digest = _digest(pixels)
        self._packed = zlib.compress(np.ascontiguousarray(pixels), 1)
        self._shape, self._dtype = pixels.shape, pixels.dtype

    def baseline(self):
        return np.frombuffer(zlib.decompress(self._packed), dtype=self._dtype).reshape(self._shape)

    @property
    def nbytes(self):
        return len(self._packed)


class GroupLink:
    """A view group's panorama group; groups hold no pixels, only structure."""

    nbytes = 0

    def __init__(self, src_id):
        self.src_id = src_id


def _copy_look(src, node):
    """Gives a view layer or group the look of its panorama counterpart."""
    node.setOpacity(src.opacity())
    node.setBlendingMode(src.blendingMode())
    node.setVisible(src.visible())
    if hasattr(src, "passThroughMode") and hasattr(node, "setPassThroughMode"):
        node.setPassThroughMode(src.passThroughMode())


def _create(doc, name, kind):
    """A new paint layer or group. Groups need createGroupLayer: createNode gives a plain Node
    without the group functions."""
    if kind == "grouplayer":
        return doc.createGroupLayer(name)
    return doc.createNode(name, kind)


_SESSION_NUMBERS = itertools.count(1)


class Session:
    """An ongoing projection: the panorama, the view, and which view layer mirrors which panorama layer."""

    def __init__(self, src_doc, view_doc, view, dtype, channels, links):
        self.src_doc = src_doc
        self.view_doc = view_doc
        self.view = view
        self.dtype = dtype
        self.channels = channels
        self.links = links  # view layer unique id -> Link
        self.number = next(_SESSION_NUMBERS)  # tells undo steps made from this view apart


class PreviewDocker(DockWidget):
    """The 360° preview as its own docker, so it can float as a movable, resizable window.

    It holds no state: the SpherePaint docker in the same window feeds it and
    receives its mouse input.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("SpherePaint 360° Preview"))
        self.view = PanoramaPreview(self)
        self.view.setToolTip(tr("360° preview of the projection: drag to look around, scroll to zoom"))
        self.view.directionChanged.connect(lambda yaw, pitch: self._main("_on_picker_drag", yaw, pitch))
        self.view.directionPicked.connect(lambda yaw, pitch: self._main("_on_picker_release", yaw, pitch))
        self.view.fovStep.connect(lambda steps: self._main("_step_fov", steps))
        self.setWidget(self.view)
        # The floating window's own border is thin; a corner grip is easier to grab. It
        # resizes the top-level window, so it is only shown while the docker floats.
        self.grip = QSizeGrip(self.view)
        self.grip.setFixedSize(22, 22)
        self.grip.setStyleSheet("background: rgba(0, 0, 0, 110); border-top-left-radius: 5px;")
        self.grip.setToolTip(tr("Drag to resize the preview window"))
        self.topLevelChanged.connect(lambda _floating: self._place_grip())
        self.view.installEventFilter(self)
        _PREVIEWS.add(self)

    def _place_grip(self):
        # A dock that is not yet in a main window also counts as floating, so check the parent too.
        floating = self.isFloating() and self.parentWidget() is not None
        self.grip.setVisible(floating)
        self.grip.move(self.view.width() - self.grip.width(), self.view.height() - self.grip.height())

    def eventFilter(self, watched, event):
        if watched is self.view and event.type() in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show):
            self._place_grip()
        return False

    def _main(self, method, *args):
        dockers = _same_window(_DOCKERS, self)
        if dockers:
            getattr(dockers[0], method)(*args)

    def showEvent(self, event):
        super().showEvent(event)
        dockers = _same_window(_DOCKERS, self)
        if dockers:
            dockers[0].push_to_preview(self)

    def canvasChanged(self, canvas):
        pass  # the SpherePaint docker refreshes the preview


class SphereDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(TITLE)
        self.session = None
        self._last_source_id = None  # panorama whose stored direction was last restored
        self._preview_image = None  # downscaled panorama shared with the 360° preview docker
        self._thumbnail_doc = None  # id of the document it was made from
        self.aspect = 1.0  # projection width / height
        self.history = {}  # panorama id -> undo steps for its write-backs, oldest first (see apply)
        _DOCKERS.add(self)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Direction: thumbnail, then yaw / pitch / field of view on one row.
        self.yaw = self._spin(-180, 180, 0, 15, "°", wrapping=True)
        self.pitch = self._spin(-90, 90, 0, 15, "°")
        self.fov = self._spin(20, 150, 90, 10, "°", decimals=0)
        self.yaw.setToolTip(tr("Yaw – turn left and right"))
        self.pitch.setToolTip(tr("Pitch – tilt up and down"))
        self.fov.setToolTip(tr("Field of view"))

        self.picker = DirectionPicker()
        self.picker.setToolTip(tr("Click or drag to choose the direction; scroll to change the field of view"))
        self.picker.directionChanged.connect(self._on_picker_drag)
        self.picker.directionPicked.connect(self._on_picker_release)
        self.picker.fovStep.connect(self._step_fov)
        for spin in (self.yaw, self.pitch, self.fov):
            spin.valueChanged.connect(self._sync_picker)
        layout.addWidget(self.picker)

        direction = QHBoxLayout()
        direction.setSpacing(4)
        for text, spin in (("Yaw", self.yaw), ("Pitch", self.pitch), ("FOV", self.fov)):
            label = QLabel(text)  # standard abbreviations, kept untranslated; tooltips explain them
            label.setToolTip(spin.toolTip())
            direction.addWidget(label)
            direction.addWidget(spin, 1)
        layout.addLayout(direction)

        quick = QHBoxLayout()
        quick.setSpacing(2)
        for label, yaw, pitch in (("Front", 0, 0), ("Right", 90, 0), ("Back", 180, 0),
                                  ("Left", -90, 0), ("Up", None, 90), ("Down", None, -90)):
            b = QToolButton()
            b.setText(tr(label))
            b.setAutoRaise(True)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, y=yaw, p=pitch: self._preset(y, p))
            quick.addWidget(b)
        layout.addLayout(quick)

        # Main actions: large, side by side.
        main = QHBoxLayout()
        self.btn_project = QPushButton(tr("Project"))
        self.btn_project.setToolTip(tr("Creates/updates an undistorted perspective view of the image, with all its paint layers"))
        self.btn_project.clicked.connect(self.project)
        self.btn_apply = QPushButton(tr("Write back"))
        self.btn_apply.setToolTip(
            tr("Transfers what changed in the view's layers to the same layers of the equirectangular image"))
        self.btn_apply.clicked.connect(self.apply)
        for button, icon in ((self.btn_project, "tool_perspectivegrid"), (self.btn_apply, "merge-layer-below")):
            button.setMinimumHeight(32)
            _set_icon(button, icon)
            main.addWidget(button)
        layout.addLayout(main)

        # Secondary actions and the menu with everything used less often.
        secondary = QHBoxLayout()
        # Push buttons centre their icon and text; stretched tool buttons left-align them.
        self.btn_undo = QPushButton(tr("Undo"))
        self.btn_undo.setToolTip(tr("Undo last write-back"))
        _set_icon(self.btn_undo, "edit-undo")
        self.btn_undo.clicked.connect(self.undo_apply)
        self.btn_toggle = QPushButton(tr("Flat / Projection"))
        _set_icon(self.btn_toggle, "view-refresh")
        self.btn_toggle.setToolTip(tr("Switches between the flat image and the projection"))
        self.btn_toggle.clicked.connect(self.toggle_view)
        for button in (self.btn_undo, self.btn_toggle):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            secondary.addWidget(button)
        self.btn_more = QToolButton()
        self.btn_more.setText("⋯")
        self.btn_more.setToolTip(tr("More: preview, guide layer, cube maps, export and settings"))
        self.btn_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_more.setMinimumWidth(36)
        self.btn_more.setIcon(_menu_icon(self.btn_more))
        self.btn_more.setMenu(self._build_menu())
        secondary.addWidget(self.btn_more)
        # The icon makes Undo taller than the others; give the whole row one height.
        row_height = max(b.sizeHint().height() for b in (self.btn_undo, self.btn_toggle, self.btn_more))
        for button in (self.btn_undo, self.btn_toggle, self.btn_more):
            button.setFixedHeight(row_height)
        layout.addLayout(secondary)

        self.status = QLabel(tr("Open an equirectangular image (2:1) and press 'Project'."))
        self.status.setWordWrap(True)
        font = self.status.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.9))
        self.status.setFont(font)
        self.status.setEnabled(False)  # drawn greyed out, as secondary information
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.setWidget(root)
        self._update_buttons()
        # Watching the panorama for direct edits (see _watch_panorama).
        self._watch = None  # (document id, fingerprint, time of the last change, refresh due)
        self._watch_timer = QtCore.QTimer(self)
        self._watch_timer.setInterval(WATCH_INTERVAL_MS)
        self._watch_timer.timeout.connect(self._watch_panorama)
        self._load_settings()

    def _load_settings(self):
        """Restores the panel's settings from the last session and saves them whenever they change.

        The view direction is not among them: it is stored in each panorama instead.
        """
        self.fov.setValue(_read_setting("fov", self.fov.value()))
        self.project_on_release.setChecked(_read_setting("projectOnRelease", self.project_on_release.isChecked()))
        self.auto_size.setChecked(_read_setting("autoViewSize", self.auto_size.isChecked()))
        self.view_size.setValue(_read_setting("viewSize", self.view_size.value()))
        aspect = _read_setting("aspectRatio", self.aspect)
        self.set_aspect(min(self.aspect_actions, key=lambda v: abs(v - aspect)))

        self.fov.valueChanged.connect(lambda value: _write_setting("fov", float(value)))
        self.project_on_release.toggled.connect(lambda on: _write_setting("projectOnRelease", on))
        self.auto_size.toggled.connect(lambda on: _write_setting("autoViewSize", on))
        self.live_thumbnail.setChecked(_read_setting("liveThumbnail", False))
        self._live_toggled(self.live_thumbnail.isChecked())
        self.live_thumbnail.toggled.connect(lambda on: _write_setting("liveThumbnail", on))
        self.live_thumbnail.toggled.connect(self._live_toggled)
        self.view_size.valueChanged.connect(lambda value: _write_setting("viewSize", int(value)))

    def _build_menu(self):
        menu = QMenu(self)
        self.act_preview = menu.addAction(tr("Open 360° preview"), self.open_preview)
        self.act_preview.setToolTip(tr("Opens the 360° preview as a window you can move and resize"))
        self.act_guide = menu.addAction(tr("Add guide layer"), self.add_guide)
        self.act_guide.setToolTip(tr("Adds a layer with a labelled grid (front, right, back, left, top, bottom)"))
        cube = menu.addMenu(tr("Cube map"))
        cube.setToolTipsVisible(True)
        self.act_export_cube = cube.addAction(tr("Export cube map…"), self.export_cube)
        self.act_export_cube.setToolTip(tr("Saves the panorama as six cube faces (front, right, back, left, top, bottom)"))
        self.act_import_cube = cube.addAction(tr("Import cube map…"), self.import_cube)
        self.act_import_cube.setToolTip(tr("Builds a panorama from six cube faces; choose the *_front image"))
        self.act_export_360 = menu.addAction(tr("Export for 360° viewers…"), self.export_360)
        self.act_export_360.setToolTip(tr("Saves a JPEG with 360° metadata (GPano), recognised by Facebook, "
                                          "Google Photos, Kuula and other viewers"))
        menu.addSeparator()
        aspect_menu = menu.addMenu(tr("Aspect ratio"))
        group = QActionGroup(self)
        group.setExclusive(True)
        self.aspect_actions = {}
        for label, value in ASPECT_RATIOS:
            action = aspect_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == self.aspect)
            action.triggered.connect(lambda _=False, v=value: self.set_aspect(v))
            group.addAction(action)
            self.aspect_actions[value] = action
        self.project_on_release = menu.addAction(tr("Project when the mouse is released"))
        self.project_on_release.setCheckable(True)
        self.live_thumbnail = menu.addAction(tr("Update the thumbnail and preview while painting in the panorama"))
        self.live_thumbnail.setCheckable(True)
        self.live_thumbnail.setToolTip(tr("Checks the panorama for changes every second and refreshes the thumbnail "
                                          "and the 360° preview once you have stopped painting"))
        self.auto_size = menu.addAction(tr("Automatic view size (same density as the image)"))
        self.auto_size.setCheckable(True)
        self.auto_size.setChecked(True)
        self.act_view_size = menu.addAction(tr("View size…"), self._ask_view_size)
        self.act_view_size.setEnabled(False)
        self.auto_size.toggled.connect(lambda on: self.act_view_size.setEnabled(not on))
        menu.setToolTipsVisible(True)
        # Kept as a value holder for the custom view size (not shown in the panel).
        self.view_size = QSpinBox(self)
        self.view_size.setRange(256, 8192)
        self.view_size.setValue(1024)
        self.view_size.hide()
        return menu

    def set_aspect(self, value):
        """Sets the projection's aspect ratio (width / height); the field of view stays horizontal."""
        self.aspect = value
        _write_setting("aspectRatio", value)
        action = self.aspect_actions.get(value)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        self._sync_picker()

    def _ask_view_size(self):
        value, ok = QInputDialog.getInt(self, TITLE, tr("View size in pixels:"), self.view_size.value(), 256, 8192, 128)
        if ok:
            self.view_size.setValue(value)

    def canvasChanged(self, canvas):
        self._update_buttons()
        self._restore_direction()
        self._refresh_thumbnail()

    # --- remembered direction -------------------------------------------------

    def _save_direction(self, doc):
        """Stores yaw, pitch and field of view in the document (kept in .kra files)."""
        data = {"yaw": self.yaw.value(), "pitch": self.pitch.value(), "fov": self.fov.value(), "aspect": self.aspect}
        try:
            doc.setAnnotation(ANNOTATION, "SpherePaint view direction", QByteArray(json.dumps(data).encode()))
        except (AttributeError, RuntimeError):  # older Krita without annotations, or closed document
            pass

    def _restore_direction(self):
        """Restores the direction stored in a panorama when switching to it from another panorama."""
        doc = Krita.instance().activeDocument()
        if doc is None or (self._alive() and _doc_id(doc) == _doc_id(self.session.view_doc)):
            return  # the projection view itself keeps the current values
        doc_id = _doc_id(doc)
        if doc_id == self._last_source_id:
            return
        self._last_source_id = doc_id
        try:
            data = json.loads(bytes(doc.annotation(ANNOTATION)) or b"null")
        except (AttributeError, RuntimeError, ValueError):
            return
        if isinstance(data, dict):
            for spin, key in ((self.yaw, "yaw"), (self.pitch, "pitch"), (self.fov, "fov")):
                if isinstance(data.get(key), (int, float)):
                    spin.setValue(float(data[key]))
            if isinstance(data.get("aspect"), (int, float)):
                # Snap to the closest preset so the menu always shows a checked ratio.
                self.set_aspect(min(self.aspect_actions, key=lambda v: abs(v - data["aspect"])))

    # --- mouse control ------------------------------------------------------

    def _step_fov(self, steps):
        self.fov.setValue(self.fov.value() + 5 * steps)

    def _sync_picker(self, *_):
        self.picker.setView(self.yaw.value(), self.pitch.value(), self.fov.value(), self.aspect)
        for preview in self._previews():
            preview.view.setView(self.yaw.value(), self.pitch.value(), self.fov.value(), self.aspect)

    def _on_picker_drag(self, yaw, pitch):
        self.yaw.setValue(yaw)
        self.pitch.setValue(pitch)

    def _on_picker_release(self, yaw, pitch):
        self._on_picker_drag(yaw, pitch)
        if self.project_on_release.isChecked() and (self._alive() or Krita.instance().activeDocument()):
            self.project()

    def _thumbnail_source(self):
        """The equirectangular image to show in the picker: the session's source, else the active image."""
        if self._alive():
            return self.session.src_doc
        doc = Krita.instance().activeDocument()
        if doc is not None and abs(doc.width() - 2 * doc.height()) <= 1:
            return doc
        return None

    def _refresh_thumbnail(self):
        """Updates the picker thumbnail and the 360° preview from the current panorama."""
        doc = self._thumbnail_source()
        try:
            image = None
            if doc is not None:
                image = (_quick_thumbnail(doc, PREVIEW_SOURCE_WIDTH, PREVIEW_SOURCE_WIDTH // 2)
                         or doc.thumbnail(PREVIEW_SOURCE_WIDTH, PREVIEW_SOURCE_WIDTH // 2))
        except RuntimeError:  # the document was closed meanwhile
            image = None
        self._preview_image = image
        self._thumbnail_doc = _doc_id(doc) if image is not None else None
        self.picker.setImage(image.scaled(512, 256) if image is not None else None)
        for preview in self._previews():
            preview.view.setSource(image)

    def _live_toggled(self, on):
        self._watch = None
        if on:
            self._watch_timer.start()
        else:
            self._watch_timer.stop()

    def _watch_panorama(self, now=None):
        """Refreshes the thumbnail and the 360° preview after the panorama has been painted in directly.

        Krita has no signal for "the image changed", so the active panorama is sampled
        every second. A change is acted on only once the image has stayed the same for
        a moment, so the thumbnail isn't remade while a stroke or a filter is still going.
        """
        now = time.monotonic() if now is None else now
        doc = Krita.instance().activeDocument()
        if doc is None or (self._alive() and _doc_id(doc) == _doc_id(self.session.view_doc)):
            return  # the view is refreshed by write-back
        source = self._thumbnail_source()
        if source is None or _doc_id(source) != _doc_id(doc):
            return  # not a panorama
        try:
            fingerprint = _fingerprint(doc)
        except RuntimeError:  # closed meanwhile
            return
        doc_id = _doc_id(doc)
        if self._watch is None or self._watch[0] != doc_id:
            self._watch = (doc_id, fingerprint, now, False)  # a new document: just remember it
        elif fingerprint != self._watch[1]:
            self._watch = (doc_id, fingerprint, now, True)
        elif self._watch[3] and now - self._watch[2] >= WATCH_QUIET_S:
            self._watch = (doc_id, fingerprint, now, False)
            self._refresh_thumbnail()

    def _patch_thumbnail(self, boxes):
        """Redraws only the given panorama areas in the thumbnail and preview, instead of letting
        Krita downscale the whole panorama again (seconds for 10K+ images). Returns False when
        that isn't possible, so the caller makes a full thumbnail."""
        image = self._preview_image
        doc = self._thumbnail_source()
        if image is None or image.isNull() or doc is None or _doc_id(doc) != self._thumbnail_doc:
            return False
        if not boxes:
            return True  # nothing in the panorama changed
        if doc.colorModel() != "RGBA" or doc.colorDepth() != "U8":
            return False
        width, height = doc.width(), doc.height()
        sx, sy = image.width() / width, image.height() / height
        try:
            doc.waitForDone()
            image = image.convertToFormat(QImage.Format.Format_ARGB32)
            painter = QPainter(image)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            for x, y, w, h in boxes:
                # Whole thumbnail pixels, and the panorama area they are made from.
                tx0, ty0 = int(math.floor(x * sx)), int(math.floor(y * sy))
                tx1 = min(image.width(), int(math.ceil((x + w) * sx)))
                ty1 = min(image.height(), int(math.ceil((y + h) * sy)))
                px0, py0 = int(round(tx0 / sx)), int(round(ty0 / sy))
                px1, py1 = min(width, int(round(tx1 / sx))), min(height, int(round(ty1 / sy)))
                if tx1 <= tx0 or ty1 <= ty0 or px1 <= px0 or py1 <= py0:
                    continue
                data = bytes(doc.pixelData(px0, py0, px1 - px0, py1 - py0))  # BGRA, the layout of ARGB32
                part = QImage(data, px1 - px0, py1 - py0, (px1 - px0) * 4, QImage.Format.Format_ARGB32)
                painter.drawImage(tx0, ty0, part.scaled(tx1 - tx0, ty1 - ty0, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
            painter.end()
        except RuntimeError:  # the document was closed meanwhile
            return False
        self._preview_image = image
        self.picker.setImage(image.scaled(512, 256))
        for preview in self._previews():
            preview.view.setSource(image)
        return True

    # --- 360° preview window ------------------------------------------------

    def _previews(self):
        return _same_window(_PREVIEWS, self)

    def push_to_preview(self, preview):
        """Gives a (newly shown) preview docker the current panorama and direction."""
        preview.view.setSource(self._preview_image)
        preview.view.setView(self.yaw.value(), self.pitch.value(), self.fov.value(), self.aspect)

    def open_preview(self):
        previews = self._previews()
        if not previews:
            self._fail(tr("The 360° preview docker is not available. Enable it under Settings → Dockers → {name}.",
                          name=tr("SpherePaint 360° Preview")))
            return
        preview = previews[0]
        if not preview.isVisible():
            preview.setFloating(True)
            preview.resize(480, 480)
        preview.show()
        preview.raise_()
        self.push_to_preview(preview)

    # --- helpers ------------------------------------------------------------

    def _spin(self, lo, hi, value, step, suffix, wrapping=False, decimals=1):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setSingleStep(step)
        s.setValue(value)
        s.setSuffix(suffix)
        s.setWrapping(wrapping)
        return s

    def _preset(self, yaw, pitch):
        if yaw is not None:
            self.yaw.setValue(yaw)
        self.pitch.setValue(pitch)

    def _open_ids(self):
        return {_doc_id(d) for d in Krita.instance().documents()}

    def _alive(self):
        s = self.session
        if not s:
            return False
        ids = self._open_ids() - {None}
        alive = _doc_id(s.src_doc) in ids and _doc_id(s.view_doc) in ids
        if not alive:
            self.session = None  # a document was closed; start over on the next projection
        return alive

    def _update_buttons(self):
        alive = self._alive()
        self.btn_apply.setEnabled(alive)
        self.btn_toggle.setEnabled(alive)
        steps = len(self._undo_steps()) if alive else 0
        self.btn_undo.setEnabled(steps > 0)
        self.btn_undo.setToolTip(tr("Undo last write-back ({count} available)", count=steps) if steps
                                 else tr("Undo last write-back"))

    def _show(self, doc):
        if not doc:
            return
        app = Krita.instance()
        target = _doc_id(doc)
        for window in app.windows():
            for view in window.views():
                if _doc_id(view.document()) == target:
                    window.showView(view)
                    view.setVisible()
                    return
        app.activeWindow().addView(doc)

    def _mirror(self, doc, view_doc, view, channels, dtype, active_src_id=None):
        """Fills the view with the panorama's paint layers and groups, seen in the view's direction.

        The view's layers are rebuilt with the panorama's structure: every paint layer
        and group, in the same order and groups, with its name, opacity, blending mode
        and visibility. Below them lies the locked, hidden reference: the whole merged
        image, which also shows what can't be mirrored, such as filter or vector layers.
        Returns the links for write-back and the view layer mirroring ``active_src_id``.
        """
        w, h = doc.width(), doc.height()
        doc.waitForDone()
        region = P.view_region(view, w, h)
        origin = (region[0], region[1], w, h)
        root = view_doc.rootNode()
        for child in root.childNodes():
            if child.name() != REFERENCE_LAYER:
                child.remove()
        ref = view_doc.nodeByName(REFERENCE_LAYER)
        if ref is None:
            ref = view_doc.createNode(REFERENCE_LAYER, "paintlayer")
            root.addChildNode(ref, None)
            ref.setVisible(False)

        mirrors, links, active = [], {}, None

        def build(src_parent, view_parent, above):
            for src in src_parent.childNodes():
                kind = src.type()
                if kind not in ("paintlayer", "grouplayer"):
                    continue  # filter, fill, vector … layers are only in the reference
                node = _create(view_doc, src.name(), kind)
                view_parent.addChildNode(node, above)
                _copy_look(src, node)
                above = node
                if kind == "grouplayer":
                    links[node.uniqueId()] = GroupLink(src.uniqueId())
                    build(src, node, None)
                else:
                    mirrors.append((node, src))

        build(doc.rootNode(), root, ref)
        empty = np.zeros((view.height, view.width, channels), dtype=dtype)
        reference = None
        for start in range(0, max(1, len(mirrors)), MIRROR_BATCH):
            batch = mirrors[start:start + MIRROR_BATCH]
            busy = [(node, src) for node, src in batch if src.bounds().width() > 0]  # empty layers stay empty
            images = [_read_region(src, region, w, channels, dtype) for _, src in busy]
            if reference is None:
                images.append(_read_region(doc, region, w, channels, dtype))
            projected = P.equirect_to_views(images, view, origin) if images else []
            if reference is None:
                reference = projected.pop()
            contents = dict(zip((id(node) for node, _ in busy), projected))
            for node, src in batch:
                content = contents.get(id(node), empty)
                if content is not empty:
                    _write(node, content, 0, 0)
                links[node.uniqueId()] = Link(src.uniqueId(), content)
                if src.uniqueId() == active_src_id:
                    active = node
        ref.setLocked(False)
        _write(ref, reference, 0, 0)
        ref.setLocked(True)
        view_doc.refreshProjection()
        if active is None and mirrors:
            active = mirrors[-1][0]
        return links, active

    def toggle_view(self):
        """Switches between the flat equirectangular image and the projection."""
        if not self._alive():
            self._fail(tr("No active projection. Press 'Project view' first."))
            return
        active = _doc_id(Krita.instance().activeDocument())
        on_view = active == _doc_id(self.session.view_doc)
        self._show(self.session.src_doc if on_view else self.session.view_doc)

    def _close_view(self, view_doc):
        """Closes a replaced projection without a save prompt; unapplied changes were handled before."""
        try:
            view_doc.setModified(False)
            view_doc.close()
        except RuntimeError:  # already closed by the user
            pass

    def _busy(self, text):
        self.status.setText(text)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    def _done(self, text, changed=None):
        """Finishes an operation; ``changed`` lists the panorama areas (x, y, w, h) it modified, if known."""
        QApplication.restoreOverrideCursor()
        self.status.setText(text)
        self._update_buttons()
        if changed is None or not self._patch_thumbnail(changed):
            self._refresh_thumbnail()

    def _fail(self, text):
        QApplication.restoreOverrideCursor()
        self.status.setText(text)
        QMessageBox.warning(self, TITLE, text)
        self._update_buttons()

    # --- project ------------------------------------------------------------

    def project(self):
        app = Krita.instance()
        doc = app.activeDocument()
        active_src_id = None
        if self._alive() and _doc_id(doc) == _doc_id(self.session.view_doc):
            # When the view is active, keep the same panorama and the layer mirrored by the active one.
            link = self.session.links.get(doc.activeNode().uniqueId()) if doc.activeNode() else None
            active_src_id = link.src_id if link else None
            doc = self.session.src_doc
        elif doc is not None and doc.activeNode() is not None:
            active_src_id = doc.activeNode().uniqueId()
        if doc is None:
            self._fail(tr("No image is open."))
            return
        _commit_pending_strokes(doc, self.session.view_doc if self._alive() else None)
        if self._alive():
            # Asked for any panorama: the current view may be replaced and closed below.
            if self._has_unapplied_changes():
                answer = QMessageBox.question(
                    self, TITLE,
                    tr("The view has changes that haven't been written back. Write them back first? "
                       "Otherwise they are discarded."),
                    YES | NO | CANCEL)
                if answer == CANCEL:
                    return
                if answer == YES:
                    self.apply()

        dtype = DTYPES.get(doc.colorDepth())
        if dtype is None:
            self._fail(tr("Colour depth {depth} is not supported.", depth=doc.colorDepth()))
            return
        w, h = doc.width(), doc.height()
        if abs(w - 2 * h) > 1:
            answer = QMessageBox.question(
                self, TITLE,
                tr("The image is {w}×{h}, not 2:1. Equirectangular images are usually 2:1. Continue anyway?",
                   w=w, h=h))
            if answer != YES:
                return

        fov = self.fov.value()
        size = P.matching_view_size(w, fov) if self.auto_size.isChecked() else self.view_size.value()
        if self.auto_size.isChecked():
            self.view_size.setValue(size)
        height = P.view_height(size, self.aspect)
        view = P.View(self.yaw.value(), self.pitch.value(), fov, size, height)
        layers = _paint_layers(doc)
        channels = len(layers[0][0].channels()) if layers else CHANNELS.get(doc.colorModel(), 4)

        self._busy(tr("Projecting…"))
        try:
            previous = self.session if self._alive() else None
            same_source = previous is not None and _doc_id(doc) == _doc_id(previous.src_doc)
            reuse = (same_source and previous.view_doc.width() == size
                     and previous.view_doc.height() == height)
            if reuse:
                view_doc = previous.view_doc
            else:
                view_doc = app.createDocument(size, height, tr("Sphere view – {name}", name=doc.name() or tr("untitled")),
                                              doc.colorModel(), doc.colorDepth(), doc.colorProfile(),
                                              doc.resolution())
            links, active = self._mirror(doc, view_doc, view, channels, dtype, active_src_id)

            self.session = Session(doc, view_doc, view, dtype, channels, links)
            for step in self.history.get(_doc_id(doc), []):
                step["links"] = None  # only meaningful for their own view
            if not reuse:
                app.activeWindow().addView(view_doc)
                if previous is not None:
                    self._close_view(previous.view_doc)  # keep a single projection open
            else:
                self._show(view_doc)
            if active is not None:
                view_doc.setActiveNode(active)
        except Exception as e:  # show the error in the panel instead of crashing Krita
            self._fail(tr("Projection failed: {error}", error=e))
            return
        self._save_direction(doc)
        self._last_source_id = _doc_id(doc)
        self._done(tr("View {width}×{height} px, yaw {yaw:.0f}°, pitch {pitch:.0f}°, FOV {fov:.0f}°. "
                      "Paint in any of its layers, then press 'Write back'.",
                      width=size, height=height, yaw=self.yaw.value(), pitch=self.pitch.value(), fov=fov),
                   [])  # projecting leaves the panorama unchanged

    # --- guide layer --------------------------------------------------------

    def add_guide(self):
        doc = self._thumbnail_source()
        if doc is None:
            self._fail(tr("Open an equirectangular image (2:1) first."))
            return
        if doc.colorModel() != "RGBA" or doc.colorDepth() not in guide.SUPPORTED_DEPTHS:
            self._fail(tr("The guide layer needs an RGBA image with 8 or 16 bits per channel."))
            return
        previous_layer = doc.activeNode()
        self._busy(tr("Creating guide layer…"))
        try:
            pixels = guide.build_guide(doc.width(), doc.height(), doc.colorDepth())
            node = doc.createNode(tr("SpherePaint guide"), "paintlayer")
            root = doc.rootNode()
            children = root.childNodes()
            root.addChildNode(node, children[-1] if children else None)  # on top of everything
            _write(node, pixels, 0, 0)
            node.setOpacity(180)
            doc.refreshProjection()
            if previous_layer is not None:
                doc.setActiveNode(previous_layer)  # keep painting where the user was
        except Exception as e:
            self._fail(tr("Creating the guide layer failed: {error}", error=e))
            return
        self._done(tr("Guide layer added. Hide or delete it like any other layer."))

    # --- cube map -----------------------------------------------------------

    def export_cube(self):
        doc = self._thumbnail_source()
        if doc is None:
            self._fail(tr("Open an equirectangular image (2:1) first."))
            return
        dtype = DTYPES.get(doc.colorDepth())
        if dtype is None:
            self._fail(tr("Colour depth {depth} is not supported.", depth=doc.colorDepth()))
            return
        folder = QFileDialog.getExistingDirectory(self, tr("Export cube map to folder"))
        if not folder:
            return
        base = os.path.splitext(os.path.basename(doc.fileName() or ""))[0] or "panorama"
        ext = ".exr" if doc.colorDepth().startswith("F") else ".png"
        paths = {name: os.path.join(folder, f"{base}_{name}{ext}") for name, _, _ in P.CUBE_FACES}
        existing = [p for p in paths.values() if os.path.exists(p)]
        if existing and QMessageBox.question(
                self, TITLE, tr("{count} of the files already exist. Overwrite them?", count=len(existing))
        ) != YES:
            return
        self._busy(tr("Exporting cube map…"))
        try:
            w, h = doc.width(), doc.height()
            merged = np.frombuffer(bytes(doc.pixelData(0, 0, w, h)), dtype=dtype).reshape(h, w, -1)
            face_size = P.matching_view_size(w, 90)
            for name, face in P.equirect_to_cube(merged, face_size):
                self._save_image(face, paths[name], doc)
        except Exception as e:
            self._fail(tr("Exporting the cube map failed: {error}", error=e))
            return
        self._done(tr("Cube map exported: six {size}×{size} px faces in {folder}.", size=face_size, folder=folder))

    def export_360(self):
        doc = self._thumbnail_source()
        if doc is None:
            self._fail(tr("Open an equirectangular image (2:1) first."))
            return
        base = os.path.splitext(doc.fileName() or "panorama")[0]
        path, _ = QFileDialog.getSaveFileName(self, tr("Export for 360° viewers"), base + "_360.jpg",
                                              tr("JPEG image (*.jpg *.jpeg)"))
        if not path:
            return
        if not path.lower().endswith((".jpg", ".jpeg")):
            path += ".jpg"
        self._busy(tr("Exporting…"))
        batch = doc.batchmode()
        try:
            info = InfoObject()
            info.setProperty("quality", 95)
            doc.setBatchmode(True)  # no export dialog
            doc.waitForDone()
            if not doc.exportImage(path, info):
                raise OSError(path)
            with open(path, "rb") as f:
                data = f.read()
            with open(path, "wb") as f:
                f.write(xmp.add_gpano(data, doc.width(), doc.height()))
        except Exception as e:
            self._fail(tr("Exporting failed: {error}", error=e))
            return
        finally:
            doc.setBatchmode(batch)
        self._done(tr("Saved {file} with 360° metadata.", file=os.path.basename(path)))

    def _save_image(self, pixels, path, like):
        """Writes pixels to an image file through a temporary Krita document with ``like``'s colour space."""
        height, width = pixels.shape[:2]
        app = Krita.instance()
        tmp = app.createDocument(width, height, os.path.basename(path), like.colorModel(),
                                 like.colorDepth(), like.colorProfile(), like.resolution())
        try:
            layer = tmp.rootNode().childNodes()[0]
            _write(layer, pixels, 0, 0)
            tmp.refreshProjection()
            tmp.setBatchmode(True)
            if not tmp.exportImage(path, InfoObject()):
                raise OSError(path)
        finally:
            tmp.setModified(False)
            tmp.close()

    def import_cube(self):
        front, _ = QFileDialog.getOpenFileName(
            self, tr("Choose the front face of the cube map"), "",
            tr("Images (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.kra *.webp)"))
        if not front:
            return
        stem, ext = os.path.splitext(front)
        if not stem.lower().endswith("_front"):
            self._fail(tr("Choose the file whose name ends in _front."))
            return
        prefix = stem[:-len("_front")]
        paths = {name: f"{prefix}_{name}{ext}" for name, _, _ in P.CUBE_FACES}
        missing = [os.path.basename(p) for p in paths.values() if not os.path.exists(p)]
        if missing:
            self._fail(tr("Missing cube faces: {files}", files=", ".join(missing)))
            return
        self._busy(tr("Importing cube map…"))
        app = Krita.instance()
        opened = []
        try:
            faces = []
            first = None
            for name, yaw, pitch in P.CUBE_FACES:
                face_doc = app.openDocument(paths[name])
                if face_doc is None:
                    raise OSError(paths[name])
                opened.append(face_doc)
                face_doc.waitForDone()
                first = first or face_doc
                if face_doc.width() != face_doc.height() or face_doc.width() != first.width():
                    raise ValueError(tr("All faces must be square and the same size."))
                if (face_doc.colorModel(), face_doc.colorDepth()) != (first.colorModel(), first.colorDepth()):
                    raise ValueError(tr("All faces must have the same colour model and depth."))
                dtype = DTYPES.get(face_doc.colorDepth())
                if dtype is None:
                    raise ValueError(tr("Colour depth {depth} is not supported.", depth=face_doc.colorDepth()))
                n = face_doc.width()
                pixels = np.frombuffer(bytes(face_doc.pixelData(0, 0, n, n)), dtype=dtype)
                pixels = pixels.reshape(n, n, -1)
                faces.append((P.View(yaw, pitch, 90, n), pixels))
            width, height = P.equirect_size_for_faces(first.width())
            equirect = P.cube_to_equirect(faces, width, height)
            name = os.path.basename(prefix) or "panorama"
            doc = app.createDocument(width, height, name, first.colorModel(), first.colorDepth(),
                                     first.colorProfile(), first.resolution())
            _write(doc.rootNode().childNodes()[0], equirect, 0, 0)
            doc.refreshProjection()
        except Exception as e:
            self._fail(tr("Importing the cube map failed: {error}", error=e))
            return
        finally:
            for face_doc in opened:
                face_doc.setModified(False)
                face_doc.close()
        app.activeWindow().addView(doc)
        self._done(tr("Panorama {width}×{height} px created from the cube map.", width=width, height=height))

    # --- write back ---------------------------------------------------------

    def _pending_changes(self):
        """What would be written back: [(view layer, its Link or None for a new layer, pixels, mask)].

        A new layer counts only while it is visible – hidden new layers are sketches.
        """
        s = self.session
        width, height = s.view.width, s.view.height
        pending = []
        for node, shown in _paint_layers(s.view_doc, skip=REFERENCE_LAYER):
            link = s.links.get(node.uniqueId())
            if link is None and not shown:
                continue
            pixels = _read(node, 0, 0, width, height, s.channels, s.dtype)
            if link is not None and _digest(pixels) == link.digest:
                continue  # untouched
            before = link.baseline() if link is not None else np.zeros_like(pixels)
            mask = P.change_mask(before, pixels)
            if mask.any():
                pending.append((node, link, pixels, mask))
        return pending

    def _has_unapplied_changes(self):
        if not self._alive():
            return False
        try:
            return bool(self._pending_changes())
        except LookupError:
            return True  # let the user decide; write-back will explain the problem

    def apply(self):
        if not self._alive():
            self._fail(tr("No active projection. Press 'Project view' first."))
            return
        s = self.session
        self._busy(tr("Writing back…"))
        try:
            _commit_pending_strokes(s.view_doc, s.src_doc)
            pending = self._pending_changes()
            if not pending:
                self._done(tr("Nothing has changed in the view since the last projection."))
                return
            # Resolve every target layer before touching any pixels. A new view layer, or one
            # whose panorama layer has been deleted, gets a new panorama layer (see _place).
            targets = []
            for node, link, pixels, mask in pending:
                target = _find_node(s.src_doc, link.src_id) if link is not None else None
                if target is not None and target.type() != "paintlayer":
                    target = None
                targets.append(target)

            refresh = None  # the part of the view whose reference can change
            margin = _reference_margin(s.view, s.src_doc.width())
            for _, _, _, mask in pending:
                refresh = _union(_changed_rect(mask, s.view, margin), refresh)
            undo = {"pixels": [], "created": [], "session": s.number, "refresh": refresh,
                    "links": {node.uniqueId(): link for node, link, _, _ in pending}}
            touched = []
            changed = 0
            for (node, link, pixels, mask), target in zip(pending, targets):
                if target is None:
                    target = s.src_doc.createNode(node.name(), "paintlayer")
                    self._place(target, node, undo["created"], undo["links"])
                changed += self._write_layer_back(target, pixels, mask, undo["pixels"])
                touched.append(target.name())
                s.links[node.uniqueId()] = Link(target.uniqueId(), pixels)
            s.src_doc.refreshProjection()
            self._push_undo(undo)
            self._refresh_reference(refresh)
        except Exception as e:
            self._fail(tr("Write-back failed: {error}", error=e))
            return
        self._done(tr("Done: {count} pixels updated in {layers}.",
                      count=f"{changed:,}".replace(",", " "),
                      layers=", ".join(f"'{t}'" for t in touched)),
                   _changed_boxes(undo["pixels"], s.src_doc.width()))

    def _panorama_parent(self, view_parent, created, undo_links):
        """The panorama group (or root) matching a view group, created when the group is new."""
        s = self.session
        if view_parent.uniqueId() == s.view_doc.rootNode().uniqueId():
            return s.src_doc.rootNode()
        link = s.links.get(view_parent.uniqueId())
        group = _find_node(s.src_doc, link.src_id) if link is not None else None
        if group is None:
            group = _create(s.src_doc, view_parent.name(), "grouplayer")
            self._place(group, view_parent, created, undo_links)
            undo_links.setdefault(view_parent.uniqueId(), link)
            s.links[view_parent.uniqueId()] = GroupLink(group.uniqueId())
        return group

    def _place(self, target, view_node, created, undo_links):
        """Puts a new panorama layer or group where its view counterpart is: in the matching
        group, just above the panorama counterpart of the nearest layer below it there, or at
        the bottom of that group. New groups in the view become new panorama groups.

        The pending changes are handled bottom first, so new layers created in one
        write-back stack up in the view's order.
        """
        s = self.session
        view_parent = view_node.parentNode()
        parent = self._panorama_parent(view_parent, created, undo_links)
        siblings = view_parent.childNodes()
        position = next(i for i, n in enumerate(siblings) if n.uniqueId() == view_node.uniqueId())
        below = None
        for sibling in reversed(siblings[:position]):
            link = s.links.get(sibling.uniqueId())
            below = _find_node(s.src_doc, link.src_id) if link is not None else None
            if below is not None:
                break
        parent.addChildNode(target, below)
        created.append(target)

    def _refresh_reference(self, rect=None):
        """Re-projects the merged panorama into the view's locked reference layer.

        ``rect`` (left, top, right, bottom) limits it to the part of the view that can have changed.
        """
        s = self.session
        ref = s.view_doc.nodeByName(REFERENCE_LAYER)
        if ref is None:
            return
        w, h = s.src_doc.width(), s.src_doc.height()
        rect = rect or (0, 0, s.view.width, s.view.height)
        s.src_doc.waitForDone()  # make sure the merged image includes the write-back
        region = P.view_region(s.view, w, h, rect)
        merged = _read_region(s.src_doc, region, w, s.channels, s.dtype)
        locked = ref.locked()
        ref.setLocked(False)
        _write(ref, P.equirect_to_view(merged, s.view, (region[0], region[1], w, h), rect), rect[0], rect[1])
        ref.setLocked(locked)
        s.view_doc.refreshProjection()

    def _write_layer_back(self, target, painted, mask, undo_pixels):
        """Projects one view layer's changes onto a panorama layer; returns the pixel count."""
        s = self.session
        w, h = s.src_doc.width(), s.src_doc.height()
        # Only the panorama pixels under the painted part of the view can change.
        x0, y0, x1, y1 = P.view_region(s.view, w, h, _changed_rect(mask, s.view))
        changed = 0
        for r0 in range(y0, y1, P.CHUNK_ROWS):
            r1 = min(y1, r0 + P.CHUNK_ROWS)
            result = P.view_to_equirect_rows(painted, mask, s.view, w, h, r0, r1, x0, x1)
            if result is None:
                continue
            weight, colour = result
            cols = np.nonzero((weight > 0).any(axis=0))[0]
            c0, c1 = int(cols[0]), int(cols[-1]) + 1
            for start, offset, length in _spans(x0 + c0, x0 + c1, w):
                part = slice(c0 + offset, c0 + offset + length)
                old = _read(target, start, r0, length, r1 - r0, s.channels, s.dtype)
                wgt = weight[:, part, None]
                new = old.astype(np.float32) * (1 - wgt) + colour[:, part] * wgt
                _write(target, P._to_dtype(new, s.dtype), start, r0)
                undo_pixels.append((target, r0, start, old))
            changed += int((weight > 0).sum())
        return changed

    # --- undo ---------------------------------------------------------------

    def _undo_steps(self):
        """The undo history of the current panorama (oldest first); empty without a projection."""
        if self.session is None:
            return []
        return self.history.get(_doc_id(self.session.src_doc), [])

    def _push_undo(self, step):
        """Adds a write-back to the history, dropping the oldest steps beyond the limits."""
        open_ids = self._open_ids()
        for doc_id in [d for d in self.history if d not in open_ids]:
            del self.history[doc_id]  # the panorama was closed
        steps = self.history.setdefault(_doc_id(self.session.src_doc), [])
        steps.append(step)

        def size(entry):
            total = sum(old.nbytes for _, _, _, old in entry["pixels"])
            return total + sum(link.nbytes for link in (entry["links"] or {}).values() if link is not None)

        while len(steps) > 1 and (len(steps) > UNDO_STEPS or sum(size(e) for e in steps) > UNDO_BYTES):
            steps.pop(0)

    def undo_apply(self):
        """Undoes the latest write-back of the current panorama; repeat to go further back."""
        if not self._alive() or not self._undo_steps():
            return
        s = self.session
        steps = self._undo_steps()
        step = steps[-1]
        same_view = step["session"] == s.number and step["links"] is not None
        if not same_view:
            # Made from another view: the open view shows the panorama with that write-back,
            # so it is projected again, which replaces what has been painted there since.
            _commit_pending_strokes(s.view_doc)
            if self._has_unapplied_changes():
                answer = QMessageBox.question(
                    self, TITLE,
                    tr("This write-back was made from another view. Undoing it projects the current view "
                       "again, which discards the changes in it that haven't been written back. Continue?"))
                if answer != YES:
                    return
        steps.pop()
        self._busy(tr("Undoing…"))
        try:
            for target, r0, c0, old in reversed(step["pixels"]):
                _write(target, old, c0, r0)
            for node in reversed(step["created"]):  # layers before the new groups holding them
                node.remove()
            s.src_doc.refreshProjection()
            if same_view:
                # The view still contains the painting, so compare against the state before the
                # write-back; a layer whose panorama layer was just removed counts as new again.
                for view_id, link in step["links"].items():
                    if link is None:
                        s.links.pop(view_id, None)
                    else:
                        s.links[view_id] = link
                self._refresh_reference(step["refresh"])
                text = tr("Write-back undone in the equirectangular image ({count} more can be undone). "
                          "The view is unchanged – press 'Write back' again to redo it.", count=len(steps))
            else:
                self._reproject_session()
                text = tr("Write-back undone in the equirectangular image ({count} more can be undone). "
                          "The view has been projected again.", count=len(steps))
        except Exception as e:
            self._fail(tr("Undo failed: {error}", error=e))
            return
        self._done(text, _changed_boxes(step["pixels"], s.src_doc.width()))

    def _reproject_session(self):
        """Projects the panorama into the open view again, in the same direction."""
        s = self.session
        active = s.view_doc.activeNode()
        link = s.links.get(active.uniqueId()) if active is not None else None
        s.links, node = self._mirror(s.src_doc, s.view_doc, s.view, s.channels, s.dtype,
                                     link.src_id if link else None)
        if node is not None:
            s.view_doc.setActiveNode(node)
