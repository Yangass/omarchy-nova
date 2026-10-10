#!/usr/bin/env python3
"""Compile assets/shaders/wave.frag into the shipped plugin file wave.frag.qsb.

The shader body is shared with previews/ui02d-wave/index.html (the wave playground) so the browser
preview and the panel draw the same wave. --check verifies that the two bodies
match and, when qsb is available, that the committed .qsb matches its source.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets/shaders/wave.frag"
OUTPUT = ROOT / "wave.frag.qsb"
PREVIEW = ROOT / "previews/ui02d-wave/index.html"
BEGIN = "// ---- shared body: identical in previews/ui02d-wave and assets/shaders/wave.frag ----"
END = "// ---- end shared body ----"
# Linux Quickshell renders through OpenGL or Vulkan; SPIR-V is always included.
QSB_ARGS = ["--glsl", "100 es,120,150"]


def shared_body(text: str, name: str) -> str:
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise SystemExit(f"{name}: shared shader body markers missing or duplicated")
    return text.split(BEGIN, 1)[1].split(END, 1)[0]


def find_qsb() -> str | None:
    return shutil.which("qsb") or next((p for p in ("/usr/lib/qt6/bin/qsb",) if Path(p).is_file()), None)


def compile_to(qsb: str, target: Path) -> None:
    subprocess.run([qsb, *QSB_ARGS, "-o", str(target), str(SOURCE)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args()
    if shared_body(SOURCE.read_text(), "wave.frag") != shared_body(PREVIEW.read_text(), "preview"):
        print("FAIL shader body differs between wave.frag and the UI-02D playground")
        return 1
    qsb = find_qsb()
    if not args.check:
        if not qsb: raise SystemExit("qsb not found; install qt6-shadertools")
        compile_to(qsb, OUTPUT)
        print(f"wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size} bytes)")
        return 0
    if not OUTPUT.is_file():
        print("FAIL wave.frag.qsb is missing")
        return 1
    if not qsb:
        print("SKIP qsb unavailable; body sync verified only")
        return 0
    with tempfile.TemporaryDirectory() as temporary:
        fresh = Path(temporary) / "wave.frag.qsb"
        compile_to(qsb, fresh)
        if fresh.read_bytes() != OUTPUT.read_bytes():
            print("FAIL wave.frag.qsb is stale; run tools/build_wave_shader.py")
            return 1
    print("PASS shader body in sync and wave.frag.qsb matches its source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
