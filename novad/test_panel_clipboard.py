"""No editable text field may sit in the shell waiting for the clipboard (marketplace review
#11195, 2026-10-13): on released Qt every editable TextInput reads the whole clipboard text
on each clipboard change, so NOVA's fields are GuardedTextField (read-only until the user
presses a key or the mouse in them). tools/check_clipboard_guard_interaction.py measures it
in a private compositor; this test keeps new fields from skipping the guard."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "Panel.qml").read_text()
EDITABLE = re.compile(r"\b(?:Ui\.|T\.)?(TextField|TextInput|TextArea|SearchableDropdown|NumberField)\s*\{")


def plugin_qml():
    # The plugin's own QML (what the installer copies), not the test harness under tools/.
    return {path.name: path.read_text() for path in sorted(ROOT.glob("*.qml"))}


class PanelClipboard(unittest.TestCase):
    def test_every_editable_field_is_guarded(self):
        guard = PANEL[PANEL.index("component GuardedTextField: Ui.TextField {"):]
        guard = guard[:guard.index("\n  }\n")]
        for name, text in plugin_qml().items():
            outside = text.replace(guard, "") if name == "Panel.qml" else text
            found = [m.group(0) for m in EDITABLE.finditer(outside)]
            self.assertEqual(found, [], f"{name}: unguarded editable field; use GuardedTextField")
        self.assertEqual(PANEL.count("GuardedTextField {"), 2, "chat box and ADD A MODEL field")

    def test_the_guard_keeps_fields_read_only_until_the_user_acts(self):
        guard = PANEL[PANEL.index("component GuardedTextField: Ui.TextField {"):]
        guard = guard[:guard.index("\n  }\n")]
        self.assertIn("readOnly: !(userEditing && root.opened && visible)", guard)
        self.assertIn("onVisibleChanged: if (!visible) userEditing = false", guard)
        self.assertIn("if (!root.opened) guardedField.userEditing = false", guard)
        self.assertIn("cursorDelegate:", guard)
        # Programmatic focus (the chat box is focused whenever CHAT shows) must not arm a field.
        self.assertNotIn("userEditing = true", PANEL.replace(guard, ""))

    def test_text_edits_are_read_only(self):
        for name, text in plugin_qml().items():
            for match in re.finditer(r"\bTextEdit\s*\{", text):
                body = text[match.end():match.end() + 1200]
                self.assertIn("readOnly: true", body[:body.find("\n    }")], f"{name}: editable TextEdit")


if __name__ == "__main__":
    unittest.main()
