"""Qt bindings for both Krita 5 (PyQt5) and Krita 6 (PyQt6).

Uses whichever binding Krita has already loaded, so the plugin never mixes Qt
versions. The rest of the code uses fully scoped enum names
(``Qt.CursorShape.WaitCursor``), which work in PyQt5 5.15 and are required by PyQt6.
"""
import sys

if "PyQt6" in sys.modules:
    from PyQt6 import QtCore, QtGui, QtWidgets
elif "PyQt5" in sys.modules:
    from PyQt5 import QtCore, QtGui, QtWidgets
else:
    try:
        from PyQt5 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PyQt6 import QtCore, QtGui, QtWidgets

PYQT6 = QtCore.PYQT_VERSION_STR.startswith("6")

pyqtSignal = QtCore.pyqtSignal
QByteArray = QtCore.QByteArray
QLocale = QtCore.QLocale
QPointF = QtCore.QPointF
QRectF = QtCore.QRectF
QSize = QtCore.QSize
QStandardPaths = QtCore.QStandardPaths
Qt = QtCore.Qt

QColor = QtGui.QColor
QFont = QtGui.QFont
QImage = QtGui.QImage
QPainter = QtGui.QPainter
QPainterPath = QtGui.QPainterPath
QPen = QtGui.QPen

QAction = getattr(QtGui, "QAction", None) or QtWidgets.QAction  # moved to QtGui in Qt 6
QApplication = QtWidgets.QApplication
QInputDialog = QtWidgets.QInputDialog
QMenu = QtWidgets.QMenu
QCheckBox = QtWidgets.QCheckBox
QDoubleSpinBox = QtWidgets.QDoubleSpinBox
QFileDialog = QtWidgets.QFileDialog
QFormLayout = QtWidgets.QFormLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QMessageBox = QtWidgets.QMessageBox
QPushButton = QtWidgets.QPushButton
QSizeGrip = QtWidgets.QSizeGrip
QSizePolicy = QtWidgets.QSizePolicy
QSpinBox = QtWidgets.QSpinBox
QToolButton = QtWidgets.QToolButton
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget


def event_pos(event):
    """Mouse position of an event as QPoint (Qt 6 replaced pos() with position())."""
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()
