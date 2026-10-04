"""Finds the bundled NumPy matching the running Python and platform.

Release zips contain one folder per Python version and platform under
``_vendor``, e.g. ``cp313-win-x86_64`` or ``cp312-macos-arm64``, because the
Python bundled with Krita differs between platforms and versions. A source
checkout may instead have NumPy directly in ``_vendor``.
"""
import os
import platform
import sys

_PLATFORMS = {"win32": "win", "linux": "linux", "darwin": "macos"}
_MACHINES = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}


def vendor_tag():
    """Folder name for the running interpreter, e.g. 'cp313-win-x86_64'."""
    plat = _PLATFORMS.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    machine = _MACHINES.get(machine, machine)
    return f"cp{sys.version_info.major}{sys.version_info.minor}-{plat}-{machine}"


def add_vendor_path(plugin_dir):
    """Puts the matching vendor folder first on sys.path; returns the folder used."""
    base = os.path.join(plugin_dir, "_vendor")
    specific = os.path.join(base, vendor_tag())
    chosen = specific if os.path.isdir(specific) else base
    if os.path.isdir(chosen) and chosen not in sys.path:
        sys.path.insert(0, chosen)
    return chosen
