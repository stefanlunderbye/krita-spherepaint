"""Every string passed to tr() must have a Swedish translation with the same placeholders.

Reads the source with ``ast`` so neither Krita nor Qt is needed.
"""
import ast
import string

from conftest import PLUGIN_DIR

UI_MODULES = ("docker.py", "picker.py", "preview.py", "guide.py", "actions.py")
# Strings translated through a variable rather than a literal tr("...") call.
DYNAMIC_KEYS = ("Front", "Right", "Back", "Left", "Up", "Down",
                "FRONT", "RIGHT", "BACK", "LEFT", "TOP", "BOTTOM")


def swedish_catalog():
    tree = ast.parse((PLUGIN_DIR / "i18n.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SV":
            return ast.literal_eval(node.value)
    raise AssertionError("SV catalog not found in i18n.py")


def translated_strings():
    keys = set(DYNAMIC_KEYS)
    for module in UI_MODULES:
        tree = ast.parse((PLUGIN_DIR / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                keys.add(node.args[0].value)
            # Menu texts of the shortcut actions are translated through a loop variable.
            if (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ACTIONS"):
                keys.update(text for _, text, _ in ast.literal_eval(node.value))
    return keys


def placeholders(text):
    return sorted({field for _, field, _, _ in string.Formatter().parse(text) if field})


def test_every_string_has_a_swedish_translation():
    missing = sorted(translated_strings() - swedish_catalog().keys())
    assert not missing


def test_translations_keep_the_same_placeholders():
    sv = swedish_catalog()
    wrong = [k for k in translated_strings() if k in sv and placeholders(k) != placeholders(sv[k])]
    assert not wrong


def test_no_unused_translations():
    unused = sorted(swedish_catalog().keys() - translated_strings())
    assert not unused
