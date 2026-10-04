"""Makes the plugin's Krita-independent modules importable without Krita."""
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "spherepaint"
sys.path.insert(0, str(PLUGIN_DIR))
