"""Docker panel: switches between the equirectangular image (flat) and a perspective view."""
import json
import os
import weakref

import numpy as np
from krita import DockWidget, InfoObject, Krita

from . import guide, xmp
from . import projection as P
from .i18n import tr
from .picker import DirectionPicker
from .preview import PanoramaPreview
from .qt import (
    QApplication, QByteArray, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QSizeGrip, QSpinBox, Qt, QtCore, QToolButton, QVBoxLayout, QWidget,
)

YES, NO, CANCEL = (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No,
                   QMessageBox.StandardButton.Cancel)

DTYPES = {"U8": np.uint8, "U16": np.uint16, "F16": np.float16, "F32": np.float32}
TITLE = "SpherePaint"  # product name, not translated
PREVIEW_SOURCE_WIDTH = 2048  # downscaled panorama used by the thumbnail and the 360° preview
ANNOTATION = "spherepaint"  # document annotation holding the last view direction
PAINT_LAYER = tr("Paint here")
REFERENCE_LAYER = tr("Reference (whole image)")


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


def _doc_id(doc):
    """Stable identity for a document; None if it is missing or has been closed."""
    if doc is None:
        return None
    try:
        root = doc.rootNode()
    except RuntimeError:  # the underlying Qt object has already been deleted
        return None
    return root.uniqueId() if root is not None else None


def _extra_view_layers(view_doc):
    """Visible paint layers in the view besides 'Paint here' and the reference.

    Each is written back to the panorama layer with the same name.
    """
    return [n for n in view_doc.rootNode().childNodes()
            if n.type() == "paintlayer" and n.visible()
            and n.name() not in (PAINT_LAYER, REFERENCE_LAYER)]


def _find_paint_node(view_doc):
    """The layer to write back, looked up fresh each time.

    Merging layers in Krita replaces the node, so a stored reference goes stale.
    Prefer the layer named PAINT_LAYER; otherwise, if exactly one paint layer
    besides the reference exists (e.g. after a merge that kept another name),
    use it and give it the expected name. Returns None if it is ambiguous.
    """
    candidates = [n for n in view_doc.rootNode().childNodes()
                  if n.type() == "paintlayer" and n.name() != REFERENCE_LAYER]
    for node in candidates:
        if node.name() == PAINT_LAYER:
            return node
    if len(candidates) == 1:
        candidates[0].setName(PAINT_LAYER)
        return candidates[0]
    return None


