"""No text field in the shell may ever be editable to Qt (marketplace review #11195,
2026-10-13, follow-up 2026-10-14): on released Qt an editable TextInput reads the whole
clipboard text on each clipboard change and the moment it turns editable, so NOVA's fields
are GuardedTextField (always read-only; the field edits its own text) and paste goes
through a capped, time-limited wl-paste. tools/check_clipboard_guard_interaction.py
measures it in a private compositor; this test keeps new fields from skipping the guard."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "Panel.qml").read_text()
EDITABLE = re.compile(r"\b(?:Ui\.|T\.)?(TextField|TextInput|TextArea|SearchableDropdown|NumberField)\s*\{")


def plugin_qml():
    # The plugin's own QML (what the installer copies), not the test harness under tools/.
    return {path.name: path.read_text() for path in sorted(ROOT.glob("*.qml"))}


def guard_source():
    guard = PANEL[PANEL.index("component GuardedTextField: Ui.TextField {"):]
    return guard[:guard.index("\n  }\n")]


class PanelClipboard(unittest.TestCase):
    def test_every_editable_field_is_guarded(self):
        guard = guard_source()
        for name, text in plugin_qml().items():
            outside = text.replace(guard, "") if name == "Panel.qml" else text
            found = [m.group(0) for m in EDITABLE.finditer(outside)]
            self.assertEqual(found, [], f"{name}: unguarded editable field; use GuardedTextField")
        self.assertEqual(PANEL.count("GuardedTextField {"), 2, "chat box and ADD A MODEL field")

    def test_the_guard_is_never_editable_to_qt(self):
        guard = guard_source()
        self.assertIn("readOnly: true", guard)
        self.assertIn("cursorDelegate:", guard)
        for name, text in plugin_qml().items():
            # No binding or assignment may turn a field editable, and Qt's own paste()
            # reads the whole clipboard: paste only through root.pasteInto.
            self.assertNotRegex(text, r"readOnly\s*[:=](?!\s*true\b)", f"{name}: readOnly other than true")
            self.assertNotRegex(text, r"\.paste\(\)", f"{name}: Qt paste() reads the whole clipboard")

    def test_paste_is_capped_in_size_and_time(self):
        reader = PANEL[PANEL.index("id: boundedPaste"):]
        reader = reader[:reader.index("\n  }\n")]
        self.assertIn("timeout 2 sh -c 'wl-paste --no-newline --type text", reader)
        self.assertIn("| head -c", reader)
        self.assertIn("root.pasteLimitBytes + 1", reader)
        self.assertIn("readonly property int pasteLimitBytes: 131072", PANEL)

    def test_text_edits_are_read_only(self):
        for name, text in plugin_qml().items():
            for match in re.finditer(r"\bTextEdit\s*\{", text):
                body = text[match.end():match.end() + 1200]
                self.assertIn("readOnly: true", body[:body.find("\n    }")], f"{name}: editable TextEdit")


if __name__ == "__main__":
    unittest.main()
