#!/usr/bin/env python3
"""MACHINE telemetry box (2026-10-07, after fresh-VM run 5).

Run under a private compositor. Checks that readings the machine cannot report are
hidden (not shown as empty boxes), that a changing value never moves the graph,
and that colour warms up only near the limit. NOVA_TELEMETRY_CAPTURE=<dir> saves
screenshots of the three cases.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview

QML_CHECK = r'''
  TestCase {
    name: "TelemetryInteraction"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r }
    function capture(name) {
      if (!"@CAPTURE@") return
      var done = false
      item("panelKeyCatcher").parent.parent.grabToImage(function(image) { image.saveToFile("@CAPTURE@/" + name + ".png"); done = true })
      var n = 0; while (!done && n++ < 100) wait(20)
    }
    function host(values) { for (var key in values) previewHost[key] = values[key]; wait(120) }
    function test_telemetry() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      novaPanel.onboardingState = "READY"; novaPanel.onboardingCodes = []
      // Telemetry lives in the MACHINE view (UX Phase 2).
      novaPanel.panelView = "machine"
      wait(300)
      test("a GPU machine shows every reading in one framed box", function() {
        host({gpuAvailable:true, gpu:2, vramUsed:23.8, vramTotal:31.8, cpu:3, memory:25, memoryUsed:7.5, memoryTotal:30.5,
              disk:58, diskUsed:114.7, diskTotal:198, systemTemperature:62, temperatureLabel:"nvme:fixture",
              gpuHistory:[0,4,8,3,7,2,1,9,12,4,2,2,3,30,64,80,41,12,4,2], cpuHistory:[8,12,10,16,18,6,4,3,5,9,3,2,3,4,8,12,3,2,4,3]})
        check(item("telemetryTitle").text === "MACHINE", "title: " + item("telemetryTitle").text)
        ;["telemetryGpu", "telemetryGpuMemory", "telemetryCpu", "telemetryMemory", "telemetryDisk"].forEach(function(name) {
          check(item(name).visible, name + " hidden") })
        check(item("telemetryDisk").value === "83 GiB free", "disk: " + item("telemetryDisk").value)
        check(item("telemetryTemperature").visible && item("telemetryTemperature").text === "SSD 62°C", "temperature: " + item("telemetryTemperature").text)
        check(item("telemetryFrame").height < 140, "box too tall: " + item("telemetryFrame").height)
        capture("telemetry-gpu")
      })
      test("a changing value never moves the graph", function() {
        var row = item("telemetryGpuMemory"), widths = []
        ;[1.2, 23.8, 9.0].forEach(function(v) { host({vramUsed:v}); widths.push(row.children[2].width + "@" + row.children[2].x) })
        check(widths[0] === widths[1] && widths[1] === widths[2], "graph moved: " + widths)
      })
      test("a machine without GPU or sensor shows no empty boxes", function() {
        host({gpuAvailable:false, gpu:-1, vramUsed:-1, vramTotal:-1, systemTemperature:-1, cpu:7, memory:40, memoryUsed:3.1, memoryTotal:7.7,
              disk:41, diskUsed:15.6, diskTotal:38})
        check(!item("telemetryGpu").visible && !item("telemetryGpuMemory").visible, "GPU rows shown without a GPU")
        check(!item("telemetryTemperature").visible, "temperature shown without a sensor")
        check(item("telemetryCpu").visible && item("telemetryMemory").visible && item("telemetryDisk").visible, "CPU/RAM/disk hidden")
        capture("telemetry-no-gpu")
      })
      test("colour warms up only near the limit", function() {
        host({gpuAvailable:true, gpu:97, vramUsed:30.9, vramTotal:31.8, memory:88, memoryUsed:26.8, systemTemperature:86})
        check(Qt.colorEqual(item("telemetryGpuMemory").tint, Color.urgent), "97% memory not urgent")
        check(!Qt.colorEqual(item("telemetryMemory").tint, novaPanel.accent) && !Qt.colorEqual(item("telemetryMemory").tint, Color.urgent), "88% not warm")
        host({memory:40, memoryUsed:12.2})
        check(Qt.colorEqual(item("telemetryMemory").tint, novaPanel.accent), "40% not neutral")
        capture("telemetry-hot")
      })
      console.warn("TELEMETRY_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    with tempfile.TemporaryDirectory(prefix="nova-telemetry-", dir="/tmp") as raw:
        shell = preview.prepare_config(Path(raw), fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        check = QML_CHECK.replace("@CAPTURE@", os.environ.get("NOVA_TELEMETRY_CAPTURE", ""))
        shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
        result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
    line = next((row.split("TELEMETRY_RESULTS ", 1)[1] for row in output.splitlines() if "TELEMETRY_RESULTS " in row), None)
    if line is None:
        print(output)
        raise SystemExit("Telemetry check did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
    errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
    if errors or any(not row["passed"] for row in rows):
        print("\n".join(errors) or output[-3000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