class Session:
    """An ongoing projection: the source image, the view, and what the view looked like originally."""

    def __init__(self, src_doc, src_node, view_doc, view_node, view, dtype, channels, baseline):
        self.src_doc = src_doc
        self.src_node = src_node
        self.view_doc = view_doc
        self.view_node = view_node
        self.view = view
        self.dtype = dtype
        self.channels = channels
        self.baseline = baseline
        self.extra_baselines = {}  # extra view layer name -> pixels at the last write-back
        self.undo = None  # state needed to undo the last write-back (see apply)


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
        _DOCKERS.add(self)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        form = QFormLayout()

        self.yaw = self._spin(-180, 180, 0, 15, " °", wrapping=True)
        self.pitch = self._spin(-90, 90, 0, 15, " °")
        self.fov = self._spin(20, 150, 90, 10, " °")
        self.size = QSpinBox()
        self.size.setRange(256, 8192)
        self.size.setSingleStep(128)
        self.size.setSuffix(" px")
        self.auto_size = QCheckBox(tr("Auto (same density as the image)"))
        self.auto_size.setChecked(True)
        self.size.setEnabled(False)
        self.auto_size.toggled.connect(lambda on: self.size.setEnabled(not on))

        self.picker = DirectionPicker()
        self.picker.setToolTip(tr("Click or drag to choose the direction; scroll to change the field of view"))
        self.picker.directionChanged.connect(self._on_picker_drag)
        self.picker.directionPicked.connect(self._on_picker_release)
        self.picker.fovStep.connect(lambda steps: self.fov.setValue(self.fov.value() + 5 * steps))
        self.project_on_release = QCheckBox(tr("Project when the mouse is released"))
        self.project_on_release.setChecked(True)
        for spin in (self.yaw, self.pitch, self.fov):
            spin.valueChanged.connect(self._sync_picker)
        layout.addWidget(self.picker)

        self.btn_preview = QPushButton(tr("Open 360° preview"))
        self.btn_preview.setToolTip(tr("Opens the 360° preview as a window you can move and resize"))
        self.btn_preview.clicked.connect(self.open_preview)
        layout.addWidget(self.btn_preview)
        layout.addWidget(self.project_on_release)

        form.addRow(tr("Yaw"), self.yaw)
        form.addRow(tr("Pitch"), self.pitch)
        form.addRow(tr("Field of view"), self.fov)
        form.addRow(tr("View size"), self.size)
        form.addRow("", self.auto_size)
        layout.addLayout(form)

        quick = QHBoxLayout()
        for label, yaw, pitch in (("Front", 0, 0), ("Right", 90, 0), ("Back", 180, 0),
                                  ("Left", -90, 0), ("Up", None, 90), ("Down", None, -90)):
            b = QPushButton(tr(label))
            b.setMinimumWidth(10)
            b.clicked.connect(lambda _=False, y=yaw, p=pitch: self._preset(y, p))
            quick.addWidget(b)
        layout.addLayout(quick)

        self.btn_project = QPushButton(tr("Project view"))
        self.btn_project.setToolTip(tr("Creates/updates an undistorted perspective view of the active layer"))
        self.btn_project.clicked.connect(self.project)
        self.btn_apply = QPushButton(tr("Write back to sphere"))
        self.btn_apply.setToolTip(
            tr("Transfers what changed in the layer '{layer}' to the equirectangular image", layer=PAINT_LAYER))
        self.btn_apply.clicked.connect(self.apply)
        self.btn_undo = QPushButton(tr("Undo last write-back"))
        self.btn_undo.clicked.connect(self.undo_apply)
        layout.addWidget(self.btn_project)
        layout.addWidget(self.btn_apply)
        layout.addWidget(self.btn_undo)

        toggle = QHBoxLayout()
        self.btn_flat = QPushButton(tr("Show flat"))
        self.btn_flat.clicked.connect(lambda: self._show(self.session and self.session.src_doc))
        self.btn_proj = QPushButton(tr("Show projection"))
        self.btn_proj.clicked.connect(lambda: self._show(self.session and self.session.view_doc))
        toggle.addWidget(self.btn_flat)
        toggle.addWidget(self.btn_proj)
        layout.addLayout(toggle)

        self.btn_guide = QPushButton(tr("Add guide layer"))
        self.btn_guide.setToolTip(tr("Adds a layer with a labelled grid (front, right, back, left, top, bottom)"))
        self.btn_guide.clicked.connect(self.add_guide)
        layout.addWidget(self.btn_guide)

        cube = QHBoxLayout()
        self.btn_export_cube = QPushButton(tr("Export cube map…"))
        self.btn_export_cube.setToolTip(tr("Saves the panorama as six cube faces (front, right, back, left, top, bottom)"))
        self.btn_export_cube.clicked.connect(self.export_cube)
        self.btn_import_cube = QPushButton(tr("Import cube map…"))
        self.btn_import_cube.setToolTip(tr("Builds a panorama from six cube faces; choose the *_front image"))
        self.btn_import_cube.clicked.connect(self.import_cube)
        cube.addWidget(self.btn_export_cube)
        cube.addWidget(self.btn_import_cube)
        layout.addLayout(cube)

        self.btn_export_360 = QPushButton(tr("Export for 360° viewers…"))
        self.btn_export_360.setToolTip(tr("Saves a JPEG with 360° metadata (GPano), recognised by Facebook, "
                                          "Google Photos, Kuula and other viewers"))
        self.btn_export_360.clicked.connect(self.export_360)
        layout.addWidget(self.btn_export_360)

        self.status = QLabel(tr("Open an equirectangular image (2:1) and select the layer you want to paint on."))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.setWidget(root)
        self._update_buttons()

    def canvasChanged(self, canvas):
        self._update_buttons()
        self._restore_direction()
        self._refresh_thumbnail()

    # --- remembered direction -------------------------------------------------

    def _save_direction(self, doc):
        """Stores yaw, pitch and field of view in the document (kept in .kra files)."""
        data = {"yaw": self.yaw.value(), "pitch": self.pitch.value(), "fov": self.fov.value()}
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

    # --- mouse control ------------------------------------------------------

    def _step_fov(self, steps):
        self.fov.setValue(self.fov.value() + 5 * steps)

    def _sync_picker(self, *_):
        self.picker.setView(self.yaw.value(), self.pitch.value(), self.fov.value())
        for preview in self._previews():
            preview.view.setView(self.yaw.value(), self.pitch.value(), self.fov.value())

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
            image = doc.thumbnail(PREVIEW_SOURCE_WIDTH, PREVIEW_SOURCE_WIDTH // 2) if doc is not None else None
        except RuntimeError:  # the document was closed meanwhile
            image = None
        self._preview_image = image
        self.picker.setImage(image.scaled(512, 256) if image is not None else None)
        for preview in self._previews():
            preview.view.setSource(image)

    # --- 360° preview window ------------------------------------------------

    def _previews(self):
        return _same_window(_PREVIEWS, self)

    def push_to_preview(self, preview):
        """Gives a (newly shown) preview docker the current panorama and direction."""
        preview.view.setSource(self._preview_image)
        preview.view.setView(self.yaw.value(), self.pitch.value(), self.fov.value())

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

    def _spin(self, lo, hi, value, step, suffix, wrapping=False):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(1)
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
        self.btn_flat.setEnabled(alive)
        self.btn_proj.setEnabled(alive)
        self.btn_undo.setEnabled(alive and bool(self.session.undo))

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

    def _fill_extra_layers(self, doc, node, view_doc, view, w, h, channels, dtype):
        """Shows each extra view layer's same-named panorama layer from the new direction.

        Returns the baselines for write-back: the projected content, so only what the
        user changes is transferred. Layers without a panorama counterpart start empty.
        """
        baselines = {}
        size = view.size
        for layer in _extra_view_layers(view_doc):
            source = doc.nodeByName(layer.name())
            if (source is not None and source.type() == "paintlayer"
                    and source.uniqueId() != node.uniqueId()):
                content = P.equirect_to_view(_read(source, 0, 0, w, h, channels, dtype), view)
                baselines[layer.name()] = content
            else:
                content = np.zeros((size, size, channels), dtype=dtype)
            _write(layer, content, 0, 0)
        return baselines

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

    def _done(self, text):
        QApplication.restoreOverrideCursor()
        self.status.setText(text)
        self._update_buttons()
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
        node = doc.activeNode() if doc else None
        if self._alive() and _doc_id(doc) == _doc_id(self.session.view_doc):
            # When the view is active, keep the same source image and layer.
            doc, node = self.session.src_doc, self.session.src_node
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

        if node is None or node.type() != "paintlayer":
            self._fail(tr("Select a regular paint layer in the equirectangular image."))
            return
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
        size = P.matching_view_size(w, fov) if self.auto_size.isChecked() else self.size.value()
        if self.auto_size.isChecked():
            self.size.setValue(size)
        view = P.View(self.yaw.value(), self.pitch.value(), fov, size)
        channels = len(node.channels())

        self._busy(tr("Projecting…"))
        try:
            layer_px = _read(node, 0, 0, w, h, channels, dtype)
            projected = P.equirect_to_view(layer_px, view)
            merged = np.frombuffer(bytes(doc.pixelData(0, 0, w, h)), dtype=dtype).reshape(h, w, channels)
            reference = P.equirect_to_view(merged, view)
            del layer_px, merged

            previous = self.session if self._alive() else None
            same_source = previous is not None and _doc_id(doc) == _doc_id(previous.src_doc)
            reuse = same_source and previous.view_doc.width() == size
            if reuse:
                view_doc = self.session.view_doc
                view_node = _find_paint_node(view_doc)
                if view_node is None:
                    view_node = view_doc.createNode(PAINT_LAYER, "paintlayer")
                    view_doc.rootNode().addChildNode(view_node, None)
                ref_node = view_doc.nodeByName(REFERENCE_LAYER)
            else:
                view_doc = app.createDocument(size, size, tr("Sphere view – {name}", name=doc.name() or tr("untitled")),
                                              doc.colorModel(), doc.colorDepth(), doc.colorProfile(),
                                              doc.resolution())
                root = view_doc.rootNode()
                for child in root.childNodes():
                    root.removeChildNode(child)
                ref_node = view_doc.createNode(REFERENCE_LAYER, "paintlayer")
                view_node = view_doc.createNode(PAINT_LAYER, "paintlayer")
                root.addChildNode(ref_node, None)
                root.addChildNode(view_node, ref_node)
                # Recreate the previous view's extra layers so their names carry over.
                above = view_node
                for name in ([l.name() for l in _extra_view_layers(previous.view_doc)] if previous else []):
                    extra = view_doc.createNode(name, "paintlayer")
                    root.addChildNode(extra, above)
                    above = extra
            if ref_node is not None:
                ref_node.setLocked(False)
                _write(ref_node, reference, 0, 0)
                ref_node.setLocked(True)
            _write(view_node, projected, 0, 0)
            extra_baselines = self._fill_extra_layers(doc, node, view_doc, view, w, h, channels, dtype)
            view_doc.refreshProjection()

            same_layer = same_source and previous.src_node.uniqueId() == node.uniqueId()
            undo = previous.undo if same_layer else None
            self.session = Session(doc, node, view_doc, view_node, view, dtype, channels, projected)
            self.session.extra_baselines = extra_baselines
            self.session.undo = undo
            if not reuse:
                app.activeWindow().addView(view_doc)
                if previous is not None:
                    self._close_view(previous.view_doc)  # keep a single projection open
            else:
                self._show(view_doc)
            view_doc.setActiveNode(view_node)
        except Exception as e:  # show the error in the panel instead of crashing Krita
            self._fail(tr("Projection failed: {error}", error=e))
            return
        self._save_direction(doc)
        self._last_source_id = _doc_id(doc)
        self._done(tr("View {size}×{size} px, yaw {yaw:.0f}°, pitch {pitch:.0f}°, FOV {fov:.0f}°. "
                      "Paint in the layer '{layer}', then press 'Write back'.",
                      size=size, yaw=self.yaw.value(), pitch=self.pitch.value(), fov=fov, layer=PAINT_LAYER))

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

    def _current_view_pixels(self):
        s = self.session
        node = _find_paint_node(s.view_doc)
        if node is None:
            raise LookupError(tr("Cannot tell which layer to write back. Merge your layers into one "
                                 "layer named '{layer}'.", layer=PAINT_LAYER))
        s.view_node = node
        n = s.view.size
        return _read(node, 0, 0, n, n, s.channels, s.dtype)

    def _pending_changes(self):
        """What would be written back: [(view layer name or None for 'Paint here', pixels, mask)]."""
        s = self.session
        n = s.view.size
        painted = self._current_view_pixels()
        pending = []
        mask = P.change_mask(s.baseline, painted)
        if mask.any():
            pending.append((None, painted, mask))
        for layer in _extra_view_layers(s.view_doc):
            pixels = _read(layer, 0, 0, n, n, s.channels, s.dtype)
            base = s.extra_baselines.get(layer.name())
            mask = P.change_mask(np.zeros_like(pixels) if base is None else base, pixels)
            if mask.any():
                pending.append((layer.name(), pixels, mask))
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
            # Resolve every target layer before touching any pixels.
            targets = []
            for name, pixels, mask in pending:
                if name is None:
                    targets.append((s.src_node, False))
                    continue
                existing = s.src_doc.nodeByName(name)
                if existing is not None and existing.type() != "paintlayer":
                    raise LookupError(tr("The layer '{layer}' in the panorama is not a paint layer.", layer=name))
                targets.append((existing, False) if existing is not None else (None, True))

            undo = {"pixels": [], "created": [], "baseline": s.baseline,
                    "extra_baselines": dict(s.extra_baselines)}
            touched = []
            changed = 0
            for (name, pixels, mask), (target, create) in zip(pending, targets):
                if create:
                    target = s.src_doc.createNode(name, "paintlayer")
                    s.src_node.parentNode().addChildNode(target, s.src_node)  # just above the source layer
                    undo["created"].append(target)
                changed += self._write_layer_back(target, pixels, mask, undo["pixels"])
                touched.append(target.name())
                if name is None:
                    s.baseline = pixels
                else:
                    s.extra_baselines[name] = pixels
            s.src_doc.refreshProjection()
            s.src_doc.setActiveNode(s.src_node)
            s.undo = undo
            self._refresh_reference()
        except Exception as e:
            self._fail(tr("Write-back failed: {error}", error=e))
            return
        self._done(tr("Done: {count} pixels updated in {layers}.",
                      count=f"{changed:,}".replace(",", " "),
                      layers=", ".join(f"'{t}'" for t in touched)))

    def _refresh_reference(self):
        """Re-projects the merged panorama into the view's locked reference layer."""
        s = self.session
        ref = s.view_doc.nodeByName(REFERENCE_LAYER)
        if ref is None:
            return
        w, h = s.src_doc.width(), s.src_doc.height()
        s.src_doc.waitForDone()  # make sure the merged image includes the write-back
        merged = np.frombuffer(bytes(s.src_doc.pixelData(0, 0, w, h)), dtype=s.dtype).reshape(h, w, -1)
        locked = ref.locked()
        ref.setLocked(False)
        _write(ref, P.equirect_to_view(merged, s.view), 0, 0)
        ref.setLocked(locked)
        s.view_doc.refreshProjection()

    def _write_layer_back(self, target, painted, mask, undo_pixels):
        """Projects one view layer's changes onto a panorama layer; returns the pixel count."""
        s = self.session
        w, h = s.src_doc.width(), s.src_doc.height()
        changed = 0
        for r0 in range(0, h, P.CHUNK_ROWS):
            r1 = min(h, r0 + P.CHUNK_ROWS)
            result = P.view_to_equirect_rows(painted, mask, s.view, w, h, r0, r1)
            if result is None:
                continue
            weight, colour = result
            cols = np.nonzero((weight > 0).any(axis=0))[0]
            c0, c1 = int(cols[0]), int(cols[-1]) + 1
            old = _read(target, c0, r0, c1 - c0, r1 - r0, s.channels, s.dtype)
            wgt = weight[:, c0:c1, None]
            new = old.astype(np.float32) * (1 - wgt) + colour[:, c0:c1] * wgt
            _write(target, P._to_dtype(new, s.dtype), c0, r0)
            undo_pixels.append((target, r0, c0, old))
            changed += int((weight > 0).sum())
        return changed

    def undo_apply(self):
        if not self._alive() or not self.session.undo:
            return
        s = self.session
        undo = s.undo
        for target, r0, c0, old in reversed(undo["pixels"]):
            _write(target, old, c0, r0)
        for node in undo["created"]:
            node.remove()
        s.src_doc.refreshProjection()
        # The view still contains the painting, so compare against the state before the write-back.
        s.baseline = undo["baseline"]
        s.extra_baselines = undo["extra_baselines"]
        s.undo = None
        self._refresh_reference()
        self._done(tr("The last write-back has been undone in the equirectangular image. "
                      "The view is unchanged – press 'Write back' again to redo it."))
