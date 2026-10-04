"""Translates the plugin's texts to match Krita's interface language.

The source strings are English. Krita only translates its own strings, so the
plugin determines the language the same way Krita does: first a language
chosen under Settings → Switch Application Language (klanguageoverridesrc),
otherwise the system language.
"""
import configparser
import os

from .qt import QLocale, QStandardPaths

SV = {
    "Project": "Projicera",
    "Write back": "Skriv tillbaka",
    "Undo": "Ångra",
    "Flat / Projection": "Platt / Projektion",
    "Switches between the flat image and the projection": "Växlar mellan den platta bilden och projektionen",
    "More: preview, guide layer, cube maps, export and settings":
        "Mer: förhandsvisning, hjälplager, kubkartor, export och inställningar",
    "Yaw – turn left and right": "Yaw – vrid åt vänster och höger",
    "Pitch – tilt up and down": "Pitch – luta uppåt och nedåt",
    "Cube map": "Kubkarta",
    "Automatic view size (same density as the image)": "Automatisk vystorlek (samma täthet som bilden)",
    "View size…": "Vystorlek…",
    "View size in pixels:": "Vystorlek i pixlar:",
    "Field of view": "Synfält (FOV)",
    "Click or drag to choose the direction; scroll to change the field of view":
        "Klicka eller dra för att välja riktning; scrolla för att ändra synfältet",
    "Project when the mouse is released": "Projicera när musknappen släpps",
    "SpherePaint: Project view": "SpherePaint: Projicera vy",
    "SpherePaint: Write back to sphere": "SpherePaint: Skriv tillbaka till sfären",
    "SpherePaint: Toggle flat / projection": "SpherePaint: Växla platt / projektion",
    "SpherePaint: Undo last write-back": "SpherePaint: Ångra senaste tillbakaskrivning",
    "SpherePaint: Add guide layer": "SpherePaint: Lägg till hjälplager",
    "Open the SpherePaint docker first (Settings → Dockers → SpherePaint).":
        "Öppna SpherePaint-panelen först (Inställningar → Paneler → SpherePaint).",
    "360° preview of the projection: drag to look around, scroll to zoom":
        "360°-förhandsvisning av projektionen: dra för att se dig omkring, scrolla för att zooma",
    "Open 360° preview": "Öppna 360°-förhandsvisning",
    "Opens the 360° preview as a window you can move and resize":
        "Öppnar 360°-förhandsvisningen som ett fönster du kan flytta och ändra storlek på",
    "The 360° preview docker is not available. Enable it under Settings → Dockers → {name}.":
        "Panelen för 360°-förhandsvisning finns inte. Aktivera den under Inställningar → Paneler → {name}.",
    "SpherePaint 360° Preview": "SpherePaint 360°-förhandsvisning",
    "Drag to resize the preview window": "Dra för att ändra storlek på förhandsvisningen",
    "Export for 360° viewers…": "Exportera för 360°-visare…",
    "Saves a JPEG with 360° metadata (GPano), recognised by Facebook, Google Photos, Kuula and other viewers":
        "Sparar en JPEG med 360°-metadata (GPano) som Facebook, Google Photos, Kuula och andra visare känner igen",
    "Export for 360° viewers": "Exportera för 360°-visare",
    "JPEG image (*.jpg *.jpeg)": "JPEG-bild (*.jpg *.jpeg)",
    "Exporting…": "Exporterar…",
    "Exporting failed: {error}": "Exporten misslyckades: {error}",
    "Saved {file} with 360° metadata.": "{file} är sparad med 360°-metadata.",
    "Export cube map…": "Exportera kubkarta…",
    "Saves the panorama as six cube faces (front, right, back, left, top, bottom)":
        "Sparar panoramat som sex kubsidor (fram, höger, bak, vänster, upp, ner)",
    "Import cube map…": "Importera kubkarta…",
    "Builds a panorama from six cube faces; choose the *_front image":
        "Bygger ett panorama av sex kubsidor; välj bilden *_front",
    "Export cube map to folder": "Exportera kubkarta till mapp",
    "{count} of the files already exist. Overwrite them?": "{count} av filerna finns redan. Skriva över dem?",
    "Exporting cube map…": "Exporterar kubkarta…",
    "Exporting the cube map failed: {error}": "Kubkartan kunde inte exporteras: {error}",
    "Cube map exported: six {size}×{size} px faces in {folder}.":
        "Kubkartan är exporterad: sex sidor på {size}×{size} px i {folder}.",
    "Choose the front face of the cube map": "Välj kubkartans framsida",
    "Images (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.kra *.webp)":
        "Bilder (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.kra *.webp)",
    "Choose the file whose name ends in _front.": "Välj filen vars namn slutar på _front.",
    "Missing cube faces: {files}": "Kubsidor saknas: {files}",
    "Importing cube map…": "Importerar kubkarta…",
    "All faces must be square and the same size.": "Alla sidor måste vara kvadratiska och lika stora.",
    "All faces must have the same colour model and depth.":
        "Alla sidor måste ha samma färgmodell och färgdjup.",
    "Importing the cube map failed: {error}": "Kubkartan kunde inte importeras: {error}",
    "Panorama {width}×{height} px created from the cube map.":
        "Panorama på {width}×{height} px skapat från kubkartan.",
    "Add guide layer": "Lägg till hjälplager",
    "Adds a layer with a labelled grid (front, right, back, left, top, bottom)":
        "Lägger till ett lager med ett märkt rutnät (fram, höger, bak, vänster, upp, ner)",
    "Open an equirectangular image (2:1) first.": "Öppna en equirect-bild (2:1) först.",
    "The guide layer needs an RGBA image with 8 or 16 bits per channel.":
        "Hjälplagret kräver en RGBA-bild med 8 eller 16 bitar per kanal.",
    "Creating guide layer…": "Skapar hjälplager…",
    "SpherePaint guide": "SpherePaint-hjälplager",
    "Creating the guide layer failed: {error}": "Hjälplagret kunde inte skapas: {error}",
    "Guide layer added. Hide or delete it like any other layer.":
        "Hjälplagret är tillagt. Dölj eller ta bort det som vilket lager som helst.",
    "FRONT": "FRAM",
    "RIGHT": "HÖGER",
    "BACK": "BAK",
    "LEFT": "VÄNSTER",
    "TOP": "UPP",
    "BOTTOM": "NER",
    "Front": "Fram",
    "Right": "Höger",
    "Back": "Bak",
    "Left": "Vänster",
    "Up": "Upp",
    "Down": "Ner",
    "Creates/updates an undistorted perspective view of the active layer":
        "Skapar/uppdaterar en odistorterad perspektivvy av det aktiva lagret",
    "Transfers what changed in the layer '{layer}' to the equirectangular image":
        "För över det som ändrats i lagret '{layer}' till equirect-bilden",
    "Undo last write-back": "Ångra senaste tillbakaskrivning",
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
    "Done: {count} pixels updated in {layers}.": "Klart: {count} pixlar uppdaterade i {layers}.",
    "The layer '{layer}' in the panorama is not a paint layer.":
        "Lagret '{layer}' i panoramat är inget målarlager.",
    "The last write-back has been undone in the equirectangular image. "
    "The view is unchanged – press 'Write back' again to redo it.":
        "Senaste tillbakaskrivningen är ångrad i equirect-bilden. "
        "Vyn är oförändrad – tryck 'Skriv tillbaka' igen för att göra om den.",
}

CATALOGS = {"sv": SV}


def _override_language():
    """The language chosen in Krita (Settings → Switch Application Language), if any."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
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
