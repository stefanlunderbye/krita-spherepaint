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


def test_docker_builds(plugin):
    docker = plugin.pkg.docker.SphereDocker()
    assert docker.btn_project.text()
    assert docker.btn_export_cube.text()
