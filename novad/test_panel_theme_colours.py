"""NOVA reads the theme's colours as Commons.Color, never bare Color (2026-10-20).

Qt 6.12 added a `Color` singleton to QtQuick (Color.rgba, Color.hsla…). In a plugin file
that imports QtQuick and qs.Commons unqualified, a bare `Color` now resolves to Qt's, so
`Color.accent`, `Color.muted` and `Color.foreground` were undefined and every label fell
back to black (Konstantin's screenshot after the Omarchy 4.0.6 / Qt 6.12 update; omaplug,
omadock and other plugins broke the same way). Omarchy's own files import
`qs.Commons as Commons` and write `Commons.Color`; NOVA does the same.
check_theme_colours_interaction proves the colours in the real shell modules.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QML = sorted(ROOT.glob("*.qml"))
BARE_COLOR = re.compile(r"(?<![\w.])Color\.")


def code(line):
    """The line without a // comment (NOVA's QML has no // inside strings on Color lines)."""
    return line.split("//", 1)[0]


class ThemeColours(unittest.TestCase):
    def test_no_bare_color_singleton(self):
        offenders = []
        for path in QML:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if BARE_COLOR.search(code(line)):
                    offenders.append(f"{path.name}:{number}: {line.strip()[:80]}")
        self.assertEqual(offenders, [], "bare Color.… resolves to QtQuick's Color on Qt 6.12")

    def test_files_using_commons_import_it_qualified(self):
        for path in QML:
            source = path.read_text()
            if "Commons." in source:
                self.assertRegex(source, r"(?m)^import qs\.Commons as Commons$", path.name)

    def test_theme_colours_are_still_used(self):
        panel = (ROOT / "Panel.qml").read_text()
        for name in ("foreground", "muted", "accent", "urgent", "background"):
            self.assertTrue(f"Commons.Color.{name}" in panel, name)
        bar = (ROOT / "BarWidget.qml").read_text()
        self.assertTrue("Commons.Color.accent" in bar)
        self.assertTrue("Commons.Color.bar.text" in bar)


if __name__ == "__main__":
    unittest.main()
