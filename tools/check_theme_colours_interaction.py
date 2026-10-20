#!/usr/bin/env python3
"""The panel takes the Omarchy theme's colours on the installed Qt (2026-10-20).

Qt 6.12's QtQuick `Color` singleton shadowed Omarchy's `qs.Commons` Color in plugin
files: every theme colour came back undefined and NOVA's labels turned black. This loads
the canonical Panel.qml with the real Omarchy modules and the system Qt, then checks the
panel's colours equal Commons.Color's, that no shown text is Qt's default black (unless
the theme's own foreground is black), and that no colour binding failed.
"""
import json
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview


QML_CHECK = r'''
  TestCase {
    name: "ThemeColours"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function same(a, b) { return Qt.colorEqual(a, b) }
    // Every shown text under the panel: items, the popup windows' content and their data.
    function texts(item, out, seen) {
      if (!item || seen.indexOf(item) >= 0) return out
      seen.push(item)
      if (item.visible === false) return out
      if (item.text !== undefined && item.color !== undefined && item.font !== undefined
          && String(item.text).length > 0) out.push(item)
      var lists = [item.children, item.data]
      for (var l = 0; l < lists.length; l++) {
        var kids = lists[l] || []
        for (var i = 0; i < kids.length; i++) texts(kids[i], out, seen)
      }
      if (item.contentItem && item.contentItem !== item) texts(item.contentItem, out, seen)
      return out
    }
    function test_theme_colours() {
      var results=[]
      function test(name,fn){try{fn();results.push({name:name,passed:true})}catch(e){results.push({name:name,passed:false,error:String(e)})}}
      wait(300)
      var theme = Commons.Color
      test("panel foreground, muted and accent are the theme's", function() {
        check(same(novaPanel.foreground, theme.foreground), "foreground " + novaPanel.foreground + " vs theme " + theme.foreground)
        check(same(novaPanel.muted, theme.muted), "muted " + novaPanel.muted + " vs theme " + theme.muted)
        check(same(novaPanel.accent, theme.accent), "accent " + novaPanel.accent + " vs theme " + theme.accent)
      })
      test("status and delete colours come from the theme", function() {
        check(same(novaPanel.statusColor, novaPanel.online ? theme.accent : (novaPanel.novaSetupPending ? theme.muted : theme.urgent)),
              "statusColor " + novaPanel.statusColor)
        check(!same(novaPanel.destructiveColor, "#000000"), "delete colour is black")
      })
      test("no shown text is Qt's default black", function() {
        var all = texts(novaPanel, [], []), black = []
        check(all.length > 10, "found only " + all.length + " texts")
        var themeBlack = same(theme.foreground, "#000000")
        for (var i = 0; i < all.length; i++)
          if (same(all[i].color, "#000000") && !themeBlack) black.push(String(all[i].text).slice(0, 30))
        check(black.length === 0, black.length + " of " + all.length + " texts are black: " + black.slice(0, 6).join(" | "))
      })
      console.warn("THEME_COLOURS_RESULTS "+JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["verified-awaiting-activation"]
    with tempfile.TemporaryDirectory(prefix="nova-theme-colours-", dir="/tmp") as raw:
        target = Path(raw)
        shell = preview.prepare_config(target, fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        if "import qs.Commons as Commons\n" not in text:
            text = text.replace("import qs.Commons\n", "import qs.Commons\nimport qs.Commons as Commons\n", 1)
        text = text.rsplit("\n}", 1)[0] + QML_CHECK + "\n}\n"
        shell.write_text(text)
        result = subprocess.run(["quickshell", "--path", str(target)], capture_output=True,
                                text=True, timeout=45)
        output = result.stdout + result.stderr
    marker = "THEME_COLOURS_RESULTS "
    line = next((row.split(marker, 1)[1] for row in output.splitlines() if marker in row), None)
    if line is None:
        print(output)
        raise SystemExit("Theme colours harness did not complete")
    rows = json.loads(line)
    failed_bindings = [row.strip() for row in output.splitlines()
                       if ("Unable to assign [undefined] to QColor" in row or "TypeError" in row)
                       and any(name in row for name in ("Panel.qml", "NovaMark.qml", "shell.qml"))]
    rows.append({"name": "no colour binding failed while loading", "passed": not failed_bindings,
                 "error": "; ".join(failed_bindings[:5])})
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] +
              ((": " + row["error"]) if not row["passed"] else ""))
    if any(not row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
