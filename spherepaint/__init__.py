import os

from .vendor import add_vendor_path, vendor_tag

# NumPy is not part of Krita's Python, so the plugin bundles its own copy.
_vendor = add_vendor_path(os.path.dirname(__file__))
try:
    import numpy  # noqa: F401
except ImportError as e:
    raise ImportError(
        f"SpherePaint needs NumPy for this Python ({vendor_tag()}), but none was found in {_vendor}. "
        "Install the release zip for your platform from "
        "https://github.com/stefanlunderbye/krita-spherepaint/releases"
    ) from e

from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita  # noqa: E402

from .actions import SpherePaintActions  # noqa: E402
from .docker import PreviewDocker, SphereDocker  # noqa: E402

Krita.instance().addDockWidgetFactory(
    DockWidgetFactory("spherepaint_docker", DockWidgetFactoryBase.DockRight, SphereDocker)
)
Krita.instance().addDockWidgetFactory(
    DockWidgetFactory("spherepaint_preview", DockWidgetFactoryBase.DockRight, PreviewDocker)
)
Krita.instance().addExtension(SpherePaintActions(Krita.instance()))
