#!/usr/bin/env python3
"""UX Phase 2 (2026-10-09, docs/UX_REVIEW_2026-10-08.md, previews/ux-phase2).

Run under a private compositor. Three views in the panel: CHAT (core band, the
conversation, the message box), MODELS (the model list at full height) and MACHINE
(big core, telemetry). Tabs are words; the model bar and Alt+1/2/3 switch views; an
approval always shows in CHAT; NOVA reopens on the last view.
NOVA_PHASE2_CAPTURE=<dir> saves one screenshot per view.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview


QML_CHECK = r'''
  TestCase {
    name: "UxPhase2"
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) {
      if (name.indexOf("viewTab_") === 0) {
        var tabs = findChild(novaPanel, "viewTabRepeater"); check(tabs !== null, "missing viewTabRepeater")
        return tabs.itemAt(["chat", "models", "machine"].indexOf(name.slice(8)))
      }
      var r = findChild(novaPanel, name); check(r !== null, "missing " + name); return r
    }
    function shown(name) { var r = item(name); while (r) { if (!r.visible) return false; r = r.parent } return true }
    function capture(name) {
      if (!"@CAPTURE@") return
      var done = false
      item("panelKeyCatcher").parent.parent.grabToImage(function(image) { image.saveToFile("@CAPTURE@/" + name + ".png"); done = true })
      var n = 0; while (!done && n++ < 100) wait(20)
    }
    function test_phase2() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name:name, passed:true}) }
        catch (e) { results.push({name:name, passed:false, error:String(e)}) }
      }
      novaPanel.onboardingState = "READY"; novaPanel.onboardingCodes = []
      test("NOVA reopens on the last view (saved MACHINE before this start)", function() {
        // The preview fixture already picked MODELS; start over as a fresh panel would.
        novaPanel.panelViewChosen = false
        previewHost.acceptancePreview = false; wait(200)  // the real panel reads its saved view
        check(novaPanel.panelView === "machine", "started on " + novaPanel.panelView)
      })
      novaPanel.addChat("user", "What's using my GPU?")
      novaPanel.addChat("assistant", "Qwen 3 8B is loaded in GPU memory.")
      novaPanel.showView("chat")
      wait(200)
      test("three tabs in words; CHAT is first and current", function() {
        check(item("viewTab_chat").current && !item("viewTab_models").current && !item("viewTab_machine").current, "current tab")
        check(item("viewTab_chat").x < item("viewTab_models").x && item("viewTab_models").x < item("viewTab_machine").x, "order")
        check(item("viewTabs").height > 0, "tab row hidden")
      })
      test("CHAT: core band, the conversation and the message box; no model list or telemetry", function() {
        check(shown("corePanel") && Math.abs(item("corePanel").height - novaPanel.coreBandHeight) < 1, "band: " + item("corePanel").height)
        check(shown("chatViewport") && shown("chatInput"), "conversation or input hidden")
        check(!shown("modelSelector") && !shown("telemetryFrame"), "dashboard parts in CHAT")
        check(findChild(novaPanel, "chatTitle") === null, "old CHAT / EXPAND CHAT row still there")
        capture("view-chat")
      })
      test("MODELS: the list at full height, no core and no message box", function() {
        novaPanel.showView("models"); wait(300)  // entering MODELS refreshes the list; set fixture rows after
        novaPanel.installedModelsLoading = false; novaPanel.modelsUnavailable = false
        novaPanel.discoveredModels = [
          {id:"nova-code-qwen38-96k:latest", provider:"ollama", available:true, resident:true, vram_bytes:17716740096, metadata:{size_bytes:17741872168}},
          {id:"qwen3.8:27b-q4_K_M", provider:"ollama", available:true, resident:false, vram_bytes:null, metadata:{size_bytes:17741872132}}]
        wait(150)
        check(item("viewTab_models").current, "tab not current")
        var list = item("modelSelector")
        check(shown("modelSelector") && list.height > Style.space(250), "list height: " + list.height)
        check(!shown("corePanel") && !shown("chatViewport") && !shown("chatInputRow"), "chat parts in MODELS")
        capture("view-models")
        novaPanel.catalogDownloadsExpanded = true; wait(150)
        var list = item("modelList"); list.contentY = Math.max(0, item("downloadsTitle").mapToItem(item("modelColumn"), 0, 0).y - 140); wait(80)
        check(shown("downloadsTitle") && item("downloadsTitle").text.indexOf("DOWNLOADS · ") === 0, "downloads header")
        capture("view-downloads")
        list.contentY = 0; novaPanel.catalogDownloadsExpanded = false
      })
      test("MACHINE: the big core and the telemetry, no message box", function() {
        novaPanel.showView("machine"); wait(150)
        // Up to 200 px; on short screens it gives way so the readings and GPU memory fit.
        check(shown("corePanel") && item("corePanel").height >= Style.space(72) - 1 && item("corePanel").height <= Style.space(200) + 1, "core: " + item("corePanel").height)
        check(shown("telemetryFrame") && !shown("chatInputRow") && !shown("modelSelector"), "MACHINE contents")
        capture("view-machine")
      })
      test("the panel keeps one height across views", function() {
        var h = item("layoutArea").height
        novaPanel.showView("chat"); wait(100); check(Math.abs(item("layoutArea").height - h) < 1, "chat height changed")
        novaPanel.showView("models"); wait(100); check(Math.abs(item("layoutArea").height - h) < 1, "models height changed")
      })
      test("clicking a tab and the model bar switch views", function() {
        mouseClick(item("viewTab_chat")); wait(80)
        check(novaPanel.panelView === "chat", "tab click: " + novaPanel.panelView)
        mouseClick(item("brainHeaderTarget")); wait(80)
        check(novaPanel.panelView === "models", "model bar: " + novaPanel.panelView)
      })
      test("Alt+1, Alt+2, Alt+3 switch views, also while typing", function() {
        novaPanel.showView("chat"); wait(50); item("chatInput").forceActiveFocus()
        keyClick(Qt.Key_3, Qt.AltModifier); wait(50); check(novaPanel.panelView === "machine", "Alt+3: " + novaPanel.panelView)
        keyClick(Qt.Key_2, Qt.AltModifier); wait(50); check(novaPanel.panelView === "models", "Alt+2: " + novaPanel.panelView)
        keyClick(Qt.Key_1, Qt.AltModifier); wait(50); check(novaPanel.panelView === "chat", "Alt+1: " + novaPanel.panelView)
        check(item("chatInput").text === "", "Alt+digit typed into the message box: " + item("chatInput").text)
      })
      test("an approval opens in CHAT and NOVA stays there afterwards", function() {
        novaPanel.showView("models"); wait(50)
        novaPanel.beginApproval({ id: "act_fixture", action: "fixture", summary: "FIXTURE ACTION", expiresAt: Date.now() / 1000 + 600 })
        wait(100)
        check(novaPanel.shownView === "chat" && item("viewTab_chat").current, "approval not in CHAT: " + novaPanel.shownView)
        check(shown("approvalCard"), "approval card hidden")
        novaPanel.clearPendingApproval(); wait(80)
        check(novaPanel.panelView === "chat", "went back to " + novaPanel.panelView)
      })
      test("MODELS shows how many catalog models are new", function() {
        novaPanel.catalogModels = [{catalog_id:"qwen3-8b-q4-k-m", display_name:"Qwen 3 8B (Q4_K_M)", source:{upstream_id:"qwen3:8b"}},
                                   {catalog_id:"gemma3-12b", display_name:"Gemma 3 12B", source:{upstream_id:"gemma3:12b"}},
                                   {catalog_id:"llama3-2-3b", display_name:"Llama 3.2 3B", source:{upstream_id:"llama3.2:3b"}}]
        var ids = novaPanel.catalogModels.map(function(m) { return m.catalog_id })
        check(ids.length >= 2, "fixture catalog too small")
        novaPanel.seenCatalogIds = [ids[0]]; wait(80)
        check(item("viewTab_models").badge === (ids.length - 1) + " NEW", "badge: " + item("viewTab_models").badge)
        novaPanel.seenCatalogIds = ids; wait(80)
        check(item("viewTab_models").badge === "", "badge stayed: " + item("viewTab_models").badge)
      })
      test("MACHINE gets a warning dot when the GPU runs hot or memory or disk is nearly full", function() {
        check(!item("viewTab_machine").warning, "warning without cause")
        previewHost.gpuTemperature = 88; wait(80)
        check(item("viewTab_machine").warning, "hot GPU not flagged")
        previewHost.gpuTemperature = 60; previewHost.disk = 97; wait(80)
        check(item("viewTab_machine").warning, "full disk not flagged")
        previewHost.disk = 50; wait(80)
      })
      // 2026-10-09 (Konstantin's screenshot): after the 10-minute auto-unload NOVA said
      // MODEL NOT READY and blocked chat. A selected model out of GPU memory is ready.
      test("a selected model out of GPU memory still lets you chat; the box says it loads", function() {
        novaPanel.showView("chat")
        previewHost.activeProvider = "ollama"; previewHost.activeModel = "qwen3:8b"
        previewHost.activeModelAvailable = true; previewHost.activeModelRuntimeReady = false
        novaPanel.onboardingExplicitSelection = true
        novaPanel.onboardingSelectedProvider = "ollama"; novaPanel.onboardingSelectedModel = "qwen3:8b"
        novaPanel.onboardingState = "READY"; novaPanel.onboardingCodes = ["SELECTED_MODEL_NOT_LOADED"]
        novaPanel.onboardingSnapshotFresh = true; novaPanel.onboardingReceivedAt = Date.now()
        wait(80)
        check(novaPanel.readySnapshotCoherent({explicit_selection:true, provider:"ollama", model:"qwen3:8b"}, ["SELECTED_MODEL_NOT_LOADED"]),
              "an unloaded selection is treated as a readiness conflict")
        check(!novaPanel.readySnapshotCoherent({explicit_selection:true, provider:"ollama", model:"qwen3:8b"}, ["SELECTED_MODEL_READY"]),
              "a snapshot claiming loaded must still conflict with an unloaded runtime")
        check(novaPanel.onboardingCanSend, "chat blocked for an unloaded selection")
        check(novaPanel.chatPlaceholder() === "Message NOVA… (the model loads with your first message)", "placeholder: " + novaPanel.chatPlaceholder())
        check(novaPanel.onboardingTitle() !== "MODEL NOT READY", "title: " + novaPanel.onboardingTitle())
      })
      // 2026-10-09 (Konstantin): the band still looked squashed. The wave keeps MACHINE's
      // proportions in every view: same width-to-height ratio, centred, never flatter.
      test("the core keeps the same proportions in the CHAT band and in MACHINE", function() {
        // The scene fills the band (no hard-edged box); the dot field keeps the ratio.
        var region = item("coreAnimationRegion"), wave = item("livingWave")
        novaPanel.showView("machine"); wait(120)
        var machine = wave.uFieldW / wave.height
        novaPanel.showView("chat"); wait(120)
        var band = wave.uFieldW / wave.height
        check(Math.abs(band - machine) < 0.05 * machine || machine < novaPanel.coreAspect, "field ratio chat " + band.toFixed(2) + " vs machine " + machine.toFixed(2))
        check(Math.abs(band - novaPanel.coreAspect) < 0.05, "band field ratio " + band.toFixed(2))
        check(region.width >= item("corePanel").width - 2 * Style.spacing.sm - 1 && wave.width === region.width, "scene does not fill the band")
      })
      test("a message that loads the model says so right away", function() {
        novaPanel.showView("chat")
        previewHost.activeModelRuntimeReady = false
        novaPanel.onboardingCodes = ["SELECTED_MODEL_NOT_LOADED"]
        novaPanel.chatLoadsModel = true; novaPanel.chatBusy = true
        previewHost.runtimeState = "loading_model"; previewHost.updatedAt = Date.now(); wait(120)
        check(novaPanel.stateWords("loading_model") === "LOADING THE MODEL", "status: " + novaPanel.stateWords("loading_model"))
        var hint = item("modelLoadingHint")
        check(hint.visible && hint.text.indexOf("into GPU memory") > 0, "hint: " + hint.visible + " " + hint.text)
        check(!item("slowFirstReplyHint").visible, "two hints at once")
        novaPanel.chatLoadsModel = false; wait(50)
        check(novaPanel.stateWords("loading_model") === "PREPARING REPLY" && !hint.visible, "a loaded model still says it loads")
        novaPanel.chatBusy = false; previewHost.runtimeState = "idle"
      })
      // UX Phase 2 step 2b (mock): MACHINE also shows network speed, what's in GPU memory
      // (with FREE NOW) and the versions.
      test("MACHINE shows network speed, what's in GPU memory and the versions", function() {
        previewHost.netDown = 1572864; previewHost.netUp = 20480
        previewHost.netDownHistory = [0, 524288, 1572864]; previewHost.netUpHistory = [0, 10240, 20480]
        previewHost.gpuName = "NVIDIA GeForce RTX 5090"; previewHost.gpuDriver = "610.57"
        novaPanel.providerVersion = "0.33.3"
        novaPanel.showView("machine"); wait(300)
        novaPanel.installedModelsLoading = false; novaPanel.modelsUnavailable = false
        novaPanel.discoveredModels = [
          {id:"nova-code-qwen38-96k:latest", provider:"ollama", available:true, resident:true, vram_bytes:17716740096, metadata:{size_bytes:17741872168}},
          {id:"qwen3.8:27b-q4_K_M", provider:"ollama", available:true, resident:false, vram_bytes:null, metadata:{size_bytes:17741872132}}]
        wait(150)
        check(shown("telemetryNetDown") && item("telemetryNetDown").label === "NET ↓" && item("telemetryNetDown").value === "1.5 MiB/s", "down: " + item("telemetryNetDown").value)
        check(shown("telemetryNetUp") && item("telemetryNetUp").value === "20.0 KiB/s", "up: " + item("telemetryNetUp").value)
        check(Qt.colorEqual(item("telemetryNetDown").tint, novaPanel.accent), "network row warms up like a limit")
        var gpuRows = item("gpuMemoryRepeater")
        check(gpuRows.count === 1, "resident rows: " + gpuRows.count)
        var row = gpuRows.itemAt(0)
        check(findChild(row, "gpuMemoryModelName").text === "nova-code-qwen38-96k" && findChild(row, "gpuMemoryModelSize").text === "16.5 GiB", "row text")
        check(findChild(row, "freeGpuMemoryButton").visible && findChild(row, "freeGpuMemoryButton").text === "FREE NOW", "FREE NOW missing")
        // Run 7: a model in RAM (no graphics card) reports 0 graphics bytes; show its RAM size.
        novaPanel.discoveredModels = [{id:"llama3.2:1b", provider:"ollama", available:true, resident:true, vram_bytes:0, memory_bytes:1288490189}]; wait(80)
        check(findChild(item("gpuMemoryRepeater").itemAt(0), "gpuMemoryModelSize").text === "1.2 GiB in RAM", "CPU row: " + findChild(item("gpuMemoryRepeater").itemAt(0), "gpuMemoryModelSize").text)
        var versions = item("machineVersions").text
        check(versions.indexOf("NOVA " + novaPanel.displayUiIdentity) === 0 && versions.indexOf("Ollama 0.33.3") > 0 && versions.indexOf("NVIDIA GeForce RTX 5090 · driver 610.57") > 0, "versions: " + versions)
        check(item("corePanel").height >= Style.space(72) - 1 && item("machineDetailsViewport").y + item("machineDetailsViewport").height <= item("fixedBottom").y + 1
              && item("corePanel").height >= novaPanel.coreBandHeight - 1, "MACHINE overflows or its core is smaller than CHAT's band")
        capture("view-machine-full")
        novaPanel.discoveredModels = [{id:"qwen3.8:27b-q4_K_M", provider:"ollama", available:true, resident:false}]; wait(80)
        check(shown("gpuMemoryEmpty") && item("gpuMemoryEmpty").text.indexOf("Nothing is in GPU memory") === 0, "empty state")
        novaPanel.showView("chat")
      })
      // 2026-10-09 (Konstantin's screenshots): tooltips near the panel's edge were cut off.
      test("tooltips stay inside the panel, also on rows created later", function() {
        novaPanel.showView("models"); wait(300)
        novaPanel.installedModelsLoading = false; novaPanel.modelsUnavailable = false
        novaPanel.discoveredModels = [{id:"nova-code-qwen38-96k:latest", provider:"ollama", available:true, resident:true, vram_bytes:17716740096, metadata:{size_bytes:17741872168}}]
        wait(400)
        var tips = [], panelItem = item("panelKeyCatcher")
        function collect(o) { if (!o) return; var d = o.data || []; for (var i = 0; i < d.length; i++) { var c = d[i]; if (!c) continue; if (String(c).indexOf("ToolTip") >= 0) tips.push(c); if (c.data !== undefined) collect(c) } }
        collect(item("layoutArea"))
        check(tips.length > 5, "tooltips found: " + tips.length)
        var loose = tips.filter(function(t) { return !(t.margins >= 0) })
        check(loose.length === 0, loose.length + " tooltips may leave the panel")
        // A long tooltip on the leftmost tab really is placed inside the window.
        var chatTab = findChild(novaPanel, "viewTabRepeater").itemAt(0), tip = null
        for (var k = 0; k < tips.length; k++) if (tips[k].parent === chatTab) tip = tips[k]
        check(tip !== null, "CHAT tab tooltip missing")
        tip.text = "A deliberately long tooltip text that is far wider than the tab it belongs to"; tip.delay = 0; tip.visible = true; wait(200)
        // The panel window is wider than its frame: measure against the panel's content.
        var left = tip.contentItem.mapToItem(item("layoutArea"), 0, 0).x
        var right = tip.contentItem.mapToItem(item("layoutArea"), tip.contentItem.width, 0).x
        check(left >= -1 && right <= item("layoutArea").width + 1, "tooltip leaves the panel: " + left.toFixed(1) + ".." + right.toFixed(1) + " of " + item("layoutArea").width)
        tip.visible = false
        novaPanel.showView("chat")
      })
      // Run 6 (2026-10-10) polish: vendor-neutral graphics, Ollama not installed.
      test("run 6: VERSIONS names any graphics vendor and says when Ollama is missing", function() {
        var GiB = 1024 * 1024 * 1024
        previewHost.gpuAvailable = false
        novaPanel.graphicsDevices = null
        check(novaPanel.graphicsLine() === "Graphics: not checked yet", "unknown: " + novaPanel.graphicsLine())
        novaPanel.graphicsDevices = []
        check(novaPanel.graphicsLine() === "No graphics card detected", "none: " + novaPanel.graphicsLine())
        novaPanel.graphicsDevices = [{vendor: "amd", model: "Radeon RX 7800 XT", memory_kind: "dedicated", vram_total_bytes: 16 * GiB},
                                     {vendor: "intel", model: null, memory_kind: "unknown", vram_total_bytes: null},
                                     {vendor: "amd", model: "Navi 32", memory_kind: "unknown", vram_total_bytes: 16 * GiB}]
        check(novaPanel.graphicsLine() === "Radeon RX 7800 XT · 16.0 GiB\nINTEL graphics · shares main memory\nNavi 32", "devices: " + novaPanel.graphicsLine())
        novaPanel.providerVersion = ""; novaPanel.ollamaInstalled = false; wait(50)
        var versions = item("machineVersions").text
        check(versions.indexOf("Ollama not installed") > 0 && versions.indexOf("NVIDIA") < 0, "versions: " + versions)
        novaPanel.ollamaInstalled = true; wait(50)
        check(item("machineVersions").text.indexOf("Ollama version unknown") > 0, "installed, no version")
        previewHost.gpuAvailable = true; novaPanel.graphicsDevices = null; novaPanel.providerVersion = "0.33.3"
      })
      test("chat answers: light Markdown, and model output can never add tags or fetch anything", function() {
        var html = novaPanel.chatMarkdownHtml
        check(html("**What I am**") === "<b>What I am</b>", "bold: " + html("**What I am**"))
        check(html("an *every* action") === "an <i>every</i> action", "italic: " + html("an *every* action"))
        check(html("2 * 3 * 4") === "2 * 3 * 4", "lone stars stay: " + html("2 * 3 * 4"))
        check(html("**What I *don't* do**") === "<b>What I <i>don't</i> do</b>", "nested: " + html("**What I *don't* do**"))
        check(html("use `ollama list`").indexOf(">ollama list</span>") > 0, "code: " + html("use `ollama list`"))
        check(html("## Heading") === "<b>Heading</b>", "heading: " + html("## Heading"))
        check(html("- one\n  - two") === "•&nbsp;one<br>&nbsp;&nbsp;&nbsp;•&nbsp;two", "list: " + html("- one\n  - two"))
        check(html("1. first") === "1.&nbsp;first", "numbered: " + html("1. first"))
        check(html("```\nx <y>\n```").indexOf("<pre") === 0 && html("```\nx <y>\n```").indexOf("x &lt;y&gt;") > 0, "fence: " + html("```\nx <y>\n```"))
        check(html("```\nstill streaming").indexOf("<pre") === 0, "open fence while streaming")
        var hostile = html("<b>raw</b> <img src=\"http://127.0.0.1:1/x\"> ![pic](http://127.0.0.1:1/p.png) [site](http://127.0.0.1:1/l) <script>x</script> & done")
        check(hostile.indexOf("<img") < 0 && hostile.indexOf("<script") < 0 && hostile.indexOf("<a ") < 0 && hostile.indexOf("<b>raw") < 0, "tags leaked: " + hostile)
        check(hostile.indexOf("&lt;b&gt;raw&lt;/b&gt;") === 0 && hostile.indexOf(" pic ") > 0 && hostile.indexOf("site (http://127.0.0.1:1/l)") > 0 && hostile.indexOf("&amp; done") > 0, "shown as text: " + hostile)
      })
      test("catalog upkeep: VERSIONS dates the model list and nudges after 60 days", function() {
        var saved = novaPanel.catalogModels
        novaPanel.catalogModels = [{catalog_id: "a", metadata: {checked_at: "2026-09-08T12:21:36Z"}},
                                   {catalog_id: "b", metadata: {checked_at: "2026-10-10T10:38:48Z"}},
                                   {catalog_id: "requested-c", user_requested: true, metadata: {checked_at: "2026-12-30T00:00:00Z"}}]
        var now = Date.parse("2026-11-01T00:00:00Z")
        check(novaPanel.modelListLine(now) === "Model list 2026-10-10", "fresh: " + novaPanel.modelListLine(now))
        now = Date.parse("2026-12-10T00:00:00Z")
        check(novaPanel.modelListLine(now) === "Model list from 2026-10-10: update NOVA for newer models", "old: " + novaPanel.modelListLine(now))
        wait(50)
        check(item("machineVersions").text.indexOf("\nModel list") > 0, "not in VERSIONS: " + item("machineVersions").text)
        novaPanel.catalogModels = []
        check(novaPanel.modelListLine(now) === "", "empty list should say nothing")
        novaPanel.catalogModels = saved
      })
      test("run 6: offline, the model bar and MODELS name the step and the core offers no Pause", function() {
        previewHost.online = false; wait(80)
        var setup = novaPanel.novaSetupPending
        check(novaPanel.brainSummary() === (setup ? "MODEL · AFTER SETUP" : "MODEL · NOVA OFFLINE"), "bar: " + novaPanel.brainSummary())
        check(!findChild(novaPanel, "corePause").visible, "Pause on an offline core")
        if (setup) {
          novaPanel.modelsUnavailable = true; novaPanel.showView("models"); wait(80)
          check(shown("modelsUnavailableReason") && item("modelsUnavailableReason").text === "Finish SET UP NOVA in CHAT first.", "no reason on MODELS")
          novaPanel.showView("chat")
        }
        previewHost.online = true; wait(80)
        check(findChild(novaPanel, "corePause").visible || novaPanel.coreReducedMotion, "Pause gone while online")
      })
      console.warn("PHASE2_RESULTS " + JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["installed-no-selection"]
    with tempfile.TemporaryDirectory(prefix="nova-phase2-", dir="/tmp") as raw, \
         tempfile.TemporaryDirectory(prefix="nova-phase2-state-", dir="/tmp") as state:
        saved = Path(state) / "nova" / "panel-view.json"
        saved.parent.mkdir()
        saved.write_text(json.dumps({"version": 1, "view": "machine"}) + "\n")
        shell = preview.prepare_config(Path(raw), fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        check = QML_CHECK.replace("@CAPTURE@", os.environ.get("NOVA_PHASE2_CAPTURE", ""))
        shell.write_text(text.rsplit("\n}", 1)[0] + check + "\n}\n")
        env = dict(os.environ, XDG_STATE_HOME=state)
        result = subprocess.run(["quickshell", "--path", raw], capture_output=True, text=True, timeout=90, env=env)
        output = result.stdout + result.stderr
        last = json.loads(saved.read_text()).get("view")
    line = next((row.split("PHASE2_RESULTS ", 1)[1] for row in output.splitlines() if "PHASE2_RESULTS " in row), None)
    if line is None:
        print(output)
        raise SystemExit("Phase 2 check did not complete")
    rows = json.loads(line)
    rows.append({"name": "the last view is saved for the next start", "passed": last == "chat",
                 "error": "saved view: " + str(last)})
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
    errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop", "Unable to assign [undefined]"))]
    if errors or any(not row["passed"] for row in rows):
        print("\n".join(errors) or output[-3000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
