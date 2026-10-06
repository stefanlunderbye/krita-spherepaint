"""The Qt parts of the plugin with PyQt5 (Krita 5) or PyQt6 (Krita 6).

Skipped when no PyQt is installed. Run locally with either binding installed:
    QT_QPA_PLATFORM=offscreen pytest tests/test_qt_compat.py
"""
import importlib.util
import os
import sys
import types

import numpy as np
import pytest

from conftest import PLUGIN_DIR

if importlib.util.find_spec("PyQt6") is None and importlib.util.find_spec("PyQt5") is None:
    pytest.skip("PyQt is not installed", allow_module_level=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_KEEP = []  # Qt windows must outlive the test: deleting them frees their dockers in C++


def keep(widget):
    _KEEP.append(widget)
    return widget


@pytest.fixture(scope="module")
def plugin():
    """Imports the plugin package with a stand-in for Krita's own module."""
    spec = importlib.util.spec_from_file_location("spherepaint_qt_standalone", PLUGIN_DIR / "qt.py")
    qt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qt)
    app = qt.QApplication.instance() or qt.QApplication([])

    class FakeKrita:
        @staticmethod
        def instance():
            return FakeKrita()

        def addDockWidgetFactory(self, factory):
            pass

        def addExtension(self, extension):
            pass

        def icon(self, name):
            return qt.QtGui.QIcon()

        settings = {}  # stands in for kritarc

        def readSetting(self, group, name, default):
            return FakeKrita.settings.get((group, name), default)

        def writeSetting(self, group, name, value):
            assert isinstance(value, str)
            FakeKrita.settings[(group, name)] = value

    class FakeExtension:
        def __init__(self, *args):
            pass

    krita = types.ModuleType("krita")
    krita.Krita = FakeKrita
    krita.Extension = FakeExtension
    krita.DockWidget = qt.QtWidgets.QDockWidget
    krita.InfoObject = object
    krita.DockWidgetFactory = lambda *args: None
    krita.DockWidgetFactoryBase = types.SimpleNamespace(DockRight=0)
    sys.modules["krita"] = krita
    sys.path.insert(0, str(PLUGIN_DIR.parent))
    import spherepaint
    yield types.SimpleNamespace(pkg=spherepaint, qt=spherepaint.qt, app=app)


def test_guide_face_renders(plugin):
    from spherepaint import guide
    face = guide.render_face("front", 128)
    assert face.shape == (128, 128, 4)
    assert (face[..., 3] > 0).any()


def test_picker_maps_mouse_to_yaw_and_pitch(plugin):
    from spherepaint.picker import DirectionPicker
    qt = plugin.qt
    picker = DirectionPicker()
    picker.resize(400, 200)
    picked = []
    picker.directionPicked.connect(lambda yaw, pitch: picked.append((yaw, pitch)))

    def send(kind, x, y):
        pos = qt.QPointF(x, y)
        button = qt.Qt.MouseButton.LeftButton
        held = qt.Qt.MouseButton.NoButton if kind == qt.QtCore.QEvent.Type.MouseButtonRelease else button
        mods = qt.Qt.KeyboardModifier.NoModifier
        event = (qt.QtGui.QMouseEvent(kind, pos, pos, button, held, mods) if qt.PYQT6
                 else qt.QtGui.QMouseEvent(kind, pos, button, held, mods))
        qt.QApplication.sendEvent(picker, event)

    send(qt.QtCore.QEvent.Type.MouseButtonPress, 300, 50)
    send(qt.QtCore.QEvent.Type.MouseButtonRelease, 300, 50)
    assert picked == [(90.0, 45.0)]


def test_qimage_to_bgra_keeps_pixels(plugin):
    from spherepaint.preview import qimage_to_bgra
    qt = plugin.qt
    image = qt.QImage(3, 2, qt.QImage.Format.Format_ARGB32)
    image.fill(qt.QColor(10, 20, 30, 255))
    pixels = qimage_to_bgra(image)
    assert pixels.shape == (2, 3, 4)
    assert tuple(pixels[1, 2]) == (30, 20, 10, 255)  # BGRA


