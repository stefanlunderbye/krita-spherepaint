# Changelog

All notable changes to SpherePaint are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Changed
- The view mirrors the panorama's layers: every paint layer and group appears in the view with the same name, order, opacity, blending mode and visibility, in the same groups, and *Write back* transfers the changes of every layer you painted in to the layer it mirrors. *Paint here* is gone; the layer that was active in the panorama is active in the view. Layers are matched by identity, so layers with the same name never mix and renaming in the view is fine.
- A new layer in the view becomes a new panorama layer at the same place – in the same group, above the same neighbour. A new group in the view becomes a new panorama group. Hidden new layers are not written back (sketches).
- The reference layer (the merged image) is hidden by default; show it to see filter, vector and other layers that can't be mirrored.

## [0.7.0] – 2026-10-06

### Added
- Undo in several steps: **Undo** takes back up to 10 write-backs per panorama, one at a time (fewer when they are very large). Undoing a write-back made from another direction projects the open view again, after asking if it has changes that haven't been written back.

### Changed
- Polished panel buttons: *Undo* and *Flat / Projection* have centred text and the lower row one height. New icons: a perspective grid for *Project*, merge-down for *Write back* (instead of a floppy disk, which suggested saving to a file), circular arrows for *Flat / Projection* and three lines for the **⋯** menu.

## [0.6.0] – 2026-10-06

### Added
- Manual and README section on using SpherePaint with AI image generation (fixing poles and seams of generated panoramas, inpainting in undistorted views).
- Aspect ratio for the projection (**⋯ → Aspect ratio**): 1:1, 4:3, 3:2, 16:9, 3:4 or 9:16. The field of view is horizontal; the thumbnail outline and the 360° preview frame follow the ratio, and the ratio is remembered per document.
- The panel's settings (field of view, aspect ratio, *Project when the mouse is released*, *Automatic view size* and the view size) are saved in Krita's settings and restored when Krita starts.

### Changed
- Much faster with very large panoramas. *Project* reads only the part of the panorama the view covers and projects the layer and the reference in one pass; *Write back* and the reference refresh after it only process the painted area; the work runs on several CPU cores, and the pixel sampling itself is about twice as fast. The panel's panorama thumbnail is no longer remade by Krita from the whole image after every projection and write-back: only the changed area is redrawn, and a full refresh of an 8-bit image takes a fraction of a second. In a 16K test the computation for projecting went from about 25 s to 5–7 s, and for writing back a brush stroke (including the reference refresh) from about 25 s to under a second.

## [0.5.0] – 2026-10-04

### Changed
- Redesigned docker: yaw, pitch and field of view on one row under the thumbnail, compact direction buttons, large *Project* and *Write back* buttons side by side, *Undo* and *Flat / Projection* below, and less frequent tools (360° preview, guide layer, cube maps, 360° export, view size settings) in a **⋯** menu. Uses Krita's own icons where available.

## [0.4.0] – 2026-10-04

### Added
- 360° preview as its own docker (*SpherePaint 360° Preview*) that floats as a movable, resizable window: drag to look around, scroll to zoom, release to project. The yellow frame shows exactly what the projection will cover.
- The view direction (yaw, pitch, field of view) is stored in the panorama document and restored when switching back to it or reopening the .kra file.
- *Export for 360° viewers…*: saves a JPEG with Google Photo Sphere (GPano) XMP metadata, so Facebook, Google Photos, Kuula and other viewers show it as a panorama.
- Prepared for Krita 6: the plugin uses whichever of PyQt5/PyQt6 Krita has loaded, with fully scoped Qt enum names.
- Issue templates, a contributing guide and this changelog.

## [0.3.0] – 2026-10-04

### Added
- Several layers: extra paint layers in the view are written back to same-named panorama layers (created if missing) and show their existing content when projecting.
- Cube map export and import (six faces, PNG or EXR).
- Guide layer with labelled cube faces, edges, grid and centre crosses.
- Keyboard-shortcut actions (project, write back, toggle view, undo, guide layer) with an `.action` file for Krita's shortcut editor.
- Linux and macOS release zips with NumPy for Python 3.10–3.13, chosen at startup.
- Automated tests on Windows, Linux and macOS.

### Changed
- Resampling uses premultiplied alpha, removing dark halos on semi-transparent edges.
- The view's reference layer is refreshed after write-back and undo.
- Pending move/transform strokes are finished before pixels are read.
- *Undo last write-back* covers every affected layer and removes layers it created.

## [0.2.0] – 2026-10-04

### Added
- Mouse control: a panorama thumbnail in the docker to click or drag for yaw and pitch, scroll for field of view, with the view outline drawn live.
- Optional projection when the mouse is released.

### Changed
- Only one projection view is kept open; it is reused or replaced.
- Unapplied changes are checked before switching panorama, and the undo step survives a replaced view.

## [0.1.0] – 2026-10-04

### Added
- First release: docker with yaw, pitch and field of view, projection of the active layer into an undistorted perspective view, write-back of changed pixels only, flat/projection toggle, one-step undo, English and Swedish interface, plugin manual.

[Unreleased]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/stefanlunderbye/krita-spherepaint/releases/tag/v0.1.0
