"""NOVA's labels show text as text (marketplace review #11195, 2026-10-15).

Qt's Text defaults to AutoText: a string that looks like HTML is rendered as HTML, and an
<img> in it makes Qt load that file on the shell's only thread. ADD A MODEL showed the
typed reference in such a label. Every Text in NOVA's QML is therefore PlainText; the one
rich-text item is the chat answer, which is built by chatMarkdownHtml (everything escaped
first, only NOVA's own tags added back; check_ux_phase2_interaction feeds it hostile input).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QML = sorted(ROOT.glob("*.qml"))


def own_bindings(source, open_at):
    """The text of the block opening at source[open_at] == '{', without nested blocks or strings."""
    depth, out, i = 0, [], open_at
    while i < len(source):
        c = source[i]
        if c in "\"'":
            quote = c; i += 1
            while i < len(source) and source[i] != quote:
                i += 2 if source[i] == "\\" else 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: return "".join(out)
        elif depth == 1:
            out.append(c)
        i += 1
    return "".join(out)


class PlainText(unittest.TestCase):
    def test_every_text_is_plain_text(self):
        offenders = []
        for path in QML:
            source = path.read_text()
            for match in re.finditer(r"(?<![\w.])Text\s*\{", source):
                if "textFormat: Text.PlainText" not in own_bindings(source, match.end() - 1):
                    offenders.append(f"{path.name}:{source.count(chr(10), 0, match.start()) + 1}")
        self.assertEqual(offenders, [], "Text without textFormat: Text.PlainText")

    def test_the_only_rich_text_is_the_escaped_chat_answer(self):
        rich = []
        for path in QML:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"\b(?:Text|TextEdit)\.(?:RichText|StyledText|AutoText|MarkdownText)\b", line):
                    rich.append((path.name, line.strip()))
        self.assertEqual(rich, [("Panel.qml",
                                 'textFormat: chatLine.role === "assistant" ? TextEdit.RichText : TextEdit.PlainText')])
        panel = (ROOT / "Panel.qml").read_text()
        self.assertIn('text: chatLine.role === "assistant" ? root.chatMarkdownHtml(chatLine.shownText) : chatLine.shownText', panel)

    def test_every_text_edit_says_its_format(self):
        for path in QML:
            source = path.read_text()
            for match in re.finditer(r"(?<![\w.])TextEdit\s*\{", source):
                self.assertIn("textFormat:", own_bindings(source, match.end() - 1), path.name)

    def test_the_chat_renderer_escapes_before_adding_tags(self):
        panel = (ROOT / "Panel.qml").read_text()
        renderer = panel[panel.index("function chatMarkdownHtml("):]
        renderer = renderer[:renderer.index("\n  }\n")]
        self.assertIn('var lines = esc(String(source || "")).split("\\n")', renderer)
        self.assertNotIn("<img", renderer)
        self.assertNotIn("<a ", renderer)

    def test_the_chat_renderer_never_rescans_a_line(self):
        # Audit 2026-10-16: ".+?" and "[^)]*" ran from every opener to the end of the line,
        # so 30 KB of "**a " took 100 ms per redraw. Each pattern stops at its next opener.
        panel = (ROOT / "Panel.qml").read_text()
        renderer = panel[panel.index("function chatMarkdownHtml("):]
        renderer = renderer[:renderer.index("\n  }\n")]
        for pattern in re.findall(r"\.replace\((/.+?/g)", renderer):
            self.assertNotRegex(pattern, r"\.[*+]|\[\^\)\]|\[\^\\\]\]", pattern)


if __name__ == "__main__":
    unittest.main()
