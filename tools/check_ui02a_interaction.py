#!/usr/bin/env python3
"""Isolated canonical-panel checks for the UI-02A header and model controls."""
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import tempfile
import time

import nova_install as install
import onboarding_preview as preview


ROOT = Path(__file__).resolve().parents[1]
LONG_ID = "MyCaseSensitive-Long-Local-Model-Alias-with-MixedCASE:Latest"
QML_CHECK = r'''
  TestCase {
    name: "Ui02aPanel"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function child(name) { var item = findChild(novaPanel, name); check(item !== null, "missing " + name); return item }
    function capture(item, name) {
      if (!"@CAPTURE@") return
      var finished = false, saved = false
      item.grabToImage(function(result) { saved = result.saveToFile("@CAPTURE@/" + name + ".png"); finished = true })
      var count = 0; while (!finished && count++ < 100) wait(20)
      check(finished && saved, "capture failed: " + name)
    }
    function test_presentation() {
      var results = [], geometry = {}
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (error) { results.push({name:name, passed:false, error:String(error)}) }
      }
      var header = child("statusHeader"), build = child("uiBuildText")
      var brain = child("brainLabel"), link = child("linkStatus")
      var robot = child("novaRobot"), title = child("novaTitle"), online = child("onlineIndicator")
      var identityTarget = child("brainHeaderTarget"), identityTooltip = child("brainIdentityTooltip")
      var list = child("modelList"), cue = child("modelScrollCue"), thumb = child("modelScrollThumb")
      test("packaged identity and honest development fallback", function() {
        check(build.text === @EXPECTED@, "wrong loaded UI identity: " + build.text)
        check(novaPanel.uiIdentityText("bad metadata") === "UI build unknown · dev", "malformed metadata promoted")
        check(novaPanel.uiIdentityText(null) === "UI build unknown · dev", "missing metadata promoted")
        check(novaPanel.uiIdentityText("v0.12.0 · ui-abcdef123456") === "v0.12.0 · ui-abcdef123456",
              "well-formed package identity rejected")
        geometry.identity = build.text
      })
      test("ONLINE, OFFLINE and long identity stay in header bounds", function() {
        novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingExplicitSelection = true
        novaPanel.onboardingSelectedProvider = "ollama"
        novaPanel.onboardingSelectedModel = @LONG_ID@
        previewHost.online = true
        wait(60)
        check(identityTooltip.text.indexOf("Selected model: ollama / " + @LONG_ID@) === 0,
              "tooltip lost literal selected identity: " + identityTooltip.text)
        check(identityTarget.Accessible.name === identityTooltip.text,
              "accessible name lost literal selected identity")
        check(link.x + link.width <= build.x && brain.x + brain.width <= header.width + 1,
              "status text overlaps metadata")
        check(build.x >= 0 && build.x + build.width <= header.width + 1,
              "build label leaves header")
        check(robot.settings !== undefined && robot.settings.coreAngle === 225 && robot.width > 0 && robot.x >= 0,
              "NOVA mark is missing or outside header")
        check(robot.x + robot.width <= title.x && title.x + title.width <= online.x,
              "robot, title and ONLINE overlap")
        check(online.x + online.width <= header.width + 1 && robot.y >= 0
              && robot.y + robot.height <= header.height + 1,
              "robot or ONLINE leaves header")
        check(brain.width > 0 && online.x >= 0, "narrow header has no text allocation")
        capture(header, "online-header")
        previewHost.online = false
        wait(60)
        check(link.text === "NOVA is offline", "offline link is not truthful: " + link.text)
        check(build.text === @EXPECTED@, "offline state changed build identity")
        capture(header, "offline-header")
        previewHost.online = true
      })
      test("brain bar keeps its width while the status sample ages", function() {
        // Konstantin 2026-10-07: "LIVE NOW" / "UPDATED 5S AGO" resized the brain bar.
        var widths = []
        ;[0, 7000, 40000, 0].forEach(function(age) {
          previewHost.updatedAt = Date.now() - age
          novaPanel.clockTick++
          wait(40)
          widths.push(brain.width)
        })
        check(widths.every(function(w) { return w === widths[0] }), "brain bar width changed with sample age: " + widths)
        check(brain.x + brain.width <= header.width + 1, "brain bar leaves header")
      })
      test("stale telemetry sample is said in the footer, once", function() {
        var hint = child("refreshHint")
        previewHost.updatedAt = Date.now(); novaPanel.clockTick++; wait(40)
        check(hint.text === "LIVE", "fresh footer: " + hint.text)
        previewHost.updatedAt = Date.now() - 7000; novaPanel.clockTick++; wait(40)
        check(hint.text === "LIVE", "one late poll must not read as stale: " + hint.text)
        previewHost.updatedAt = Date.now() - 45000; novaPanel.clockTick++; wait(40)
        check(hint.text === "STALE · 45S", "stale footer: " + hint.text)
        previewHost.updatedAt = Date.now(); novaPanel.clockTick++; wait(40)
        check(hint.text === "LIVE", "footer did not recover: " + hint.text)
      })
      test("literal switching target and no stale identity while checking or unselected", function() {
        novaPanel.modelSwitching = true
        novaPanel.requestedModel = @LONG_ID@
        wait(30)
        check(identityTooltip.text === "Switching to model: " + @LONG_ID@,
              "switching tooltip lost literal target")
        check(identityTarget.Accessible.name === identityTooltip.text,
              "switching accessible name lost literal target")
        novaPanel.modelSwitching = false
        novaPanel.requestedModel = ""
        novaPanel.onboardingSnapshotFresh = false
        wait(30)
        check(identityTooltip.text === "Checking model setup" && identityTarget.Accessible.name === identityTooltip.text,
              "checking state leaked stale selected identity")
        novaPanel.onboardingSnapshotFresh = true
        novaPanel.onboardingExplicitSelection = false
        wait(30)
        check(identityTooltip.text === "No model selected" && identityTarget.Accessible.name === identityTooltip.text,
              "no-selection state leaked stale selected identity")
      })
      test("loaded generation stays labelled after backing metadata changes", function() {
        var first = build.text
        console.warn("UI02A_PANEL_CREATED")
        wait(500)
        check(build.text === first, "backing file change relabelled loaded panel")
      })
      // UX Phase 2: the MODELS list is full height; give it enough rows to overflow on any screen.
      function manyModels() {
        var many = [{id:"qwen3:8b",provider:"ollama",available:true}]
        for (var m = 0; m < 12; m++) many.push({id:"fixture-model-" + m + ":latest",provider:"ollama",available:true})
        many.push({id:@LONG_ID@,provider:"ollama",available:true})
        return many
      }
      test("overflow cue tracks top, middle, bottom and disappears without overflow", function() {
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:manyModels()})); wait(80)
        check(cue.visible && cue.height === list.height, "overflow cue missing or outside viewport")
        list.contentY = 0; wait(40)
        check(Math.abs(thumb.y) <= 1, "top position wrong")
        var range = list.contentHeight - list.height
        list.contentY = range / 2; wait(40)
        check(Math.abs(thumb.y - (cue.height - thumb.height) / 2) <= 1, "middle position wrong")
        list.contentY = range; wait(40)
        check(Math.abs(thumb.y + thumb.height - cue.height) <= 1, "bottom position wrong")
        geometry.scroll = {viewport:list.height, content:list.contentHeight, top:0,
                           middle:(cue.height-thumb.height)/2, bottom:cue.height-thumb.height}
        capture(child("modelSelector"), "bottom-selector")
        list.contentHeight = list.height - 1; wait(40)
        check(!cue.visible, "non-overflow content retained cue")
        list.contentHeight = Qt.binding(function() { return child("modelColumn").implicitHeight })
        wait(40)
        check(cue.visible, "content-height binding did not recover")
      })
      test("refresh, details and catalog disclosure update the cue", function() {
        var before = list.contentHeight
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:[
          {id:"qwen3:8b",provider:"ollama",available:true}
        ]})); wait(50)
        check(list.contentHeight < before, "inventory refresh did not change scroll range")
        novaPanel.applyInstalledModelsResult(200, JSON.stringify({models:manyModels()})); wait(50)
        check(list.contentHeight >= before && cue.visible, "restored inventory did not restore cue")
        var row = child("installedModelsRepeater").itemAt(0)
        var details = findChild(row, "installedModelDetailsButton")
        var originalHeight = details.height, originalWidth = details.width
        check(details.focusable && details.enabled && details.opacity === 1, "enabled Details is not clear/focusable")
        // MODELS v2: DETAILS is a quiet text button on the facts line (no fill, no border).
        check(!details.bordered && details.text === "DETAILS", "Details is not the quiet text button")
        details.forceActiveFocus(); wait(30)
        check(details.activeFocus && details._showFocusRing, "enabled Details has no visible focus state")
        details.enabled = false; wait(40)
        check(details.opacity < 0.6 && details.height === originalHeight && details.width === originalWidth,
              "disabled Details lost size or distinction")
        details.enabled = true
        novaPanel.toggleInstalledModelDetails(row.modelData); wait(50)
        check(list.contentHeight > before && cue.visible, "expanded details did not update content range")
        novaPanel.toggleCatalogDownloads(); wait(50)
        check(list.contentHeight > before, "expanded catalog did not update scroll range")
        var catalogDetails = findChild(child("catalogModelRepeater").itemAt(0), "modelDetailsButton")
        check(catalogDetails && catalogDetails.focusable && catalogDetails.opacity === 1,
              "catalog Details lost focus/contrast")
        list.contentY = 0
        capture(child("modelSelector"), "details-selector")
      })
      if (@PACKAGED@) {
        Commons.Color.foreground = "#f1dfbf"; Commons.Color.background = "#102230"
        Commons.Color.accent = "#77cfc2"; Commons.Color.muted = "#a6b5b6"
        wait(50)
        capture(header, "alternate-header")
        capture(child("modelSelector"), "alternate-selector")
      }
      console.warn("UI02A_RESULTS " + JSON.stringify({cases:results,geometry:geometry}))
    }
  }
'''