def test_preview_renders_and_drag_turns_the_view(plugin):
    from spherepaint.preview import PanoramaPreview
    qt = plugin.qt
    source = qt.QImage(256, 128, qt.QImage.Format.Format_ARGB32)
    source.fill(qt.QColor(200, 100, 50, 255))
    preview = PanoramaPreview()
    preview.resize(200, 200)
    preview.setSource(source)
    preview.setView(0.0, 0.0, 90.0)
    assert not preview.grab().isNull()

    moves = []
    preview.directionChanged.connect(lambda yaw, pitch: moves.append((yaw, pitch)))

    def send(kind, x, y):
        pos = qt.QPointF(x, y)
        button = qt.Qt.MouseButton.LeftButton
        held = qt.Qt.MouseButton.NoButton if kind == qt.QtCore.QEvent.Type.MouseButtonRelease else button
        mods = qt.Qt.KeyboardModifier.NoModifier
        event = (qt.QtGui.QMouseEvent(kind, pos, pos, button, held, mods) if qt.PYQT6
                 else qt.QtGui.QMouseEvent(kind, pos, button, held, mods))
        qt.QApplication.sendEvent(preview, event)

    send(qt.QtCore.QEvent.Type.MouseButtonPress, 100, 100)
    send(qt.QtCore.QEvent.Type.MouseMove, 150, 100)  # drag right by a quarter of the width
    assert moves[-1] == (-22.5, 0.0)  # grab-and-drag: the view turns left


def test_docker_builds(plugin):
    docker = plugin.pkg.docker.SphereDocker()
    assert docker.btn_project.text() and docker.btn_apply.text()
    assert docker.act_export_cube.text() and docker.act_export_360.text()
    assert docker.project_on_release.isChecked() and docker.auto_size.isChecked()
    assert not docker.act_view_size.isEnabled()
    docker.auto_size.setChecked(False)
    assert docker.act_view_size.isEnabled()


def test_preview_docker_follows_the_docker_in_the_same_window(plugin):
    qt = plugin.qt
    window = keep(qt.QtWidgets.QMainWindow())
    main = plugin.pkg.docker.SphereDocker()
    preview = plugin.pkg.docker.PreviewDocker()
    window.addDockWidget(qt.Qt.DockWidgetArea.RightDockWidgetArea, main)
    window.addDockWidget(qt.Qt.DockWidgetArea.RightDockWidgetArea, preview)

    main.yaw.setValue(45.0)
    assert preview.view._yaw == 45.0  # the docker drives the preview

    preview.view.directionChanged.emit(-30.0, 10.0)
    assert (main.yaw.value(), main.pitch.value()) == (-30.0, 10.0)  # and the preview drives the docker

    preview.view.fovStep.emit(-2)
    assert main.fov.value() == 80.0

    main.aspect_actions[16 / 9].trigger()
    assert main.aspect == 16 / 9 and main.aspect_actions[16 / 9].isChecked()
    assert not main.aspect_actions[1.0].isChecked()  # the ratios are exclusive
    assert preview.view._aspect == 16 / 9 and main.picker._aspect == 16 / 9


def test_resize_grip_only_shows_while_floating(plugin):
    qt = plugin.qt
    window = keep(qt.QtWidgets.QMainWindow())
    preview = plugin.pkg.docker.PreviewDocker()
    window.addDockWidget(qt.Qt.DockWidgetArea.RightDockWidgetArea, preview)
    window.show()
    assert not preview.grip.isVisibleTo(preview)
    preview.setFloating(True)
    preview.resize(400, 300)
    qt.QApplication.processEvents()
    assert preview.grip.isVisibleTo(preview)
    view = preview.view
    assert preview.grip.geometry().bottomRight() == qt.QtCore.QPoint(view.width() - 1, view.height() - 1)
    preview.setFloating(False)
    assert not preview.grip.isVisibleTo(preview)


def test_widened_fov_keeps_the_projection_square(plugin):
    from spherepaint.preview import widened_fov
    assert widened_fov(90, 300, 300) == pytest.approx(90)
    assert widened_fov(90, 600, 300) > 90
    assert widened_fov(90, 300, 600) < 90
    assert widened_fov(160, 4000, 100) == pytest.approx(170)


