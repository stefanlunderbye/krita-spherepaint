"""Translates the plugin's texts to match Krita's interface language.

The source strings are English. Krita only translates its own strings, so the
plugin determines the language the same way Krita does: first a language
chosen under Settings → Switch Application Language (klanguageoverridesrc),
otherwise the system language.
"""
import configparser
import os

from PyQt5.QtCore import QLocale, QStandardPaths

SV = {
    "Yaw": "Girvinkel (yaw)",
    "Pitch": "Lutning (pitch)",
    "Field of view": "Synfält (FOV)",
    "View size": "Vystorlek",
    "Auto (same density as the image)": "Auto (samma täthet som bilden)",
    "Click or drag to choose the direction; scroll to change the field of view":
        "Klicka eller dra för att välja riktning; scrolla för att ändra synfältet",
    "Project when the mouse is released": "Projicera när musknappen släpps",
    "Front": "Fram",
    "Right": "Höger",
    "Back": "Bak",
    "Left": "Vänster",
    "Up": "Upp",
    "Down": "Ner",
    "Project view": "Projicera vy",
    "Creates/updates an undistorted perspective view of the active layer":
        "Skapar/uppdaterar en odistorterad perspektivvy av det aktiva lagret",
    "Write back to sphere": "Skriv tillbaka till sfären",
    "Transfers what changed in the layer '{layer}' to the equirectangular image":
        "För över det som ändrats i lagret '{layer}' till equirect-bilden",
    "Undo last write-back": "Ångra senaste tillbakaskrivning",
    "Show flat": "Visa platt",
    "Show projection": "Visa projektion",
    "Open an equirectangular image (2:1) and select the layer you want to paint on.":
        "Öppna en equirect-bild (2:1) och välj lagret du vill måla på.",
    "Paint here": "Måla här",
    "Reference (whole image)": "Referens (hela bilden)",
    "Sphere view – {name}": "Sfärvy – {name}",
    "untitled": "namnlös",
    "No image is open.": "Ingen bild är öppen.",
    "The view has changes that haven't been written back. Write them back first? "
    "Otherwise they are discarded.":
        "Vyn har ändringar som inte skrivits tillbaka. Skriva tillbaka dem först? "
        "Annars kastas de.",
    "Select a regular paint layer in the equirectangular image.":
        "Välj ett vanligt målarlager (paint layer) i equirect-bilden.",
    "Colour depth {depth} is not supported.": "Färgdjupet {depth} stöds inte.",
    "The image is {w}×{h}, not 2:1. Equirectangular images are usually 2:1. Continue anyway?":
        "Bilden är {w}×{h}, inte 2:1. Equirect-bilder brukar vara 2:1. Fortsätta ändå?",
    "Projecting…": "Projicerar…",
    "Projection failed: {error}": "Projektionen misslyckades: {error}",
    "View {size}×{size} px, yaw {yaw:.0f}°, pitch {pitch:.0f}°, FOV {fov:.0f}°. "
    "Paint in the layer '{layer}', then press 'Write back'.":
        "Vy {size}×{size} px, yaw {yaw:.0f}°, pitch {pitch:.0f}°, FOV {fov:.0f}°. "
        "Måla i lagret '{layer}', tryck sedan 'Skriv tillbaka'.",
    "No active projection. Press 'Project view' first.":
        "Ingen aktiv projektion. Tryck 'Projicera vy' först.",
    "Writing back…": "Skriver tillbaka…",
    "Cannot tell which layer to write back. Merge your layers into one layer named '{layer}'.":
        "Kan inte avgöra vilket lager som ska skrivas tillbaka. Slå ihop lagren till ett lager "
        "som heter '{layer}'.",
    "Nothing has changed in the view since the last projection.":
        "Inget har ändrats i vyn sedan senaste projektionen.",
    "Write-back failed: {error}": "Tillbakaskrivningen misslyckades: {error}",
    "Done: {count} pixels updated in '{layer}'.": "Klart: {count} pixlar uppdaterade i '{layer}'.",
    "The last write-back has been undone in the equirectangular image. "
    "The view is unchanged – press 'Write back' again to redo it.":
        "Senaste tillbakaskrivningen är ångrad i equirect-bilden. "
        "Vyn är oförändrad – tryck 'Skriv tillbaka' igen för att göra om den.",
}

CATALOGS = {"sv": SV}


def _override_language():
    """The language chosen in Krita (Settings → Switch Application Language), if any."""
    base = QStandardPaths.writableLocation(QStandardPaths.GenericConfigLocation)
    path = os.path.join(base, "klanguageoverridesrc")
    if not os.path.isfile(path):
        return None
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
        value = parser.get("Language", "krita", fallback="")
    except (configparser.Error, OSError):
        return None
    return value.split(":")[0] or None


def _detect_language():
    lang = _override_language()
    if not lang:
        ui = QLocale().uiLanguages()
        lang = ui[0] if ui else QLocale().name()
    return lang.replace("-", "_").split("_")[0].lower()


LANGUAGE = _detect_language()
_catalog = CATALOGS.get(LANGUAGE, {})


def tr(text, **values):
    """Translates text into Krita's language and fills in {placeholders}."""
    translated = _catalog.get(text, text)
    return translated.format(**values) if values else translated