def run_case(raw: Path, *, packaged: bool, screenshots: Path | None) -> dict:
    fixture = preview.scenarios()["installed-no-selection"]
    fixture["models_expanded"] = True
    fixture["discovered_models"] = [
        {"id": model, "provider": "ollama", "available": True}
        for model in ("qwen3:8b", "qwen3:14b", "qwen3.8:27b-q4_K_M", "nova-code-qwen38-96k:latest", LONG_ID)
    ]
    raw.mkdir()
    shell = preview.prepare_config(raw, fixture)
    panel = raw / "Panel.qml"
    if packaged:
        home = raw / "isolated-home"; home.mkdir()
        staged = install.Installer(install.Paths(ROOT, home), Path("/usr/bin/python3"), "copy")._stage_plugin()
        panel.unlink(); shutil.copyfile(staged / "Panel.qml", panel)
        identity = re.search(r'packagedUiIdentity: "([^"]+)"', panel.read_text()).group(1)
        shutil.copyfile(staged / "manifest.json", raw / "manifest.json")
        shutil.copyfile(staged / "BarWidget.qml", raw / "BarWidget.qml")
    else:
        identity = "UI build unknown · dev"
    source = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
    checks = (QML_CHECK.replace("@EXPECTED@", json.dumps(identity, ensure_ascii=False))
             .replace("@LONG_ID@", json.dumps(LONG_ID))
             .replace("@CAPTURE@", str(screenshots) if screenshots else "")
             .replace("@PACKAGED@", "true" if packaged else "false"))
    shell.write_text(source.rsplit("\n}", 1)[0] + checks + "\n}\n")
    process = subprocess.Popen(["quickshell", "--path", str(raw)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    deadline = time.monotonic() + 40
    changed_after_creation = False
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill(); process.wait()
                raise RuntimeError("UI-02A fixture timed out")
            events = selector.select(timeout=remaining)
            if not events:
                continue
            line = process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if packaged and "UI02A_PANEL_CREATED" in line:
                (raw / "manifest.json").write_text('{"version":"99.0.0"}')
                (raw / "BarWidget.qml").write_text("// changed after panel creation\n")
                changed_after_creation = True
    result_code = process.wait(timeout=2)
    output = "".join(lines)
    if packaged and not changed_after_creation:
        raise RuntimeError("packaged panel did not signal creation before metadata change")
    marker = "UI02A_RESULTS "
    report = next((json.loads(line.split(marker, 1)[1]) for line in output.splitlines() if marker in line), None)
    if report is None:
        print(output)
        raise RuntimeError("UI-02A fixture did not complete")
    for case in report["cases"]:
        print(("PASS " if case["passed"] else "FAIL ") + case["name"] +
              ((": " + case["error"]) if not case["passed"] else ""))
    print("GEOMETRY " + json.dumps(report["geometry"], sort_keys=True))
    if result_code or any(not case["passed"] for case in report["cases"]):
        print(output)
        raise RuntimeError("UI-02A fixture failed")
    return report


def main():
    with tempfile.TemporaryDirectory(prefix="nova-ui02a-", dir="/tmp") as directory:
        root = Path(directory)
        images = Path(os.environ.get("NOVA_UI02A_EVIDENCE_DIR", str(root / "screenshots")))
        images.mkdir(parents=True, exist_ok=True)
        run_case(root / "packaged", packaged=True, screenshots=images)
        run_case(root / "development", packaged=False, screenshots=None)
        print("PASS isolated packaged and development panels; no production API")


if __name__ == "__main__":
    main()