def test_widened_fov_follows_the_aspect_ratio(plugin):
    from spherepaint.preview import projection_rect, widened_fov
    assert projection_rect(1600, 900, 16 / 9) == pytest.approx((1600, 900))
    assert projection_rect(1000, 900, 16 / 9) == pytest.approx((1000, 562.5))  # limited by the width
    assert widened_fov(90, 1600, 900, 16 / 9) == pytest.approx(90)  # the frame is the projection
    assert widened_fov(90, 1000, 1000, 16 / 9) == pytest.approx(90)  # full width, dimmed above and below
    assert widened_fov(90, 900, 900, 9 / 16) > 90  # a narrow portrait frame leaves room on the sides


class FakeLayer:
    """A layer (or document) whose pixels live in a NumPy array; reads must stay inside the image."""

    def __init__(self, pixels):
        self.pixels = pixels.copy()
        self.reads = []

    def width(self):
        return self.pixels.shape[1]

    def height(self):
        return self.pixels.shape[0]

    def pixelData(self, x, y, w, h):
        assert 0 <= x and x + w <= self.width() and 0 <= y and y + h <= self.height()
        self.reads.append(w * h)
        return self.pixels[y:y + h, x:x + w].tobytes()

    def setPixelData(self, data, x, y, w, h):
        assert 0 <= x and x + w <= self.width() and 0 <= y and y + h <= self.height()
        self.pixels[y:y + h, x:x + w] = np.frombuffer(bytes(data), np.uint8).reshape(h, w, -1)


@pytest.mark.parametrize("yaw,pitch", [(0, 0), (179, 10), (-179, -20), (60, 85)])
def test_write_back_reads_and_writes_only_the_painted_region(plugin, yaw, pitch):
    import projection as P
    from test_projection import H, W, crop, noise_equirect, write_back

    docker = plugin.pkg.docker
    eq = noise_equirect()
    layer = FakeLayer(eq)
    view = P.View(yaw, pitch, 90, 300)
    region = P.view_region(view, W, H)
    assert np.array_equal(docker._read_region(layer, region, W, 4, np.uint8), crop(eq, region))

    img = P.equirect_to_view(eq, view)
    painted = img.copy()
    painted[140:160, 0:40] = (10, 200, 30, 255)  # at the left edge, so a seam view wraps
    mask = P.change_mask(img, painted)
    panel = docker.SphereDocker()
    panel.session = types.SimpleNamespace(src_doc=layer, view=view, channels=4, dtype=np.uint8)
    layer.reads.clear()
    undo = []
    changed = panel._write_layer_back(layer, painted, mask, undo)
    assert np.array_equal(layer.pixels, write_back(eq, painted, mask, view))
    assert changed > 0 and sum(layer.reads) < W * H / 20  # far less than the whole panorama
    for _, r0, c0, old in reversed(undo):
        docker._write(layer, old, c0, r0)
    assert np.array_equal(layer.pixels, eq)


def test_reference_margin_covers_wide_views(plugin):
    import projection as P
    margin = plugin.pkg.docker._reference_margin
    assert margin(P.View(0, 0, 90, P.matching_view_size(8192, 90)), 8192) <= 6
    assert margin(P.View(0, 0, 150, 1000), 4096) > margin(P.View(0, 0, 90, 1000), 4096)


@pytest.mark.parametrize("yaw,pitch,fov", [(0, 0, 90), (179, 30, 120), (20, -80, 60)])
def test_partial_reference_refresh_matches_a_full_one(plugin, yaw, pitch, fov):
    import projection as P
    from test_projection import H, W, noise_equirect, write_back

    docker = plugin.pkg.docker
    eq = noise_equirect()
    view = P.View(yaw, pitch, fov, P.matching_view_size(W, fov))
    before = P.equirect_to_view(eq, view)
    painted = before.copy()
    painted[200:230, 5:60] = (255, 0, 0, 255)
    mask = P.change_mask(before, painted)
    after = write_back(eq, painted, mask, view)

    rect = docker._changed_rect(mask, view, docker._reference_margin(view, W))
    region = P.view_region(view, W, H, rect)
    from test_projection import crop
    reference = before.copy()
    left, top, right, bottom = rect
    reference[top:bottom, left:right] = P.equirect_to_view(crop(after, region), view, (region[0], region[1], W, H), rect)
    assert np.array_equal(reference, P.equirect_to_view(after, view))


