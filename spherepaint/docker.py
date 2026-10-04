"""Docker panel: switches between the equirectangular image (flat) and a perspective view."""
import numpy as np
from krita import DockWidget, Krita
from PyQt5.QtCore import QByteArray, Qt
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from . import projection as P
from .i18n import tr

DTYPES = {"U8": np.uint8, "U16": np.uint16, "F16": np.float16, "F32": np.float32}
TITLE = "SpherePaint"  # product name, not translated
PAINT_LAYER = tr("Paint here")
REFERENCE_LAYER = tr("Reference (whole image)")


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
        self.undo = []  # [(y, x, old pixels)] from the last write-back


class SphereDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(TITLE)
        self.session = None

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

        self.status = QLabel(tr("Open an equirectangular image (2:1) and select the layer you want to paint on."))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.setWidget(root)
        self._update_buttons()

    def canvasChanged(self, canvas):
        self._update_buttons()

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

    def _busy(self, text):
        self.status.setText(text)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()

    def _done(self, text):
        QApplication.restoreOverrideCursor()
        self.status.setText(text)
        self._update_buttons()

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
        if self._alive() and _doc_id(doc) == _doc_id(self.session.src_doc):
            if self._has_unapplied_changes():
                answer = QMessageBox.question(
                    self, TITLE,
                    tr("The view has changes that haven't been written back. Write them back first?"),
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
                if answer == QMessageBox.Cancel:
                    return
                if answer == QMessageBox.Yes:
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
            if answer != QMessageBox.Yes:
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

            reuse = (self._alive() and _doc_id(doc) == _doc_id(self.session.src_doc)
                     and self.session.view_doc.width() == size)
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
            if ref_node is not None:
                ref_node.setLocked(False)
                _write(ref_node, reference, 0, 0)
                ref_node.setLocked(True)
            _write(view_node, projected, 0, 0)
            view_doc.refreshProjection()

            same_layer = reuse and self.session.src_node.uniqueId() == node.uniqueId()
            undo = self.session.undo if same_layer else []
            self.session = Session(doc, node, view_doc, view_node, view, dtype, channels, projected)
            self.session.undo = undo
            if not reuse:
                app.activeWindow().addView(view_doc)
            else:
                self._show(view_doc)
            view_doc.setActiveNode(view_node)
        except Exception as e:  # show the error in the panel instead of crashing Krita
            self._fail(tr("Projection failed: {error}", error=e))
            return
        self._done(tr("View {size}×{size} px, yaw {yaw:.0f}°, pitch {pitch:.0f}°, FOV {fov:.0f}°. "
                      "Paint in the layer '{layer}', then press 'Write back'.",
                      size=size, yaw=self.yaw.value(), pitch=self.pitch.value(), fov=fov, layer=PAINT_LAYER))

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

    def _has_unapplied_changes(self):
        if not self._alive():
            return False
        try:
            current = self._current_view_pixels()
        except LookupError:
            return True  # let the user decide; write-back will explain the problem
        return bool(P.change_mask(self.session.baseline, current).any())

    def apply(self):
        if not self._alive():
            self._fail(tr("No active projection. Press 'Project view' first."))
            return
        s = self.session
        self._busy(tr("Writing back…"))
        try:
            painted = self._current_view_pixels()
            mask = P.change_mask(s.baseline, painted)
            if not mask.any():
                self._done(tr("Nothing has changed in the view since the last projection."))
                return
            w, h = s.src_doc.width(), s.src_doc.height()
            undo = []
            changed = 0
            for r0 in range(0, h, P.CHUNK_ROWS):
                r1 = min(h, r0 + P.CHUNK_ROWS)
                result = P.view_to_equirect_rows(painted, mask, s.view, w, h, r0, r1)
                if result is None:
                    continue
                weight, color = result
                cols = np.nonzero((weight > 0).any(axis=0))[0]
                c0, c1 = int(cols[0]), int(cols[-1]) + 1
                old = _read(s.src_node, c0, r0, c1 - c0, r1 - r0, s.channels, s.dtype)
                wgt = weight[:, c0:c1, None]
                new = old.astype(np.float32) * (1 - wgt) + color[:, c0:c1] * wgt
                _write(s.src_node, P._to_dtype(new, s.dtype), c0, r0)
                undo.append((r0, c0, old))
                changed += int((weight > 0).sum())
            s.src_doc.refreshProjection()
            s.baseline = painted
            s.undo = undo
        except Exception as e:
            self._fail(tr("Write-back failed: {error}", error=e))
            return
        self._done(tr("Done: {count} pixels updated in '{layer}'.",
                      count=f"{changed:,}".replace(",", " "), layer=s.src_node.name()))

    def undo_apply(self):
        if not self._alive() or not self.session.undo:
            return
        s = self.session
        for r0, c0, old in s.undo:
            _write(s.src_node, old, c0, r0)
        s.src_doc.refreshProjection()
        s.undo = []
        # The view still contains the painting, so the next comparison must be against the state before it.
        s.baseline = P.equirect_to_view(
            _read(s.src_node, 0, 0, s.src_doc.width(), s.src_doc.height(), s.channels, s.dtype), s.view)
        self._done(tr("The last write-back has been undone in the equirectangular image. "
                      "The view is unchanged – press 'Write back' again to redo it."))
