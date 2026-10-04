# Changelog

All notable changes to SpherePaint are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added
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

[Unreleased]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/stefanlunderbye/krita-spherepaint/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/stefanlunderbye/krita-spherepaint/releases/tag/v0.1.0
