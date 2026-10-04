"""The .action file (needed for Krita's shortcut editor) must match the actions the plugin creates."""
import ast
import xml.etree.ElementTree as ET

from conftest import PLUGIN_DIR

ACTION_FILE = PLUGIN_DIR.parent / "spherepaint.action"


def plugin_actions():
    tree = ast.parse((PLUGIN_DIR / "actions.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ACTIONS":
            return {action_id: text for action_id, text, _ in ast.literal_eval(node.value)}
    raise AssertionError("ACTIONS not found in actions.py")


def file_actions():
    root = ET.parse(ACTION_FILE).getroot()
    return {a.get("name"): a.findtext("text") for a in root.iter("Action")}


def test_action_file_lists_exactly_the_plugin_actions():
    assert file_actions().keys() == plugin_actions().keys()


def test_action_file_uses_the_same_menu_texts():
    assert file_actions() == plugin_actions()
