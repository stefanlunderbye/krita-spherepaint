import os
import sys

# NumPy is not part of Krita's Python, so the plugin bundles its own copy.
_vendor = os.path.join(os.path.dirname(__file__), "_vendor")
if _vendor not in sys.path:
    sys.path.insert(0, _vendor)

from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita  # noqa: E402

from .actions import SpherePaintActions  # noqa: E402
from .docker import SphereDocker  # noqa: E402

Krita.instance().addDockWidgetFactory(
    DockWidgetFactory("spherepaint_docker", DockWidgetFactoryBase.DockRight, SphereDocker)
)
Krita.instance().addExtension(SpherePaintActions(Krita.instance()))