def test_thumbnail_is_patched_where_the_panorama_changed(plugin):
    qt = plugin.qt
    docker = plugin.pkg.docker

    class FakeDoc(FakeLayer):
        def colorModel(self):
            return "RGBA"

        def colorDepth(self):
            return "U8"

        def waitForDone(self):
            pass

        def rootNode(self):
            return types.SimpleNamespace(uniqueId=lambda: "panorama")

    def thumbnail(pixels, width=512, height=256):
        h, w = pixels.shape[:2]
        data = pixels.tobytes()
        image = qt.QImage(data, w, h, w * 4, qt.QImage.Format.Format_ARGB32)
        return image.scaled(width, height, qt.Qt.AspectRatioMode.IgnoreAspectRatio,
                            qt.Qt.TransformationMode.SmoothTransformation).convertToFormat(qt.QImage.Format.Format_ARGB32)

    qimage_to_bgra = plugin.pkg.preview.qimage_to_bgra
    rng = np.random.default_rng(2)
    pixels = np.zeros((1000, 2000, 4), np.uint8)
    pixels[..., :3] = rng.integers(0, 256, 3)
    pixels[..., 3] = 255
    doc = FakeDoc(pixels)
    panel = docker.SphereDocker()
    panel._thumbnail_source = lambda: doc
    panel._preview_image = thumbnail(doc.pixels)
    panel._thumbnail_doc = "panorama"
    assert panel._patch_thumbnail([])  # nothing changed: nothing to do

    doc.pixels[300:420, 1900:2000] = (0, 0, 255, 255)  # a stroke at the right edge...
    doc.pixels[300:420, 0:50] = (0, 0, 255, 255)  # ...continuing across the seam
    boxes = docker._changed_boxes([(None, 300, 1900, np.zeros((120, 100, 4))), (None, 300, 0, np.zeros((120, 50, 4)))],
                                  2000)
    assert sorted(boxes) == [(0, 300, 50, 120), (1900, 300, 100, 120)]
    assert panel._patch_thumbnail(boxes)
    patched = qimage_to_bgra(panel._preview_image).astype(int)
    expected = qimage_to_bgra(thumbnail(doc.pixels)).astype(int)
    assert np.abs(patched - expected).max() <= 40  # only edge pixels differ, from rounding to whole pixels
    assert np.abs(patched - expected).mean() < 0.2
    assert (patched[160:200, 2:10, 0] > 200).all()  # the stroke shows on both sides of the seam
    assert (patched[160:200, 500:510, 0] > 200).all()

    quick = qimage_to_bgra(docker._quick_thumbnail(doc, 512, 256)).astype(int)
    assert quick.shape == (256, 512, 4)
    assert np.abs(quick - expected).mean() < 2  # two rows per thumbnail row are close to the full average
    panel._thumbnail_doc = "another document"
    assert not panel._patch_thumbnail(boxes)  # a thumbnail of another image must be remade


def test_settings_come_back_in_the_next_session(plugin):
    docker = plugin.pkg.docker
    first = docker.SphereDocker()
    first.fov.setValue(120)
    first.aspect_actions[16 / 9].trigger()
    first.project_on_release.setChecked(False)
    first.auto_size.setChecked(False)
    first.view_size.setValue(3000)

    second = docker.SphereDocker()  # as after restarting Krita
    assert second.fov.value() == 120
    assert second.aspect == 16 / 9 and second.aspect_actions[16 / 9].isChecked()
    assert not second.project_on_release.isChecked()
    assert not second.auto_size.isChecked() and second.act_view_size.isEnabled()
    assert second.view_size.value() == 3000

    # Back to the defaults, so the other tests start from them.
    second.fov.setValue(90)
    second.set_aspect(1.0)
    second.project_on_release.setChecked(True)
    second.auto_size.setChecked(True)
    second.view_size.setValue(1024)
