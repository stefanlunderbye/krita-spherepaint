"""Krita actions for the docker's commands, so they can get keyboard shortcuts.

They appear under Tools → Scripts and in Settings → Configure Krita →
Keyboard Shortcuts, where the user assigns keys.
"""
from krita import Extension, Krita
from PyQt5.QtWidgets import QMessageBox

from .docker import TITLE, docker_for_active_window
from .i18n import tr

# (action id, menu text, docker method)
ACTIONS = (
    ("spherepaint_project", "SpherePaint: Project view", "project"),
    ("spherepaint_write_back", "SpherePaint: Write back to sphere", "apply"),
    ("spherepaint_toggle_view", "SpherePaint: Toggle flat / projection", "toggle_view"),
    ("spherepaint_undo_write_back", "SpherePaint: Undo last write-back", "undo_apply"),
    ("spherepaint_add_guide", "SpherePaint: Add guide layer", "add_guide"),
)


class SpherePaintActions(Extension):
    def setup(self):
        pass

    def createActions(self, window):
        for action_id, text, method in ACTIONS:
            action = window.createAction(action_id, tr(text), "tools/scripts")
            action.triggered.connect(lambda _=False, m=method: self._run(m))

    def _run(self, method):
        docker = docker_for_active_window()
        if docker is None:
            window = Krita.instance().activeWindow()
            QMessageBox.information(window.qwindow() if window else None, TITLE,
                                    tr("Open the SpherePaint docker first (Settings → Dockers → SpherePaint)."))
            return
        getattr(docker, method)()
