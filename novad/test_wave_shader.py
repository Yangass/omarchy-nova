import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAVE_FRAG = ROOT / "assets" / "shaders" / "wave.frag"
PANEL = ROOT / "Panel.qml"
PLAYGROUND = ROOT / "previews" / "ui02d-wave" / "index.html"

# The retired Shockwave orbit: none of these may appear anywhere in the three files.
ORBIT_TOKENS = ["uOrb", "uHorFollow", "orbitAt", "orbLayer", "orbHalo", "orbScale",
                "Shockwave", "SHOCKWAVE"]
# These panel/playground-only knobs retire with the orbit; the shader never had them.
PANEL_PLAYGROUND_TOKENS = ["orbI", "loadPerMinute", "horFollow", "loadStyle", "loadSpeed"]


def read(path: Path) -> str:
    return path.read_text()


def shader_uniforms(text: str):
    """Names declared in wave.frag's `layout(std140) uniform buf { ... }` block."""
    m = re.search(r"uniform\s+buf\s*\{(.*?)\};", text, re.S)
    assert m, "wave.frag: uniform buf block not found"
    names = []
    for line in m.group(1).splitlines():
        line = line.strip().rstrip(";")
        if not line:
            continue
        parts = line.split()
        names.append(parts[-1])
    return names


def panel_properties(text: str):
    """`property <type> <name>` declarations in Panel.qml."""
    return set(re.findall(r"property\s+\w+\s+([A-Za-z_]\w*)\s*:", text))


def playground_uniforms(text: str):
    """`uniform <type> <name>;` declarations inside the playground's UNIFORMS template."""
    m = re.search(r"const UNIFORMS = `(.*?)`;", text, re.S)
    assert m, "playground: UNIFORMS string not found"
    return set(re.findall(r"uniform\s+\w+\s+([A-Za-z_]\w*)\s*;", m.group(1)))


# The wave playground is a development tool; the public release tree does not ship previews/.
HAS_PLAYGROUND = PLAYGROUND.exists()


class TestWaveShader(unittest.TestCase):
    def test_no_orbit_left(self):
        for name, path in (("shader", WAVE_FRAG), ("panel", PANEL), ("playground", PLAYGROUND)):
            if not path.exists() and path is PLAYGROUND: continue
            text = read(path)
            for token in ORBIT_TOKENS:
                self.assertNotIn(token, text, "%s still contains %r" % (name, token))
        for name, path in (("panel", PANEL), ("playground", PLAYGROUND)):
            if not path.exists() and path is PLAYGROUND: continue
            text = read(path)
            for token in PANEL_PLAYGROUND_TOKENS:
                self.assertNotIn(token, text, "%s still contains %r" % (name, token))

    def test_uniforms_in_sync(self):
        frag_names = shader_uniforms(read(WAVE_FRAG))
        panel_props = panel_properties(read(PANEL))
        playground = playground_uniforms(read(PLAYGROUND)) if HAS_PLAYGROUND else None
        for name in frag_names:
            if name in ("qt_Matrix", "qt_Opacity"):
                continue
            self.assertIn(name, panel_props, "Panel.qml missing property for uniform %r" % name)
            if playground is not None:
                self.assertIn(name, playground, "playground UNIFORMS missing %r" % name)

    def test_light_styles_kept(self):
        frag = read(WAVE_FRAG)
        self.assertIn("float lightAmount(", frag)
        self.assertIn("vec2 lightPos()", frag)
        for entry in ("uLightStyle", "uLightPath"):
            self.assertIn(entry, shader_uniforms(frag), "wave.frag lost uniform %r" % entry)
        panel = read(PANEL)
        self.assertIn("function wantedLightStyle() { return 1 }", panel)


if __name__ == "__main__":
    unittest.main()
