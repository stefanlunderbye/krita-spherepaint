"""The Qt parts of the plugin with PyQt5 (Krita 5) or PyQt6 (Krita 6).

Skipped when no PyQt is installed. Run locally with either binding installed:
    QT_QPA_PLATFORM=offscreen pytest tests/test_qt_compat.py
"""
import importlib.util
import os
import sys
import types

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
