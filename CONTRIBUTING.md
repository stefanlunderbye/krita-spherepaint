# Contributing to SpherePaint

Thanks for your interest! Bug reports, testing on Linux and macOS, translations and code are all welcome.

## Reporting bugs

Open an issue with the **Bug report** template. Please include your Krita version, operating system, how the plugin was installed, and the steps to reproduce. If Krita shows a Python error, paste the full text.

Linux and macOS reports are especially valuable – the plugin is mainly developed and tested on Windows.

## Development setup

1. Clone the repository.
2. Link the plugin into Krita's resource folder (**Settings → Manage Resources → Open Resource Folder**):
   - `spherepaint/` and `spherepaint.desktop` into `pykrita/`
   - `spherepaint.action` into `actions/`
3. Put NumPy for Krita's Python into `spherepaint/_vendor/` (see *From source* in the README). It is not committed.
4. Restart Krita and enable SpherePaint in the Python Plugin Manager. Restart Krita again after code changes.

## Code layout

| File | Purpose |
|---|---|
| `spherepaint/projection.py` | Reprojection maths (pure NumPy, no Krita) |
| `spherepaint/docker.py` | The docker panel and all Krita document handling |
| `spherepaint/picker.py` | Mouse direction picker |
| `spherepaint/guide.py` | Guide layer rendering |
| `spherepaint/xmp.py` | GPano metadata for 360° JPEG export |
| `spherepaint/actions.py` | Shortcut actions |
| `spherepaint/i18n.py` | Translations |
| `spherepaint/qt.py` | PyQt5/PyQt6 compatibility |
| `spherepaint/vendor.py` | Picks the bundled NumPy for the running Python |

Keep maths and file-format code free of Krita so it can be tested.

## Tests

```
pip install numpy pytest
pytest
```

The Qt tests in `tests/test_qt_compat.py` run when PyQt5 or PyQt6 is installed (`QT_QPA_PLATFORM=offscreen` on headless systems) and are skipped otherwise. GitHub Actions runs the suite on Windows, Linux and macOS.

## Code style

- Follow the existing style: small functions, docstrings explaining *why*, English comments.
- Use fully scoped Qt enums (`Qt.CursorShape.WaitCursor`) and import Qt names from `spherepaint/qt.py`, so the plugin works with both PyQt5 and PyQt6.
- Every user-visible string goes through `tr()`; add a Swedish translation in `i18n.py` – the tests check this.

## Translations

Add a dictionary for your language in `spherepaint/i18n.py` (like `SV`) and register it in `CATALOGS`. Keep `{placeholders}` unchanged.

## Pull requests

- One topic per pull request, with tests for maths or format changes.
- Make sure `pytest` passes.
- Add a line under *Unreleased* in `CHANGELOG.md`.
- By contributing you agree that your work is licensed under the MIT licence.
