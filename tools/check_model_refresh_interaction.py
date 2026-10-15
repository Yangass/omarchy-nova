#!/usr/bin/env python3
"""Exercise the real bounded model-refresh REQUEST path in Panel.qml.

Unlike check_model_status_interaction.py (pure logic), this drives
``novaPanel.refreshModels()`` end-to-end against a stubbed provider so the
actual XMLHttpRequest path is covered: provider failure with a visible retry
control, recovery after retry, a stalled request that releases the loading
guard through the bounded QML deadline, a late reply that must not overwrite a
newer response, a malformed 200 body being treated as failure while a valid
empty ``{"models":[]}`` body is success, and overlap protection that allows
exactly one in-flight request.

On top of the pure request-path cases, this harness verifies the combined
UI + request behavior required for the installed-model inventory:

- the recovered model id is rendered verbatim in the installed section after
  a successful refresh and after an error/retry, not only present in state;
- the normal "refresh installed inventory" button stays usable (enabled) after
  a success and after a valid empty result;
- an old reply that arrives while a newer request is still in flight cannot
  release the newer request's loading guard, change its in-flight state, or
  apply its own data;
- overlap is verified by the stub's captured ``/models`` request COUNT, not
  only by the serial counter.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import subprocess
import tempfile

import fake_nova
import onboarding_preview as preview


# Ordered responses, popped one per GET /models. Entry shape: (status, body, delay_ms)
SCRIPT = [
    (503, {"error": "provider_unavailable"}, 0),
    (200, {"models": [{"id": "fresh-m1", "provider": "ollama", "available": True}]}, 0),
    (200, {"models": [{"id": "stale", "provider": "ollama", "available": True}]}, 3500),
    (200, {"models": [{"id": "fresh-m2", "provider": "ollama", "available": True}]}, 0),
    (200, {"unexpected": "missing models array"}, 0),
    (200, {"models": []}, 0),
    (200, {"models": [{"id": "overlap", "provider": "ollama", "available": True}]}, 0),
    # T7: A is the older (slower) request, B the newer (faster) request.
    (200, {"models": [{"id": "inflight-a", "provider": "ollama", "available": True}]}, 2500),
    (200, {"models": [{"id": "inflight-b", "provider": "ollama", "available": True}]}, 1500),
]
SAFE_BODY = (200, {"models": []}, 0)

QML_CHECK = r'''
  NovaClient { id: fakeNova; socketPath: "@SOCKET@" }
  TestCase {
    name: "IsolatedModelRefreshInteraction"
    when: novaPanel.opened && novaPanel.catalogModels.length > 0
    function check(value, message) { if (!value) throw new Error(message) }
    function child(name) {
      var item = findChild(novaPanel, name)
      if (item === null) {
        var rows = findChild(novaPanel, "catalogModelRepeater")
        if (rows && rows.count) item = findChild(rows.itemAt(0), name)
      }
      check(item !== null, "Missing " + name)
      return item
    }
    function click(item) {
      var catcher = child("panelKeyCatcher"), point = item.mapToItem(catcher, item.width/2, item.height/2)
      mouseClick(catcher, point.x, point.y, Qt.LeftButton)
    }
    function installedRowLabel() {
      var row = findChild(novaPanel, "installedModelsRepeater")
      if (row === null || row.count < 1) return null
      return findChild(row.itemAt(0), "installedModelId")
    }
    function requestCount() {
      var count = null
      var xhr = fakeNova.request()
      xhr.onreadystatechange = function() {
        if (xhr.readyState === XMLHttpRequest.DONE && xhr.status === 200) {
          try { count = JSON.parse(xhr.responseText).count } catch (e) { count = null }
        }
      }
      xhr.open("GET", "/__requests__")
      xhr.send()
      var guard = 0
      while (count === null && guard < 200) { wait(10); guard++ }
      return count
    }
    function test_refresh_request_path() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name: name, passed: true}) }
        catch (e) { results.push({name: name, passed: false, error: String(e)}) }
      }
      previewHost.novaClient = fakeNova
      previewHost.acceptancePreview = false
      novaPanel.panelView = "models"
      test("T1_provider_failure_flags_unavailable_and_keeps_retry_visible", function() {
        novaPanel.refreshModels()
        wait(600)
        check(novaPanel.modelsUnavailable === true, "provider failure was not applied")
        check(novaPanel.discoveredModels.length === 0, "stale models remained after failure")
        var ctrl = child("modelsRefreshControl")
        check(ctrl.visible === true, "retry control was hidden after failure")
        check(ctrl.enabled === true, "retry control was disabled after failure")
      })
      test("T2_retry_recovers_inventory_and_renders_alias", function() {
        click(child("modelsRefreshControl"))
        wait(600)
        check(novaPanel.modelsUnavailable === false, "retry did not clear the failure state")
        check(novaPanel.discoveredModels.length === 1 && novaPanel.discoveredModels[0].id === "fresh-m1",
          "retry did not recover the model inventory")
        var label = installedRowLabel()
        check(label !== null && String(label.text) === "fresh-m1",
          "installed id not rendered verbatim after success: " + (label !== null ? label.text : ""))
        var normalBtn = findChild(novaPanel, "refreshInstalledModelsButton")
        check(normalBtn !== null && normalBtn.enabled === true, "normal refresh missing/disabled after success")
      })
      test("T3_stalled_request_releases_loading_guard_via_deadline", function() {
        check(novaPanel.installedModelsLoading === false, "guard was already busy before the stall test")
        novaPanel.refreshModels()
        check(novaPanel.installedModelsLoading === true, "loading guard was not taken on the stalled request")
        wait(2600)
        check(novaPanel.installedModelsLoading === false, "stalled request did not release the loading guard")
        check(novaPanel.modelsUnavailable === true, "stall was not treated as a failure")
        check(novaPanel.discoveredModels.length === 0, "stalled request leaked stale data")
      })
      test("T4_retry_after_stall_recovers_and_late_reply_cannot_overwrite", function() {
        novaPanel.refreshModels()
        wait(600)
        check(novaPanel.discoveredModels.length === 1 && novaPanel.discoveredModels[0].id === "fresh-m2",
          "retry after stall did not recover fresh inventory")
        var label = installedRowLabel()
        check(label !== null && String(label.text) === "fresh-m2",
          "installed id not rendered verbatim after error/retry: " + (label !== null ? label.text : ""))
        wait(2200)
        check(novaPanel.discoveredModels.length === 1 && novaPanel.discoveredModels[0].id === "fresh-m2",
          "late stale reply overwrote the newer inventory")
        check(novaPanel.modelsUnavailable === false, "late stale reply flipped the panel into failure")
      })
      test("T5a_malformed_200_response_is_a_failure", function() {
        novaPanel.refreshModels()
        wait(600)
        check(novaPanel.modelsUnavailable === true, "malformed 200 response was not flagged as failure")
        check(novaPanel.discoveredModels.length === 0, "malformed payload produced a fake empty inventory")
      })
      test("T5b_valid_empty_inventory_is_success_and_refresh_stays_usable", function() {
        novaPanel.refreshModels()
        wait(600)
        check(novaPanel.modelsUnavailable === false, "valid empty inventory was wrongly flagged as failure")
        check(novaPanel.discoveredModels.length === 0, "valid empty inventory produced models")
        var normalBtn = findChild(novaPanel, "refreshInstalledModelsButton")
        check(normalBtn !== null && normalBtn.enabled === true, "normal refresh missing/disabled after valid empty")
      })
      test("T6_overlap_guard_allows_exactly_one_request", function() {
        var baselineSerial = novaPanel.modelsRequestSerial
        var baselineCount = requestCount()
        check(baselineCount !== null, "could not read the stub's /models request count")
        novaPanel.refreshModels()
        var afterFirstSerial = novaPanel.modelsRequestSerial
        novaPanel.refreshModels()
        var afterSecondSerial = novaPanel.modelsRequestSerial
        check(afterFirstSerial === baselineSerial + 1, "first overlap request did not advance the serial")
        check(afterSecondSerial === afterFirstSerial, "second overlap request advanced the serial (duplicate request sent)")
        check(novaPanel.installedModelsLoading === true, "loading guard was not held across the duplicate call")
        var countAfter = requestCount()
        check(countAfter === baselineCount + 1, "expected exactly one new /models request, saw " + (countAfter - baselineCount))
        wait(600)
        check(novaPanel.installedModelsLoading === false, "T6 request did not settle and release the guard")
      })
      test("T7_old_reply_while_newer_request_in_flight_cannot_interfere", function() {
        // A: older, slower request. Its own 2s deadline fires first, so A is
        // already failed (finished=true) before its real reply lands.
        novaPanel.refreshModels()
        check(novaPanel.installedModelsLoading === true, "A guard not held")
        wait(2400)
        check(novaPanel.installedModelsLoading === false, "A's deadline did not release the guard")
        check(novaPanel.modelsUnavailable === true, "A was not treated as a failure")
        // B: newer request, in flight. A's stale reply arrives during this window.
        novaPanel.refreshModels()
        check(novaPanel.installedModelsLoading === true, "B guard not held")
        wait(800)
        check(novaPanel.installedModelsLoading === true, "stale A reply released B's loading guard while B in flight")
        check(novaPanel.modelsUnavailable === true, "stale A reply changed B's in-flight state")
        check(novaPanel.discoveredModels.length === 0, "stale A reply applied data while B in flight")
        // Let B settle; the newer request must win.
        wait(1200)
        check(novaPanel.discoveredModels.length === 1 && novaPanel.discoveredModels[0].id === "inflight-b",
          "newer request B did not win over the stale A reply")
        check(novaPanel.modelsUnavailable === false, "newer request B did not clear the failure state")
      })
      console.warn("MODEL_REFRESH_RESULTS " + JSON.stringify(results))
    }
  }
'''


class _Stub(BaseHTTPRequestHandler):
    def _send(self, status, body):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/__requests__":
            with SCRIPT_LOCK:
                count = REQUEST_COUNT[0]
            self._send(200, {"count": count})
            return
        if self.path != "/models":
            self._send(404, {"error": "not_found"})
            return
        with SCRIPT_LOCK:
            REQUEST_COUNT[0] += 1
            if SCRIPT:
                status, body, delay = SCRIPT.pop(0)
            else:
                status, body, delay = SAFE_BODY
        if delay:
            time.sleep(delay / 1000.0)
        self._send(status, body)

    def log_message(self, *args):
        pass


SCRIPT_LOCK = threading.Lock()
REQUEST_COUNT = [0]


def main():
    fixture = preview.scenarios()["verified-awaiting-activation"]
    server = fake_nova.server(_Stub)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="nova-model-refresh-", dir="/tmp") as raw:
            target = Path(raw)
            shell = preview.prepare_config(target, fixture)
            text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
            text = text.rsplit("\n}", 1)[0] + QML_CHECK.replace("@SOCKET@", server.server_address) + "\n}\n"
            shell.write_text(text)
            result = subprocess.run(["quickshell", "--path", str(target)], capture_output=True,
                                    text=True, timeout=90)
            output = result.stdout + result.stderr
    finally:
        server.shutdown()
    marker = "MODEL_REFRESH_RESULTS "
    line = next((row.split(marker, 1)[1] for row in output.splitlines() if marker in row), None)
    if line is None:
        print(output)
        raise SystemExit("Model refresh interaction harness did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] +
              ((": " + row["error"]) if not row["passed"] else ""))
    if any(not row["passed"] for row in rows):
        print(output)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
