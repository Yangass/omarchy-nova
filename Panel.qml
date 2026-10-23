import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

Ui.Panel {
  id: root
  moduleName: "io.github.yangass.nova"
  // Replaced only in the installer-staged copy. The loaded QML generation keeps
  // this literal even if the source or installed files change later.
  readonly property string packagedUiIdentity: "__NOVA_PACKAGED_UI_ID__"
  function uiIdentityText(value) {
    return typeof value === "string" && /^(v\d+\.\d+\.\d+|version unknown) · ui-[0-9a-f]{12}$/.test(value)
      ? value : "UI build unknown · dev"
  }
  // A git-managed install (Omarchy's Add Plugin) is never stamped; name the
  // manifest version and the checked-out commit instead, read when the panel loads.
  property string manifestVersion: ""
  property string gitRevision: ""
  onGitRevisionChanged: if (opened) checkForUpdates(false)
  function gitIdentityText(version, revision) {
    if (!/^[0-9a-f]{40}$/.test(revision)) return ""
    return (/^\d+\.\d+\.\d+$/.test(version) ? "v" + version : "version unknown") + " · git " + revision.slice(0, 7)
  }
  readonly property string displayUiIdentity: {
    var packaged = uiIdentityText(packagedUiIdentity)
    if (packaged !== "UI build unknown · dev") return packaged
    return gitIdentityText(manifestVersion, gitRevision) || packaged
  }
  // Read once with a one-shot process: a FileView inside the plugin directory
  // would make Quickshell reload the whole shell when an update rewrites it.
  // Only the plugin's own .git counts, not a repository around ~/.config.
  Process {
    // Line 1: the commit (empty without the plugin's own .git); the rest: the manifest.
    command: ["sh", "-c", "{ [ -e \"$1/.git\" ] && git -C \"$1\" rev-parse HEAD; } || echo; cat \"$1/manifest.json\"", "sh", root.pluginDir]
    running: root.uiIdentityText(root.packagedUiIdentity) === "UI build unknown · dev"
    stdout: StdioCollector {
      onStreamFinished: {
        var out = String(text), split = out.indexOf("\n")
        root.gitRevision = split > 0 ? out.slice(0, split).trim() : ""
        try { root.manifestVersion = String(JSON.parse(out.slice(split + 1)).version || "") } catch (error) {}
      }
    }
  }
  ipcTarget: "io.github.yangass.nova"
  manageIpc: false
  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root
  readonly property bool online: hostWidget ? hostWidget.online : false
  readonly property real cpu: hostWidget ? hostWidget.cpu : -1
  readonly property real memory: hostWidget ? hostWidget.memory : -1
  readonly property real memoryUsed: hostWidget ? hostWidget.memoryUsed : -1
  readonly property real memoryTotal: hostWidget ? hostWidget.memoryTotal : -1
  readonly property bool gpuAvailable: hostWidget ? hostWidget.gpuAvailable : false
  readonly property string gpuName: hostWidget ? hostWidget.gpuName : "GPU unavailable"
  readonly property real gpu: hostWidget ? hostWidget.gpu : -1
  readonly property real vramUsed: hostWidget ? hostWidget.vramUsed : -1
  readonly property real vramTotal: hostWidget ? hostWidget.vramTotal : -1
  readonly property real gpuTemperature: hostWidget ? hostWidget.gpuTemperature : -1
  readonly property real gpuPower: hostWidget ? hostWidget.gpuPower : -1
  readonly property real disk: hostWidget ? hostWidget.disk : -1
  readonly property real diskUsed: hostWidget ? hostWidget.diskUsed : -1
  readonly property real diskTotal: hostWidget ? hostWidget.diskTotal : -1
  readonly property string diskMount: hostWidget ? hostWidget.diskMount : "/"
  readonly property real systemTemperature: hostWidget ? hostWidget.systemTemperature : -1
  readonly property string temperatureLabel: hostWidget ? hostWidget.temperatureLabel : "Thermals"
  readonly property string runtimeState: hostWidget ? hostWidget.runtimeState : "error"
  readonly property string currentTool: hostWidget ? hostWidget.currentTool : ""
  readonly property string activeProvider: hostWidget ? hostWidget.activeProvider : ""
  readonly property string activeModel: hostWidget ? hostWidget.activeModel : ""
  readonly property bool activeModelAvailable: hostWidget ? hostWidget.activeModelAvailable : false
  readonly property bool activeModelRuntimeReady: hostWidget ? hostWidget.activeModelRuntimeReady : false
  readonly property string backendModelTarget: hostWidget ? hostWidget.modelTarget : ""
  readonly property string backendModelPhase: hostWidget ? hostWidget.modelPhase : ""
  readonly property var cpuHistory: hostWidget ? hostWidget.cpuHistory : []
  readonly property var memoryHistory: hostWidget ? hostWidget.memoryHistory : []
  readonly property var gpuHistory: hostWidget ? hostWidget.gpuHistory : []
  // MACHINE network rows (2026-10-09, QWEN-14 data): bytes/s; the dot history is scaled
  // to the recent peak of both directions (at least 64 KiB/s, so idle noise stays flat).
  // Run 6 (2026-10-10): without a dedicated GPU Ollama runs models in normal RAM, where
  // "GPU memory" (and "Freed 0.0 GiB") was wrong. One word for every memory line.
  readonly property string memoryWord: gpuAvailable ? "GPU memory" : "memory"
  readonly property string gpuDriver: hostWidget && typeof hostWidget.gpuDriver === "string" ? hostWidget.gpuDriver : ""
  readonly property real netDown: hostWidget && typeof hostWidget.netDown === "number" ? hostWidget.netDown : -1
  readonly property real netUp: hostWidget && typeof hostWidget.netUp === "number" ? hostWidget.netUp : -1
  readonly property var netDownRaw: hostWidget && Array.isArray(hostWidget.netDownHistory) ? hostWidget.netDownHistory : []
  readonly property var netUpRaw: hostWidget && Array.isArray(hostWidget.netUpHistory) ? hostWidget.netUpHistory : []
  readonly property real netPeak: Math.max.apply(null, [65536].concat(netDownRaw, netUpRaw))
  function rateLabel(bytes) {
    if (!valid(bytes)) return "—"
    if (bytes < 1024) return Math.round(bytes) + " B/s"
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KiB/s"
    return (bytes / 1048576).toFixed(1) + " MiB/s"
  }
  readonly property var residentModels: discoveredModels.filter(function(m) { return m && m.resident === true })
  readonly property var vramHistory: hostWidget ? hostWidget.vramHistory : []
  readonly property double updatedAt: hostWidget ? hostWidget.updatedAt : 0
  readonly property color foreground: bar ? bar.foreground : Commons.Color.foreground
  readonly property color muted: Commons.Color.muted
  readonly property color accent: Commons.Color.accent
  // Before NOVA's services run, the setup box already shows the remedy; a red
  // OFFLINE beside it read as broken to a first-time user (walkthrough run 2).
  readonly property bool novaSetupPending: {
    var step = online ? null : setupStep()
    return step !== null && (step.id === "nova" || step.id === "nova-start")
  }
  readonly property color statusColor: online ? accent : (novaSetupPending ? muted : Commons.Color.urgent)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  property int clockTick: 0
  property bool chatBusy: false
  // When the current reply was requested; drives the slow-first-reply hint.
  property double chatStartedAt: 0
  property string chatError: ""
  // CHAT-02 sessions are process-local backend context. The panel instance is
  // persistent across close/reopen, but shell/backend restarts may invalidate
  // the opaque identifier. Visible transcript text remains display-only then.
  property string chatSessionId: ""
  property int chatSessionGeneration: 0
  property bool chatSessionSetupInFlight: false
  property int chatSessionSetupSerial: 0
  // CHAT-02E New chat: one reset may be in flight at a time; the serial plus
  // session generation keep a stale reset reply from touching a replacement.
  property bool chatResetInFlight: false
  property int chatResetSerial: 0
  property int chatResetGeneration: 0
  property int chatResetPendingClear: 0
  property int chatRequestSerial: 0
  property int activeChatRequestSerial: 0
  property bool chatRetentionPending: false
  property int chatRetentionSerial: 0
  property var chatRetentionRetryCallback: null
  property string chatContextNotice: ""
  property var pendingApproval: null
  // Installation and controlled-action approvals share one foreground surface.
  // The user's dashboard/chat preferences remain unchanged underneath it.
  readonly property bool approvalForeground: pendingApproval !== null || removeNovaOpen
  // UX Phase 2 (Konstantin, 2026-10-09, previews/ux-phase2): three views in the panel.
  // NOVA reopens on the last one; an approval always shows in CHAT, where its result lands.
  property string panelView: "chat"
  // The saved view applies only at startup, before anything else picked a view.
  property bool panelViewChosen: false
  onPanelViewChanged: panelViewChosen = true
  readonly property string shownView: approvalForeground ? "chat" : panelView
  readonly property bool coreVisible: shownView !== "models"
  // CHAT's core band: tall enough for a readable wave at coreAspect (the shader
  // squashes its rows below that; Konstantin 2026-10-09: "so compressed it almost looks bad").
  // Short screens give the room back to the conversation and approvals (down to 64 px).
  readonly property real coreBandHeight: Math.max(Style.space(64), Math.min(Style.space(140), layoutArea ? layoutArea.height * 0.18 : 0))
  readonly property real coreAspect: 3.6
  readonly property bool selectorVisible: shownView === "models"
  // MACHINE tab dot: the GPU is hot, or memory or disk is nearly full.
  readonly property bool machineWarning: (valid(gpuTemperature) && gpuTemperature >= 85) || ratio(memory) >= 0.95 || ratio(disk) >= 0.95
  property string approvalFeedback: ""
  property var approvalRequestTimeoutCallback: null
  property var recoveryRequestTimeoutCallback: null
  property int recoveryRequestSerial: 0
  // A keyboard approval is armed only after the submission key has been
  // released. This prevents the Enter that sent the chat request, including
  // its auto-repeat, from deciding an approval that arrives asynchronously.
  property bool approvalShortcutArmed: true
  property bool chatSubmissionKeyDown: false
  property int approvalFocusIndex: -1
  property real approvalReadingPosition: -1
  property bool approvalFollowLatest: true
  property bool modelsUnavailable: false
  property bool installedModelsLoading: false
  property var modelsRequestTimeoutCallback: null
  property int modelsRequestSerial: 0
  property var discoveredModels: []
  property var catalogModels: []
  // The shipped model list's date: its newest checked entry (catalog upkeep, 2026-10-10).
  // Installs follow small Ollama changes by themselves; an old list only misses newer models.
  // Chat answers as light formatting (2026-10-10). Model output is untrusted: everything is
  // HTML-escaped first, then only bold, italic, code, code blocks, headings and lists are
  // added back as tags NOVA writes itself. Qt's own Markdown mode fetched image URLs from
  // the text and rendered raw HTML, so it is not used: images show their alt text, links
  // show as text with the address, and nothing is fetched or clickable.
  // Linear time (audit 2026-10-16): no pattern scans past the next opener of its own kind
  // (bold stops at the next "**", a link at the next "[" or "("), so text full of openers
  // that never close costs no more than plain text.
  function chatMarkdownHtml(source) {
    function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") }
    var codeColor = String(root.accent), mutedColor = String(root.muted)
    function inline(s) {
      var codes = []
      s = s.replace(/`([^`]+)`/g, function(m, c) { codes.push(c); return "\u0001" + (codes.length - 1) + "\u0001" })
      s = s.replace(/!\[([^\[\]]*)\]\([^()]*\)/g, "$1")
      s = s.replace(/\[([^\[\]]+)\]\(([^()\s]+)\)/g, "$1 ($2)")
      s = s.replace(/\*\*(?=\S)((?:[^*]|\*(?!\*))*?\S)\*\*/g, "<b>$1</b>").replace(/__(?=\S)((?:[^_]|_(?!_))*?\S)__/g, "<b>$1</b>")
      s = s.replace(/(^|[^*\w])\*(?=\S)([^*]*?\S)\*(?![*\w])/g, "$1<i>$2</i>")
      return s.replace(/\u0001(\d+)\u0001/g, function(m, i) { return "<span style=\"color:" + codeColor + "\">" + codes[Number(i)] + "</span>" })
    }
    function indent(spaces) { return new Array(Math.floor(spaces.length / 2) * 3 + 1).join("&nbsp;") }
    var lines = esc(String(source || "")).split("\n"), out = [], fence = null
    lines.forEach(function(line) {
      if (/^\s*```/.test(line)) {
        if (fence === null) { fence = []; return }
        out.push("<pre style=\"color:" + codeColor + "\">" + fence.join("\n") + "</pre>"); fence = null; return
      }
      if (fence !== null) { fence.push(line); return }
      var m
      if ((m = line.match(/^#{1,6}\s+(.*)$/))) out.push("<b>" + inline(m[1]) + "</b>")
      else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) out.push("<span style=\"color:" + mutedColor + "\">──────</span>")
      else if ((m = line.match(/^(\s*)[-*+]\s+(.*)$/))) out.push(indent(m[1]) + "•&nbsp;" + inline(m[2]))
      else if ((m = line.match(/^(\s*)(\d+)[.)]\s+(.*)$/))) out.push(indent(m[1]) + m[2] + ".&nbsp;" + inline(m[3]))
      else out.push(inline(line))
    })
    if (fence !== null) out.push("<pre style=\"color:" + codeColor + "\">" + fence.join("\n") + "</pre>")  // still streaming
    return out.join("<br>").replace(/<br>(<pre)/g, "$1").replace(/(<\/pre>)<br>/g, "$1")
  }
  function modelListLine(nowMs) {
    var newest = ""
    catalogModels.forEach(function(item) {
      var checked = item && !item.user_requested && item.metadata && item.metadata.checked_at
      if (typeof checked === "string" && checked > newest) newest = checked
    })
    if (newest === "") return ""
    var day = newest.slice(0, 10), age = ((nowMs || Date.now()) - Date.parse(newest)) / 86400000
    return age > 60 ? "Model list from " + day + ": update NOVA for newer models" : "Model list " + day
  }
  property var acquisitionByCatalog: ({})
  property var acquisitionHistoryByCatalog: ({})
  property var preflightByCatalog: ({})
  property string acquisitionStatus: ""
  property string trackedAcquisitionId: ""
  property var modelFits: ({})
  property bool selectorDetailsExpanded: false
  property bool catalogDownloadsExpanded: false
  property var modelDetailsByCatalog: ({})
  property string hardwareSummary: "LOCAL AI // FIT PENDING"
  // Graphics devices from novad's hardware profile (any vendor); null until it answers.
  property var graphicsDevices: null
  // Run 6 (2026-10-10): VERSIONS said "No NVIDIA GPU reported" on every non-NVIDIA machine.
  function graphicsLine() {
    if (gpuAvailable) return gpuName + (gpuDriver !== "" ? " · driver " + gpuDriver : "")
    if (graphicsDevices === null) return "Graphics: not checked yet"
    if (!graphicsDevices.length) return "No graphics card detected"
    return graphicsDevices.map(function(d) {
      var name = d.model || (d.vendor && d.vendor !== "unknown" ? d.vendor.toUpperCase() + " graphics" : "Graphics device")
      // Same rule as novad's model_fit._integrated: not dedicated and under 2 GiB of VRAM.
      var shared = d.memory_kind !== "dedicated" && !(d.vram_total_bytes >= 2 * 1024 * 1024 * 1024)
      return name + (d.memory_kind === "dedicated" ? (d.vram_total_bytes > 0 ? " · " + bytesLabel(d.vram_total_bytes) : "") : (shared ? " · shares main memory" : ""))
    }).join("\n")
  }
  property string catalogSummary: "CATALOG // CHECKING"
  property string recommendationSummary: "RECOMMEND // CHECKING"
  property string modelSwitchStatus: ""
  property bool modelSwitching: false
  property string requestedModel: ""
  property string onboardingState: "CHECKING"
  property var onboardingActions: []
  property var onboardingCodes: []
  property var onboardingLimitations: []
  property string onboardingTarget: ""
  property bool onboardingRequestInFlight: false
  // A background re-check runs with the last answer still on screen (refreshOnboarding).
  property bool onboardingRecheckQuiet: false
  onOnboardingRequestInFlightChanged: if (!onboardingRequestInFlight) onboardingRecheckQuiet = false
  property int onboardingRequestSerial: 0
  property bool onboardingSnapshotFresh: false
  property double onboardingReceivedAt: 0
  property string activationReconcileProvider: ""
  property string activationReconcileModel: ""
  property double activationReconcileDeadline: 0
  property int readinessConflictRetries: 0
  property bool onboardingExplicitSelection: false
  property string onboardingSelectedProvider: ""
  property string onboardingSelectedModel: ""
  // Development acceptance previews inject classifier-backed fixture data.
  // The normal shell host never sets these properties. Preview mode is
  // intentionally read-only: all operations below are resolved locally.
  readonly property bool acceptancePreview: hostWidget && hostWidget.acceptancePreview === true
  readonly property var acceptancePreviewFixture: acceptancePreview && hostWidget.acceptancePreviewFixture
                                                   ? hostWidget.acceptancePreviewFixture : null
  readonly property string acceptancePreviewLabel: acceptancePreviewFixture && typeof acceptancePreviewFixture.label === "string"
                                                   ? acceptancePreviewFixture.label : "FIXTURE"
  readonly property bool acceptanceSimulation: acceptancePreview
                                               && acceptancePreviewFixture.continuous_simulation === true
  property string acceptanceSimulationStage: ""
  property bool acceptanceSimulationFailure: false
  property bool acceptanceSimulationReplyShown: false
  // One height for every view, so switching views doesn't make the panel jump.
  readonly property real basePanelHeight: Style.space(780)
  readonly property bool modelTransitionActive: modelSwitching || effectiveState === "model_switching"
  onModelTransitionActiveChanged: {
    if (!modelTransitionActive && activationReconcileModel !== "") reconcileActivation()
    else if (!modelTransitionActive && onboardingState === "ACTIVATION_FAILED") refreshOnboarding()
  }
  onActiveModelRuntimeReadyChanged: invalidateConflictingReadiness()
  onActiveModelAvailableChanged: invalidateConflictingReadiness()
  onActiveModelChanged: invalidateConflictingReadiness()
  onActiveProviderChanged: invalidateConflictingReadiness()
  readonly property bool chatContextOperationActive: chatSessionSetupInFlight || chatRetentionPending
  readonly property string transitionTarget: modelSwitching && requestedModel !== "" ? requestedModel
    : (backendModelTarget !== "" ? backendModelTarget : requestedModel)
  readonly property int chatHistoryCount: chatHistoryModel.count
  readonly property bool onboardingCanSend: {
    clockTick
    return onboardingSnapshotFresh && onboardingState === "READY"
      && online && !modelTransitionActive && onboardingExplicitSelection
      && onboardingSelectedProvider === activeProvider && onboardingSelectedModel === activeModel
      && activeModelAvailable && (activeModelRuntimeReady || modelLoadsOnSend)
      && (acceptanceSimulation
          || (onboardingReceivedAt > 0 && Date.now() - onboardingReceivedAt <= 45000))
      && (!acceptanceSimulation || !acceptanceSimulationReplyShown)
  }

  function valid(value) { return isFinite(value) && value >= 0 }
  function percent(value) { return valid(value) ? Math.round(value) + "%" : "—" }
  function gib(value) { return valid(value) ? value.toFixed(1) + " GiB" : "—" }
  function celsius(value) { return valid(value) ? Math.round(value) + "°C" : "—" }
  function watts(value) { return valid(value) ? Math.round(value) + " W" : "—" }
  function ratio(value) { return valid(value) ? Math.max(0, Math.min(1, value / 100)) : 0 }
  function temperatureIsNvme() { return typeof temperatureLabel === "string" && temperatureLabel.toLowerCase().indexOf("nvme:") === 0 }
  function friendlyTemperatureLabel() { return temperatureIsNvme() ? "SSD temperature" : "Hardware temperature" }
  function temperatureDetail() {
    if (!valid(systemTemperature)) return "Temperature sensor unavailable"
    return temperatureIsNvme() ? "Highest reported NVMe sensor" : "Highest reported hardware sensor: " + temperatureLabel
  }
  function gpuActivitySubtitle() { return gpuAvailable ? gpuName : "GPU activity unavailable" }
  function gpuMemorySubtitle() {
    if (!gpuAvailable) return "GPU memory unavailable"
    var values = []
    if (valid(gpuTemperature)) values.push(celsius(gpuTemperature))
    if (valid(gpuPower)) values.push(watts(gpuPower))
    return values.length ? values.join("  ·  ") : "Temperature and power unavailable"
  }
  function gpuActivityTooltip() {
    return "How busy the graphics processor is." + (gpuAvailable && gpuName ? "\n\nGPU: " + gpuName : "")
  }
  function gpuMemoryTooltip() {
    return "Memory occupied on the graphics card. High memory use does not necessarily mean high activity." + (gpuAvailable ? "\n\n" + gpuMemorySubtitle() : "")
  }
  // The telemetry sample is stale after three missed polls; one late poll is not news
  // (Konstantin, 2026-10-07: a ticking "UPDATED 5S AGO" was noise and moved the header).
  // The core has its own stale dim, fed by the faster runtime-state poll (activityFresh).
  readonly property int statusStaleMs: 3 * (hostWidget && hostWidget.refreshMs > 0 ? hostWidget.refreshMs : 10000)
  readonly property bool statusStale: {
    clockTick
    return online && updatedAt > 0 && Date.now() - updatedAt > statusStaleMs
  }
  function refreshAge() {
    clockTick
    if (!online) return novaSetupPending ? "WAITING FOR SETUP" : "SERVICE OFFLINE"
    if (!updatedAt) return "AWAITING SAMPLE"
    if (!statusStale) return "LIVE"
    var seconds = Math.max(0, Math.round((Date.now() - updatedAt) / 1000))
    return "STALE · " + (seconds < 120 ? seconds + "S" : Math.floor(seconds / 60) + "M")
  }
  function refresh() {
    if (acceptancePreview) { applyAcceptancePreview(); return }
    if (hostWidget && hostWidget.refresh) hostWidget.refresh()
  }
  property bool approvalDetailsOpen: false
  onPendingApprovalChanged: if (!pendingApproval) approvalDetailsOpen = false
  // Tooltips (ours and the ones Omarchy's buttons create) are centred on their control
  // and may spill past the panel's frame (Konstantin, 2026-10-09). Before one shows, fit
  // it to the panel width (long text wraps) and slide it inside the frame.
  function placeTooltip(tip) {
    var owner = tip.parent
    if (!owner || !layoutArea || typeof owner.mapToItem !== "function") return
    if (tip.contentItem && tip.contentItem.wrapMode !== undefined) tip.contentItem.wrapMode = Text.Wrap
    tip.width = Math.min(tip.implicitWidth, layoutArea.width)
    var ownerX = owner.mapToItem(layoutArea, 0, 0).x
    tip.x = Math.max(-ownerX, Math.min(layoutArea.width - ownerX - tip.width, (owner.width - tip.width) / 2))
  }
  property var placedTooltips: []
  function fitTooltips(item) {
    var list = item && item.data ? item.data : []
    for (var i = 0; i < list.length; i++) {
      var child = list[i]
      if (!child) continue
      if (String(child).indexOf("ToolTip") >= 0 && child.aboutToShow && placedTooltips.indexOf(child) < 0) {
        placedTooltips.push(child)  // connect once
        child.aboutToShow.connect(function(tip) { return function() { root.placeTooltip(tip) } }(child))
      }
      if (child.data !== undefined) fitTooltips(child)
    }
  }
  Timer {
    id: tooltipFitTimer; interval: 250
    onTriggered: {
      root.placedTooltips = root.placedTooltips.filter(function(tip) { return !!tip })  // rows that were removed
      root.fitTooltips(layoutArea)
    }
  }
  onOpenedChanged: if (opened) tooltipFitTimer.restart()
  onDiscoveredModelsChanged: tooltipFitTimer.restart()
  function setPanelView(view) {
    if (view === panelView) return false
    panelView = view
    tooltipFitTimer.restart()
    if (!acceptancePreview) panelViewFile.setText(JSON.stringify({ version: 1, view: view }) + "\n")
    return true
  }
  // An approval opens in CHAT and the user stays there to read its result.
  onApprovalForegroundChanged: if (approvalForeground) setPanelView("chat")
  function showView(view) {
    if (["chat", "models", "machine"].indexOf(view) < 0) return
    var entering = setPanelView(view)
    if (view === "chat" && !approvalForeground) restoreChatInputFocus()
    if (entering && view === "machine" && !acceptancePreview) refreshModels()  // what's in GPU memory
    if (!entering || view !== "models" || acceptancePreview) return
    refreshModels()
    refreshFit()
    refreshCatalog()
    refreshAcquisitions()
    refreshRecommendations()
  }
  property string panelViewPath: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")) + "/nova/panel-view.json"
  FileView {
    id: panelViewFile
    path: root.acceptancePreview ? "" : root.panelViewPath
    blockLoading: true  // tiny file: the saved view applies before anything else switches views
    printErrors: false
    onLoaded: {
      try { var view = JSON.parse(text()).view; if (!root.panelViewChosen && ["chat", "models", "machine"].indexOf(view) >= 0) root.panelView = view }
      catch (error) { }
    }
  }
  // 0.15: NOVA's welcome in chat, once (also once for people who already have a model): what
  // it set up, the working context and why, things to try, and the coding CLI. NOVA's own
  // words from novad's numbers, not the model talking.
  property string welcomePath: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")) + "/nova/panel-welcome.json"
  property bool welcomeSeen: false
  property bool welcomeRead: false
  property var welcomeData: null
  FileView {
    id: welcomeFile
    path: root.acceptancePreview ? "" : root.welcomePath
    printErrors: false
    onLoaded: { try { root.welcomeSeen = JSON.parse(text()).seen === true } catch (error) { root.welcomeSeen = false } root.welcomeRead = true }
    onLoadFailed: { root.welcomeSeen = false; root.welcomeRead = true }
  }
  readonly property bool welcomeWanted: !acceptancePreview && welcomeRead && !welcomeSeen && onboardingState === "READY" && activeModel !== ""
  readonly property bool welcomeVisible: welcomeWanted && welcomeData !== null && welcomeData.model === activeModel
  onWelcomeWantedChanged: if (welcomeWanted && (!welcomeData || welcomeData.model !== activeModel)) loadWelcome()
  function loadWelcome() {
    var model = activeModel
    novaJson("GET", "/cli/setup?model=" + encodeURIComponent(model), null, 10000, function(status, payload) {
      welcomeData = status === 200 && payload.model ? payload : { model: model }
      // The card names the model as Ollama lists it; the bar may say it without ":latest".
      if (welcomeData.model !== model) welcomeData = Object.assign({}, welcomeData, { model: model, listed: welcomeData.model })
    })
  }
  function markWelcomeSeen() {
    welcomeSeen = true
    if (!acceptancePreview) welcomeFile.setText(JSON.stringify({ version: 1, seen: true }) + "\n")
  }
  function welcomeTry(prompt) {
    markWelcomeSeen()
    if (!chatInput) return
    chatInput.text = prompt
    sendChat()
  }
  function welcomeSetUpCli() {
    markWelcomeSeen()
    showView("models")
    openCliSetup({ id: welcomeData && welcomeData.listed ? welcomeData.listed : activeModel })
  }
  function welcomeContextLine() {
    if (!welcomeData || !welcomeData.context) return ""
    var line = "Working context " + contextLabel(welcomeData.context) + " · how much conversation it keeps in mind"
    if (welcomeData.numbers && welcomeData.verdict)
      line += " (its maximum is " + contextLabel(welcomeData.numbers.native_context) + "; " + contextLabel(welcomeData.context) + " leaves "
        + gibLabel(welcomeData.verdict.spare_bytes) + " of graphics memory for your desktop)"
    return line + ". Replies stay on this computer; nothing goes online."
  }
  function catalogAttention() {
    for (var i = 0; i < catalogModels.length; ++i) {
      var model = catalogModels[i]
      var record = model && acquisitionFor(model.catalog_id)
      if (!record) continue
      if (record.state === "RECOVERY_REQUIRED" && !record.recovery_resolution)
        return { index: i, label: "Review installation recovery", target: catalogRuntimeId(model) }
      if (["APPROVED", "STARTING", "DOWNLOADING", "VERIFYING", "CANCEL_REQUESTED"].indexOf(record.state) >= 0)
        return { index: i, label: "Show installation progress", target: catalogRuntimeId(model) }
    }
    return null
  }
  // NEW models (2026-10-08): catalog ids the user hasn't seen yet. The first run
  // records the current catalog silently; closing the download catalog marks all seen.
  property string seenCatalogPath: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")) + "/nova/panel-seen-catalog.json"
  property var seenCatalogIds: null  // null until read (or known missing)
  FileView {
    id: seenCatalogFile
    path: root.acceptancePreview ? "" : root.seenCatalogPath
    printErrors: false
    onLoaded: {
      try { var ids = JSON.parse(text()).seen; root.seenCatalogIds = Array.isArray(ids) ? ids.filter(function(id) { return typeof id === "string" }) : [] }
      catch (error) { root.seenCatalogIds = [] }
    }
    onLoadFailed: root.seenCatalogIds = []
  }
  readonly property var newCatalogIds: !Array.isArray(seenCatalogIds) || seenCatalogIds.length === 0 ? []
    : catalogModels.map(function(m) { return m.catalog_id }).filter(function(id) { return seenCatalogIds.indexOf(id) < 0 })
  function catalogIsNew(catalogId) { return newCatalogIds.indexOf(catalogId) >= 0 }
  function markCatalogSeen() {
    if (!Array.isArray(seenCatalogIds) || catalogModels.length === 0) return
    var ids = seenCatalogIds.slice()
    catalogModels.forEach(function(m) { if (ids.indexOf(m.catalog_id) < 0) ids.push(m.catalog_id) })
    if (ids.length === seenCatalogIds.length) return
    seenCatalogIds = ids
    if (!acceptancePreview) seenCatalogFile.setText(JSON.stringify({ version: 1, seen: ids }) + "\n")
  }
  // First run: nothing is "new" on a fresh install; remember today's catalog.
  onCatalogModelsChanged: {
    tooltipFitTimer.restart()
    if (Array.isArray(seenCatalogIds) && seenCatalogIds.length === 0 && catalogModels.length > 0) markCatalogSeen()
  }
  onSeenCatalogIdsChanged: if (Array.isArray(seenCatalogIds) && seenCatalogIds.length === 0 && catalogModels.length > 0) markCatalogSeen()
  // ADD A MODEL (2026-10-08): a name or ollama.com/library link from the user. novad
  // validates it, reads its size and fingerprint from Ollama's library without
  // downloading, and lists it with NOVA's usual checks and approval.
  property bool addModelBusy: false
  property bool addModelFailed: false
  property string addModelStatus: ""
  function catalogSizeBytes(modelData) {
    var bytes = modelData && modelData.acquisition && modelData.acquisition.expected_size_bytes
    if (typeof bytes !== "number") bytes = modelData && modelData.properties && modelData.properties.weight_size_bytes
    return typeof bytes === "number" ? bytes : null
  }
  function catalogSizeLabel(modelData) {
    var bytes = catalogSizeBytes(modelData)
    return bytes !== null ? bytesLabel(bytes) : ""
  }
  function requestedTag(modelData) { return modelData && modelData.user_requested === true ? "  YOU ADDED" : "" }
  function approvalSourceText(trust) {
    if (trust === "backend_allowlist") return "On NOVA's list of trusted sources"
    if (trust === "user_requested") return "Not in NOVA's catalog: you asked for this one"
    return "Not on NOVA's list of trusted sources"
  }
  // QWEN-10: a model the user added can leave the list again; nothing is uninstalled.
  function canRemoveRequested(modelData) {
    if (!modelData || modelData.user_requested !== true || addModelBusy) return false
    var state = acquisitionState(modelData)
    return ["DOWNLOADING", "VERIFYING", "INSTALLING", "AWAITING APPROVAL"].indexOf(state) < 0
  }
  function removeRequestedModel(modelData) {
    if (!modelData || modelData.user_requested !== true || addModelBusy || acceptancePreview) return
    var name = String(modelData.display_name || modelData.catalog_id).replace(/\s*\([^)]*\)\s*$/, "")
    addModelBusy = true; addModelFailed = false
    addModelStatus = "Removing " + name + " from your list…"
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      var result = null
      try { result = JSON.parse(xhr.responseText) } catch (error) { result = null }
      var status = result && typeof result.status === "string" ? result.status : ""
      root.addModelBusy = false
      root.addModelFailed = status !== "removed" && status !== "not_found"
      root.addModelStatus = status === "removed" ? "Removed " + name + " from your list. Nothing was uninstalled."
        : status === "not_found" ? name + " was already gone from your list."
        : "NOVA's background service didn't answer. Try again in a moment."
      if (status === "removed" || status === "not_found") root.refreshCatalog()
    }
    try {
      root.openNova(xhr, "DELETE", "/models/requested/" + encodeURIComponent(modelData.catalog_id))
      xhr.timeout = 15000
      xhr.send()
    } catch (error) { addModelBusy = false; addModelFailed = true; addModelStatus = "NOVA's background service didn't answer. Try again in a moment." }
  }
  function addRequestedModel(text) {
    var reference = String(text || "").trim()
    if (reference === "" || addModelBusy || acceptancePreview) return
    addModelBusy = true; addModelFailed = false
    addModelStatus = "Looking up " + reference + " in Ollama's library…"
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      var result = null
      try { result = JSON.parse(xhr.responseText) } catch (error) { result = null }
      var status = result && typeof result.status === "string" ? result.status : ""
      var name = result && typeof result.name === "string" ? result.name : reference
      root.addModelBusy = false
      root.addModelFailed = status !== "added" && status !== "in_catalog"
      root.addModelStatus = ({
        added: "Added " + name + " to the list below. NOVA runs its usual checks before anything downloads.",
        in_catalog: name + " is already in NOVA's catalog below.",
        not_found: "Ollama's library has no model called " + name + ".",
        registry_unavailable: "Couldn't reach Ollama's library. Check the internet connection and try again.",
        unsupported_model: "NOVA can't read " + name + " from Ollama's library, so it can't check it.",
        invalid_reference: "Use a model name from Ollama's library, like gemma4:12b, or its ollama.com/library link."
      })[status] || "NOVA's background service didn't answer. Try again in a moment."
      if (status === "added" || status === "in_catalog") {
        if (addModelInput) addModelInput.text = ""
        root.refreshCatalog()
      }
    }
    try {
      root.openNova(xhr, "POST", "/models/requested")
      xhr.setRequestHeader("Content-Type", "application/json")
      xhr.timeout = 120000  // the registry probe reads a few small blobs
      xhr.send(JSON.stringify({ reference: reference }))
    } catch (error) { addModelBusy = false; addModelFailed = true; addModelStatus = "NOVA's background service didn't answer. Try again in a moment." }
  }
  function toggleCatalogDownloads() {
    catalogDownloadsExpanded = !catalogDownloadsExpanded
    if (!catalogDownloadsExpanded) { markCatalogSeen(); return }
    var attention = catalogAttention()
    if (!attention) return
    Qt.callLater(function() {
      if (!catalogDownloadsExpanded || !catalogModelRepeater || !modelList) return
      var target = attention.target
      if (!target && attention.index < catalogModels.length) target = root.catalogRuntimeId(catalogModels[attention.index])
      if (!target) return
      for (var i = 0; i < catalogModelRepeater.count; ++i) {
        var row = catalogModelRepeater.itemAt(i)
        if (row && row.modelData && root.catalogRuntimeId(row.modelData) === target) {
          var top = row.mapToItem(modelList.contentItem, 0, 0).y
          modelList.contentY = Math.max(0, Math.min(modelList.contentHeight - modelList.height, top))
          return
        }
      }
    })
  }
  function onboardingHasAction(action) {
    return Array.isArray(onboardingActions) && onboardingActions.indexOf(action) >= 0
  }
  function onboardingRefreshAllowed() {
    return root.opened && !chatBusy && pendingApproval === null
      && !modelTransitionActive && trackedAcquisitionId === ""
      && ["approval_required", "executing_action", "verifying_action"].indexOf(effectiveState) < 0
  }
  function setOnboardingUnavailable() {
    onboardingRequestInFlight = false
    onboardingSnapshotFresh = false
    onboardingState = "NEEDS_USER_SETUP"
    onboardingActions = ["RETRY_CHECK"]
    onboardingCodes = ["NOVAD_UNAVAILABLE"]
    onboardingLimitations = ["ONBOARDING_SNAPSHOT_UNAVAILABLE"]
    onboardingTarget = ""
    onboardingExplicitSelection = false
    onboardingSelectedProvider = ""
    onboardingSelectedModel = ""
  }
  function invalidateConflictingReadiness() {
    if (acceptancePreview || modelTransitionActive) return
    if (onboardingCodes.indexOf("READINESS_CONFLICT") >= 0) {
      Qt.callLater(root.refreshOnboarding)
      return
    }
    if (onboardingState !== "READY" || readySnapshotCoherent({explicit_selection: onboardingExplicitSelection,
        provider: onboardingSelectedProvider, model: onboardingSelectedModel}, onboardingCodes)) return
    ++onboardingRequestSerial
    onboardingRequestInFlight = false
    onboardingSnapshotFresh = false
    onboardingState = "CHECKING"
    Qt.callLater(root.refreshOnboarding)
  }
  // 2026-10-09 (Konstantin): a selected model out of GPU memory (the 10-minute auto-unload)
  // is ready; novad says so with SELECTED_MODEL_NOT_LOADED and the first message loads it.
  readonly property bool modelLoadsOnSend: onboardingCodes.indexOf("SELECTED_MODEL_NOT_LOADED") >= 0
  function readySnapshotCoherent(configuration, codes) {
    var loadsOnSend = Array.isArray(codes) && codes.indexOf("SELECTED_MODEL_NOT_LOADED") >= 0
    return configuration.explicit_selection === true && configuration.provider === activeProvider
      && configuration.model === activeModel && activeModelAvailable && (activeModelRuntimeReady || loadsOnSend)
  }
  function reconcileActivation() {
    if (activationReconcileModel === "" || acceptancePreview) return
    if (Date.now() >= activationReconcileDeadline) {
      activationReconcileTimer.stop()
      activationReconcileProvider = ""
      activationReconcileModel = ""
      ++onboardingRequestSerial
      setOnboardingUnavailable()
      return
    }
    if (!onboardingRequestInFlight && onboardingRefreshAllowed()) refreshOnboarding()
  }
  function beginActivationReconciliation(provider, model) {
    // A pre-activation snapshot must never overwrite the completed target.
    ++onboardingRequestSerial
    onboardingRequestInFlight = false
    onboardingSnapshotFresh = false
    onboardingState = "CHECKING"
    onboardingActions = []
    activationReconcileProvider = provider
    activationReconcileModel = model
    activationReconcileDeadline = Date.now() + 40000
    activationReconcileTimer.start()
    reconcileActivation()
  }
  function applyAcceptanceFixture(fixture) {
    if (!fixture) return
    var payload = fixture.onboarding || ({})
    onboardingRequestInFlight = false
    onboardingState = typeof payload.primary_state === "string" ? payload.primary_state : "CHECKING"
    onboardingActions = Array.isArray(payload.allowed_actions) ? payload.allowed_actions : []
    onboardingCodes = Array.isArray(payload.explanation_codes) ? payload.explanation_codes : []
    onboardingLimitations = Array.isArray(payload.limitations) ? payload.limitations : []
    onboardingTarget = typeof payload.target_id === "string" ? payload.target_id : ""
    var configuration = fixture.configuration || ({})
    onboardingExplicitSelection = configuration.explicit_selection === true
    onboardingSelectedProvider = onboardingExplicitSelection && typeof configuration.provider === "string" ? configuration.provider : ""
    onboardingSelectedModel = onboardingExplicitSelection && typeof configuration.model === "string" ? configuration.model : ""
    onboardingSnapshotFresh = fixture.snapshot_fresh !== false
    onboardingReceivedAt = onboardingSnapshotFresh ? Date.now() : 0
    discoveredModels = Array.isArray(fixture.discovered_models) ? fixture.discovered_models : []
    catalogModels = Array.isArray(fixture.catalog_models) ? fixture.catalog_models : []
    modelFits = fixture.model_fits || ({})
    preflightByCatalog = fixture.preflight_by_catalog || ({})
    acquisitionByCatalog = fixture.acquisition_by_catalog || ({})
    acquisitionHistoryByCatalog = fixture.acquisition_history_by_catalog || ({})
    hardwareSummary = fixture.hardware_summary || "LOCAL AI // FIXTURE"
    catalogSummary = fixture.catalog_summary || "CATALOG // FIXTURE"
    recommendationSummary = fixture.recommendation_summary || "RECOMMEND // FIXTURE"
    acquisitionStatus = fixture.acquisition_status || ""
    modelsUnavailable = fixture.models_unavailable === true
    panelView = fixture.models_expanded === true ? "models" : (fixture.view || "chat")
    selectorDetailsExpanded = fixture.selector_details_expanded === true
    // Preview fixtures are reusable snapshots. Clone the approval so a local
    // decision cannot leak into a restarted simulation attempt.
    pendingApproval = fixture.pending_approval
      ? JSON.parse(JSON.stringify(fixture.pending_approval)) : null
  }
  function applyAcceptancePreview() {
    if (!acceptancePreviewFixture) return
    if (acceptanceSimulation) {
      if (acceptanceSimulationStage === "") acceptanceSimulationStage = "initial"
      applyAcceptanceSimulationStage(acceptanceSimulationStage)
      return
    }
    applyAcceptanceFixture(acceptancePreviewFixture)
  }
  function acceptanceSimulationFixture(stage) {
    if (!acceptanceSimulation || !acceptancePreviewFixture.simulation_states) return null
    return acceptancePreviewFixture.simulation_states[stage] || null
  }
  function applyAcceptanceSimulationStage(stage) {
    var fixture = acceptanceSimulationFixture(stage)
    if (!fixture) return false
    acceptanceSimulationStage = stage
    applyAcceptanceFixture(fixture)
    if (hostWidget) {
      var host = fixture.host || ({})
      hostWidget.runtimeState = host.runtime_state || "idle"
      hostWidget.activeProvider = host.active_provider || ""
      hostWidget.activeModel = host.active_model || ""
      hostWidget.activeModelAvailable = host.active_available === true
      hostWidget.activeModelRuntimeReady = host.runtime_ready === true
      hostWidget.modelTarget = stage === "activating" ? onboardingTarget : ""
      hostWidget.modelPhase = stage === "activating" ? "loading" : ""
    }
    return true
  }
  function resetAcceptanceSimulation() {
    if (!acceptanceSimulation) return
    acceptanceSimulationTimer.stop()
    acceptanceSimulationFailure = false
    acceptanceSimulationReplyShown = false
    modelSwitchStatus = ""
    chatError = ""
    chatBusy = false
    modelSwitching = false
    requestedModel = ""
    chatHistoryModel.clear()
    applyAcceptanceSimulationStage("initial")
  }
  function toggleAcceptanceSimulationFailure() {
    if (!acceptanceSimulation || acceptanceSimulationStage !== "initial"
        || pendingApproval !== null) return
    acceptanceSimulationFailure = !acceptanceSimulationFailure
    acquisitionStatus = acceptanceSimulationFailure
      ? "SIMULATION // NEXT VERIFICATION WILL FAIL"
      : "SIMULATION // NEXT VERIFICATION WILL SUCCEED"
  }
  function beginAcceptanceSimulationInstall() {
    if (!acceptanceSimulation || acceptanceSimulationStage !== "initial") return
    if (!applyAcceptanceSimulationStage("proposed")) return
    beginApproval(pendingApproval)
  }
  function beginAcceptanceSimulationActivation(modelId) {
    if (!acceptanceSimulation || acceptanceSimulationStage !== "installed") return
    if (!applyAcceptanceSimulationStage("activating")) return
    requestedModel = modelId
    modelSwitching = true
    modelSwitchStatus = "SIMULATION // LOADING AND VERIFYING"
    acceptanceSimulationTimer.restart()
  }
  function advanceAcceptanceSimulation() {
    if (!acceptanceSimulation) return
    if (acceptanceSimulationStage === "downloading") {
      applyAcceptanceSimulationStage("verifying")
      acceptanceSimulationTimer.restart()
      return
    }
    if (acceptanceSimulationStage === "verifying") {
      applyAcceptanceSimulationStage(acceptanceSimulationFailure ? "failed" : "installed")
      return
    }
    if (acceptanceSimulationStage === "activating") {
      modelSwitching = false
      requestedModel = ""
      applyAcceptanceSimulationStage("ready")
      modelSwitchStatus = "SIMULATION // MODEL READY"
    }
  }
  function sendAcceptanceSimulationReply() {
    var message = chatInput ? chatInput.text.trim() : ""
    if (!acceptanceSimulation || acceptanceSimulationStage !== "ready"
        || !onboardingCanSend || chatBusy || modelTransitionActive
        || acceptanceSimulationReplyShown || message === "") return
    chatInput.text = ""
    addChat("user", message)
    addChat("assistant", acceptancePreviewFixture.canned_reply
            || "CANNED FIXTURE REPLY // NO INFERENCE")
    acceptanceSimulationReplyShown = true
  }
  function acceptanceSimulationStageLabel() {
    return ({
      "initial": "NO MODEL INSTALLED",
      "proposed": "AWAITING APPROVAL",
      "downloading": "SIMULATED DOWNLOAD",
      "verifying": "SIMULATED VERIFICATION",
      "failed": "SIMULATED FAILURE",
      "installed": "INSTALLED // INACTIVE",
      "activating": "SIMULATED ACTIVATION",
      "ready": acceptanceSimulationReplyShown ? "CANNED REPLY SHOWN" : "READY // NO INFERENCE"
    })[acceptanceSimulationStage] || "STARTING"
  }
  // quiet: a background re-check (every 30 s while open; waiting on a CLI or a setup step).
  // A fresh answer stays on screen until the new one arrives: blanking to CHECKING on every
  // re-check was a jitter every 30 s on slower PCs (Konstantin's friend, 2026-10-23). Send
  // stays bounded by the answer's 45 s age (onboardingCanSend).
  function refreshOnboarding(quiet) {
    if (acceptancePreview) { applyAcceptancePreview(); return }
    if (onboardingRequestInFlight || !onboardingRefreshAllowed()) return
    onboardingRequestInFlight = true
    onboardingRecheckQuiet = quiet === true && onboardingSnapshotFresh
    if (!onboardingRecheckQuiet) {
      onboardingSnapshotFresh = false
      onboardingState = "CHECKING"
      onboardingActions = []
    }
    var serial = ++onboardingRequestSerial
    var xhr = root.novaRequest()
    var finished = false
    function finishUnavailable() {
      if (finished || serial !== onboardingRequestSerial) return
      finished = true
      setOnboardingUnavailable()
    }
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE || finished || serial !== onboardingRequestSerial) return
      if (xhr.status !== 200) { finishUnavailable(); return }
      try {
        var payload = JSON.parse(xhr.responseText)
        if (payload.schema_version !== 1 || typeof payload.primary_state !== "string"
            || !Array.isArray(payload.allowed_actions)) { finishUnavailable(); return }
        var configuration = payload.configuration || ({})
        if (activationReconcileModel !== "" && (configuration.explicit_selection !== true
            || configuration.provider !== activationReconcileProvider
            || configuration.model !== activationReconcileModel
            || payload.primary_state === "ACTIVATING"
            || (payload.primary_state === "READY" && !readySnapshotCoherent(configuration, payload.explanation_codes)))) {
          finished = true
          onboardingRequestInFlight = false
          onboardingState = "CHECKING"
          onboardingSnapshotFresh = false
          return
        }
        finished = true
        onboardingRequestInFlight = false
        var readinessConflict = payload.primary_state === "READY" && !readySnapshotCoherent(configuration, payload.explanation_codes)
        var explicitIdentity = configuration.explicit_selection === true
          && typeof configuration.provider === "string" && configuration.provider !== ""
          && typeof configuration.model === "string" && configuration.model !== ""
        onboardingState = readinessConflict ? (explicitIdentity ? "NEEDS_LOAD" : "CHECKING") : payload.primary_state
        onboardingActions = readinessConflict ? ["RETRY_CHECK"] : payload.allowed_actions
        onboardingCodes = readinessConflict ? ["READINESS_CONFLICT"]
          : (Array.isArray(payload.explanation_codes) ? payload.explanation_codes : [])
        onboardingLimitations = Array.isArray(payload.limitations) ? payload.limitations : []
        onboardingTarget = typeof payload.target_id === "string" ? payload.target_id : ""
        var observedProvider = payload.observations && payload.observations.provider || ({})
        providerVersion = typeof observedProvider.version === "string" ? observedProvider.version : ""
        providerMinimumVersion = typeof observedProvider.minimum_version === "string" ? observedProvider.minimum_version : ""
        onboardingExplicitSelection = configuration.explicit_selection === true
        onboardingSelectedProvider = onboardingExplicitSelection && typeof configuration.provider === "string" ? configuration.provider : ""
        onboardingSelectedModel = onboardingExplicitSelection && typeof configuration.model === "string" ? configuration.model : ""
        onboardingReceivedAt = Date.now()
        onboardingSnapshotFresh = !readinessConflict || explicitIdentity
        if (readinessConflict) {
          if (++readinessConflictRetries <= 3) readinessConflictTimer.restart()
        } else {
          readinessConflictRetries = 0
          readinessConflictTimer.stop()
        }
        if (activationReconcileModel !== "") {
          activationReconcileProvider = ""
          activationReconcileModel = ""
          activationReconcileTimer.stop()
        }
      } catch (error) { finishUnavailable() }
    }
    xhr.onerror = finishUnavailable
    xhr.ontimeout = finishUnavailable
    root.openNova(xhr, "GET", "/onboarding/state")
    // The backend's 30-second collection budget is cooperative. This transport
    // deadline is the panel's hard bound and does not imply that an aborted
    // read-only server collector was cancelled.
    xhr.timeout = 35000
    xhr.send()
  }
  function onboardingTitle() {
    if (acceptanceSimulation) {
      if (onboardingState === "ACQUISITION_PROPOSED") return "SIMULATED INSTALL APPROVAL"
      if (onboardingState === "ACQUISITION_IN_PROGRESS")
        return onboardingCodes.indexOf("ACQUISITION_VERIFYING") >= 0 ? "SIMULATED VERIFICATION" : "SIMULATED DOWNLOAD"
      if (onboardingState === "INSTALLATION_FAILED") return "SIMULATED INSTALLATION FAILURE"
      if (onboardingState === "ACTIVATING") return "SIMULATED LOADING AND VERIFICATION"
      if (onboardingState === "READY") return "LOCAL MODEL READY // SIMULATED"
    }
    if (onboardingCodes.indexOf("PREVIEW_APPROVAL_RECORDED") >= 0) return "APPROVAL PREVIEW COMPLETE"
    if (onboardingCodes.indexOf("PREVIEW_APPROVAL_CANCELLED") >= 0) return "INSTALL PREVIEW CANCELLED"
    return ({
      "CHECKING": "CHECKING LOCAL AI",
      "NEEDS_USER_SETUP": "LOCAL SETUP NEEDED",
      "NEEDS_MODEL": "MODEL NEEDED",
      "NEEDS_ACTIVATION": "CHOOSE A MODEL",
      "NEEDS_LOAD": "MODEL NOT READY",
      "ACQUISITION_PROPOSED": "INSTALL APPROVAL",
      "ACQUISITION_IN_PROGRESS": "INSTALLING MODEL",
      "INSTALLATION_FAILED": "INSTALLATION FAILED",
      "INSTALLATION_RECOVERY_REQUIRED": "INSTALLATION NEEDS RECOVERY",
      "ACTIVATING": "LOADING AND VERIFYING",
      "ACTIVATION_FAILED": "MODEL DIDN'T START",
      "READY": "LOCAL MODEL READY"
    })[onboardingState] || "LOCAL AI STATUS UNKNOWN"
  }
  function onboardingMessage() {
    if (acceptanceSimulation) {
      if (onboardingState === "ACQUISITION_PROPOSED") return "Review this fixture approval. No simulated download starts before approval."
      if (onboardingState === "ACQUISITION_IN_PROGRESS")
        return onboardingCodes.indexOf("ACQUISITION_VERIFYING") >= 0
          ? "Simulating exact artifact verification. Activation remains separate."
          : "Simulating provider download progress. No model or provider is being changed."
      if (onboardingState === "INSTALLATION_FAILED") return "The selected fixture branch simulated a verification failure. No retry was started."
      if (onboardingState === "NEEDS_ACTIVATION" && onboardingCodes.indexOf("ACQUISITION_INSTALLED") >= 0)
        return "Simulated installation is verified. It is not in use yet; choose LOAD AND USE to continue."
      if (onboardingState === "ACTIVATING") return "Simulating model loading and readiness verification before selection."
      if (onboardingState === "READY") return acceptanceSimulationReplyShown
        ? "The canned fixture reply was shown. Restart to repeat the simulation."
        : "Simulated activation is verified. Type a message to display the canned first reply."
    }
    if (onboardingCodes.indexOf("PREVIEW_APPROVAL_RECORDED") >= 0)
      return "Approval input was simulated. No installation or download was started."
    if (onboardingCodes.indexOf("PREVIEW_APPROVAL_CANCELLED") >= 0)
      return "Cancellation input was simulated. No installation or download was started."
    if (onboardingState === "CHECKING") return onboardingCodes.indexOf("READINESS_CONFLICT") >= 0
      ? "Current model status is inconsistent. Check again before using chat."
      : "Checking prerequisites, model selection, and current readiness."
    if (onboardingState === "NEEDS_USER_SETUP") {
      if (onboardingCodes.indexOf("NOVAD_UNAVAILABLE") >= 0) return "NOVA’s background helpers aren’t installed or running yet."
      if (onboardingCodes.indexOf("PROVIDER_UNAVAILABLE") >= 0) return "Ollama, the engine that runs AI models, isn’t installed or running yet."
      if (onboardingCodes.indexOf("PROVIDER_OUTDATED") >= 0) return "This Ollama is too old for NOVA."
      return "A required local hardware check isn’t available. Complete local setup, then check again."
    }
    if (onboardingState === "NEEDS_MODEL") {
      if (onboardingHasAction("PROPOSE_INSTALL")) return onboardingTarget !== ""
        ? "No local model is installed yet. " + friendlyModelName(onboardingTarget) + " fits this computer."
        : "No local model is installed yet. This catalog model fits this computer."
      if (onboardingHasAction("CHOOSE_INSTALL_CANDIDATE")) return "Several catalog models fit this computer. Choose one in the model list."
      var blocked = installBlockSummary()
      if (blocked !== "") return blocked
      return "NOVA can’t offer an installable catalog model from the current evidence. Check the model details, then check again."
    }
    if (onboardingState === "NEEDS_ACTIVATION") {
      if (onboardingCodes.indexOf("ACQUISITION_INSTALLED") >= 0) return "Installed and verified, but not in use yet. LOAD AND USE loads it, checks that it answers, and makes it NOVA’s model."
      return "Choose an installed model. NOVA will load and verify it before saving the selection."
    }
    if ((onboardingState === "NEEDS_LOAD" || onboardingState === "NEEDS_ACTIVATION") && cliLoadingModel(onboardingTarget))
      return "The coding CLI is loading this model. NOVA shows it as ready once it's loaded."
    if (onboardingState === "NEEDS_LOAD") return onboardingCodes.indexOf("READINESS_CONFLICT") >= 0
      ? "The selected model’s current readiness conflicts with NOVA’s latest status. Check again or load and verify it."
      : "The selected model is installed but is not ready for this session."
    if (onboardingState === "ACQUISITION_PROPOSED") return "Review the existing installation approval. No download starts before approval."
    if (onboardingState === "ACQUISITION_IN_PROGRESS") return onboardingCodes.indexOf("ACQUISITION_VERIFYING") >= 0 ? "Checking the downloaded model. It won't start by itself afterwards." : "Downloading the model you approved. It won't start by itself afterwards."
    if (onboardingState === "INSTALLATION_FAILED") return "Installation did not complete. Open the model list for the recorded status."
    if (onboardingState === "INSTALLATION_RECOVERY_REQUIRED") return "Installation needs recovery. NOVA will not retry automatically."
    if (onboardingState === "ACTIVATING") return "Loading and verifying the selected model before saving it."
    if (onboardingState === "ACTIVATION_FAILED") return "NOVA couldn’t start and check this model. Your previous model stays selected."
    if (onboardingState === "READY") {
      if (onboardingLimitations.indexOf("HARDWARE_PROFILE_UNKNOWN") >= 0) return "Your model is ready; type a message below. This computer's hardware details couldn't be read."
      if (onboardingLimitations.indexOf("HARDWARE_PROFILE_PARTIAL") >= 0) return "Your model is ready; type a message below. Some hardware details couldn't be read."
      return "Your model is ready; type a message below."
    }
    return "Current local-model state could not be established."
  }
  // Beginner setup steps (fresh-friend walkthrough): each opens Omarchy's floating
  // terminal with a fixed command, so the user sees and approves every step.
  readonly property string pluginDir: decodeURIComponent(String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "")).replace(/\/$/, "")
  property bool novaSetupScriptPresent: false
  property bool ollamaInstalled: false
  property string providerVersion: ""
  property string providerMinimumVersion: ""
  property string setupLaunched: ""
  property var terminalLauncher: null
  // REMOVE-01 (Konstantin 2026-10-23: "remove it all: NOVA, models and local LLMs"): a card
  // with three choices and real sizes, are-you-sure, then the same floating terminal as
  // INSTALL OLLAMA runs tools/nova-uninstall.sh with the choice (sudo only for Ollama).
  property bool removeNovaOpen: false
  property string removeNovaChoice: "everything"
  property bool removeNovaSure: false
  property bool removeScriptPresent: false
  property real modelStorageBytes: -1  // every model's files, each counted once (novad)
  readonly property var removeNovaChoices: [
    { id: "everything", title: "Everything", text: "NOVA, its settings and chats, every model, and Ollama itself." },
    { id: "models", title: "NOVA and its models", text: "NOVA, its settings and chats, and every model. Ollama stays for other tools." },
    { id: "nova", title: "NOVA only", text: "NOVA, its settings and chats. Ollama and every model stay." }]
  function openRemoveNova() {
    removeNovaChoice = "everything"; removeNovaSure = false; removeNovaOpen = true
    if (!opened && typeof open === "function") open()
  }
  function closeRemoveNova() { removeNovaOpen = false; removeNovaSure = false }
  // A model NOVA installed (its catalog download) or built (a coding CLI build); anything
  // else another tool or the user pulled, and it goes too.
  function modelFromNova(item) {
    var match = matchedCatalogEntryFor(item), record = match ? acquisitionByCatalog[match.catalog_id] : null
    return !!(item && (item.tailored_for || (record && record.state === "INSTALLED")))
  }
  function removeNovaFrees(choice) {
    if (choice === "nova") return "< 0.1 GiB"
    var bytes = modelStorageBytes >= 0 ? modelStorageBytes
      : discoveredModels.reduce(function(sum, item) { return sum + (item && item.metadata && item.metadata.size_bytes > 0 ? item.metadata.size_bytes : 0) }, 0)
    return bytesLabel(bytes)
  }
  function removeNovaBlocker() {
    if (cliState && Array.isArray(cliState.profiles) && cliState.profiles.some(function(p) { return p && p.running === true }))
      return "A coding CLI is running: close it first."
    if (trackedAcquisitionId !== "") return "A model is downloading: let it finish first."
    if (chatBusy) return "NOVA is answering: wait a moment."
    if (!removeScriptPresent) return "This copy of NOVA has no remove script; see NOVA's README, \"Remove\"."
    return ""
  }
  function launchRemoveNova() {
    if (removeNovaBlocker() !== "" || ["everything", "models", "nova"].indexOf(removeNovaChoice) < 0) return
    var command = "omarchy-launch-floating-terminal-with-presentation " + shellQuote(shellQuote(pluginDir + "/tools/nova-uninstall.sh") + " --" + removeNovaChoice)
    if (typeof terminalLauncher === "function") terminalLauncher(command)  // test hook only
    else if (bar && typeof bar.run === "function") bar.run(command)
    else return
    closeRemoveNova()
    // Step aside so the terminal (and its password prompt) is not covered by the panel.
    if (!acceptancePreview && typeof terminalLauncher !== "function") close()
  }
  FileView {
    id: removeScript
    path: root.pluginDir + "/tools/nova-uninstall.sh"
    printErrors: false
    onLoaded: root.removeScriptPresent = true
    onLoadFailed: root.removeScriptPresent = false
  }
  FileView {
    id: novaSetupScript
    path: root.pluginDir + "/tools/nova-setup.sh"
    printErrors: false
    onLoaded: root.novaSetupScriptPresent = true
    onLoadFailed: root.novaSetupScriptPresent = false
  }
  FileView {
    id: ollamaUnit
    // Present exactly when the Ollama package is installed; small enough to read.
    path: "/usr/lib/systemd/system/ollama.service"
    printErrors: false
    onLoaded: root.ollamaInstalled = true
    onLoadFailed: root.ollamaInstalled = false
  }
  function shellQuote(value) { return "'" + String(value).replace(/'/g, "'\\''") + "'" }
  function setupStep() {
    if (acceptancePreview || onboardingState !== "NEEDS_USER_SETUP") return null
    if (onboardingCodes.indexOf("NOVAD_UNAVAILABLE") >= 0) {
      if (novaSetupScriptPresent)
        return { id: "nova", label: "SET UP NOVA", command: shellQuote(pluginDir + "/tools/nova-setup.sh"),
                 text: "NOVA needs three small background helpers. SET UP NOVA opens a terminal and installs them for your user; no administrator password is needed." }
      return { id: "nova-start", label: "START NOVA", command: "systemctl --user restart novad.service nova-system-tools.service nova-actions.service",
               text: "NOVA's background helpers are installed but not running. START NOVA opens a terminal and starts them." }
    }
    if (onboardingCodes.indexOf("PROVIDER_UNAVAILABLE") >= 0) {
      if (ollamaInstalled)
        return { id: "ollama-start", label: "START OLLAMA", command: "sudo systemctl --quiet enable --now ollama",
                 text: "Ollama, the engine that runs AI models on this computer, is installed but not running. START OLLAMA opens a terminal and starts it; it asks for your password." }
      var pkg = hostWidget && hostWidget.gpuAvailable === true ? "ollama-cuda" : "ollama"
      // A never-updated Omarchy has no usable package lists yet (only the installer's
      // offline.db), so ask pacman whether it can see the package; if not,
      // `omarchy update` is Omarchy's way to get the lists (walkthrough run 3).
      return { id: "ollama", label: "INSTALL OLLAMA",
               command: "{ pacman -Si " + pkg + " >/dev/null 2>&1 || omarchy update; } && omarchy pkg add " + pkg + " && sudo systemctl --quiet enable --now ollama"
                        + " || { echo; echo 'Ollama was not installed. The message above says why; then try INSTALL OLLAMA again in the NOVA panel.'; }",
               text: "NOVA runs AI models with Ollama, a free engine for this computer. INSTALL OLLAMA opens a terminal that installs " + pkg
                     + " with Omarchy's package tool and starts it; it asks for your password, like other Omarchy installs." }
    }
    if (onboardingCodes.indexOf("PROVIDER_OUTDATED") >= 0)
      // Omarchy updates the whole system rather than one package (Arch has no
      // partial upgrades), then the new Ollama is started.
      return { id: "ollama-update", label: "UPDATE OLLAMA",
               command: "omarchy update && sudo systemctl --quiet restart ollama"
                        + " || { echo; echo 'Ollama was not updated. The message above says why; then try UPDATE OLLAMA again in the NOVA panel.'; }",
               text: "This computer has " + (providerVersion ? "Ollama " + providerVersion : "an older Ollama") + "; NOVA needs "
                     + (providerMinimumVersion ? providerMinimumVersion + " or newer" : "a newer one") + ". UPDATE OLLAMA opens a terminal that runs Omarchy's update (it updates the whole system, as Omarchy always does) and restarts Ollama; it asks for your password." }
    return null
  }
  function runSetupStep() {
    var step = setupStep()
    if (!step) return
    // Quoted as one argument, like Omarchy's own menu, so `&&` runs inside the terminal.
    var command = "omarchy-launch-floating-terminal-with-presentation " + shellQuote(step.command)
    if (typeof terminalLauncher === "function") terminalLauncher(command)  // test hook only
    else if (bar && typeof bar.run === "function") bar.run(command)
    else return
    setupLaunched = step.id
    setupFollowUp.ticks = 0
    setupFollowUp.restart()
    // Step aside so the terminal (and its password prompt) is not covered by the panel.
    if (!acceptancePreview && typeof terminalLauncher !== "function") close()
  }
  // UPD-01: git-managed installs check for a newer NOVA at most weekly (the script
  // caches its answer); clicking the version checks now. Copy installs have no
  // script, so nothing is shown.
  property string updateStatus: ""      // "", checking, current, available, failed, unsupported
  property string updateRemote: ""
  property double updateCheckedAt: 0     // epoch seconds of the answer
  property bool updateFeedback: false    // show the result of a check the user asked for
  property var updateCheckRunner: null   // test hook: function(args, done(line))
  readonly property bool updateSupported: gitRevision !== "" && updateStatus !== "" && updateStatus !== "unsupported"
  readonly property bool updateAllowed: updateStatus === "available" && !chatBusy && !modelTransitionActive
                                        && pendingApproval === null && trackedAcquisitionId === ""
  function applyUpdateCheck(line) {
    var parts = String(line).trim().split(/\s+/)
    if (["current", "available", "failed", "unsupported"].indexOf(parts[0]) < 0) { updateStatus = updateStatus === "checking" ? "failed" : updateStatus; return }
    updateStatus = parts[0]
    updateRemote = /^[0-9a-f]{40}$/.test(parts[2] || "") ? parts[2] : ""
    updateCheckedAt = Number(parts[3]) || 0
    if (updateFeedback) updateFeedbackTimer.restart()
  }
  function checkForUpdates(now) {
    // Only a git-managed install (its commit already read for the footer) checks;
    // copy installs never start the process.
    if (acceptancePreview || gitRevision === "" || updateStatus === "checking" || updateStatus === "unsupported") return
    var args = now ? ["--now"] : []
    if (now) { updateFeedback = true; updateFeedbackTimer.stop(); updateStatus = "checking" }
    if (typeof updateCheckRunner === "function") { updateCheckRunner(args, applyUpdateCheck); return }
    updateCheckProcess.command = [pluginDir + "/tools/nova-update-check.sh"].concat(args)
    updateCheckProcess.running = true
  }
  function runUpdate() {
    if (!updateAllowed) return
    var command = "omarchy-launch-floating-terminal-with-presentation " + shellQuote(shellQuote(pluginDir + "/tools/nova-update.sh"))
    if (typeof terminalLauncher === "function") terminalLauncher(command)  // test hook only
    else if (bar && typeof bar.run === "function") bar.run(command)
    else return
    if (!acceptancePreview && typeof terminalLauncher !== "function") close()
  }
  function updateLinkText() {
    if (updateStatus === "available") return "UPDATE NOVA"
    if (!updateFeedback) return ""
    return ({checking: "CHECKING…", current: "UP TO DATE", failed: "CHECK FAILED"})[updateStatus] || ""
  }
  function updateTooltipText() {
    if (!updateSupported) return ""
    var when = updateCheckedAt > 0 ? " · last checked " + new Date(updateCheckedAt * 1000).toLocaleDateString(Qt.locale(), Locale.ShortFormat) : ""
    if (updateStatus === "available") return "A newer NOVA (" + updateRemote.slice(0, 7) + ") is available" + when
    if (updateStatus === "failed") return "Couldn't reach NOVA's update source" + when + " · click to try again"
    return "NOVA checks for updates once a week" + when + " · click to check now"
  }
  Process {
    id: updateCheckProcess
    stdout: StdioCollector { onStreamFinished: root.applyUpdateCheck(text) }
  }
  Timer { id: updateFeedbackTimer; interval: 6000; onTriggered: root.updateFeedback = false }
  function onboardingActionLabel() {
    if (!onboardingSnapshotFresh && onboardingState === "CHECKING") return ""
    if (onboardingHasAction("PROPOSE_INSTALL")) return onboardingTarget !== "" ? "INSTALL " + friendlyModelName(onboardingTarget).toUpperCase() : "INSTALL MODEL"
    if (onboardingHasAction("CHOOSE_INSTALL_CANDIDATE") || onboardingHasAction("CHOOSE_INSTALLED_MODEL")) return "VIEW MODELS"
    if ((onboardingHasAction("LOAD_AND_VERIFY") || onboardingHasAction("ACTIVATE_MODEL")) && cliLoadingModel(onboardingTarget)) return ""
    if (onboardingHasAction("LOAD_AND_VERIFY")) return "LOAD AND VERIFY " + (onboardingTarget ? runtimeDisplayName(onboardingTarget) : "MODEL").toUpperCase()
    if (onboardingHasAction("ACTIVATE_MODEL")) return "LOAD AND USE " + (onboardingTarget ? runtimeDisplayName(onboardingTarget) : "MODEL").toUpperCase()
    if (onboardingHasAction("RECHECK_MODEL_OPTIONS") || onboardingHasAction("VIEW_RECOVERY_DETAILS")) return "VIEW MODELS"
    if (onboardingHasAction("RETRY_CHECK")) return "CHECK AGAIN"
    return ""
  }
  function modelForCatalog(catalogId) {
    return catalogModels.find(function(item) { return item && item.catalog_id === catalogId }) || null
  }
  // A person-facing name for a catalog id ("Llama 3.2 1B"), falling back to the id.
  // Approval DETAILS in words: how NOVA knows the model's size and fingerprint.
  function provenancePlain(parts) {
    if (!Array.isArray(parts) || !parts.length) return "From NOVA's own model list"
    var methods = { registry_manifest_probe: "Size and fingerprint read from Ollama's model registry",
                    registry_probe: "Read from Ollama's model registry",
                    manual_local_seed: "Entered by hand in NOVA's model list",
                    transcribed_local_manifest_and_catalog_snapshot: "Size and fingerprint taken from a checked earlier download" }
    return parts.map(function(part) {
      var text = String(part)
      var date = text.match(/^(\d{4}-\d{2}-\d{2})T[\d:.]+Z?$/)
      if (date) return "checked " + date[1]
      return methods[text] || text.replace(/_/g, " ")
    }).join(", ")
  }
  // The person-facing name for an installed model id ("llama3.2:1b" -> "Llama 3.2 1B (Q8_0)").
  function runtimeDisplayName(runtimeId) {
    var item = catalogModels.filter(function(m) { return catalogRuntimeId(m) === runtimeId })[0]
    // A model outside the catalog (e.g. a custom alias) keeps its own name, minus ":latest".
    return item && typeof item.display_name === "string" && item.display_name !== "" ? item.display_name : String(runtimeId).replace(/:latest$/, "")
  }
  // Beginner-facing name: the catalog name without its quantization ("Llama 3.2 1B").
  // The quantization stays in the model's details. Catalog names are unique without it.
  function friendlyModelName(catalogId) { return catalogDisplayName(catalogId).replace(/\s*\([^)]*\)\s*$/, "") }
  // One naming rule for the model bar and the installed list: the catalog name without
  // its quantization, or the model's own name without Ollama's default ":latest" tag.
  // The exact Ollama name stays in the row's details and in tooltips.
  function modelPlainName(id) { return runtimeDisplayName(id).replace(/\s*\([^)]*\)\s*$/, "") }
  function catalogDisplayName(catalogId) {
    var item = modelForCatalog(catalogId)
    return item && typeof item.display_name === "string" && item.display_name !== "" ? item.display_name : String(catalogId)
  }
  function showModelChoices() {
    if (panelView !== "models" || approvalForeground) showView("models")
    else { refreshModels(); refreshFit(); refreshCatalog(); refreshAcquisitions(); refreshRecommendations() }
  }
  function performOnboardingAction() {
    if (acceptancePreview) { performAcceptancePreviewAction(); return }
    if (onboardingRequestInFlight && !onboardingRecheckQuiet) return
    if (!onboardingSnapshotFresh) {
      if (onboardingHasAction("RETRY_CHECK")) refreshOnboarding()
      return
    }
    if (onboardingHasAction("PROPOSE_INSTALL") && onboardingTarget !== "") {
      var record = modelForCatalog(onboardingTarget)
      proposeCatalogAcquisition(onboardingTarget, record ? record.display_name : onboardingTarget)
      return
    }
    if ((onboardingHasAction("LOAD_AND_VERIFY") || onboardingHasAction("ACTIVATE_MODEL")) && onboardingTarget !== "") {
      selectModel(onboardingTarget, true)
      return
    }
    if (onboardingHasAction("CHOOSE_INSTALL_CANDIDATE") || onboardingHasAction("CHOOSE_INSTALLED_MODEL")
        || onboardingHasAction("RECHECK_MODEL_OPTIONS") || onboardingHasAction("VIEW_RECOVERY_DETAILS")) {
      showModelChoices()
      return
    }
    if (onboardingHasAction("RETRY_CHECK")) refreshOnboarding()
  }
  function performAcceptancePreviewAction() {
    if (!acceptancePreviewFixture) return
    if (acceptanceSimulation) {
      if (onboardingHasAction("PROPOSE_INSTALL")) {
        beginAcceptanceSimulationInstall()
        return
      }
      if (onboardingHasAction("CHOOSE_INSTALL_CANDIDATE") || onboardingHasAction("CHOOSE_INSTALLED_MODEL")
          || onboardingHasAction("RECHECK_MODEL_OPTIONS") || onboardingHasAction("VIEW_RECOVERY_DETAILS")) {
        panelView = "models"
        return
      }
      if (onboardingHasAction("ACTIVATE_MODEL") || onboardingHasAction("LOAD_AND_VERIFY")) {
        beginAcceptanceSimulationActivation(onboardingTarget)
        return
      }
      return
    }
    if (onboardingHasAction("PROPOSE_INSTALL") && acceptancePreviewFixture.install_approval) {
      beginApproval(acceptancePreviewFixture.install_approval)
      onboardingState = "ACQUISITION_PROPOSED"
      onboardingActions = ["DECIDE_INSTALL_APPROVAL"]
      onboardingCodes = ["INSTALL_APPROVAL_REQUIRED"]
      return
    }
    if (onboardingHasAction("CHOOSE_INSTALL_CANDIDATE") || onboardingHasAction("CHOOSE_INSTALLED_MODEL")
        || onboardingHasAction("RECHECK_MODEL_OPTIONS") || onboardingHasAction("VIEW_RECOVERY_DETAILS")) {
      panelView = "models"
      return
    }
    if (onboardingHasAction("ACTIVATE_MODEL") || onboardingHasAction("LOAD_AND_VERIFY")) {
      onboardingState = "ACTIVATING"
      onboardingActions = []
      onboardingCodes = ["PREVIEW_ACTIVATION_NOT_EXECUTED"]
      modelSwitchStatus = "PREVIEW // ACTIVATION NOT EXECUTED"
      return
    }
    if (onboardingHasAction("RETRY_CHECK")) applyAcceptancePreview()
  }
  function isExplicitlySelected(modelData) {
    return onboardingSnapshotFresh && onboardingExplicitSelection
      && catalogRuntimeId(modelData) === onboardingSelectedModel
  }
  function brainSummary() {
    // Plain name in the bar; the exact provider and id stay in the tooltip.
    function plain(id) { return modelPlainName(id) }
    if (modelTransitionActive) return "MODEL · SWITCHING TO " + plain(transitionTarget)
    if (!online) return novaSetupPending ? "MODEL · AFTER SETUP" : "MODEL · NOVA OFFLINE"
    if (!onboardingSnapshotFresh) return "MODEL · CHECKING SETUP"
    if (!onboardingExplicitSelection) return "MODEL · NONE SELECTED"
    return "MODEL · " + plain(onboardingSelectedModel) + (activeModelAvailable ? "" : " · UNAVAILABLE")
  }
  function brainIdentityText() {
    if (modelTransitionActive) return transitionTarget !== "" ? "Switching to model: " + transitionTarget : "Switching model; target unavailable"
    if (!onboardingSnapshotFresh) return "Checking model setup"
    if (!onboardingExplicitSelection) return "No model selected"
    if (onboardingSelectedProvider === "" || onboardingSelectedModel === "") return "Selected model identity unavailable"
    return "Selected model: " + onboardingSelectedProvider + " / " + onboardingSelectedModel
      + (activeModelAvailable ? "" : " · unavailable")
  }
  function chatPlaceholder() {
    if (!online) return novaSetupPending ? "Finish setting up NOVA to chat…" : "NOVA's background service isn't running…"
    if (chatBusy) return "NOVA is replying…"
    if (acceptanceSimulation && acceptanceSimulationReplyShown) return "Canned first reply shown. Restart to repeat."
    if (!onboardingCanSend) return "Complete local-model setup to chat…"
    if (acceptanceSimulation && !modelLoadsOnSend) return "Type anything to show the canned first reply…"
    return modelLoadsOnSend && !activeModelRuntimeReady ? "Message NOVA… (the model loads with your first message)" : "Message NOVA…"
  }
  function refreshModels() {
    if (acceptancePreview) return
    if (installedModelsLoading) return
    installedModelsLoading = true
    var serial = ++modelsRequestSerial
    var xhr = root.novaRequest()
    var finished = false
    function finish(status, body) {
      if (finished) return
      finished = true
      if (serial !== modelsRequestSerial) return
      modelsRequestDeadline.stop()
      modelsRequestTimeoutCallback = null
      installedModelsLoading = false
      applyInstalledModelsResult(status, body)
    }
    xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status, xhr.responseText) }
    xhr.onerror = function() { finish(0, "") }
    xhr.ontimeout = function() { finish(0, "") }
    modelsRequestTimeoutCallback = function() { finish(0, "") }
    modelsRequestDeadline.restart()
    try {
      root.openNova(xhr, "GET", "/models")
      xhr.timeout = 2000
      xhr.send()
    } catch (error) { finish(0, "") }
  }
  function applyInstalledModelsResult(status, body) {
    var payload = null
    try { payload = status === 200 ? JSON.parse(body) : null } catch (error) { payload = null }
    if (payload && typeof payload === "object" && Array.isArray(payload.models)) {
      discoveredModels = payload.models.filter(function(item) { return !(item && item.tailored_for) })
      modelStorageBytes = typeof payload.storage_bytes === "number" && payload.storage_bytes >= 0 ? payload.storage_bytes : -1
      modelsUnavailable = false
      // Run 6: "Freed … loads again when it's next used" stayed under "IN MEMORY" after
      // the model loaded again. An unload note lasts until the model is back in memory.
      if (unloadNoteModel !== "" && removalStatusModel === unloadNoteModel
          && payload.models.some(function(item) { return item && item.id === unloadNoteModel && item.resident === true })) {
        setRemovalStatus("", "", false); unloadNoteModel = ""
      }
      refreshCliProfiles()
      return
    }
    discoveredModels = []
    modelsUnavailable = true
  }
  // Why the list is short on a machine without its own graphics memory (walkthrough:
  // an 8 GB VM fits only the 1B model). The wording comes from novad's budget
  // explanation (one source, QWEN-03). Empty when a GPU budget applies or it is unknown.
  property string budgetNote: ""
  function budgetNoteText(budget) {
    if (!budget || budget.mode !== "cpu" || !(budget.operating_budget_bytes > 0) || !(budget.reserve_bytes > 0)) return ""
    return typeof budget.explanation === "string" ? budget.explanation : ""
  }
  function refreshFit() {
    if (acceptancePreview) return
    modelFits = ({})
    hardwareSummary = "LOCAL AI // FIT PENDING"
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      if (xhr.status !== 200) { hardwareSummary = "LOCAL AI // FIT UNAVAILABLE"; return }
      try {
        var payload = JSON.parse(xhr.responseText), fits = ({})
        ;(payload.models || []).forEach(function(item) { fits[item.id] = item.fit })
        modelFits = fits
        var cap = payload.hardware.capacity, budget = payload.budget
        budgetNote = budgetNoteText(budget)
        graphicsDevices = Array.isArray(cap.gpus) ? cap.gpus : null
        var candidate = cap.gpus.filter(function(gpu) { return gpu.id === budget.device })[0]
        var device = candidate ? (candidate.model || candidate.vendor.toUpperCase()) + " · VRAM " + bytesLabel(candidate.vram_total_bytes) : (cap.gpus.length ? "GPU // DEVICE UNKNOWN" : "CPU")
        hardwareSummary = "LOCAL AI // " + device + " · RAM " + bytesLabel(cap.memory.total_bytes) + " · EST. BUDGET " + bytesLabel(budget.operating_budget_bytes) + (payload.provider_available ? "" : " · OLLAMA OFFLINE")
      } catch (error) { hardwareSummary = "LOCAL AI // FIT UNAVAILABLE" }
    }
    root.openNova(xhr, "GET", "/models/fit")
    xhr.timeout = 20000
    xhr.send()
  }
  function refreshCatalog() {
    if (acceptancePreview) return
    refreshModelUpdates(false)  // novad only asks Ollama when the weekly check is due
    catalogSummary = "CATALOG // CHECKING"
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      if (xhr.status !== 200) { catalogSummary = "CATALOG // UNAVAILABLE"; return }
      try {
        var payload = JSON.parse(xhr.responseText)
        if (payload.status !== "ready" && payload.status !== "partial") { catalogSummary = "CATALOG // UNAVAILABLE"; return }
        catalogModels = Array.isArray(payload.models) ? payload.models : []
        preflightByCatalog = ({})
        catalogModels.forEach(function(item) { if (item && item.installed_match && item.installed_match.match_status !== "exact") refreshPreflight(item) })
        var counts = payload.installed_match_counts || ({})
        var exact = Number(counts.exact) || 0
        var mismatch = Number(counts.revision_mismatch) || 0
        var ambiguous = Number(counts.ambiguous) || 0
        var unknown = Number(counts.unknown) || 0
        if (mismatch > 0) catalogSummary = "CATALOG // REVISION MISMATCH"
        else if (ambiguous > 0) catalogSummary = "CATALOG // AMBIGUOUS"
        else if (unknown > 0) catalogSummary = "CATALOG // UNKNOWN"
        else catalogSummary = "CATALOG // LOCAL // " + (Number(payload.record_count) || 0) + " MODELS // EXACT " + exact
      } catch (error) { catalogSummary = "CATALOG // UNAVAILABLE" }
    }
    root.openNova(xhr, "GET", "/catalog/models")
    xhr.timeout = 2000
    xhr.send()
  }
  function refreshPreflight(modelData) {
    if (acceptancePreview) return
    if (!modelData || typeof modelData.catalog_id !== "string") return
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      var next = ({})
      for (var key in preflightByCatalog) next[key] = preflightByCatalog[key]
      if (xhr.status === 200) {
        try { next[modelData.catalog_id] = JSON.parse(xhr.responseText) }
        catch (error) { next[modelData.catalog_id] = { status: "UNKNOWN" } }
      } else next[modelData.catalog_id] = { status: "UNKNOWN" }
      preflightByCatalog = next
    }
    root.openNova(xhr, "GET", "/acquisition/models/" + encodeURIComponent(modelData.catalog_id) + "/preflight")
    xhr.timeout = 5000
    xhr.send()
  }
  function acquisitionFor(catalogId) { return acquisitionByCatalog[catalogId] || null }
  function recoveryAllowsRetry(state) {
    return !!(state && state.state === "RECOVERY_REQUIRED"
      && state.recovery_resolution && state.recovery_resolution.retry_allowed === true)
  }
  function acquisitionState(modelData) {
    var state = acquisitionFor(modelData.catalog_id)
    if (state && typeof state.state === "string") {
      if (state.state === "INSTALLED") return "INSTALLED AFTER ACQUISITION"
      if (state.state === "PROPOSED") return "AWAITING APPROVAL"
      if (state.state === "APPROVED" || state.state === "STARTING") return "INSTALLING"
      // A declined or expired proposal is terminal audit history. It must not
      // hide a newly eligible preflight or prevent a fresh proposal.
      if (state.state === "RECOVERY_REQUIRED" && state.recovery_resolution
          && state.recovery_resolution.outcome === "EXACT_INSTALLED")
        return catalogIsInstalled(modelData) ? "INSTALLED" : "BLOCKED"
      if (state.state !== "REJECTED" && state.state !== "EXPIRED" && !recoveryAllowsRetry(state)) return state.state
    }
    var match = modelData.installed_match && modelData.installed_match.match_status
    if (match === "exact") return "INSTALLED"
    if (!modelData.acquisition) return "UNVERIFIED"
    var preflight = preflightByCatalog[modelData.catalog_id]
    if (preflight && preflight.status === "ELIGIBLE") return "AVAILABLE TO INSTALL"
    if (preflight && preflight.status === "BLOCKED") return "BLOCKED"
    if (preflight && preflight.status === "UNKNOWN") return "UNKNOWN"
    return "CHECKING"
  }
  function catalogRuntimeId(modelData) { return modelData.source && typeof modelData.source.upstream_id === "string" ? modelData.source.upstream_id : "" }
  function catalogIsInstalled(modelData) { return !!(modelData.installed_match && modelData.installed_match.match_status === "exact") }
  function acquisitionDetail(modelData) {
    var state = acquisitionFor(modelData.catalog_id)
    if (recoveryAllowsRetry(state)) return "Previous attempt was interrupted. Recovery reviewed; a new attempt requires fresh approval."
    if (state && state.state === "DOWNLOADING") {
      return "Progress may count parts downloaded earlier."
    }
    if (state && state.state === "VERIFYING") return "EXACT ARTIFACT VERIFICATION"
    if (state && state.state === "RECOVERY_REQUIRED") return "NO AUTOMATIC RETRY"
    if (state && state.state === "FAILED") return "NO AUTOMATIC RETRY"
    if (state && state.state === "INSTALLED") return "VERIFIED // NOT IN USE YET"
    if (state && state.state === "EXPIRED") return "PREVIOUS APPROVAL EXPIRED // REVIEW A NEW PROPOSAL"
    if (state && state.state === "REJECTED") return "PREVIOUS PROPOSAL DECLINED // NO DOWNLOAD STARTED"
    if (!catalogIsInstalled(modelData) && modelData.acquisition) return "TRUSTED CATALOG ARTIFACT"
    return ""
  }
  function acquisitionHistory(modelData) {
    var records = acquisitionHistoryByCatalog[modelData.catalog_id]
    if (!Array.isArray(records) || !records.length) {
      var latest = acquisitionFor(modelData.catalog_id)
      records = latest ? [latest] : []
    }
    return records.map(function(state) { return acquisitionHistoryEntry(state) }).filter(function(text) { return text !== "" }).join("\n\n")
  }
  function acquisitionHistoryEntry(state) {
    if (!state || !state.id) return ""
    var result = "Previous attempt: " + state.id + "\nOutcome: " + String(state.state || "unknown").replace(/_/g, " ")
    if (typeof state.updated_at === "number") result += "\nRecorded: " + new Date(state.updated_at * 1000).toLocaleString()
    if (state.error) result += "\nReported failure: " + String(state.error).replace(/_/g, " ")
    if (state.failure_detail && state.failure_detail.provider_message) result += "\nProvider detail: " + state.failure_detail.provider_message
    if (state.historical_progress && typeof state.historical_progress.observed_at === "number")
      result += "\nHistorical provider progress observed: " + new Date(state.historical_progress.observed_at * 1000).toLocaleString() + " (not current activity)"
    if (state.recovery_resolution) result += "\nRecovery reviewed: " + String(state.recovery_resolution.outcome).replace(/_/g, " ")
    return result
  }
  function reviewRecovery(modelData) {
    if (pendingApproval || chatBusy || modelTransitionActive || trackedAcquisitionId !== "") return
    var state = acquisitionFor(modelData.catalog_id)
    if (!state || state.state !== "RECOVERY_REQUIRED" || state.recovery_resolution) return
    beginApproval({ id: state.id, recovery: true, acquisition: false, target: "", process: "", pid: 0, owner: "", application: "", catalogId: modelData.catalog_id,
      summary: "REVIEW " + catalogRuntimeId(modelData), decision: "", assessment: null,
      operatorConfirmed: false, impact: "Reviewing recovery never starts a download. Any new installation needs fresh preflight and approval." })
    checkRecoveryAssessment()
  }
  function recoveryAssessmentText() {
    var assessment = pendingApproval && pendingApproval.assessment
    if (!assessment) return "Checking the installed artifact and known acquisition activity…"
    var descriptions = {
      "EXACT": "The expected artifact is installed and its identity was verified. No new download is needed.",
      "ABSENT": "The expected artifact is not installed. Resolution may permit a separate new attempt.",
      "MISMATCH": "A different artifact revision is installed. Recovery is blocked; review the catalog and provider identity.",
      "UNVERIFIABLE": "The installed artifact could not be checked reliably. Recovery is blocked until the provider can be checked."
    }
    var text = descriptions[assessment.artifact_state] || "Artifact identity is unknown."
    if (assessment.provider_activity === "ACTIVE") text += " A known provider acquisition is active; resolution is blocked."
    else if (assessment.provider_activity === "INACTIVE_CONFIRMED") text += " The recorded provider activity is confirmed inactive."
    else text += " Ollama cannot report all pull jobs. NOVA cannot machine-verify that no other model download is running."
    if (assessment.resolution_status === "RESOLVED") text += " Recovery has already been reviewed."
    return text
  }
  function recoveryCanResolve() {
    var item = pendingApproval, assessment = item && item.assessment
    return !!(item && item.recovery && !item.decision && assessment
      && assessment.resolution_status === "AVAILABLE" && assessment.provider_activity !== "ACTIVE"
      && (assessment.artifact_state === "EXACT" || assessment.artifact_state === "ABSENT")
      && (!assessment.operator_attestation_required || item.operatorConfirmed === true))
  }
  function toggleRecoveryConfirmation() {
    if (!pendingApproval || !pendingApproval.recovery || pendingApproval.decision) return
    var next = JSON.parse(JSON.stringify(pendingApproval))
    next.operatorConfirmed = next.operatorConfirmed !== true
    pendingApproval = next
  }
  function checkRecoveryAssessment() {
    if (!pendingApproval || !pendingApproval.recovery || ["sending", "checking"].indexOf(pendingApproval.decision) >= 0) return
    var id = pendingApproval.id, serial = ++recoveryRequestSerial, next = JSON.parse(JSON.stringify(pendingApproval))
    next.decision = "checking"; next.assessment = null; next.operatorConfirmed = false
    pendingApproval = next; approvalFeedback = "Checking recovery…"
    if (acceptancePreview) {
      finishRecoveryAssessment(id, 200, JSON.stringify(acceptancePreviewFixture.recovery_assessment || ({ })))
      return
    }
    var xhr = root.novaRequest(), finished = false
    function finish(status, body) {
      if (finished) return
      finished = true
      if (serial !== root.recoveryRequestSerial) return
      recoveryRequestDeadline.stop(); root.recoveryRequestTimeoutCallback = null
      root.finishRecoveryAssessment(id, status, body)
    }
    xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status, xhr.responseText) }
    xhr.onerror = function() { finish(0, "") }; xhr.ontimeout = function() { finish(0, "") }
    try {
      recoveryRequestTimeoutCallback = function() { finish(0, "") }; recoveryRequestDeadline.restart()
      root.openNova(xhr, "GET", "/acquisitions/" + encodeURIComponent(id) + "/recovery")
      xhr.send()
    } catch (error) { finish(0, "") }
  }
  function finishRecoveryAssessment(id, status, body) {
    if (!pendingApproval || !pendingApproval.recovery || pendingApproval.id !== id || pendingApproval.decision !== "checking") return
    var next = JSON.parse(JSON.stringify(pendingApproval)), payload = ({})
    try { payload = JSON.parse(body) } catch (error) { }
    if (status === 200 && typeof payload.assessment_id === "string" && payload.assessment_id.length === 64 && payload.acquisition_id === id
        && ["EXACT", "ABSENT", "MISMATCH", "UNVERIFIABLE"].indexOf(payload.artifact_state) >= 0
        && ["UNKNOWN", "ACTIVE", "INACTIVE_CONFIRMED"].indexOf(payload.provider_activity) >= 0
        && typeof payload.operator_attestation_required === "boolean") {
      next.assessment = payload
      next.decision = payload.resolution_status === "AVAILABLE" ? "" : "unavailable"
      approvalFeedback = payload.resolution_status === "AVAILABLE"
        ? "Resolution only permits the next supported step. It does not start downloading."
        : "Recovery cannot be resolved from this assessment. Check again after addressing the reported condition."
    } else {
      next.decision = "unavailable"
      approvalFeedback = "Recovery assessment unavailable. Check the runtime/provider and check again."
    }
    pendingApproval = next
    Qt.callLater(function() { if (root.pendingApproval && root.pendingApproval.recovery) cancelApprovalControl.forceActiveFocus() })
  }
  function decideRecovery(decision) {
    if (!pendingApproval || !pendingApproval.recovery) return
    if (pendingApproval.decision === "sending") return
    if (decision === "deny") {
      clearPendingApproval(); approvalFeedback = "Recovery review closed. No download started."
      applyStreamState("idle", ""); restoreChatInputFocus(); return
    }
    if (!recoveryCanResolve()) return
    var serial = ++recoveryRequestSerial, item = JSON.parse(JSON.stringify(pendingApproval)), body = { assessment_id: item.assessment.assessment_id }
    if (item.assessment.operator_attestation_required) body.operator_attestation = "NO_PROVIDER_ACQUISITION_ACTIVE"
    item.decision = "sending"; pendingApproval = item; approvalShortcutArmed = false
    approvalFeedback = "Submitting recovery resolution…"
    if (acceptancePreview) {
      finishRecoveryDecision(item.id, 201, JSON.stringify({outcome:item.assessment.artifact_state === "EXACT" ? "EXACT_INSTALLED" : "RETRY_ALLOWED", retry_allowed:item.assessment.artifact_state === "ABSENT"}))
      return
    }
    var xhr = root.novaRequest(), finished = false
    function finish(status, response) {
      if (finished) return
      finished = true
      if (serial !== root.recoveryRequestSerial) return
      recoveryRequestDeadline.stop(); root.recoveryRequestTimeoutCallback = null
      root.finishRecoveryDecision(item.id, status, response)
    }
    xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status, xhr.responseText) }
    xhr.onerror = function() { finish(0, "") }; xhr.ontimeout = function() { finish(0, "") }
    try {
      recoveryRequestTimeoutCallback = function() { finish(0, "") }; recoveryRequestDeadline.restart()
      root.openNova(xhr, "POST", "/acquisitions/" + encodeURIComponent(item.id) + "/resolve-recovery")
      xhr.setRequestHeader("Content-Type", "application/json"); xhr.send(JSON.stringify(body))
    } catch (error) { finish(0, "") }
  }
  function finishRecoveryDecision(id, status, body) {
    if (!pendingApproval || !pendingApproval.recovery || pendingApproval.id !== id || pendingApproval.decision !== "sending") return
    var next = JSON.parse(JSON.stringify(pendingApproval)), payload = ({})
    try { payload = JSON.parse(body) } catch (error) { }
    next.decision = "unavailable"
    if (status === 201 && (payload.outcome === "RETRY_ALLOWED" || payload.outcome === "EXACT_INSTALLED")) {
      next.decision = "resolved"
      approvalFeedback = payload.outcome === "RETRY_ALLOWED"
        ? "Recovery reviewed. No download started. A new installation requires fresh checks and approval."
        : "Recovery reviewed. The exact artifact is already installed. Activation remains separate."
      if (acceptancePreview) {
        var records = ({}); for (var key in acquisitionByCatalog) records[key] = acquisitionByCatalog[key]
        var record = JSON.parse(JSON.stringify(records[next.catalogId])); record.recovery_resolution = payload
        records[next.catalogId] = record; acquisitionByCatalog = records
      } else { refreshAcquisitions(); refreshCatalog() }
    } else if (payload.error === "stale_recovery_assessment") approvalFeedback = "The assessment changed. Check again and review the new facts before resolving."
    else if (payload.error === "recovery_already_resolved") { approvalFeedback = "Recovery was already reviewed. Close this review and check the model row."; refreshAcquisitions(); refreshCatalog() }
    else if (status === 0) approvalFeedback = "Resolution result unavailable. Check again before taking any further action; do not retry automatically."
    else approvalFeedback = "Recovery resolution blocked" + (typeof payload.error === "string" ? ": " + payload.error.replace(/_/g, " ") : ". Check again.")
    pendingApproval = next
    applyStreamState("idle", "")
  }
  function acquisitionBlobProgress(modelData) {
    var state = acquisitionFor(modelData.catalog_id)
    if (!state || state.state !== "DOWNLOADING") return { visible: false, determinate: false }
    var progress = state.progress || ({})
    var completed = progress.current_blob_completed_bytes
    var total = progress.current_blob_total_bytes
    var index = progress.current_blob_index
    var known = typeof completed === "number" && typeof total === "number"
                && isFinite(completed) && isFinite(total)
                && completed >= 0 && total > 0 && completed <= total
    if (!known) return {
      visible: true,
      determinate: false,
      label: "DOWNLOAD PART" + (typeof index === "number" ? " " + index : "") + " // PROGRESS UNAVAILABLE",
      detail: "Downloading continues; this blob has no meaningful total yet."
    }
    var fraction = completed / total
    return {
      visible: true,
      determinate: true,
      fraction: fraction,
      label: "DOWNLOAD PART " + (typeof index === "number" ? index : "") + " // " + Math.floor(fraction * 100) + "%",
      detail: bytesLabel(completed) + " / " + bytesLabel(total) + " · may count parts downloaded earlier"
    }
  }
  function refreshAcquisitions() {
    if (acceptancePreview) return
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      if (xhr.status !== 200) { acquisitionStatus = "ACQUISITION STATUS UNAVAILABLE"; return }
      try {
        var payload = JSON.parse(xhr.responseText), mapped = ({}), histories = ({})
        ;(payload.acquisitions || []).forEach(function(item) {
          var descriptor = item && item.preflight && item.preflight.descriptor
          if (descriptor && typeof descriptor.catalog_id === "string") {
            if (!mapped[descriptor.catalog_id]) mapped[descriptor.catalog_id] = item
            if (!histories[descriptor.catalog_id]) histories[descriptor.catalog_id] = []
            histories[descriptor.catalog_id].push(item)
          }
        })
        acquisitionByCatalog = mapped
        acquisitionHistoryByCatalog = histories
        acquisitionStatus = ""
        if (trackedAcquisitionId !== "") refreshTrackedAcquisition()
      } catch (error) { acquisitionStatus = "ACQUISITION STATUS UNAVAILABLE" }
    }
    root.openNova(xhr, "GET", "/acquisitions")
    xhr.timeout = 2000
    xhr.send()
  }
  // Run 6 (2026-10-10): "INSTALLED AND CHECKED // … // NOT IN USE YET" stayed at the top of
  // MODELS after the model was loaded and even after it was deleted. A finished install's
  // line only lasts until the next load or delete.
  readonly property string installCheckingFeedback: "Approval accepted. Installation status is being checked."
  function clearFinishedInstallStatus() {
    if (acquisitionStatus.indexOf("INSTALLED") === 0) acquisitionStatus = ""
    if (approvalFeedback.indexOf("Download finished") === 0) approvalFeedback = ""
  }
  function acquisitionLifecycleStatus(item) {
    if (!item) return "ACQUISITION STATUS UNKNOWN"
    var state = item.state || "UNKNOWN"
    var descriptor = item.preflight && item.preflight.descriptor
    var target = (descriptor && descriptor.catalog_id ? catalogDisplayName(descriptor.catalog_id) : String((descriptor && descriptor.artifact_id) || "MODEL")).toUpperCase()
    if (item.preflight && item.preflight.intent === "update") {
      if (state === "INSTALLED") return item.verification === "matched" ? "UPDATED AND CHECKED // " + target : "UPDATED // " + target + " // CHECK NOT CONFIRMED"
      if (state === "FAILED") return "UPDATE FAILED // " + target
      if (state === "RECOVERY_REQUIRED") return "UPDATE INTERRUPTED // " + target + " // RECOVERY REVIEW REQUIRED"
      if (state === "REJECTED") return "UPDATE DECLINED // " + target
      if (state === "EXPIRED") return "UPDATE APPROVAL EXPIRED // " + target
      if (state === "VERIFYING") return "CHECKING THE UPDATE // " + target
      if (state === "APPROVED") return "UPDATE APPROVED // " + target + " // STARTING"
      return "UPDATING // " + target + " // " + state
    }
    if (state === "INSTALLED") return item.verification === "matched"
      ? "INSTALLED AND CHECKED // " + target + " // NOT IN USE YET"
      : "INSTALLED // " + target + " // CHECK NOT CONFIRMED // NOT IN USE YET"
    if (state === "FAILED") return "INSTALLATION FAILED // " + target
    if (state === "RECOVERY_REQUIRED") return "INSTALLATION INTERRUPTED // " + target + " // RECOVERY REVIEW REQUIRED"
    if (state === "CANCELLED") return "INSTALLATION CANCELLED // " + target
    if (state === "REJECTED") return "INSTALLATION DECLINED // " + target
    if (state === "EXPIRED") return "INSTALL APPROVAL EXPIRED // " + target
    if (state === "VERIFYING") return "VERIFYING INSTALLATION // " + target
    if (state === "APPROVED") return "INSTALL APPROVED // " + target + " // STARTING"
    return "INSTALLING // " + target + " // " + state
  }
  function acquisitionTerminalFeedback(item) {
    if (!item) return "Installation status is unavailable."
    if (item.preflight && item.preflight.intent === "update") {
      if (item.state === "INSTALLED") return item.verification === "matched"
        ? "Update finished and checked. The newer build is used from the model's next load."
        : "Update finished, but NOVA couldn't confirm its check."
      if (item.state === "REJECTED" || item.state === "EXPIRED") return "Update not started. Your current build is unchanged."
      return "The update didn't finish. Check the model's card before trying again."
    }
    if (item.state === "INSTALLED") return item.verification === "matched"
      ? "Download finished and checked. Choose LOAD AND USE to start it."
      : "Download finished, but NOVA couldn't confirm its check. Nothing was started."
    if (item.state === "FAILED") return "Installation failed. Nothing was started."
    if (item.state === "RECOVERY_REQUIRED") return "Installation was interrupted and needs a review before another try. Nothing was started."
    if (item.state === "CANCELLED") return "Installation was cancelled. Nothing was started."
    if (item.state === "REJECTED") return "Installation was declined. No download was started by this proposal."
    if (item.state === "EXPIRED") return "Installation approval expired. No download was started by this proposal."
    return ""
  }
  function applyTrackedAcquisition(item) {
    if (!item) return
    var descriptor = item.preflight && item.preflight.descriptor
    if (descriptor && descriptor.catalog_id) {
      var next = ({}); for (var key in acquisitionByCatalog) next[key] = acquisitionByCatalog[key]
      next[descriptor.catalog_id] = item; acquisitionByCatalog = next
    }
    var state = item.state || "UNKNOWN"
    acquisitionStatus = acquisitionLifecycleStatus(item)
    if (["APPROVED", "STARTING", "DOWNLOADING", "VERIFYING", "CANCEL_REQUESTED"].indexOf(state) >= 0) {
      // Run 6: the first real status replaces "being checked"; the core and the row show progress.
      if (approvalFeedback === installCheckingFeedback) approvalFeedback = ""
      onboardingState = "ACQUISITION_IN_PROGRESS"
      onboardingActions = []
      onboardingCodes = ["ACQUISITION_" + state]
      onboardingTarget = descriptor && descriptor.catalog_id ? descriptor.catalog_id : ""
    }
    if (["INSTALLED", "FAILED", "RECOVERY_REQUIRED", "CANCELLED", "REJECTED", "EXPIRED"].indexOf(state) >= 0) {
      approvalFeedback = acquisitionTerminalFeedback(item)
      acquisitionTimer.stop(); trackedAcquisitionId = ""
      if (!acceptancePreview) { refreshModels(); refreshCatalog(); refreshOnboarding(); refreshModelUpdates(false) }
    }
  }
  function refreshTrackedAcquisition() {
    if (acceptancePreview) return
    if (trackedAcquisitionId === "") return
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      if (xhr.status !== 200) { acquisitionStatus = "ACQUISITION STATUS UNAVAILABLE"; acquisitionTimer.stop(); return }
      try {
        applyTrackedAcquisition(JSON.parse(xhr.responseText))
      } catch (error) { acquisitionStatus = "ACQUISITION STATUS UNAVAILABLE"; acquisitionTimer.stop() }
    }
    root.openNova(xhr, "GET", "/acquisitions/" + encodeURIComponent(trackedAcquisitionId))
    xhr.timeout = 2000
    xhr.send()
  }
  // Delete an installed model from this computer (QWEN-11 contract): a red trash icon,
  // a proposal from novad, the normal approval card, and a re-check at approval.
  property bool removalBusy: false
  property string removalStatus: ""
  property string removalStatusModel: ""
  property bool removalFailed: false
  property string unloadNoteModel: ""
  // Delete must read as delete in every theme: the theme's urgent colour when it is
  // actually red, otherwise a fixed soft red (some themes map "red"/urgent to green).
  readonly property color destructiveColor: {
    var c = Commons.Color.urgent, h = c.hslHue, sat = c.hslSaturation
    return h >= 0 && (h <= 20 / 360 || h >= 340 / 360) && sat > 0.3 ? c : "#e5534b"
  }
  function removalBlockerText(code) {
    return ({SELECTED_MODEL: "it's NOVA's selected model (choose another first)",
             LOADED_IN_MEMORY: "it's loaded in " + memoryWord + " (it unloads 10 minutes after its last use)",
             CLI_SESSION_RUNNING: "a coding CLI is using it (close it first)",
             CLI_STATE_UNKNOWN: "NOVA couldn't check whether a coding CLI is using it",
             NOT_INSTALLED: "it isn't installed any more",
             DIGEST_CHANGED: "it changed since you asked (try again)"})[code] || String(code).toLowerCase().replace(/_/g, " ")
  }
  // 2026-10-12: a model built on another (same weights, other settings) shares its files;
  // deleting it frees only what no other model uses. Say so, and name who keeps the files.
  function sharedNames(names) {
    var list = (names || []).map(function(n) { return modelPlainName(n) })
    return list.length <= 2 ? list.join(" and ") : list.slice(0, 2).join(", ") + " and " + (list.length - 2) + " more"
  }
  function removalFreesText(freesBytes, sizeBytes, sharedWith) {
    if (typeof freesBytes === "number")
      return "frees " + transferLabel(freesBytes) + ((sharedWith || []).length ? " (its main files stay: " + sharedNames(sharedWith) + " use" + (sharedWith.length === 1 ? "s" : "") + " them too)" : "")
    return typeof sizeBytes === "number" ? "frees up to " + bytesLabel(sizeBytes) + " (less if another model shares its files)" : ""
  }
  function removalTooltip(modelData) {
    var size = modelData && modelData.metadata && modelData.metadata.size_bytes
    var frees = removalFreesText(modelData.delete_frees_bytes, size, modelData.shares_files_with)
    return "Delete " + modelData.id + " from this computer" + (frees ? " · " + frees : "") + " · asks for approval first"
  }
  function removalApprovalLine(item) {
    var frees = removalFreesText(item.freesBytes, item.size, item.sharedWith)
    return (frees ? frees.charAt(0).toUpperCase() + frees.slice(1) + " · " : "")
      + ((item.alsoDeletes || []).length ? "its coding CLI goes too (" + item.alsoDeletes.join(", ") + ") · " : "") + "you can download it again later"
  }
  function setRemovalStatus(model, text, failed) { removalStatusModel = model; removalStatus = text; removalFailed = failed; unloadNoteModel = "" }
  // Delete/unload results go into the chat (a deleted model's row disappears);
  // blocked cases also stay on the row, where the user acts on them.
  function reportModelResult(model, text, blocked, keepOnRow) {
    setRemovalStatus(model, blocked || keepOnRow ? text : "", blocked)
    addSystemLine(blocked ? model + ": " + text : text)
  }
  function proposeModelRemoval(modelData) {
    if (!modelData || typeof modelData.id !== "string" || removalBusy || pendingApproval !== null || acceptancePreview) return
    var model = modelData.id
    removalBusy = true
    setRemovalStatus(model, "Checking whether " + model + " can be deleted…", false)
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      root.removalBusy = false
      var payload = null
      try { payload = JSON.parse(xhr.responseText) } catch (error) { payload = null }
      if (xhr.status === 201 && payload && payload.status === "proposed" && typeof payload.id === "string") {
        root.setRemovalStatus(model, "", false)
        root.beginApproval({ id: payload.id, action: "remove_installed_model", removal: true, model: model,
          expiresAt: payload.expires_at, size: payload.size_bytes,
          freesBytes: payload.frees_bytes, sharedWith: Array.isArray(payload.shared_with) ? payload.shared_with : [],
          alsoDeletes: Array.isArray(payload.also_deletes) ? payload.also_deletes : [],
          summary: "DELETE " + model.toUpperCase() + " FROM THIS COMPUTER",
          // 0.16: the model's coding CLI build (same files, its own context) goes with it.
          impact: Array.isArray(payload.also_deletes) && payload.also_deletes.length
            ? "Ollama deletes this model, its coding CLI build and the files only they use; files other models use stay. Its OPEN CLI goes too; your other settings stay. You can download it again later."
            : "Ollama deletes this model and the files only it uses; files other models use stay. NOVA's settings stay. You can download it again later." })
      } else if (payload && payload.status === "blocked" && Array.isArray(payload.blockers)) {
        root.reportModelResult(model, "Not deleted: " + payload.blockers.map(root.removalBlockerText).join("; ") + ".", true)
      } else root.reportModelResult(model, "NOVA's background service didn't answer. Nothing was deleted.", false)
    }
    try {
      root.openNova(xhr, "POST", "/models/installed/" + encodeURIComponent(model) + "/remove")
      xhr.timeout = 15000
      xhr.send()
    } catch (error) { removalBusy = false; reportModelResult(model, "NOVA's background service didn't answer. Nothing was deleted.", false) }
  }
  // Run 7: CPU-only machines report 0 graphics bytes; show the RAM the model holds instead.
  function residentSizeLabel(modelData) {
    if (!modelData) return ""
    if (modelData.vram_bytes > 0) return bytesLabel(modelData.vram_bytes)
    return modelData.memory_bytes > 0 ? bytesLabel(modelData.memory_bytes) + " in RAM" : ""
  }
  // QWEN-12: free GPU memory now (reversible: the model loads again when next used).
  function unloadTooltip(modelData) {
    var bytes = modelData && (modelData.vram_bytes > 0 ? modelData.vram_bytes : modelData.memory_bytes)
    return "Free " + (typeof bytes === "number" && bytes > 0 ? bytesLabel(bytes) + " of " : "") + memoryWord + " now · " + modelData.id + " loads again when it's next used"
  }
  function unloadModel(modelData) {
    if (!modelData || typeof modelData.id !== "string" || removalBusy || acceptancePreview) return
    var model = modelData.id
    removalBusy = true
    setRemovalStatus(model, "Freeing " + memoryWord + "…", false)
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      root.removalBusy = false
      var payload = null
      try { payload = JSON.parse(xhr.responseText) } catch (error) { payload = null }
      var status = payload && typeof payload.status === "string" ? payload.status : ""
      if (status === "unloaded")
        root.reportModelResult(model, (typeof payload.freed_bytes === "number" && payload.freed_bytes > 0 ? "Freed " + root.bytesLabel(payload.freed_bytes) + " of " + root.memoryWord + ". "
          : root.memoryWord.charAt(0).toUpperCase() + root.memoryWord.slice(1) + " freed. ") + model + " loads again when it's next used.", false, true)
      else if (status === "not_loaded") root.reportModelResult(model, model + " was not in " + root.memoryWord + ".", false, true)
      else if (status === "busy") root.reportModelResult(model, "NOVA is busy right now; try again when it's idle.", true)
      else if (status === "blocked" && Array.isArray(payload.blockers))
        root.reportModelResult(model, "Not unloaded: " + payload.blockers.map(root.removalBlockerText).join("; ") + ".", true)
      else root.reportModelResult(model, "Ollama couldn't unload " + model + ".", false, true)
      if (status === "unloaded" || status === "not_loaded") root.unloadNoteModel = model
      root.refreshModels()
    }
    try {
      root.openNova(xhr, "POST", "/models/installed/" + encodeURIComponent(model) + "/unload")
      xhr.timeout = 45000
      xhr.send()
    } catch (error) { removalBusy = false; reportModelResult(model, "NOVA's background service didn't answer.", false, true) }
  }
  function finishRemovalDecision(item, decision, status, payload) {
    clearPendingApproval()
    approvalFeedback = ""
    var model = item.model
    if (decision === "deny") reportModelResult(model, "Kept " + model + ". Nothing was deleted.", false)
    else if (status === 200 && payload.status === "deleted") {
      clearFinishedInstallStatus()
      var shared = Array.isArray(payload.shared_with) ? payload.shared_with : []
      reportModelResult(model, "Deleted " + model + (Array.isArray(payload.also_deleted) && payload.also_deleted.length ? " and its coding CLI" : "")
        + (typeof payload.freed_bytes === "number" ? " · freed " + transferLabel(payload.freed_bytes) : "")
        + (shared.length ? " · its main files stay because " + sharedNames(shared) + " use" + (shared.length === 1 ? "s" : "") + " them" : "") + ".", false)
    } else if (payload && payload.status === "blocked" && Array.isArray(payload.blockers))
      reportModelResult(model, "Not deleted: " + payload.blockers.map(removalBlockerText).join("; ") + ".", true)
    else if (status === 410 || payload.error === "expired_approval") reportModelResult(model, "The approval expired. Nothing was deleted.", false)
    else if (payload && payload.error === "still_installed") reportModelResult(model, "Ollama reported success but " + model + " is still installed.", false)
    else reportModelResult(model, "Ollama couldn't delete " + model + ". Nothing was removed.", false)
    refreshModels()
    restoreChatInputFocus()
  }
  function proposeAcquisition(modelData) {
    if (!modelData || acquisitionState(modelData) !== "AVAILABLE TO INSTALL") return
    if (acceptancePreview) { performAcceptancePreviewAction(); return }
    proposeCatalogAcquisition(modelData.catalog_id, modelData.display_name || catalogRuntimeId(modelData))
  }
  function proposeCatalogAcquisition(catalogId, displayName) {
    if (acceptancePreview) { performAcceptancePreviewAction(); return }
    if (typeof catalogId !== "string" || catalogId === "" || pendingApproval !== null
        || chatBusy || modelTransitionActive) return
    acquisitionStatus = "PREPARING INSTALL // " + String(displayName || catalogId).toUpperCase()
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      try {
        var payload = JSON.parse(xhr.responseText)
        if (xhr.status !== 201 || !payload.id || !payload.preflight || !payload.preflight.descriptor) {
          acquisitionStatus = payload.status === "NO_OP_ALREADY_INSTALLED" ? "INSTALLED"
            : payload.error === "model_gone_upstream" ? "INSTALL NOT AVAILABLE · OLLAMA NO LONGER OFFERS THIS MODEL"
            : payload.error === "model_changed_too_much_upstream" ? "INSTALL NOT AVAILABLE · OLLAMA CHANGED THIS MODEL; WAIT FOR A NOVA UPDATE"
            : "INSTALL NOT AVAILABLE"
          refreshCatalog(); refreshOnboarding(); return
        }
        var detail = payload.preflight
        // catalog_repins: Ollama re-published the tag; novad checked and pinned the new version.
        var republished = payload.upstream && (payload.upstream.status === "repinned"
          || payload.upstream.repinned === true)
        beginApproval({ id: payload.id, action: "acquire_catalog_model", acquisition: true,
          expiresAt: payload.expires_at,
          summary: "INSTALL " + String(displayName || catalogId),
          provider: detail.descriptor.provider || "UNKNOWN", size: detail.descriptor.expected_artifact_size,
          requiredSpace: detail.disk && detail.disk.total_required, availableSpace: detail.disk && detail.disk.free_space,
          fit: detail.fit_state || "UNKNOWN", trust: detail.trust_basis || "UNVERIFIED",
          provenance: provenancePlain(detail.descriptor.provenance_summary),
          impact: (republished ? "Ollama updated this model after NOVA's list was made; NOVA checked the new version's size and type. " : "")
            + "Ollama downloads the model and NOVA checks that it is complete. Nothing else on this computer changes." })
        if (payload.upstream && payload.upstream.status === "repinned") refreshCatalog()
        onboardingState = "ACQUISITION_PROPOSED"
        onboardingActions = ["DECIDE_INSTALL_APPROVAL"]
        acquisitionStatus = "AWAITING INSTALL APPROVAL"
      } catch (error) { acquisitionStatus = "INSTALL NOT AVAILABLE" }
    }
    root.openNova(xhr, "POST", "/acquisition/models/" + encodeURIComponent(catalogId) + "/propose")
    xhr.timeout = 45000  // novad first checks with Ollama whether the tag was re-published
    xhr.send()
  }
  // Every request to NOVA goes over its private socket (BarWidget.novaRequest,
  // local_socket.py): only this user's processes can reach it or answer on it, web pages
  // can't reach it at all, and no key is sent (marketplace reviews 2026-10-11, 2026-10-14).
  function novaRequest() {
    if (hostWidget && hostWidget.novaClient) return hostWidget.novaClient.request()
    // No bar (previews): the request fails like an offline NOVA.
    var r = { readyState: 0, status: 0, responseText: "", timeout: 0, onreadystatechange: null, onload: null, onerror: null, ontimeout: null }
    r.open = function() { r.readyState = 1 }
    r.setRequestHeader = function() {}
    r.abort = function() {}
    r.send = function() { Qt.callLater(function() { r.readyState = 4; if (r.onreadystatechange) r.onreadystatechange(); if (r.onerror) r.onerror() }) }
    return r
  }
  function openNova(xhr, method, path) { xhr.open(method, path) }
  // 0.14.0 model updates (mock previews/model-updates, Konstantin 2026-10-10): novad checks
  // installed models against Ollama's library once a week or on REFRESH. NOVA never
  // downloads on its own: UPDATE opens the normal approval card for the newer build.
  property var modelUpdates: ({})          // installed model id -> novad's result
  property int modelUpdatesAvailable: 0
  property bool modelUpdatesChecking: false
  property string modelUpdateBusy: ""      // catalog id being proposed, skipped or shown again
  function applyModelUpdates(payload) {
    modelUpdates = payload && payload.models ? payload.models : ({})
    modelUpdatesAvailable = payload && typeof payload.available === "number" ? payload.available : 0
    modelUpdatesChecking = !!(payload && payload.checking)
    if (modelUpdatesChecking) modelUpdatesTimer.restart()
  }
  function refreshModelUpdates(force) {
    if (acceptancePreview) return
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      if (xhr.status !== 200 && xhr.status !== 202) { root.modelUpdatesChecking = false; return }
      try { root.applyModelUpdates(JSON.parse(xhr.responseText)) } catch (error) { root.modelUpdatesChecking = false }
    }
    root.openNova(xhr, force ? "POST" : "GET", (force ? "/models/updates/check" : "/models/updates"))
    xhr.timeout = 5000
    xhr.send()
  }
  Timer { id: modelUpdatesTimer; interval: 3000; onTriggered: root.refreshModelUpdates(false) }
  function modelUpdateFor(modelData) {
    return modelData && typeof modelData.id === "string" && modelUpdates[modelData.id] ? modelUpdates[modelData.id] : null
  }
  function modelUpdateOffered(modelData) {
    var update = modelUpdateFor(modelData)
    return !!(update && update.status === "available" && !update.skipped)
  }
  // Sizes of a few KB must not read as "0.0 GiB".
  function transferLabel(value) {
    if (typeof value !== "number" || !isFinite(value)) return "unknown"
    if (value < 1048576) return Math.max(1, Math.round(value / 1024)) + " KB"
    if (value < 1073741824) return Math.round(value / 1048576) + " MB"
    return bytesLabel(value)
  }
  function modelUpdateWhat(update) {
    return update && update.kind === "settings"
      ? "Ollama has a newer build: the chat template and settings changed, the model weights are the same."
      : "Ollama has a newer build with new model weights (the same model, retrained or requantized)."
  }
  function modelUpdateFacts(update) {
    if (!update) return ""
    var download = (update.download_exact ? "about " : "up to ") + transferLabel(update.download_bytes)
    var sizes = typeof update.size_before === "number" && typeof update.size_after === "number"
      && Math.abs(update.size_after - update.size_before) >= 52428800
      ? " · SIZE " + bytesLabel(update.size_before) + " → " + bytesLabel(update.size_after) : ""
    return "DOWNLOAD " + download + sizes
  }
  function modelUpdateQuietLine(modelData) {
    var update = modelUpdateFor(modelData)
    if (!update) return ""
    if (update.status === "changed_too_much") return "Ollama changed this model a lot (a different size or format), so NOVA doesn't offer it as an update. Your copy keeps working; a NOVA update will list the new one."
    if (update.status === "gone") return "Ollama no longer offers this model. Your copy keeps working."
    if (update.status === "available" && update.skipped) return "Newer build skipped."
    return ""
  }
  function modelUpdateInProgress(modelData) {
    // The repinned catalog row no longer matches the older build on disk, so use the update's own id.
    var update = modelUpdateFor(modelData)
    var item = update && update.catalog_id ? acquisitionFor(update.catalog_id) : null
    return !!(item && item.preflight && item.preflight.intent === "update"
              && ["APPROVED", "STARTING", "DOWNLOADING", "VERIFYING"].indexOf(item.state) >= 0)
  }
  function setModelUpdateSkipped(modelData, skip) {
    var update = modelUpdateFor(modelData)
    if (!update || !update.catalog_id || modelUpdateBusy !== "") return
    modelUpdateBusy = update.catalog_id
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      root.modelUpdateBusy = ""
      if (xhr.status === 200) { try { root.applyModelUpdates(JSON.parse(xhr.responseText)) } catch (error) { } }
    }
    root.openNova(xhr, "POST", "/models/updates/" + encodeURIComponent(update.catalog_id) + (skip ? "/skip" : "/unskip"))
    xhr.timeout = 5000
    xhr.send()
  }
  // Run 8: a bare "UPDATE NOT AVAILABLE" hid the reason; name it in plain words.
  function modelUpdateRefusal(payload) {
    var words = { acquisition_conflict: "ANOTHER DOWNLOAD OF THIS MODEL IS RUNNING", INSUFFICIENT_STORAGE: "NOT ENOUGH FREE DISK SPACE",
                  PROVIDER_UNAVAILABLE: "OLLAMA IS NOT RUNNING", PROVIDER_NOT_READY: "OLLAMA IS NOT READY", PROVIDER_TOO_OLD: "OLLAMA IS TOO OLD FOR THIS BUILD",
                  FIT_POOR: "THE NEWER BUILD WOULD NOT FIT", FIT_UNSUPPORTED: "THE NEWER BUILD WOULD NOT FIT", TIGHT_OPT_IN_REQUIRED: "THE NEWER BUILD FITS ONLY TIGHTLY",
                  ACQUISITION_CONFLICT: "ANOTHER DOWNLOAD IS RUNNING", NOTHING_TO_UPDATE: "NOTHING TO UPDATE" }
    var codes = payload && Array.isArray(payload.blockers) && payload.blockers.length ? payload.blockers
      : (payload && typeof payload.error === "string" ? [payload.error] : [])
    var known = codes.map(function(code) { return words[code] }).filter(function(text) { return !!text })
    return known.length ? known.join(" · ") : (codes.length ? String(codes[0]).replace(/_/g, " ").toUpperCase() : "NOVA'S SERVICE DIDN'T ANSWER")
  }
  function proposeModelUpdate(modelData) {
    var update = modelUpdateFor(modelData)
    if (!update || !update.catalog_id || pendingApproval !== null || chatBusy || modelTransitionActive
        || trackedAcquisitionId !== "" || modelUpdateBusy !== "") return
    var name = modelPlainName(modelData.id)
    modelUpdateBusy = update.catalog_id
    acquisitionStatus = "PREPARING UPDATE // " + name.toUpperCase()
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      root.modelUpdateBusy = ""
      try {
        var payload = JSON.parse(xhr.responseText)
        if (xhr.status !== 201 || !payload.id || !payload.preflight || !payload.preflight.descriptor) {
          acquisitionStatus = payload.status === "NO_OP_ALREADY_INSTALLED" ? "ALREADY UP TO DATE // " + name.toUpperCase()
            : payload.error === "model_gone_upstream" ? "UPDATE NOT AVAILABLE · OLLAMA NO LONGER OFFERS THIS MODEL"
            : payload.error === "model_changed_too_much_upstream" ? "UPDATE NOT AVAILABLE · OLLAMA CHANGED THIS MODEL; WAIT FOR A NOVA UPDATE"
            : "UPDATE NOT AVAILABLE · " + modelUpdateRefusal(payload)
          refreshCatalog(); refreshModelUpdates(true); return
        }
        var detail = payload.preflight
        beginApproval({ id: payload.id, action: "acquire_catalog_model", acquisition: true, update: true,
          expiresAt: payload.expires_at,
          summary: "Update " + name + " to Ollama's newer build",
          whatChanged: update.kind === "settings" ? "chat template and settings (same weights)" : "new model weights",
          download: (update.download_exact ? "about " : "up to ") + transferLabel(update.download_bytes),
          provider: detail.descriptor.provider || "UNKNOWN", size: detail.descriptor.expected_artifact_size,
          requiredSpace: detail.disk && detail.disk.total_required, availableSpace: detail.disk && detail.disk.free_space,
          fit: detail.fit_state || "UNKNOWN", trust: detail.trust_basis || "UNVERIFIED",
          provenance: provenancePlain(detail.descriptor.provenance_summary),
          impact: "Ollama downloads the newer build and replaces the one you have; NOVA checks that it is exactly the build shown here. "
            + "Until it finishes, the current build keeps working. Your chats are not affected." })
        if (payload.upstream && payload.upstream.status === "repinned") refreshCatalog()
        acquisitionStatus = "AWAITING UPDATE APPROVAL // " + name.toUpperCase()
      } catch (error) { acquisitionStatus = "UPDATE NOT AVAILABLE" }
    }
    root.openNova(xhr, "POST", "/acquisition/models/" + encodeURIComponent(update.catalog_id) + "/propose-update")
    xhr.timeout = 45000  // novad re-reads Ollama's library before proposing
    xhr.send()
  }
  // REC-01: attributed, low-confidence suggestions for not-yet-installed models.
  // catalog_id -> {uses: [..], attributions: [{publisher, summary}], storage}
  property var modelSuggestions: ({})
  // Friendly names in list order; a model installed since the suggestions loaded drops out.
  readonly property string suggestionSummary: {
    var merged = modelSuggestions, bigFirst = gpuAvailable ? -1 : 1
    var ids = Object.keys(merged).filter(function(id) { var item = modelForCatalog(id); return !(item && catalogIsInstalled(item)) })
      .sort(function(a, b) { return bigFirst * (merged[a].storage - merged[b].storage) })
    return ids.length
      ? "SUGGESTED FOR THIS HARDWARE · low confidence · from published guides, " + (gpuAvailable ? "biggest" : "smallest") + " first: " + ids.map(catalogDisplayName).join(", ")
      : ""
  }
  // Download catalog order: suggested, then the rest, both biggest first with a graphics card
  // (Konstantin 2026-10-23: a 1B model first on a 32 GB card made no sense) and smallest first
  // without one (smaller answers faster on a CPU); then models the preflight blocked for
  // another reason (a tight fit), catalog order; then models too big for this computer,
  // smallest first. Run 6 (2026-10-10): too-big ones were mixed into the blocked group.
  readonly property var orderedCatalogModels: {
    var suggestions = modelSuggestions, preflight = preflightByCatalog
    function rank(item) {
      if (suggestions[item.catalog_id]) return 0
      var p = preflight[item.catalog_id]
      if (!(p && p.status === "BLOCKED")) return 1
      return catalogTooBig(item) ? 3 : 2
    }
    return catalogModels.map(function(item, index) { return { item: item, index: index, rank: rank(item) } })
      .sort(function(a, b) {
        if (a.rank !== b.rank) return a.rank - b.rank
        var bigFirst = gpuAvailable ? -1 : 1
        if (a.rank === 0) { var d = suggestions[a.item.catalog_id].storage - suggestions[b.item.catalog_id].storage; if (d) return bigFirst * d }
        if (a.rank === 1) {
          var ra = catalogSizeBytes(a.item), rb = catalogSizeBytes(b.item)
          if (gpuAvailable && ra !== null && rb !== null && ra !== rb) return rb - ra
        }
        if (a.rank === 3) {
          var sa = catalogSizeBytes(a.item), sb = catalogSizeBytes(b.item)
          var e = (sa === null ? Number.MAX_VALUE : sa) - (sb === null ? Number.MAX_VALUE : sb); if (e) return e
        }
        return a.index - b.index
      }).map(function(row) { return row.item })
  }
  function suggestionFor(catalogId) { return modelSuggestions[catalogId] || null }
  function suggestionLine(catalogId) {
    var s = suggestionFor(catalogId)
    if (!s) return ""
    var sources = s.attributions.map(function(a) { return a.publisher + ": " + a.summary }).join(" · ")
    return "Suggested for " + s.uses.join(" and ") + " · " + sources + " · low confidence: from a published guide, not tested by NOVA"
  }
  function applySuggestions(profileLabel, payload, merged) {
    if (!payload || !Array.isArray(payload.candidates)) return
    payload.candidates.forEach(function(row) {
      if (!row || row.selectable !== true || row.installed !== false || typeof row.catalog_id !== "string") return
      var attributions = (row.evidence || []).filter(function(e) { return e && e.policy_usable === true && e.attribution })
        .map(function(e) { return { publisher: String(e.attribution.publisher), summary: String(e.attribution.summary) } })
      if (!attributions.length) return
      var entry = merged[row.catalog_id] || { uses: [], attributions: [], storage: row.inputs && row.inputs.storage_bytes || 0 }
      if (entry.uses.indexOf(profileLabel) < 0) entry.uses.push(profileLabel)
      // Fixed order regardless of which request answered first.
      entry.uses.sort(function(a, b) { return ["everyday use", "coding"].indexOf(a) - ["everyday use", "coding"].indexOf(b) })
      attributions.forEach(function(a) {
        if (!entry.attributions.some(function(b) { return b.publisher === a.publisher && b.summary === a.summary })) entry.attributions.push(a)
      })
      merged[row.catalog_id] = entry
    })
  }
  function refreshSuggestions() {
    if (acceptancePreview) return
    var merged = ({}), pending = 2
    function done() {
      if (--pending > 0) return
      // Listed smallest first: never a quality ranking, but smaller models run faster, which matters most without a GPU.
      root.modelSuggestions = merged
    }
    ;[["everyday use", "general"], ["coding", "coding"]].forEach(function(pair) {
      var xhr = root.novaRequest()
      xhr.onreadystatechange = function() {
        if (xhr.readyState !== XMLHttpRequest.DONE) return
        try { if (xhr.status === 200) root.applySuggestions(pair[0], JSON.parse(xhr.responseText), merged) } catch (error) {}
        done()
      }
      try {
        root.openNova(xhr, "GET", "/recommendations/models?profile=" + pair[1] + "&scope=known")
        xhr.timeout = 20000
        xhr.send()
      } catch (error) { done() }
    })
  }
  // Explain every model's blockers instead of blaming the first one found.
  function installBlockSummary() {
    var per = ({})
    onboardingLimitations.forEach(function(value) {
      var match = String(value).match(/^PREFLIGHT:([^:]+):(.+)$/)
      if (match) (per[match[1]] = per[match[1]] || []).push(match[2])
    })
    var ids = Object.keys(per), total = ids.length
    if (!total) return ""
    function count(code) { return ids.filter(function(id) { return per[id].indexOf(code) >= 0 }).length }
    function some(n) { return n === total ? "all " + total : n + " of " + total }
    var parts = []
    if (count("PROVIDER_READINESS_UNKNOWN")) parts.push("NOVA can't yet confirm that Ollama can save downloaded models")
    var memory = count("FIT_POOR")
    if (memory) parts.push(some(memory) + " are too large for this computer's memory")
    var tight = ids.filter(function(id) { return per[id].length === 1 && per[id][0] === "TIGHT_OPT_IN_REQUIRED" }).length
    if (tight) parts.push(tight + " would fit only tightly")
    var tooOld = count("PROVIDER_TOO_OLD")
    if (tooOld) parts.push(some(tooOld) + " need a newer Ollama")
    var disk = count("INSUFFICIENT_STORAGE")
    if (disk) parts.push(some(disk) + " need more free disk space")
    var unresolved = count("FIT_UNRESOLVED")
    if (unresolved) parts.push("memory fit couldn't be estimated for " + some(unresolved))
    var untrusted = count("SOURCE_UNVERIFIED")
    if (untrusted) parts.push(some(untrusted) + " come from an untrusted source")
    if (!parts.length) return ""
    return "No catalog model can be installed right now: " + parts.join("; ") + "."
  }
  function refreshRecommendations() {
    if (acceptancePreview) return
    refreshSuggestions()
    recommendationSummary = "RECOMMEND // CHECKING"
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      if (xhr.status !== 200) { recommendationSummary = "RECOMMEND // UNAVAILABLE"; return }
      try {
        var payload = JSON.parse(xhr.responseText)
        if (payload.status === "NO_DEFENSIBLE_RECOMMENDATION") {
          recommendationSummary = "RECOMMEND // INSUFFICIENT EVIDENCE"
          return
        }
        if (payload.status === "RECOMMENDATION_AVAILABLE" && payload.tiers &&
            payload.tiers.recommended && Array.isArray(payload.tiers.recommended.candidates) &&
            payload.tiers.recommended.candidates.length > 0) {
          var candidate = payload.tiers.recommended.candidates[0]
          recommendationSummary = "RECOMMEND // " + String(candidate.catalog_id || "AVAILABLE").toUpperCase() + " // " + String(payload.confidence || "LOW").toUpperCase()
          return
        }
        recommendationSummary = payload.status === "RECOMMENDATION_UNAVAILABLE" ? "RECOMMEND // UNAVAILABLE" : "RECOMMEND // INSUFFICIENT EVIDENCE"
      } catch (error) { recommendationSummary = "RECOMMEND // UNAVAILABLE" }
    }
    root.openNova(xhr, "GET", "/recommendations/models")
    xhr.timeout = 20000
    xhr.send()
  }
  function bytesLabel(value) { return typeof value === "number" && isFinite(value) ? (value / 1073741824).toFixed(1) + " GiB" : "UNKNOWN" }
  function fitFor(id) { return modelFits[id] || null }
  function browseDownloadsLabel() {
    if (catalogDownloadsExpanded) return "HIDE DOWNLOADS"
    var attention = catalogAttention()
    return attention ? attention.label : "BROWSE DOWNLOADS" + (newCatalogIds.length ? " · " + newCatalogIds.length + " NEW" : "")
  }
  // MODELS v2 (Konstantin 2026-10-23: "hard to understand without reading"): a fit as one
  // word; the sentence stays in DETAILS. Colour only helps (some themes map red to green).
  readonly property color fitsColor: "#9fc17f"
  function fitWord(fit) {
    if (!fit || !fit.rating) return ""
    return ({EXCELLENT: "FITS", GOOD: "FITS", TIGHT: "TIGHT", POOR: "TOO BIG", UNSUPPORTED: "CAN'T CHAT"})[fit.rating] || ""
  }
  function fitTone(fit) { var word = fitWord(fit); return word === "FITS" ? fitsColor : (word === "TIGHT" ? cliTightColor : destructiveColor) }
  // The context NOVA runs it with (a coding CLI build's, which chat follows; else its own
  // num_ctx; else the fit's), then what it is doing.
  // NOVA's fit estimates are for local Ollama models: a same-named model from another
  // provider gets none.
  function installedFit(item) { return item && item.provider === "ollama" ? fitFor(item.id) : null }
  function installedFacts(item) {
    if (!item) return ""
    var profile = openCodeProfile(item), fit = installedFit(item), own = item.metadata && item.metadata.own_context
    var context = profile && profile.context > 0 ? profile.context : (own > 0 ? own : (fit && fit.recommended_context > 0 ? fit.recommended_context : 0))
    var state = modelTransitionActive && transitionTarget === item.id ? "switching to it…"
      : item.available !== true ? "unavailable"
      : item.resident === true ? "in " + memoryWord + " · unloads 10 min after last use"
      : installedModelSelected(item) ? "loads with your first message" : "installed"
    return (context > 0 ? contextLabel(context) + " · " : "") + state
  }
  function codingCliSummary(item, profiles) {
    var openCode = openCodeProfile(item)
    if (openCode) return "OpenCode · " + (openCode.project_place || openCode.project_label) + " · " + contextLabel(openCode.context)
      + " · " + (openCode.care === "edits" ? "asks before commands" : "asks before edits and commands")
    if (!profiles || !profiles.length) return cliState === null ? "NOVA couldn't check" : ""
    var first = profiles[0]
    return first.label + (profiles.length > 1 ? " · " + profiles.length + " projects" : " · " + first.project_label)
  }
  function fitSummary(id) {
    var fit = fitFor(id)
    if (!fit || !fit.rating || fit.rating === "UNKNOWN") return "Hardware fit hasn’t been verified yet."
    if (fit.rating === "EXCELLENT") return "Expected to run comfortably."
    if (fit.rating === "GOOD") return "Expected to run well."
    if (fit.rating === "TIGHT") return "May run with less room for long conversations."
    if (fit.rating === "POOR") return "Likely to need more memory than is comfortable."
    if (fit.rating === "UNSUPPORTED") return "This model cannot be used for NOVA chat."
    return "Hardware fit hasn’t been verified yet."
  }
  // Download rows: installed-model fit when present, else the catalog-based
  // estimate from the download preflight (labelled as an estimate).
  function rowFit(modelData) {
    var installed = fitFor(catalogRuntimeId(modelData))
    if (installed && installed.rating && installed.rating !== "UNKNOWN") return installed
    var preflight = preflightByCatalog[modelData.catalog_id]
    return preflight && preflight.fit_estimate ? preflight.fit_estimate : null
  }
  function providerPlainName(provider) { return provider === "ollama" ? "Ollama" : String(provider || "unknown") }
  function approvalFitShort(state) {
    return ({EXCELLENT: "runs comfortably", GOOD: "runs well", TIGHT: "fits with little room"})[state] || "fit not estimated"
  }
  function approvalFitText(state) {
    return ({EXCELLENT: "Estimated to run comfortably on this computer", GOOD: "Estimated to run well on this computer",
             TIGHT: "Estimated to fit, with little room to spare"})[state] || "Not estimated for this computer"
  }
  function rowFitSummary(modelData) {
    var fit = rowFit(modelData)
    if (!fit || fit.source !== "catalog_metadata_estimate") return fitSummary(catalogRuntimeId(modelData))
    var text = ({EXCELLENT: "Estimated to run comfortably", GOOD: "Estimated to run well",
                 TIGHT: "Estimated to fit with little room", POOR: "Estimated to need more memory than this computer has"})[fit.rating]
    return text ? text + " (from catalog metadata; not yet tested here)." : "Hardware fit hasn’t been verified yet."
  }
  function rowContextSummary(modelData) {
    var fit = rowFit(modelData)
    if (fit && fit.source === "catalog_metadata_estimate" && typeof fit.recommended_context === "number" && fit.recommended_context > 0)
      return "Estimated working context: " + (fit.recommended_context / 1024).toFixed(0) + "K."
    return workingContextSummary(catalogRuntimeId(modelData))
  }
  function workingContextSummary(id) {
    var fit = fitFor(id)
    if (fit && typeof fit.recommended_context === "number" && fit.recommended_context > 0)
      return "Estimated working context: " + (fit.recommended_context / 1024).toFixed(0) + "K."
    return "Working context hasn’t been estimated yet."
  }
  function preflightBlockers(modelData) {
    var preflight = preflightByCatalog[modelData.catalog_id]
    return preflight && Array.isArray(preflight.blockers) ? preflight.blockers : []
  }
  // Too big is the normal case on a small machine: these rows hide behind a toggle.
  function catalogTooBig(modelData) {
    var blockers = preflightBlockers(modelData)
    return blockers.indexOf("FIT_POOR") >= 0 || blockers.indexOf("FIT_UNSUPPORTED") >= 0
  }
  function blockedReason(modelData) {
    var blockers = preflightBlockers(modelData)
    if (catalogTooBig(modelData)) return "Too big for this computer's memory."
    if (blockers.indexOf("PROVIDER_TOO_OLD") >= 0) {
      var versions = (preflightByCatalog[modelData.catalog_id] || {}).provider_version || {}
      return versions.required && versions.installed
        ? "Needs Ollama " + versions.required + " or newer; this computer has " + versions.installed + ". Update the system to update Ollama."
        : "Needs a newer Ollama. Update the system to update Ollama."
    }
    if (blockers.indexOf("TIGHT_OPT_IN_REQUIRED") >= 0) return "Would fit only tightly, so NOVA doesn't offer it."
    if (blockers.indexOf("INSUFFICIENT_STORAGE") >= 0) return "Needs more free disk space."
    if (blockers.indexOf("FIT_UNRESOLVED") >= 0) return "NOVA couldn't estimate whether it fits this computer."
    return "Can't be installed on this computer right now."
  }
  function acquisitionPlainLabel(modelData) {
    var state = acquisitionState(modelData)
    if (acceptanceSimulation) {
      if (state === "DOWNLOADING") return "Simulated download in progress."
      if (state === "VERIFYING") return "Simulated exact artifact verification."
      if (state === "FAILED") return "Simulated installation failure."
      if (state === "INSTALLED AFTER ACQUISITION") return "Installed and verified in this simulation; activation is separate."
    }
    if (state === "INSTALLED AFTER ACQUISITION") return "Installed and checked · not in use yet."
    if (state === "AVAILABLE TO INSTALL") return "Available to install."
    if (state === "DOWNLOADING" || state === "VERIFYING" || state === "INSTALLING" || state === "AWAITING APPROVAL") return state.replace(/_/g, " ") + "."
    if (state === "BLOCKED") return blockedReason(modelData)
    if (state === "UNVERIFIED") return "This model is not available for installation yet."
    if (state === "FAILED") return "Installation did not finish."
    if (state === "RECOVERY_REQUIRED") return "Installation needs review before another attempt."
    return ""
  }
  function modelDetailsOpen(catalogId) { return modelDetailsByCatalog[catalogId] === true }
  function toggleModelDetails(catalogId) {
    var next = ({})
    for (var key in modelDetailsByCatalog) next[key] = modelDetailsByCatalog[key]
    next[catalogId] = !modelDetailsOpen(catalogId)
    modelDetailsByCatalog = next
  }
  function matchedCatalogEntryFor(installedModel) {
    if (!installedModel || typeof installedModel.id !== "string" || typeof installedModel.provider !== "string") return null
    for (var i = 0; i < catalogModels.length; ++i) {
      var m = catalogModels[i]
      if (catalogMatchesInstalledModel(m, installedModel)) return m
    }
    return null
  }
  function catalogMatchesInstalledModel(catalogModel, installedModel) {
    return catalogModel && installedModel && catalogIsInstalled(catalogModel)
      && catalogModel.source && typeof catalogModel.source.provider === "string"
      && catalogModel.source.provider !== "" && typeof installedModel.provider === "string"
      && typeof installedModel.id === "string" && installedModel.id !== ""
      && catalogModel.source.provider === installedModel.provider
      && catalogRuntimeId(catalogModel) === installedModel.id
  }
  function catalogPropertiesSummary(modelData) {
    if (!modelData) return ""
    var properties = modelData.properties || ({})
    return [properties.weight_size_bytes ? bytesLabel(properties.weight_size_bytes) : "",
      properties.native_context ? "Native maximum: " + (properties.native_context / 1024).toFixed(0) + "K" : "",
      modelData.family || "", Array.isArray(properties.quantizations) ? properties.quantizations.join(", ") : ""]
      .filter(function(x) { return x !== "" }).join("  ·  ")
  }
  function catalogFitConfidenceSummary(modelData) {
    if (!modelData) return ""
    var fit = fitFor(catalogRuntimeId(modelData))
    return fit ? "Fit estimate confidence: " + String(fit.confidence || "unknown").toUpperCase() + "."
      : "Fit estimate confidence is unknown."
  }
  function installedMatchedDetail(installedModel) {
    var e = matchedCatalogEntryFor(installedModel)
    return e ? acquisitionDetail(e) : ""
  }
  function installedMatchedHistory(installedModel) {
    var e = matchedCatalogEntryFor(installedModel)
    return e ? acquisitionHistory(e) : ""
  }
  // MODELS v2: SUGGESTED (default) · FITS · ALL. A download in progress and a model the user
  // added are always listed; with no suggestions SUGGESTED gives way to FITS.
  property string catalogFilter: "suggested"
  readonly property int suggestedCatalogCount: catalogModels.filter(function(item) { return !catalogIsInstalled(item) && !!modelSuggestions[item.catalog_id] }).length
  readonly property string effectiveCatalogFilter: catalogFilter === "suggested" && suggestedCatalogCount === 0 ? "fits" : catalogFilter
  readonly property int tooBigModelCount: catalogModels.filter(function(item) { return !catalogIsInstalled(item) && catalogTooBig(item) }).length
  function catalogFilterChoices() {
    function count(filter) { return catalogModels.filter(function(item) { return catalogRowVisibleFor(item, filter) }).length }
    var choices = []
    if (suggestedCatalogCount > 0) choices.push({ id: "suggested", label: "SUGGESTED", count: count("suggested"), tip: "Models published guides suggest for computers like this one" })
    choices.push({ id: "fits", label: "FITS", count: count("fits"), tip: "Every model that fits this computer" })
    choices.push({ id: "all", label: "ALL", count: count("all"), tip: "Every model in NOVA's list, also those too big for this computer" })
    return choices
  }
  function catalogFilterHint() {
    if (effectiveCatalogFilter === "suggested") return "Suggested from published guides for computers like this one; NOVA hasn't tested them here yet. Biggest first" + (gpuAvailable ? ": more capable, slower to answer." : ".")
    if (effectiveCatalogFilter === "fits") return gpuAvailable ? "Every model here fits your graphics card. Biggest first: more capable, slower to answer." : "Every model here fits this computer's memory. Smallest first: without a graphics card, smaller answers faster."
    return "Every model in NOVA's list; the ones too big for this computer say TOO BIG."
  }
  function catalogRowBusy(modelData) {
    var record = acquisitionByCatalog[modelData.catalog_id] || null
    return !!record && (["APPROVED", "STARTING", "DOWNLOADING", "VERIFYING", "CANCEL_REQUESTED"].indexOf(record.state) >= 0
      || (record.state === "RECOVERY_REQUIRED" && !record.recovery_resolution))
  }
  function catalogUses(modelData) {
    var tags = modelData.task_tags || [], caps = modelData.capabilities || {}, words = []
    if (tags.indexOf("coding") >= 0 || caps.coding === true) words.push("coding")
    if (tags.indexOf("general") >= 0) words.push("chat")
    if (tags.indexOf("reasoning") >= 0) words.push("reasoning")
    if (tags.indexOf("vision") >= 0 || caps.vision === true) words.push("images")
    if (!words.length && caps.chat === true) words.push("chat")
    return words.length === 1 && words[0] === "chat" ? "everyday chat" : words.join(" · ")
  }
  function catalogFacts(modelData) {
    var fit = rowFit(modelData)
    return [fit && fit.recommended_context > 0 ? contextLabel(fit.recommended_context) : "", catalogUses(modelData)].filter(function(x) { return x !== "" }).join(" · ")
  }
  function catalogRowVisible(modelData) { return catalogRowVisibleFor(modelData, effectiveCatalogFilter) }
  function catalogRowVisibleFor(modelData, filter) {
    if (!catalogIsInstalled(modelData)) {
      if (catalogRowBusy(modelData) || modelData.user_requested === true) return true
      if (filter === "suggested") return !!modelSuggestions[modelData.catalog_id]
      return filter === "all" || !catalogTooBig(modelData)
    }
    var record = acquisitionByCatalog[modelData.catalog_id] || null
    if (record) {
      if (["APPROVED", "STARTING", "DOWNLOADING", "VERIFYING", "CANCEL_REQUESTED"].indexOf(record.state) >= 0) return true
      if (record.state === "RECOVERY_REQUIRED" && !record.recovery_resolution) return true
    }
    if (modelsUnavailable || installedModelsLoading) return true
    return !discoveredModels.some(function(installedModel) {
      return catalogMatchesInstalledModel(modelData, installedModel)
    })
  }
  property string installedDetailsOpenId: ""
  function installedIdentityKey(modelData) {
    return modelData && typeof modelData.provider === "string" && typeof modelData.id === "string"
      ? JSON.stringify([modelData.provider, modelData.id]) : ""
  }
  function installedModelDetailsOpen(modelData) {
    var key = installedIdentityKey(modelData)
    return key !== "" && installedDetailsOpenId === key
  }
  function toggleInstalledModelDetails(modelData) {
    var key = installedIdentityKey(modelData)
    if (key === "") return
    installedDetailsOpenId = installedModelDetailsOpen(modelData) ? "" : key
  }
  function isSwitchDestination(modelData) {
    return modelTransitionActive && transitionTarget !== "" && (catalogRuntimeId(modelData) === transitionTarget
      || (modelData && modelData.provider === "ollama" && modelData.id === transitionTarget))
  }
  function selectModel(modelId, forceVerify, providerId) {
    if (acceptancePreview) {
      if (acceptanceSimulation) {
        beginAcceptanceSimulationActivation(modelId)
        return
      }
      onboardingTarget = modelId
      onboardingState = "ACTIVATING"
      onboardingActions = []
      onboardingCodes = ["PREVIEW_ACTIVATION_NOT_EXECUTED"]
      modelSwitchStatus = "PREVIEW // ACTIVATION NOT EXECUTED"
      return
    }
    var targetProvider = providerId || "ollama"
    if (!online || modelTransitionActive || chatBusy || chatContextOperationActive
        || chatResetInFlight || pendingApproval !== null || trackedAcquisitionId !== ""
        || ["approval_required", "executing_action", "verifying_action"].indexOf(effectiveState) >= 0
        || targetProvider !== "ollama" || (onboardingExplicitSelection && onboardingSelectedProvider !== targetProvider)
        || (installedModelReady({id: modelId, provider: targetProvider}) && forceVerify !== true)) return
    var found = !modelsUnavailable && !installedModelsLoading && discoveredModels.some(function(item) {
      return item && item.provider === targetProvider && item.id === modelId && item.available === true
    })
    if (!found) { modelSwitchStatus = "MODEL UNAVAILABLE"; return }
    modelSwitching = true; requestedModel = modelId; modelSwitchStatus = "SWITCHING // " + modelId.toUpperCase()
    // The install outcome ("Activation was not performed") is stale once activation starts.
    approvalFeedback = ""
    clearFinishedInstallStatus()
    ++onboardingRequestSerial
    onboardingRequestInFlight = false
    onboardingSnapshotFresh = false
    onboardingState = "ACTIVATING"; onboardingActions = []; onboardingTarget = modelId
    var xhr = root.novaRequest()
    var finished = false
    function finish(status, text) {
      if (finished) return
      finished = true
      modelSwitching = false
      if (status !== 200) {
        onboardingState = "ACTIVATION_FAILED"
        onboardingSnapshotFresh = false
        try {
          var failure = JSON.parse(text)
          modelSwitchStatus = failure.error === "busy" ? "MODEL SWITCH BUSY // TRY AGAIN LATER"
            : (failure.error === "model_switch_timeout" ? "MODEL SWITCH FAILED // TIMED OUT · CHECK STATUS"
            : (failure.error === "provider_unavailable" ? "MODEL SWITCH FAILED // PROVIDER OFFLINE"
            : (failure.error === "model_not_installed" ? "MODEL SWITCH FAILED // MODEL REMOVED · REFRESH"
            : "MODEL SWITCH FAILED // CHECK STATUS BEFORE RETRY")))
        } catch (error) { modelSwitchStatus = "MODEL SWITCH FAILED // CHECK STATUS BEFORE RETRY" }
        requestedModel = ""
        if (hostWidget && hostWidget.refreshModelState) hostWidget.refreshModelState()
        Qt.callLater(root.refreshOnboarding)
        return
      }
      try {
        var payload = JSON.parse(text)
        if (!payload || payload.available !== true || payload.runtime_ready !== true
            || payload.status !== "model_ready" || payload.provider !== targetProvider
            || payload.model !== modelId) {
          onboardingState = "ACTIVATION_FAILED"
          onboardingSnapshotFresh = false
          modelSwitchStatus = "MODEL SWITCH FAILED // RECHECK MODEL STATUS"
          requestedModel = ""
          if (hostWidget && hostWidget.refreshModelState) hostWidget.refreshModelState()
          Qt.callLater(root.refreshOnboarding)
          return
        }
        if (hostWidget) { hostWidget.activeProvider = payload.provider; hostWidget.activeModel = payload.model; hostWidget.activeModelAvailable = true; hostWidget.activeModelRuntimeReady = true; hostWidget.modelTarget = ""; hostWidget.modelPhase = "" }
        // The completed activation response is authoritative for selection;
        // the onboarding classifier will refresh its broader state separately.
        onboardingExplicitSelection = true
        onboardingSelectedProvider = payload.provider
        onboardingSelectedModel = payload.model
        modelSwitchStatus = "MODEL READY // " + payload.model.toUpperCase()
        requestedModel = ""
        modelStatusTimer.restart()
        refreshModels()
        beginActivationReconciliation(payload.provider, payload.model)
      } catch (error) {
        onboardingState = "ACTIVATION_FAILED"
        onboardingSnapshotFresh = false
        modelSwitchStatus = "MODEL SWITCH FAILED // RECHECK MODEL STATUS"; requestedModel = ""
        if (hostWidget && hostWidget.refreshModelState) hostWidget.refreshModelState()
        Qt.callLater(root.refreshOnboarding)
      }
    }
    xhr.onreadystatechange = function() {
      if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status,xhr.responseText)
    }
    xhr.onerror = function() { finish(0,"") }
    xhr.ontimeout = function() { finish(0,"") }
    try {
      root.openNova(xhr, "POST", "/model/active")
      xhr.setRequestHeader("Content-Type", "application/json")
      // Covers the bounded target-load plus worst-case rollback window.
      xhr.timeout = 500000
      xhr.send(JSON.stringify({ provider: targetProvider, model: modelId }))
    } catch (error) { finish(0, "") }
  }
  function installedModelSelected(item) {
    return item !== null && typeof item !== "undefined" && onboardingSnapshotFresh && onboardingExplicitSelection
      && item.provider === onboardingSelectedProvider && item.id === onboardingSelectedModel
  }
  function installedModelReady(item) {
    return installedModelSelected(item) && activeProvider === item.provider
      && activeModel === item.id && activeModelAvailable && activeModelRuntimeReady
  }
  function canLoadInstalledModel(item) {
    return online && onboardingSnapshotFresh && !modelsUnavailable && !installedModelsLoading
      && item !== null && typeof item !== "undefined" && item.provider === "ollama" && item.available === true
      && (!onboardingExplicitSelection || onboardingSelectedProvider === item.provider)
      && !installedModelReady(item) && !modelTransitionActive && !chatBusy
      && !chatContextOperationActive && !chatResetInFlight && pendingApproval === null
      && trackedAcquisitionId === ""
      && ["approval_required", "executing_action", "verifying_action"].indexOf(effectiveState) < 0
  }
  function loadInstalledModel(item) {
    if (!canLoadInstalledModel(item)) return
    selectModel(item.id, installedModelSelected(item), item.provider)
  }
  // Open CLI (CODE-01B): explicit profile per exact model; never selects or loads
  // NOVA's chat model. null state means availability is unknown.
  property var cliState: null
  property bool cliRequesting: false
  property string cliConfirmModel: ""
  // The project chosen in the confirm card when one model has several (last used first).
  property string cliChosenProfileId: ""
  // Enter launches from the confirm card only after the key that opened it is
  // released (or 300 ms pass with no Enter held), as with approvals.
  property bool cliConfirmArmed: false
  property bool cliConfirmReturnSeen: false
  property string cliStatusModel: ""
  property string cliStatus: ""
  property bool cliStatusIsReason: false  // cliStatus explains an unavailable CLI (not a launch result)
  property bool cliStatusFailed: false
  property bool cliLaunchUnconfirmed: false  // cliStatus says NOVA could not confirm the terminal yet
  // 0.15 (Konstantin, 2026-10-22): a coding CLI for every user (novad/coding_cli.py). A model's
  // card offers SET UP CODING CLI (OpenCode on the model's tailored build, one approval) and,
  // once set up, TUNE: the working context with a live graphics-memory meter and plain warnings.
  // The CLI can also ask NOVA for a change itself; that approval arrives through the bar's /state.
  property string cliSetupModel: ""
  property var cliSetupState: null
  property string cliSetupProject: ""
  property string cliSetupCare: "ask"
  property string cliSetupError: ""
  property bool cliSetupBusy: false
  property string cliTuneModel: ""
  property var cliTuneState: null
  property int cliTuneIndex: 0
  property bool cliTuneConfirm: false
  property bool cliTuneBusy: false
  property string cliTuneError: ""
  property var cliApprovalsShown: ({})
  // Words carry every verdict; colours only help (some themes map "urgent" to green).
  // Secondary text inside the tinted 0.15 cards (the theme's muted grey is too faint there).
  readonly property color cardSoftText: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.62)
  readonly property color cliTightColor: "#d9a441"
  readonly property color cliPastColor: "#b48ef0"
  readonly property var cliFallbackSteps: [4096, 8192, 12288, 16384, 24576, 32768, 40960, 49152, 65536, 98304, 131072]
  function contextLabel(tokens) { return typeof tokens === "number" && tokens > 0 ? Math.round(tokens / 1024) + "K" : "unknown" }
  function gibLabel(bytes) { return (Math.abs(bytes) / 1073741824).toFixed(1) + " GiB" }
  function openCodeProfile(item) {
    if (!cliState || !item || !Array.isArray(cliState.profiles)) return null
    for (var i = 0; i < cliState.profiles.length; ++i) {
      var profile = cliState.profiles[i]
      if (profile && profile.kind === "opencode" && profile.model === item.id) return profile
    }
    return null
  }
  // novad/coding_cli.py verdict(), for the live meter.
  function cliVerdict(numbers, tokens) {
    if (!numbers || !(tokens > 0)) return null
    var need = numbers.weights_bytes + numbers.runtime_bytes + numbers.fixed_cache_bytes + tokens * numbers.kv_bytes_per_token
    var spare = numbers.budget_bytes - need
    var level = spare < 0 ? "spills" : (spare < numbers.tight_margin_bytes ? "tight" : "fits")
    var past = tokens > numbers.native_context
    var text = level === "spills" ? "DOESN'T FIT · " + gibLabel(spare) + " would spill into system memory. Replies get several times slower, and the desktop may stutter while it runs."
      : (level === "tight" ? "TIGHT · only " + gibLabel(spare) + " of graphics memory left for the desktop and other apps; video, games or another model may stutter."
      : "FITS · " + gibLabel(need) + " of " + gibLabel(numbers.budget_bytes) + ", " + gibLabel(spare) + " to spare.")
    if (past) text += "\nPAST WHAT IT WAS TRAINED FOR (" + contextLabel(numbers.native_context) + ") · it starts to lose track of long conversations and code."
    var word = level === "spills" ? "DOESN'T FIT" : (level === "tight" ? "TIGHT" : "FITS")
    var short = level === "spills" ? gibLabel(-spare) + " would spill into system memory: several times slower"
      : (level === "tight" ? "only " + gibLabel(spare) + " left for the desktop and other apps" : gibLabel(spare) + " to spare")
    if (past) short += "; past " + contextLabel(numbers.native_context) + " it starts to lose track of long code"
    return { level: level, past: past, need: need, spare: spare, text: text, word: word, short: short }
  }
  function verdictColor(level, past) { return level === "spills" ? destructiveColor : (level === "tight" || level === "unknown" ? cliTightColor : (past ? cliPastColor : accent)) }
  function novaJson(method, path, value, timeout, done) {
    var xhr = root.novaRequest(), finished = false
    function finish(status, text) {
      if (finished) return
      finished = true
      var payload = null
      try { payload = text ? JSON.parse(text) : null } catch (error) { payload = null }
      done(status, payload && typeof payload === "object" ? payload : ({}))
    }
    xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status, xhr.responseText) }
    xhr.onerror = function() { finish(0, "") }
    xhr.ontimeout = function() { finish(0, "") }
    try {
      root.openNova(xhr, method, path)
      if (value) xhr.setRequestHeader("Content-Type", "application/json")
      xhr.timeout = timeout
      xhr.send(value ? JSON.stringify(value) : undefined)
    } catch (error) { finish(0, "") }
  }
  function openCliSetup(item) {
    cliTuneModel = ""; cliSetupModel = item.id; cliSetupState = null; cliSetupError = ""; cliSetupCare = "ask"; cliSetupProject = ""
    if (acceptancePreview) return
    novaJson("GET", "/cli/setup?model=" + encodeURIComponent(item.id), null, 10000, function(status, payload) {
      if (cliSetupModel !== item.id) return
      if (status !== 200) { cliSetupError = "NOVA couldn't prepare the setup (" + String(payload.error || "no answer").replace(/_/g, " ") + ")."; return }
      cliSetupState = payload
      cliSetupProject = Array.isArray(payload.projects) && payload.projects.length ? payload.projects[0] : ""
    })
  }
  function cliSetupTool() {
    var tools = cliSetupState && Array.isArray(cliSetupState.tools) ? cliSetupState.tools : []
    for (var i = 0; i < tools.length; ++i) if (tools[i] && tools[i].id === "opencode") return tools[i]
    return null
  }
  function proposeCliSetup() {
    var tool = cliSetupTool()
    if (!cliSetupState || cliSetupBusy || cliSetupProject === "" || !tool || tool.available !== true || pendingApproval) return
    cliSetupBusy = true; cliSetupError = ""
    var model = cliSetupModel
    novaJson("POST", "/cli/setup/proposals", { model: model, tool: "opencode", project: cliSetupProject, care: cliSetupCare }, 15000, function(status, payload) {
      cliSetupBusy = false
      if (status === 201 && typeof payload.id === "string") {
        cliSetupModel = ""
        var shown = cliApprovalsShown; shown[payload.id] = true; cliApprovalsShown = shown
        beginApproval(payload)
        return
      }
      cliSetupError = ({tool_unavailable: "OpenCode isn't on this computer. Omarchy installs it; run Omarchy's update, then try again.",
                        project_unavailable: "That folder isn't available any more. Choose another."})[payload.error]
        || ("Setup couldn't start: " + String(payload.error || "no answer").replace(/_/g, " ") + ".")
    })
  }
  function cliTuneSteps() {
    return cliTuneState && cliTuneState.numbers && Array.isArray(cliTuneState.numbers.steps) ? cliTuneState.numbers.steps : cliFallbackSteps
  }
  function cliTuneTokens() { var steps = cliTuneSteps(); return steps[Math.max(0, Math.min(steps.length - 1, cliTuneIndex))] }
  function cliTuneVerdict() { return cliTuneState ? cliVerdict(cliTuneState.numbers, cliTuneTokens()) : null }
  function openCliTune(item) {
    cliSetupModel = ""; cliTuneModel = item.id; cliTuneState = null; cliTuneConfirm = false; cliTuneError = ""
    if (acceptancePreview) return
    novaJson("GET", "/cli/tune?model=" + encodeURIComponent(item.id), null, 10000, function(status, payload) {
      if (cliTuneModel !== item.id) return
      if (status !== 200) { cliTuneError = "NOVA couldn't read this model's settings (" + String(payload.error || "no answer").replace(/_/g, " ") + ")."; return }
      cliTuneState = payload
      cliTuneIndex = Math.max(0, cliTuneSteps().indexOf(payload.context))
    })
  }
  function cliTuneRecommended() {
    if (!cliTuneState) return
    cliTuneIndex = Math.max(0, cliTuneSteps().indexOf(cliTuneState.recommended_context)); cliTuneConfirm = false
  }
  function saveCliTune(confirmed) {
    var tokens = cliTuneTokens(), verdict = cliTuneVerdict()
    if (!cliTuneState || !tokens || cliTuneBusy) return
    // Anything but a plain fit (or a model NOVA can't check) asks first.
    if (!confirmed && (!verdict || verdict.level !== "fits" || verdict.past)) { cliTuneConfirm = true; return }
    cliTuneBusy = true; cliTuneError = ""
    var model = cliTuneModel
    novaJson("POST", "/cli/tune", { model: model, context: tokens }, 130000, function(status, payload) {
      cliTuneBusy = false
      if (status === 200) {
        cliTuneModel = ""; cliTuneConfirm = false
        cliStatusModel = model; cliStatusFailed = false; cliStatusIsReason = false; cliLaunchUnconfirmed = false
        cliStatus = "Working context " + contextLabel(tokens) + ". The coding CLI uses it from its next message, and chat follows."
        refreshCliProfiles()
        return
      }
      cliTuneError = "Couldn't change it: " + String(payload.detail || payload.error || "no answer").replace(/_/g, " ") + ". Nothing changed."
    })
  }
  // The bar polls /state; a request the coding CLI made shows here (and opens the panel: the
  // user is waiting in the terminal). One answered on the other screen leaves this one.
  function considerCodingCliApproval(item) {
    if (pendingApproval && pendingApproval.coding_cli && pendingApproval.action === "coding_cli_tune" && !pendingApproval.decision
        && (!item || item.id !== pendingApproval.id)) clearPendingApproval()
    if (!item || typeof item.id !== "string" || item.coding_cli !== true || cliApprovalsShown[item.id] || pendingApproval || acceptancePreview) return
    var shown = cliApprovalsShown; shown[item.id] = true; cliApprovalsShown = shown
    beginApproval(item)
    if (!opened) open()
  }
  Connections {
    target: root.hostWidget
    ignoreUnknownSignals: true
    function onCodingCliApprovalChanged() { root.considerCodingCliApproval(root.hostWidget.codingCliApproval) }
  }
  function finishCodingCliDecision(next, decision, status, payload) {
    clearPendingApproval()
    var outcome = payload && typeof payload.status === "string" ? payload.status : ""
    if (status === 200 && outcome === "set_up") {
      approvalFeedback = "Coding CLI set up: OPEN CLI on " + modelPlainName(next.model) + " opens OpenCode in your folder. Its first start may download OpenCode."
      refreshCliProfiles()
      cliStatusModel = next.model; cliStatus = "Set up · OPEN CLI opens OpenCode (" + contextLabel(payload.context) + " working context)."; cliStatusFailed = false; cliStatusIsReason = false
      // The approval took the panel to CHAT; the result (and OPEN CLI) is on the model's card.
      showView("models")
    } else if (status === 200 && outcome === "changed") approvalFeedback = "Working context is now " + contextLabel(payload.context) + ". The coding CLI uses it from its next message."
    else if (status === 200 && outcome === "denied") approvalFeedback = "Declined. Nothing changed."
    else if (status === 200 && outcome === "failed") approvalFeedback = "It didn't work (" + String(payload.error || "unknown").slice(0, 120) + "). Nothing else changed."
    else if (status === 410) approvalFeedback = "This request expired. Nothing changed."
    else if (status === 409) approvalFeedback = "This request was already answered."
    else approvalFeedback = "Decision failed" + (payload && typeof payload.error === "string" ? ": " + payload.error.replace(/_/g, " ") : "") + "."
    applyStreamState("idle", "")
    restoreChatInputFocus()
  }
  function refreshCliProfiles() {
    if (acceptancePreview) return
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      var payload = null
      try { payload = xhr.status === 200 ? JSON.parse(xhr.responseText) : null } catch (error) { payload = null }
      payload = payload && Array.isArray(payload.profiles) ? payload : null
      // Each answer has a new checked_at; only a real change replaces cliState, so the CLI
      // rows aren't rebuilt every 5 s (a rebuild drops hover and can nudge the list).
      if (root.cliStateKey(payload) !== root.cliStateKey(root.cliState)) root.cliState = payload
      // A reason shown for an unavailable CLI ("already running") is stale once
      // that CLI is available again, e.g. after the user closed the session.
      if (root.cliStatusIsReason && root.cliStatus !== "" && root.cliState && root.cliState.profiles.some(function(profile) {
            return profile && profile.model === root.cliStatusModel && profile.available === true && profile.running !== true })) {
        root.cliStatus = ""; root.cliStatusFailed = false; root.cliStatusIsReason = false
      }
      // "Cannot confirm the terminal appeared" is answered once NOVA sees that CLI running.
      if (root.cliLaunchUnconfirmed && root.cliState && root.cliState.profiles.some(function(profile) {
            return profile && profile.model === root.cliStatusModel && profile.running === true })) {
        root.cliStatus = ""; root.cliLaunchUnconfirmed = false
      }
    }
    try {
      root.openNova(xhr, "GET", "/cli/profiles")
      xhr.timeout = 6000
      xhr.send()
    } catch (error) { cliState = null }
  }
  function cliStateKey(state) {
    if (!state) return ""
    var copy = Object.assign({}, state)
    delete copy.checked_at
    return JSON.stringify(copy)
  }
  // Online coding CLIs (ONLINE-01): Claude Code, Codex. They sign in by themselves;
  // NOVA stores no keys and they never touch NOVA's chat model. Each is shown as a
  // pseudo-item so the Open CLI card and shortcuts work unchanged.
  readonly property var onlineCliItems: !cliState ? [] : cliState.profiles.filter(function(p) {
      return p && p.kind === "online" && typeof p.id === "string" }).map(function(p) {
      return { id: "online:" + p.id, provider: "online", profileId: p.id } })
  // Every profile for this model (one per project), in the file's order.
  function cliProfilesFor(item) {
    if (!cliState || !item) return []
    return cliState.profiles.filter(function(profile) {
      if (!profile) return false
      if (item.provider === "online") return profile.kind === "online" && profile.id === item.profileId
      return profile.model === item.id && profile.provider === item.provider
    })
  }
  // First choice: the project used last that can open now, else the first that can, else
  // the one used last (its reason is then shown).
  function cliDefaultProfile(profiles) {
    if (!profiles.length) return null
    var byUse = profiles.slice().sort(function(a, b) { return (b.last_used || 0) - (a.last_used || 0) })
    for (var i = 0; i < byUse.length; i++) if (byUse[i].available === true && byUse[i].last_used) return byUse[i]
    for (var j = 0; j < profiles.length; j++) if (profiles[j].available === true) return profiles[j]
    return byUse[0].last_used ? byUse[0] : profiles[0]
  }
  // The profile OPEN CLI acts on: the card's choice while it is open, else the default.
  function cliProfileFor(item) {
    var profiles = cliProfilesFor(item)
    if (item && cliConfirmModel === item.id && cliChosenProfileId !== "")
      for (var i = 0; i < profiles.length; i++) if (profiles[i].id === cliChosenProfileId) return profiles[i]
    return cliDefaultProfile(profiles)
  }
  function moveCliChoice(item, step) {
    var profiles = cliProfilesFor(item), current = cliProfileFor(item)
    if (profiles.length < 2 || !current) return
    var index = profiles.indexOf(current)
    cliChosenProfileId = profiles[Math.max(0, Math.min(profiles.length - 1, index + step))].id
  }
  // "2 h ago", "yesterday", "3 Oct" for a time in seconds since 1970.
  function cliAgo(seconds) {
    if (typeof seconds !== "number" || seconds <= 0) return "not opened from NOVA yet"
    var elapsed = Math.max(0, Date.now() / 1000 - seconds)
    if (elapsed < 60) return "last opened just now"
    if (elapsed < 3600) return "last opened " + Math.floor(elapsed / 60) + " min ago"
    var then = new Date(seconds * 1000), today = new Date()
    var days = Math.round((new Date(today.getFullYear(), today.getMonth(), today.getDate()) - new Date(then.getFullYear(), then.getMonth(), then.getDate())) / 86400000)
    if (days === 0) return "last opened " + Math.floor(elapsed / 3600) + " h ago"
    if (days === 1) return "last opened yesterday"
    if (days < 7) return "last opened " + days + " days ago"
    return "last opened " + Qt.formatDate(then, then.getFullYear() === today.getFullYear() ? "d MMM" : "d MMM yyyy")
  }
  function cliRunningProjectLabel() {
    if (!cliState) return ""
    var running = cliState.profiles.filter(function(p) { return p && p.running === true && typeof p.project_label === "string" })
    return running.length ? running[0].project_label : ""
  }
  // A running coding CLI loads its own model on its first request; meanwhile NOVA
  // offers no second load and says what is happening (Konstantin, 2026-10-06).
  function cliLoadingModel(id) {
    return !!cliState && typeof id === "string" && id !== "" && cliState.profiles.some(function(profile) {
      return profile && profile.running === true && profile.model === id })
  }
  function cliRunning(item) { return cliProfilesFor(item).some(function(profile) { return profile.running === true }) }
  // OPEN CLI is launchable when any project for the model can open; the card's LAUNCH
  // when the chosen one can. A local CLI also waits for a model load NOVA is running: the
  // panel knows at once, novad's state says so within one refresh.
  function cliWaitsForLoad(item) { return !!item && item.provider !== "online" && modelTransitionActive }
  function cliCanLaunch(item) { return !cliRequesting && !cliWaitsForLoad(item) && cliProfilesFor(item).some(function(profile) { return profile.available === true }) }
  function cliChosenCanLaunch(item) { var profile = cliProfileFor(item); return !!profile && profile.available === true && !cliRequesting && !cliWaitsForLoad(item) }
  function cliUnavailableReason(item) {
    if (cliWaitsForLoad(item)) return cliReasonFor({ blocked: "model_loading" })
    if (!cliState) return "Coding CLI availability is unknown: NOVA's action service did not answer."
    var profile = cliProfileFor(item)
    // Name the button that loads it (LOAD AND VERIFY when it is already NOVA's model).
    if (profile && profile.blocked === "model_not_loaded" && item.provider === "ollama")
      return "Please load the model first: " + (installedModelSelected(item) ? "LOAD AND VERIFY" : "LOAD AND USE") + " puts it in GPU memory, then OPEN CLI opens."
    if (!profile) {
      var configured = cliState.profiles.filter(function(p) { return p && p.valid === true && p.kind !== "online" }).map(function(p) { return p.model })
      if (cliState.error === "invalid_profile_file") return "The coding CLI profile file is invalid; no CLI can be opened."
      if (!configured.length) return "No coding CLI is configured. Profiles live in ~/.config/nova/cli-profiles.json."
      return "No coding CLI profile for this model. Configured for: " + configured.join(", ") + "."
    }
    return cliReasonFor(profile)
  }
  // Why this one profile can't open now ("" when it can).
  function cliReasonFor(profile) {
    if (!profile || profile.available === true) return ""
    var other = cliRunningProjectLabel()
    var reasons = {
      cli_session_active: "A coding CLI session is already running.",
      model_not_loaded: "Please load the model first: its LOAD button puts it in GPU memory, then the CLI opens.",
      model_loading: "NOVA is loading the model. OPEN CLI opens once it is in GPU memory.",
      nova_busy: "NOVA is busy (answering in chat or running an action). OPEN CLI opens when it is done.",
      other_cli_session_active: (other ? "The coding CLI for " + other + " is open." : "A Qwen coding CLI is already open.") + " NOVA opens one at a time; close it first.",
      conflicting_resident_model: "Blocked: " + (profile.conflicting_models || []).join(", ") + " is loaded in GPU memory. The CLI worker needs the GPU to itself.",
      provider_unavailable: "Ollama is not reachable, so the model cannot be checked.",
      low_disk: "Less than 16 GiB of free disk; the CLI worker keeps that reserve.",
      launcher_unavailable: "The CLI launcher is missing or not safely owned.",
      project_unavailable: "The CLI project is missing or not initialized.",
      launcher_inside_project: "The CLI launcher sits inside the agent's editable project; NOVA refuses to run it.",
      command_unavailable: "The CLI program is missing or not safely owned. Check its path in ~/.config/nova/cli-profiles.json.",
      unsupported_tool: "NOVA does not know this CLI. Supported online CLIs: Claude Code, Codex.",
      invalid_profile: "The CLI profile is incomplete or has unknown fields."
    }
    return reasons[profile.blocked] || ("The CLI profile needs attention: " + String(profile.blocked || "unknown") + ".")
  }
  // The repository and branch a project follows, e.g. "NOVA master", "Pulsar main".
  function cliSyncTarget(profile) {
    var source = profile && typeof profile.sync_source === "string" && profile.sync_source !== "" ? profile.sync_source : "nova"
    var branch = profile && typeof profile.sync_branch === "string" && profile.sync_branch !== "" ? profile.sync_branch : "master"
    var name = source.toLowerCase() === "nova" ? "NOVA" : source.charAt(0).toUpperCase() + source.slice(1)
    return name + " " + branch
  }
  function cliSyncSummary(profile) {
    var state = profile ? profile.project_sync : "", target = cliSyncTarget(profile)
    if (state === "up_to_date") return "CODE  Project is up to date with " + target + "."
    if (state === "will_update") return "CODE  Project will update to the latest " + target + " (skipped if it has uncommitted changes)."
    if (state === "unreviewed_commits") return "CODE  Project has commits not yet reviewed; it will not be updated."
    return "CODE  Update status unknown; the launcher checks again and never overwrites work."
  }
  // One line per project row in the picker.
  function cliSyncShort(profile) {
    var state = profile ? profile.project_sync : "", target = cliSyncTarget(profile)
    if (state === "up_to_date") return "up to date with " + target
    if (state === "will_update") return "updates to the latest " + target + " on open"
    if (state === "unreviewed_commits") return "has commits not yet reviewed; not updated"
    return "update status checked on open"
  }
  function beginCliLaunch(item) {
    if (!cliCanLaunch(item)) return
    cliStatus = ""
    cliConfirmArmed = false
    cliConfirmReturnSeen = false
    var first = cliDefaultProfile(cliProfilesFor(item))
    cliChosenProfileId = first ? first.id : ""
    cliConfirmModel = item.id
    cliConfirmArmTimer.restart()
  }
  function cancelCliLaunch() { cliConfirmModel = ""; restoreChatInputFocus() }
  // Enter/Escape while the Open CLI card waits for a decision.
  function handleCliConfirmShortcut(decision) {
    if (cliConfirmModel === "" || pendingApproval) return false
    if (decision === "cancel") { cancelCliLaunch(); return true }
    if (!cliConfirmArmed) { cliConfirmReturnSeen = true; return true }
    var item = null
    var items = discoveredModels.concat(onlineCliItems)
    for (var i = 0; i < items.length; i++) if (items[i] && items[i].id === cliConfirmModel) item = items[i]
    if (item && cliChosenCanLaunch(item)) confirmCliLaunch(item)
    return true
  }
  // Scroll the model list so the whole item (the Open CLI card) is visible.
  function revealInModelList(item) {
    if (!item || !modelList) return
    var top = item.mapToItem(modelList.contentItem, 0, 0).y, bottom = top + item.height
    var maxY = Math.max(0, modelList.contentHeight - modelList.height)
    if (bottom > modelList.contentY + modelList.height) modelList.contentY = Math.min(maxY, bottom - modelList.height)
    if (top < modelList.contentY) modelList.contentY = Math.max(0, top)
  }
  function explainCliUnavailable(item) {
    cliConfirmModel = ""
    cliStatusModel = item.id
    cliStatusFailed = true
    cliStatus = cliUnavailableReason(item)
    cliStatusIsReason = true
    refreshCliProfiles()
  }
  function confirmCliLaunch(item) {
    var profile = cliProfileFor(item)
    if (!profile || !cliChosenCanLaunch(item)) { cliConfirmModel = ""; return }
    cliRequesting = true
    cliConfirmModel = ""
    cliStatusModel = item.id
    cliStatusFailed = false
    cliStatusIsReason = false
    cliLaunchUnconfirmed = false
    cliStatus = "REQUESTING CLI LAUNCH…"
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      var result = null
      try { result = JSON.parse(xhr.responseText) } catch (error) { result = null }
      root.cliRequesting = false
      if (xhr.status === 200 && result && result.status === "dispatched") {
        root.cliStatusFailed = false
        root.cliStatus = "CLI LAUNCH REQUESTED // NOVA CANNOT CONFIRM THE TERMINAL APPEARED"
        // NOVA never sees an online CLI "running", so its note just fades after a while.
        if (item.provider === "online") cliOnlineStatusClear.restart()
        else root.cliLaunchUnconfirmed = true
      } else {
        root.cliStatusFailed = true
        root.cliStatus = "CLI LAUNCH FAILED // " + String(result && result.error ? result.error : "no response").replace(/_/g, " ").toUpperCase()
      }
      root.refreshCliProfiles()
      cliFollowUp.restart()
    }
    try {
      root.openNova(xhr, "POST", "/cli/launch")
      xhr.setRequestHeader("Content-Type", "application/json")
      xhr.timeout = 15000
      xhr.send(JSON.stringify({ profile_id: profile.id, model: item.provider === "online" ? "online" : item.id }))
    } catch (error) { cliRequesting = false; cliStatusFailed = true; cliStatus = "CLI LAUNCH FAILED // REQUEST NOT SENT" }
  }
  function applyStreamState(state, tool) {
    if (!hostWidget) return
    hostWidget.runtimeState = state
    if (typeof hostWidget.runtimeUpdatedAt === "number") hostWidget.runtimeUpdatedAt = Date.now()
    hostWidget.currentTool = (state === "tool_call" || state === "approval_required" || state === "executing_action" || state === "verifying_action") && typeof tool === "string" ? tool : ""
  }
  function beginApproval(item) {
    approvalFeedback = ""
    if (!pendingApproval && chatViewport) {
      approvalReadingPosition = chatViewport.contentY
      approvalFollowLatest = chatViewport.followLatest
    }
    pendingApproval = JSON.parse(JSON.stringify(item))
    if (pendingApproval.expires_at !== undefined && pendingApproval.expiresAt === undefined)
      pendingApproval.expiresAt = pendingApproval.expires_at
    approvalShortcutArmed = !chatSubmissionKeyDown
    approvalFocusIndex = 1
    applyStreamState("approval_required", item.action || "")
    scheduleApprovalExpiry()
    // Foregrounding the card does not move the hidden transcript. Its content
    // and reading position are therefore restored when the request clears.
    Qt.callLater(function() {
      if (!root.pendingApproval || root.pendingApproval.id !== item.id) return
      Qt.callLater(function() {
        if (root.pendingApproval && root.pendingApproval.id === item.id && approveApprovalControl)
          approveApprovalControl.forceActiveFocus()
      })
    })
  }
  function requestApproval(item) { beginApproval(item) }
  function scheduleApprovalExpiry() {
    approvalExpiryTimer.stop()
    if (!pendingApproval || pendingApproval.decision || typeof pendingApproval.expiresAt !== "number") return
    var remaining = Math.floor(pendingApproval.expiresAt * 1000 - Date.now())
    if (remaining <= 0) { markApprovalExpired(); return }
    approvalExpiryTimer.interval = Math.max(1, Math.min(remaining, 2147483647))
    approvalExpiryTimer.restart()
  }
  function markApprovalExpired() {
    if (!pendingApproval || pendingApproval.decision) return
    var next = JSON.parse(JSON.stringify(pendingApproval))
    next.decision = "expired"
    pendingApproval = next
    approvalShortcutArmed = false
    approvalFeedback = next.acquisition
      ? "Installation approval expired. No download was started by this proposal."
      : "Action approval expired. The action was not authorized."
  }
  function clearPendingApproval() {
    approvalExpiryTimer.stop()
    recoveryRequestDeadline.stop(); recoveryRequestTimeoutCallback = null
    recoveryRequestSerial++
    var readingPosition = approvalReadingPosition, followLatest = approvalFollowLatest
    pendingApproval = null
    approvalShortcutArmed = true
    approvalFocusIndex = -1
    approvalReadingPosition = -1
    Qt.callLater(function() {
      if (root.pendingApproval || !chatViewport || readingPosition < 0) return
      chatViewport.programmaticScroll = true
      chatViewport.contentY = Math.min(readingPosition, Math.max(0, chatViewport.contentHeight - chatViewport.height))
      chatViewport.followLatest = followLatest
      Qt.callLater(function() { chatViewport.programmaticScroll = false })
    })
  }
  function restoreChatInputFocus() {
    Qt.callLater(function() {
      if (!root.pendingApproval && !root.chatBusy && chatInput && chatInput.enabled) chatInput.forceActiveFocus()
    })
  }
  function noteChatKeyPressed(event) {
    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) chatSubmissionKeyDown = true
  }
  function noteApprovalKeyReleased(event) {
    if (event.key !== Qt.Key_Return && event.key !== Qt.Key_Enter) return
    chatSubmissionKeyDown = false
    if (pendingApproval && !pendingApproval.decision) approvalShortcutArmed = true
    if (cliConfirmModel !== "") cliConfirmArmed = true
  }
  function handleApprovalShortcut(decision) {
    if (!pendingApproval) return false
    if (pendingApproval.recovery && decision === "deny" && pendingApproval.decision !== "sending") {
      decideRecovery("deny"); return true
    }
    // Escape is always an explicit rejection while a request is pending. Enter
    // remains gated until the original submit key is released.
    if (pendingApproval.decision) return true
    if (decision === "approve" && !approvalShortcutArmed) return true
    // Clear the arm before the existing decision request begins, so auto-repeat
    // and mouse/keyboard races cannot submit the immutable request twice.
    approvalShortcutArmed = false
    decideApproval(decision)
    return true
  }
  function focusApprovalControl(direction) {
    if (!pendingApproval || (pendingApproval.decision && !pendingApproval.recovery)) return
    if (pendingApproval.recovery) {
      var controls = pendingApproval.decision && pendingApproval.decision !== "checking"
        ? (pendingApproval.decision === "sending" ? [] : (pendingApproval.decision === "resolved" ? [dismissApprovalControl] : [reviewRecoveryAgainControl, dismissApprovalControl]))
        : [cancelApprovalControl]
      if (!pendingApproval.decision && pendingApproval.assessment && pendingApproval.assessment.operator_attestation_required) controls.push(recoveryConfirmationControl)
      if (recoveryCanResolve()) controls.push(approveApprovalControl)
      if (!controls.length) return
      approvalFocusIndex = (approvalFocusIndex + (direction < 0 ? -1 : 1) + controls.length) % controls.length
      controls[approvalFocusIndex].forceActiveFocus(); return
    }
    approvalFocusIndex = approvalFocusIndex < 0
      ? (direction < 0 ? 1 : 0)
      : (approvalFocusIndex + (direction < 0 ? -1 : 1) + 2) % 2
    if (approvalFocusIndex === 0) cancelApprovalControl.forceActiveFocus()
    else approveApprovalControl.forceActiveFocus()
  }
  function decideApproval(decision) {
    if (!pendingApproval || (decision !== "approve" && decision !== "deny" && !(decision === "alternative" && pendingApproval.coding_cli && pendingApproval.alternative_context))) return
    if (pendingApproval.recovery) { decideRecovery(decision); return }
    if (pendingApproval.decision) return
    approvalExpiryTimer.stop()
    approvalShortcutArmed = false
    var item = JSON.parse(JSON.stringify(pendingApproval))
    item.decision = "sending"
    pendingApproval = item
    approvalFeedback = decision === "approve" ? "Submitting approval…" : "Declining proposal…"
    if (acceptancePreview) {
      if (acceptanceSimulation) {
        pendingApproval = null
        approvalFeedback = decision === "approve" ? "Approval accepted. Starting simulated acquisition." : "Proposal declined. No simulated download started."
        applyStreamState("idle", "")
        if (decision === "approve") {
          applyAcceptanceSimulationStage("downloading")
          acceptanceSimulationTimer.restart()
        } else {
          applyAcceptanceSimulationStage("initial")
          acquisitionStatus = "SIMULATION // APPROVAL CANCELLED // NO DOWNLOAD STARTED"
          restoreChatInputFocus()
        }
        return
      }
      pendingApproval = null
      onboardingState = "ACQUISITION_PROPOSED"
      onboardingActions = []
      onboardingCodes = [decision === "approve" ? "PREVIEW_APPROVAL_RECORDED" : "PREVIEW_APPROVAL_CANCELLED"]
      acquisitionStatus = decision === "approve"
        ? "PREVIEW // APPROVAL RECORDED // NO DOWNLOAD STARTED"
        : "PREVIEW // CANCEL RECORDED // NO DOWNLOAD STARTED"
      approvalFeedback = decision === "approve" ? "Approval recorded. No preview download started." : "Proposal declined. No preview download started."
      applyStreamState("idle", "")
      restoreChatInputFocus()
      return
    }
    var xhr = root.novaRequest(), finished = false
    function finish(status, body) {
      if (finished) return
      finished = true
      approvalDecisionDeadline.stop()
      root.approvalRequestTimeoutCallback = null
      root.finishApprovalDecision(item.id, decision, status, body)
    }
    xhr.onreadystatechange = function() {
      if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status, xhr.responseText)
    }
    xhr.onerror = function() { finish(0, "") }
    xhr.ontimeout = function() { finish(0, "") }
    try {
      approvalRequestTimeoutCallback = function() { finish(0, "") }
      approvalDecisionDeadline.restart()
      root.openNova(xhr, "POST", "/approvals/" + encodeURIComponent(item.id) + "/" + decision)
      xhr.send()
    } catch (error) { finish(0, "") }
  }
  function finishApprovalDecision(id, decision, status, body) {
    if (!pendingApproval || pendingApproval.id !== id || pendingApproval.decision !== "sending") return
    var next = JSON.parse(JSON.stringify(pendingApproval)), payload = ({})
    try { payload = JSON.parse(body) } catch (error) { }
    if (next.removal) { finishRemovalDecision(next, decision, status, payload); return }
    if (next.coding_cli) { finishCodingCliDecision(next, decision, status, payload); return }
    if (status === 202 && next.acquisition) {
      clearPendingApproval()
      if (decision === "approve") {
        trackedAcquisitionId = id
        onboardingState = "ACQUISITION_IN_PROGRESS"
        onboardingActions = []
        onboardingCodes = ["ACQUISITION_APPROVED"]
        acquisitionStatus = next.update ? "UPDATING // APPROVED" : "INSTALLING // APPROVED"
        approvalFeedback = installCheckingFeedback
        acquisitionTimer.restart()
        refreshTrackedAcquisition()
      } else {
        acquisitionStatus = next.update ? "UPDATE DECLINED // YOUR CURRENT BUILD IS UNCHANGED" : "INSTALL DECLINED // NO DOWNLOAD STARTED"
        approvalFeedback = next.update ? "Update declined. Nothing was downloaded." : "Installation proposal declined. No download started."
        applyStreamState("idle", "")
        refreshAcquisitions()
        refreshOnboarding()
        restoreChatInputFocus()
      }
      return
    }
    next.decision = status === 202 ? decision : "unavailable"
    pendingApproval = next
    if (status === 202) approvalFeedback = decision === "approve" ? "Approval accepted." : "Request declined."
    else if (status === 410 || payload.error === "expired_approval") {
      next.decision = "expired"
      pendingApproval = next
      approvalFeedback = next.acquisition
        ? "Installation approval expired. No download was started by this proposal."
        : "Action approval expired. The action was not authorized."
    }
    else if (status === 409 || payload.error === "approval_already_used") approvalFeedback = "Approval is no longer available. Check request status before trying again."
    else if (status === 0) approvalFeedback = "Decision result unavailable. Check request status; do not retry automatically."
    else approvalFeedback = "Decision failed" + (typeof payload.error === "string" ? ": " + payload.error.replace(/_/g, " ") : ". Check installation status.")
    if (next.acquisition && status !== 202) {
      acquisitionStatus = "INSTALL APPROVAL UNAVAILABLE"
      refreshAcquisitions()
    }
  }
  function approvalButtonKey(event) {
    if (!pendingApproval) return
    if (event.key === Qt.Key_Escape) { handleApprovalShortcut("deny"); event.accepted = true }
    else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
      if (!event.isAutoRepeat) handleApprovalShortcut("approve")
      event.accepted = true
    }
  }
  function handleActionResult(payload) {
    if (pendingApproval && !pendingApproval.acquisition) {
      var status = payload && typeof payload.status === "string" ? payload.status : "unknown"
      approvalFeedback = status === "denied" ? "Action declined. Nothing was executed."
        : (status === "expired" ? "Action approval expired. The action was not authorized."
        : "Action result: " + status.replace(/_/g, " ") + ".")
    }
    clearPendingApproval()
  }
  function checkNovadRecovery() {
    if (acceptancePreview) return
    var xhr = root.novaRequest()
    xhr.onreadystatechange = function() {
      if (xhr.readyState === XMLHttpRequest.DONE && xhr.status === 200) {
        reconnectTimer.stop()
        chatError = ""
        addChat("assistant", "NOVA backend is online.")
      }
    }
    root.openNova(xhr, "GET", "/health")
    xhr.timeout = 1500
    xhr.send()
  }
  readonly property string effectiveState: runtimeState === "idle" && chatBusy ? "thinking" : runtimeState
  // Plain words for runtime states. During a chat, loading_model covers everything
  // before the first token (loading weights and reading the prompt).
  function stateWords(state) {
    if (state === "loading_model") return chatBusy ? (chatLoadsModel ? "LOADING THE MODEL" : "PREPARING REPLY") : "LOADING MODEL"
    if (state === "generating") return "WRITING"
    return String(state || "").replace(/_/g, " ").toUpperCase()
  }
  readonly property bool slowFirstReply: {
    clockTick
    return chatBusy && !chatLoadsModel && effectiveState === "loading_model" && chatStartedAt > 0 && Date.now() - chatStartedAt > 15000
  }
  // The message was sent while the selected model was out of GPU memory: say it loads.
  property bool chatLoadsModel: false
  onChatBusyChanged: if (!chatBusy) chatLoadsModel = false
  // Presentation only: preserve effectiveState/readiness and approval semantics.
  property bool coreMotionPaused: false
  // One spike per streamed answer chunk in the living wave (UI-02D).
  signal waveChunk()
  readonly property bool coreReducedMotion: setting("reducedMotion", false) === true
  readonly property double activityObservedAt: hostWidget && typeof hostWidget.runtimeUpdatedAt === "number"
    ? hostWidget.runtimeUpdatedAt : (acceptancePreview ? updatedAt : 0)
  readonly property bool activityFresh: {
    clockTick
    return activityObservedAt > 0 && Date.now() - activityObservedAt <= 5000
  }
  readonly property string coreMode: {
    // The Open CLI card waits for the user's own decision: amber call to action,
    // whatever the runtime status. A pending NOVA approval still takes precedence.
    if (cliConfirmModel !== "" && runtimeState !== "approval_required") return "waiting"
    if (!online) return "offline"
    if (!activityFresh) return "stale"
    if (runtimeState === "error" || runtimeState === "model_switch_error") return "error"
    if (modelSwitching || runtimeState === "model_switching") return "switching"
    if (runtimeState === "loading_model") return "preparing"
    if (runtimeState === "thinking") return "thinking"
    if (runtimeState === "generating") return "writing"
    if (runtimeState === "tool_call") return "tool"
    if (runtimeState === "executing_action" || runtimeState === "verifying_action") return "action"
    if (runtimeState === "approval_required") return "waiting"
    if (runtimeState !== "idle") return "stale"
    if (chatBusy) return "working"
    // A model download in progress (UI-02D): the sunrise light, with progress in the label.
    if (activeDownload) return "downloading"
    // A NOVA-launched coding CLI is running: real agent work NOVA knows about.
    return cliAgentActive ? "agent" : "idle"
  }
  // The acquisition (model download) currently in flight, if any.
  readonly property var activeDownload: {
    for (var key in acquisitionByCatalog) {
      var item = acquisitionByCatalog[key]
      if (item && ["APPROVED", "STARTING", "DOWNLOADING", "VERIFYING", "CANCEL_REQUESTED"].indexOf(item.state) >= 0) return item
    }
    return null
  }
  function downloadCoreLabel(item) {
    if (!item) return "DOWNLOADING"
    if (item.state === "VERIFYING") return "CHECKING DOWNLOAD"
    if (item.state === "CANCEL_REQUESTED") return "CANCELLING DOWNLOAD"
    if (item.state !== "DOWNLOADING") return "STARTING DOWNLOAD"
    var progress = item.progress || ({})
    var completed = progress.current_blob_completed_bytes, total = progress.current_blob_total_bytes
    if (typeof completed === "number" && typeof total === "number" && isFinite(completed) && isFinite(total) && total > 0 && completed >= 0 && completed <= total)
      return "DOWNLOADING · " + Math.floor(completed / total * 100) + "%"
    return "DOWNLOADING"
  }
  readonly property bool cliAgentActive: !!cliState && Array.isArray(cliState.profiles)
    && cliState.profiles.some(function(p) { return p && p.running === true })
  readonly property string coreModeLabel: ({idle:"IDLE", working:"WORKING", agent:"CODING AGENT", preparing:root.chatLoadsModel ? "LOADING MODEL" : "PREPARING",
    downloading:downloadCoreLabel(activeDownload),
    switching:"SWITCHING MODEL", thinking:"THINKING", writing:"WRITING", tool:"READ-ONLY TOOL",
    action:runtimeState === "verifying_action" ? "VERIFYING ACTION" : "APPROVED ACTION",
    waiting:root.cliConfirmModel !== "" && runtimeState !== "approval_required" ? "CONFIRM OPEN CLI" : "AWAITING APPROVAL", error:"ERROR", offline:"OFFLINE", stale:"STATUS STALE"})[coreMode]
  function addChat(role, text, sources, responseStatus) {
    while (chatHistoryModel.count >= 8) chatHistoryModel.remove(0)
    chatHistoryModel.append({ role: role, text: text, sources: Array.isArray(sources) ? sources : [],
                              responseStatus: role === "assistant"
                                ? (responseStatus || (text ? "complete" : "streaming")) : "" })
    scrollChatToEnd()
  }
  function addSystemLine(text) {
    var last = chatHistoryModel.count - 1
    if (last < 0 || chatHistoryModel.get(last).responseStatus !== "streaming") { addChat("system", text); return }
    if (chatHistoryModel.count >= 8) { chatHistoryModel.remove(0); last-- }
    chatHistoryModel.insert(last, { role: "system", text: text, sources: [], responseStatus: "" })
  }
  function appendAssistantText(text) {
    var index = chatHistoryModel.count - 1
    if (index < 0 || chatHistoryModel.get(index).role !== "assistant") return
    var entry = chatHistoryModel.get(index)
    chatHistoryModel.set(index, { role: "assistant", text: entry.text + text, sources: Array.isArray(entry.sources) ? entry.sources : [], responseStatus: entry.responseStatus || "streaming" })
    waveChunk()
    scrollChatToEnd()
  }
  function addDataSource(payload) {
    if (!payload || typeof payload.source !== "string" || typeof payload.checked_at !== "string") return
    var index = chatHistoryModel.count - 1
    if (index < 0 || chatHistoryModel.get(index).role !== "assistant") return
    var entry = chatHistoryModel.get(index), sources = Array.isArray(entry.sources) ? entry.sources.slice() : []
    if (sources.some(function(item) { return item && item.source === payload.source })) return
    sources.push({ source: payload.source, checkedAt: payload.checked_at })
    chatHistoryModel.set(index, { role: "assistant", text: entry.text, sources: sources, responseStatus: entry.responseStatus || "streaming" })
    scrollChatToEnd()
  }
  function markAssistantError(text) {
    var index = chatHistoryModel.count - 1
    if (index >= 0 && chatHistoryModel.get(index).role === "assistant") {
      var entry = chatHistoryModel.get(index)
      chatHistoryModel.set(index, { role: "assistant", text: entry.text || text,
                                   sources: Array.isArray(entry.sources) ? entry.sources : [],
                                   responseStatus: "incomplete" })
    }
    scrollChatToEnd()
  }
  function markAssistantComplete() {
    var index = chatHistoryModel.count - 1
    if (index < 0 || chatHistoryModel.get(index).role !== "assistant") return
    var entry = chatHistoryModel.get(index)
    chatHistoryModel.set(index, { role: "assistant", text: entry.text,
                                 sources: Array.isArray(entry.sources) ? entry.sources : [],
                                 responseStatus: "complete" })
  }
  function scrollChatToEnd() {
    Qt.callLater(function() {
      if (!chatViewport || (!chatViewport.followLatest && !chatViewport.nearBottom())) return
      chatViewport.programmaticScroll = true
      chatViewport.contentY = Math.max(0, chatViewport.contentHeight - chatViewport.height)
      chatViewport.followLatest = true
      Qt.callLater(function() { if (chatViewport) chatViewport.programmaticScroll = false })
    })
  }
  function createChatSession(message, sendAfterCreate) {
    if (acceptancePreview || chatSessionSetupInFlight) return
    chatSessionSetupInFlight = true
    var setupSerial = ++chatSessionSetupSerial
    var xhr = root.novaRequest(), finished = false
    function finish(status, body) {
      if (finished || setupSerial !== chatSessionSetupSerial) return
      finished = true
      chatResetDeadline.stop()
      chatSessionSetupInFlight = false
      var payload = ({})
      try { payload = body ? JSON.parse(body) : ({}) } catch (error) { payload = ({}) }
      if (payload === null || typeof payload !== "object" || Array.isArray(payload)) payload = ({})
      if (status === 201 && typeof payload === "object" && payload !== null && typeof payload.session_id === "string" && payload.session_id !== "") {
        chatSessionId = payload.session_id
        chatSessionGeneration++
        chatError = ""
        if (chatResetPendingClear !== 0 && chatResetPendingClear === chatResetSerial) {
          chatResetPendingClear = 0
          chatResetInFlight = false
          chatHistoryModel.clear()
          chatContextNotice = ""
          root.restoreChatInputFocus()
          return
        }
        if (sendAfterCreate) startChatRequest(message)
        else root.restoreChatInputFocus()
        return
      }
      if (chatResetPendingClear !== 0) {
        chatResetPendingClear = 0
        chatResetInFlight = false
      }
      chatError = "Conversation context could not be started. Your draft is preserved. Check NOVA and try Send again."
      root.restoreChatInputFocus()
    }
    xhr.onreadystatechange = function() {
      if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status, xhr.responseText)
    }
    xhr.onerror = function() { finish(0, "") }
    xhr.ontimeout = function() { finish(0, "") }
    root.openNova(xhr, "POST", "/chat/sessions")
    // New chat bounds its own creation with chatResetDeadline (5 s, reports the result as
    // uncertain); the request outlives it, so the two never race (2026-10-15).
    xhr.timeout = chatResetPendingClear !== 0 ? 10000 : 5000
    xhr.send()
  }
  function contextLossConfirmed(message, requestSerial, sessionId, generation, completed) {
    if (requestSerial !== activeChatRequestSerial || sessionId !== chatSessionId
        || generation !== chatSessionGeneration) return
    chatBusy = false
    chatRetentionPending = false
    chatRetentionSerial++
    if (!completed) {
      markAssistantError("[Message was not sent because its conversation context expired.]")
      if (chatInput && chatInput.text === "") chatInput.text = message
    }
    chatSessionId = ""
    chatSessionGeneration++
    chatContextNotice = "Earlier messages remain visible here but are no longer remembered. A new conversation context is being prepared; review your draft before sending."
    chatError = ""
    createChatSession("", false)
  }
  function finishRetentionReconciliation(serial, notice) {
    if (serial !== chatRetentionSerial) return
    retentionRetryTimer.stop()
    chatRetentionRetryCallback = null
    chatRetentionPending = false
    chatContextNotice = notice
    root.restoreChatInputFocus()
  }
  function reconcileChatRetention(sessionId, turnId, requestSerial, generation, attempt) {
    if (requestSerial !== activeChatRequestSerial || sessionId !== chatSessionId
        || generation !== chatSessionGeneration) return
    if (attempt === 0) {
      chatRetentionPending = true
      chatRetentionSerial++
    }
    var reconciliationSerial = chatRetentionSerial
    var xhr = root.novaRequest(), finished = false
    function retryOrUnknown() {
      if (reconciliationSerial !== chatRetentionSerial) return
      if (attempt >= 5) {
        finishRetentionReconciliation(reconciliationSerial,
          "Whether this reply will be remembered is unknown.")
        return
      }
      chatRetentionRetryCallback = function() {
        reconcileChatRetention(sessionId, turnId, requestSerial, generation, attempt + 1)
      }
      retentionRetryTimer.restart()
    }
    function finish(status, body) {
      if (finished || reconciliationSerial !== chatRetentionSerial
          || requestSerial !== activeChatRequestSerial || sessionId !== chatSessionId
          || generation !== chatSessionGeneration) return
      finished = true
      var payload = ({})
      try { payload = body ? JSON.parse(body) : ({}) } catch (error) { payload = ({}) }
      if (status === 404 && payload.error === "unknown_session") {
        contextLossConfirmed("", requestSerial, sessionId, generation, true)
        return
      }
      if (status !== 200 || payload.session_id !== sessionId) {
        finishRetentionReconciliation(reconciliationSerial,
          "Whether this reply will be remembered is unknown.")
        return
      }
      var completion = payload.last_completion
      if (payload.busy === true || !completion || completion.turn_id !== turnId) {
        retryOrUnknown()
        return
      }
      if (completion.retention === "OMITTED_OVERSIZED") {
        finishRetentionReconciliation(reconciliationSerial,
          "This reply was too large for conversation memory. It remains visible here, but later replies will not remember it.")
      } else if (completion.retention === "RETAINED") {
        finishRetentionReconciliation(reconciliationSerial,
          Number(completion.evicted_turns) > 0
            ? "Older conversation context was removed to stay within session limits."
            : "")
      } else {
        finishRetentionReconciliation(reconciliationSerial,
          "Whether this reply will be remembered is unknown.")
      }
    }
    xhr.onreadystatechange = function() {
      if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status, xhr.responseText)
    }
    xhr.onerror = function() { finishRetentionReconciliation(reconciliationSerial, "Whether this reply will be remembered is unknown.") }
    xhr.ontimeout = function() { finishRetentionReconciliation(reconciliationSerial, "Whether this reply will be remembered is unknown.") }
    root.openNova(xhr, "GET", "/chat/sessions/" + encodeURIComponent(sessionId))
    xhr.timeout = 1500
    xhr.send()
  }
  function startChatRequest(message) {
    if (!chatSessionId || chatBusy || chatContextOperationActive) return
    if (!onboardingCanSend || modelTransitionActive) {
      chatError = "Model readiness changed. Your draft is preserved; verify the model before sending."
      return
    }
    var requestSessionId = chatSessionId
    var requestGeneration = chatSessionGeneration
    var requestSerial = ++chatRequestSerial
    activeChatRequestSerial = requestSerial
    chatInput.text = ""
    chatError = ""
    addChat("user", message)
    addChat("assistant", "", [], "streaming")
    chatBusy = true
    chatStartedAt = Date.now()
    chatLoadsModel = modelLoadsOnSend && !activeModelRuntimeReady
    var xhr = root.novaRequest()
    var offset = 0
    var buffer = ""
    var pendingEvent = "message"
    var streamDone = false
    function currentRequest() {
      return requestSerial === activeChatRequestSerial
        && requestSessionId === chatSessionId
        && requestGeneration === chatSessionGeneration
    }
    function stopWithError(errorMessage, retentionUnknown) {
      if (streamDone || !currentRequest()) return
      streamDone = true
      chatBusy = false
      chatError = errorMessage
      if (retentionUnknown) chatContextNotice = "Whether this interrupted turn was remembered is unknown. It will not be sent again automatically."
      markAssistantError("[" + errorMessage + "]")
      root.restoreChatInputFocus()
    }
    function consumeEvents() {
      if (!currentRequest()) return
      var response = xhr.responseText || ""
      if (response.length <= offset) return
      buffer += response.slice(offset)
      offset = response.length
      // novad's events are an "event:" line and one "data:" line (single-line JSON). Act on
      // the data line itself: Quickshell's line reader holds an empty line back until more
      // bytes arrive, so waiting for the blank line that ends a frame left the last event of
      // every burst unhandled: an approval request until it expired, the final done for good
      // (a friend's install, 2026-10-22: "NOVA · INCOMPLETE", approvals that never showed).
      var newline = -1
      while ((newline = buffer.indexOf("\n")) >= 0) {
        var line = buffer.slice(0, newline)
        buffer = buffer.slice(newline + 1)
        if (line.indexOf("event:") === 0) { pendingEvent = line.slice(6).trim(); continue }
        if (line.indexOf("data:") !== 0) { if (line === "") pendingEvent = "message"; continue }
        var event = pendingEvent
        var data = line.slice(5).trim()
        pendingEvent = "message"
        try {
          var payload = JSON.parse(data)
          if (event === "token" && typeof payload.text === "string") { root.applyStreamState("generating", ""); appendAssistantText(payload.text) }
          else if (event === "status" && payload.state === "tool_call") root.applyStreamState("tool_call", payload.tool)
          else if (event === "status" && payload.state === "thinking") root.applyStreamState("thinking", "")
          else if (event === "data_source") root.addDataSource(payload)
          else if (event === "approval_required") root.requestApproval(payload)
          else if (event === "action_result") root.handleActionResult(payload)
          else if (event === "reconnecting") { streamDone = true; root.clearPendingApproval(); root.chatBusy = false; root.chatError = payload.message || "NOVA backend restart approved. Reconnecting…"; root.chatContextNotice = "This interrupted turn was not retained. It will not be sent again automatically."; root.markAssistantError("[Conversation interrupted while NOVA reconnects.]"); root.restoreChatInputFocus(); reconnectTimer.restart() }
          else if (event === "done") {
            streamDone = true
            chatBusy = false
            root.applyStreamState("idle", "")
            markAssistantComplete()
            scrollChatToEnd()
            if (payload.session_id === requestSessionId && typeof payload.turn_id === "string" && payload.turn_id !== "")
              reconcileChatRetention(requestSessionId, payload.turn_id, requestSerial, requestGeneration, 0)
            else {
              chatContextNotice = "Whether this reply will be remembered is unknown."
              root.restoreChatInputFocus()
            }
          }
          else if (event === "error") { root.applyStreamState("error", ""); stopWithError(payload.error || "novad stream failed", false) }
        } catch (error) { stopWithError("novad stream was invalid", true) }
        if (streamDone) return
      }
    }
    xhr.onreadystatechange = function() {
      if (!currentRequest()) return
      if (xhr.readyState === XMLHttpRequest.LOADING || xhr.readyState === XMLHttpRequest.DONE) consumeEvents()
      if (xhr.readyState === XMLHttpRequest.DONE && !streamDone) {
        var payload = ({})
        try { payload = xhr.responseText ? JSON.parse(xhr.responseText) : ({}) } catch (error) { payload = ({}) }
        if (xhr.status === 404 && (payload.error === "unknown_session" || payload.error === "expired_session")) {
          streamDone = true
          contextLossConfirmed(message, requestSerial, requestSessionId, requestGeneration, false)
        } else stopWithError(xhr.status === 200 ? "novad stream ended early" : "novad chat unavailable", xhr.status === 0)
      }
    }
    xhr.onerror = function() { stopWithError("novad chat unavailable", true) }
    xhr.ontimeout = function() { stopWithError("novad chat timed out", true) }
    root.openNova(xhr, "POST", "/chat/stream")
    xhr.setRequestHeader("Content-Type", "application/json")
    xhr.setRequestHeader("Accept", "text/event-stream")
    // Counted from the last line novad sent: an approval waits up to 120 s, and novad
    // sends a thinking heartbeat while a model thinks without writing.
    xhr.timeout = 190000
    xhr.timeoutIsIdle = true
    xhr.send(JSON.stringify({ message: message, session_id: requestSessionId }))
  }
  // Run 6 (2026-10-10): Enter while the model was still loading or being checked did
  // nothing at all; the message just sat in the box. Now NOVA says why and, when the
  // wait ends by itself (loading, checking), sends the message as soon as it can.
  property string chatWaitNotice: ""
  property double chatSendWhenReadyUntil: 0
  readonly property bool chatWaitResolvesItself: online && (modelTransitionActive || onboardingState === "CHECKING"
    || onboardingState === "ACTIVATING" || (onboardingState === "READY" && !onboardingCanSend))
  function chatWaitText() {
    if (modelTransitionActive || onboardingState === "ACTIVATING")
      return "Waiting for " + modelPlainName(transitionTarget || activeModel) + " to load. Your message sends by itself when it's ready."
    if (chatWaitResolvesItself) return "NOVA is checking the model. Your message sends by itself in a moment."
    if (!online) return "NOVA's background service isn't running. Your message stays here."
    return "NOVA can't chat yet: finish the step above (or pick a model in MODELS). Your message stays here."
  }
  onOnboardingCanSendChanged: {
    if (onboardingCanSend && chatSendWhenReadyUntil > 0) Qt.callLater(root.sendWhenReady)
    else if (onboardingCanSend) chatWaitNotice = ""  // ready now: the old "can't chat yet" is stale
  }
  // The wait turned into something the user has to fix (failed load, setup): say so instead.
  onChatWaitResolvesItselfChanged: if (!chatWaitResolvesItself && chatSendWhenReadyUntil > 0) Qt.callLater(root.recheckChatWait)
  function recheckChatWait() {
    if (chatSendWhenReadyUntil === 0 || onboardingCanSend || chatWaitResolvesItself) return
    chatSendWhenReadyUntil = 0; chatWaitNotice = chatWaitText()
  }
  function sendWhenReady() {
    var due = chatSendWhenReadyUntil > 0 && Date.now() <= chatSendWhenReadyUntil
    chatSendWhenReadyUntil = 0; chatWaitNotice = ""
    if (due) sendChat()
  }
  function sendChat() {
    if (pendingApproval !== null) return
    if (acceptancePreview) {
      if (acceptanceSimulation) sendAcceptanceSimulationReply()
      return
    }
    if (!chatInput || !chatInput.text.trim() || chatBusy || chatContextOperationActive || chatResetInFlight) return
    if (!onboardingCanSend || modelTransitionActive) {
      chatWaitNotice = chatWaitText()
      // No refresh from here (contract: sendChat never starts one); the 30 s readiness
      // timer and activation reconciliation settle it, then the message goes out.
      chatSendWhenReadyUntil = chatWaitResolvesItself ? Date.now() + 120000 : 0
      return
    }
    chatWaitNotice = ""; chatSendWhenReadyUntil = 0
    var message = chatInput.text.trim()
    if (welcomeVisible) markWelcomeSeen()
    if (!chatSessionId) createChatSession(message, true)
    else startChatRequest(message)
  }
  // Qt does not reliably honor xhr.timeout; this deadline settles the reset
  // and its session creation/replacement without clearing confirmed content.
  function chatResetDeadlineReached() {
    chatResetDeadline.stop()
    // A replaced context owns its own feedback. Still settle our old lease;
    // reset-driven creation is part of this operation, not unrelated replacement.
    var reportUncertainty = chatResetGeneration === chatSessionGeneration || chatResetPendingClear !== 0
    chatResetSerial++
    chatSessionSetupSerial++
    chatResetInFlight = false
    chatSessionSetupInFlight = false
    chatResetPendingClear = 0
    if (reportUncertainty)
      chatError = "The new chat could not be confirmed in time, so its result is uncertain. Transcript and draft are preserved. You can try again."
    root.restoreChatInputFocus()
  }
  function startChatReset() {
    if (acceptancePreview || chatResetInFlight || chatBusy || chatContextOperationActive
        || modelTransitionActive || pendingApproval !== null) return
    var resetSerial = ++chatResetSerial
    chatResetGeneration = chatSessionGeneration
    chatResetInFlight = true
    chatResetDeadline.start()
    if (chatSessionId === "") {
      chatResetPendingClear = resetSerial
      createChatSession("", false)
      return
    }
    var targetSessionId = chatSessionId
    var targetGeneration = chatSessionGeneration
    var xhr = root.novaRequest(), finished = false
    function finish(status, body) {
      if (finished) return
      finished = true
      if (resetSerial !== chatResetSerial || targetSessionId !== chatSessionId
          || targetGeneration !== chatSessionGeneration) return
      chatResetDeadline.stop()
      chatResetInFlight = false
      var payload = ({})
      try { payload = body ? JSON.parse(body) : ({}) } catch (error) { payload = ({}) }
      if (payload === null || typeof payload !== "object" || Array.isArray(payload)) payload = ({})
      if (status === 200 && typeof payload === "object" && payload !== null && typeof payload.session_id === "string" && payload.session_id === targetSessionId) {
        chatSessionGeneration++
        chatError = ""
        chatHistoryModel.clear()
        chatContextNotice = ""
        root.restoreChatInputFocus()
        return
      }
      if (status === 404 && payload !== null && payload.error === "unknown_session") {
        chatSessionId = ""
        chatSessionGeneration++
        chatResetPendingClear = resetSerial
        createChatSession("", false)
        chatResetDeadline.start()
        return
      }
      chatError = (status === 409 && payload.error === "busy_session")
        ? "The conversation is busy right now, so New chat could not be confirmed. Transcript and draft are preserved."
        : "New chat could not be confirmed. Transcript and draft are preserved. Retry when NOVA is reachable."
      root.restoreChatInputFocus()
    }
    xhr.onreadystatechange = function() {
      if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status, xhr.responseText)
    }
    xhr.onerror = function() { finish(0, "") }
    xhr.ontimeout = function() { finish(0, "") }
    root.openNova(xhr, "POST", "/chat/sessions/" + encodeURIComponent(targetSessionId) + "/reset")
    xhr.timeout = 5000
    xhr.send()
  }
  function open() {
    controller.show()
    if (acceptancePreview) { applyAcceptancePreview(); return }
    refresh(); refreshModels(); refreshCatalog(); refreshRecommendations(); refreshCliProfiles(); Qt.callLater(root.refreshOnboarding)
    checkForUpdates(false)
  }
  function close() { controller.hide() }
  function toggle() { if (opened) close(); else open() }
  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function") return bar.switchPanelFrom(barIdentity, direction)
    return false
  }

  Timer { interval: 1000; running: root.opened; repeat: true; onTriggered: root.clockTick++ }
  Timer { id: reconnectTimer; interval: 2000; repeat: true; onTriggered: root.checkNovadRecovery() }
  Timer { id: retentionRetryTimer; interval: 150; repeat: false; onTriggered: { if (root.chatRetentionRetryCallback) root.chatRetentionRetryCallback() } }
  Timer { id: modelStatusTimer; interval: 5000; onTriggered: root.modelSwitchStatus = "" }
  Timer { id: cliFollowUp; interval: 4000; onTriggered: root.refreshCliProfiles() }
  Timer { id: cliOnlineStatusClear; interval: 12000; onTriggered: if (root.cliStatusModel.indexOf("online:") === 0 && !root.cliStatusFailed) root.cliStatus = "" }
  Timer { id: cliConfirmArmTimer; interval: 300; onTriggered: if (root.cliConfirmModel !== "" && !root.cliConfirmReturnSeen) root.cliConfirmArmed = true }
  // While a CLI session runs, notice when it ends so CLI RUNNING turns back into OPEN CLI.
  readonly property bool cliSessionRunning: !!cliState && (cliState.client_active === true || cliState.profiles.some(function(profile) { return profile && profile.running === true }))
  Timer {
    objectName: "cliRunningWatch"; interval: 5000; repeat: true
    running: root.opened && root.cliSessionRunning && !root.acceptancePreview
    onTriggered: {
      root.refreshCliProfiles()
      // The CLI's first request loads the model; re-check so READY appears by itself.
      if ((root.onboardingState === "NEEDS_LOAD" || root.onboardingState === "NEEDS_ACTIVATION") && root.cliLoadingModel(root.onboardingTarget))
        root.refreshOnboarding(true)
    }
  }
  Timer {
    // After a setup step, re-check every 4 s for up to 3 minutes so the panel moves on by itself.
    id: setupFollowUp
    property int ticks: 0
    interval: 4000; repeat: true
    onTriggered: {
      ollamaUnit.reload(); novaSetupScript.reload()
      root.refreshOnboarding(true)
      if (++ticks > 45 || (root.setupLaunched !== "" && root.setupStep() !== null && root.setupStep().id !== root.setupLaunched)) {
        stop(); root.setupLaunched = ""
      }
    }
  }
  // While open, keep CLI state fresh: it drives both the rows and the CODING AGENT core.
  Timer { interval: 5000; running: root.opened && !root.acceptancePreview; repeat: true; onTriggered: root.refreshCliProfiles() }
  Timer { id: activationReconcileTimer; interval: 250; repeat: true; onTriggered: root.reconcileActivation() }
  Timer { id: readinessConflictTimer; interval: 2000; onTriggered: root.refreshOnboarding() }
  Timer { id: acquisitionTimer; interval: 1000; repeat: true; onTriggered: root.refreshTrackedAcquisition() }
  // A client deadline is not provider cancellation. A lost response remains
  // unknown and must be reconciled through status; never resend automatically.
  Timer { id: approvalDecisionDeadline; interval: 30000; onTriggered: { if (root.approvalRequestTimeoutCallback) root.approvalRequestTimeoutCallback() } }
  Timer { id: recoveryRequestDeadline; interval: 15000; onTriggered: { if (root.recoveryRequestTimeoutCallback) root.recoveryRequestTimeoutCallback() } }
  Timer { id: modelsRequestDeadline; interval: 2000; onTriggered: { if (root.modelsRequestTimeoutCallback) root.modelsRequestTimeoutCallback() } }
  Timer { id: chatResetDeadline; interval: 5000; onTriggered: root.chatResetDeadlineReached() }
  Timer { id: approvalExpiryTimer; repeat: false; onTriggered: root.markApprovalExpired() }
  Timer { id: acceptanceSimulationTimer; interval: 2500; onTriggered: root.advanceAcceptanceSimulation() }
  Timer { objectName: "onboardingRecheck"; interval: 30000; running: root.opened && !root.acceptancePreview; repeat: true; onTriggered: root.refreshOnboarding(true) }
  ListModel { id: chatHistoryModel }

  // MODELS v2: a fact as one word in a thin frame (FITS, TIGHT, TOO BIG, SUGGESTED…).
  component FactWord: Rectangle {
    property string text: ""
    property color tone: root.accent
    visible: text !== ""
    width: visible ? factWordText.implicitWidth + Style.space(8) : 0
    height: factWordText.implicitHeight + Style.space(2)
    color: "transparent"; border.width: 1; border.color: Qt.rgba(tone.r, tone.g, tone.b, 0.6)
    Text { id: factWordText; textFormat: Text.PlainText; anchors.centerIn: parent; text: parent.text; color: parent.tone; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.bold: true; font.letterSpacing: 1 }
  }
  component CompactModelButton: Ui.Button {
    fontSize: Style.font.bodySmall
    horizontalPadding: Math.max(4, Style.spacing.controlPaddingX * 0.7)
    verticalPadding: Math.max(2, Style.spacing.controlPaddingY * 0.33)
  }

  // Marketplace review #11195 (2026-10-13, follow-up 2026-10-14): on released Qt (6.11.2,
  // 6.12.0) an editable TextInput reads the whole clipboard text whenever the clipboard
  // changes and again the moment it turns editable (QQuickTextInput::q_canPasteChanged, also
  // called from setReadOnly(false)), with no size limit and no overall deadline; fixed upstream
  // 2026-09-13, not released yet. A read-only field keeps Qt off the clipboard, so NOVA's
  // fields never become editable to Qt: readOnly stays true and the field edits its own text
  // from key events. Paste goes through root.pasteInto (wl-paste in its own process, at most
  // pasteLimitBytes, stopped after 2 s), so the shell never holds more than that. Cost: input
  // methods and dead keys reach only editable fields, so they don't work here until Qt ships
  // the fix. Qt draws no caret in a read-only field, so the field draws its own.
  component GuardedTextField: Ui.TextField {
    id: guardedField
    readOnly: true
    cursorVisible: activeFocus
    property var undoSteps: []
    property var redoSteps: []
    property int typedTo: -1    // where the last typed character ended: typing on from there is one undo step
    property bool ownEdit: false
    signal pasteRefused(string reason)
    onTextChanged: if (!ownEdit) { undoSteps = []; redoSteps = []; typedTo = -1 }  // set from outside (sent, cleared)
    function replaceRange(start, end, insertText, typing) {
      insertText = insertText.slice(0, Math.max(0, maximumLength - (text.length - (end - start))))
      if (start === end && insertText === "") return
      if (!(typing && start === end && start === typedTo)) undoSteps = undoSteps.slice(-49).concat([{ text: text, cursor: cursorPosition }])
      redoSteps = []; typedTo = typing ? start + insertText.length : -1
      ownEdit = true; text = text.slice(0, start) + insertText + text.slice(end); ownEdit = false
      cursorPosition = start + insertText.length
    }
    function stepBack(redo) {
      var from = redo ? redoSteps : undoSteps, current = { text: text, cursor: cursorPosition }
      if (from.length === 0) return
      var step = from[from.length - 1]
      if (redo) { redoSteps = from.slice(0, -1); undoSteps = undoSteps.concat([current]) }
      else { undoSteps = from.slice(0, -1); redoSteps = redoSteps.concat([current]) }
      typedTo = -1
      ownEdit = true; text = step.text; ownEdit = false
      cursorPosition = step.cursor
    }
    function wordEdge(position, forward) {
      var inside = function() { return forward ? position < text.length : position > 0 }
      var next = function() { return text.charAt(forward ? position : position - 1) }
      while (inside() && /\s/.test(next())) position += forward ? 1 : -1
      while (inside() && /\S/.test(next())) position += forward ? 1 : -1
      return position
    }
    // Navigation, selection, copy, Select All and Enter stay Qt's (they work read-only).
    function editKey(event) {
      var start = selectionStart, end = selectionEnd, word = (event.modifiers & Qt.ControlModifier) !== 0
      if (event.matches(StandardKey.Paste)) { root.pasteInto(guardedField); return true }
      if (event.matches(StandardKey.Cut)) { if (start !== end) { copy(); replaceRange(start, end, "", false) } return true }
      if (event.matches(StandardKey.Undo)) { stepBack(false); return true }
      if (event.matches(StandardKey.Redo)) { stepBack(true); return true }
      if (event.key === Qt.Key_Backspace && start === end)
        start = word ? wordEdge(start, false) : start - (/[\uDC00-\uDFFF]/.test(text.charAt(start - 1)) ? 2 : 1)
      if (event.key === Qt.Key_Delete && start === end)
        end = word ? wordEdge(end, true) : end + (/[\uD800-\uDBFF]/.test(text.charAt(end)) ? 2 : 1)
      if (event.key === Qt.Key_Backspace || event.key === Qt.Key_Delete) {
        replaceRange(Math.max(0, start), Math.min(text.length, end), "", false); return true
      }
      // Qt's own rule (QInputControl::isAcceptableInput): Ctrl and Ctrl+Shift are shortcuts; AltGr may come as Ctrl+Alt.
      var mods = event.modifiers & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
      if (event.text === "" || /[\x00-\x1f\x7f]/.test(event.text) || mods === Qt.ControlModifier || mods === (Qt.ControlModifier | Qt.ShiftModifier)) return false
      replaceRange(start, end, event.text, true)
      return true
    }
    Keys.onPressed: function(event) { if (guardedField.editKey(event)) event.accepted = true }
    cursorDelegate: Rectangle {
      id: guardedCaret
      property bool blinkOn: true
      width: 1
      color: guardedField.color
      visible: guardedField.activeFocus && blinkOn
      function restartBlink() { blinkOn = true; blinkTimer.restart() }
      Timer {
        id: blinkTimer
        interval: Math.max(100, Qt.styleHints.cursorFlashTime / 2)
        running: guardedField.activeFocus && Qt.styleHints.cursorFlashTime >= 2
        repeat: true
        onTriggered: guardedCaret.blinkOn = !guardedCaret.blinkOn
      }
      Connections {
        target: guardedField
        function onCursorPositionChanged() { guardedCaret.restartBlink() }
        function onTextChanged() { guardedCaret.restartBlink() }
        function onActiveFocusChanged() { guardedCaret.restartBlink() }
      }
    }
  }
  // Paste for GuardedTextField: the clipboard is read by wl-paste in its own process, cut at
  // pasteLimitBytes (+1 to tell "too long") and stopped after 2 s, so a huge or stalled
  // clipboard never reaches the shell. The last line of the output is the exit status.
  readonly property int pasteLimitBytes: 131072  // a full box: 32767 characters of up to 4 bytes
  property var pasteTarget: null
  function pasteInto(field) {
    if (boundedPaste.running) return
    pasteTarget = field
    boundedPaste.running = true
  }
  function utf8Length(value) {
    var bytes = 0
    for (var i = 0; i < value.length; i++) {
      var unit = value.charCodeAt(i)
      bytes += unit < 0x80 ? 1 : (unit < 0x800 ? 2 : (unit >= 0xDC00 && unit <= 0xDFFF ? 1 : 3))
    }
    return bytes
  }
  function finishPaste(output) {
    var field = pasteTarget, cut = output.lastIndexOf("\n"), status = output.slice(cut + 1).trim()
    var content = cut < 0 ? "" : output.slice(0, cut).replace(/\r\n/g, "\n")
    pasteTarget = null
    if (!field || !field.visible || !field.enabled) return
    if (status === "124") { field.pasteRefused("Not pasted: the clipboard didn't answer within 2 seconds."); return }
    if (status === "127") { field.pasteRefused("Not pasted: pasting needs wl-paste (package wl-clipboard)."); return }
    if (content === "") return
    if (utf8Length(content) > pasteLimitBytes
        || content.length > field.maximumLength - (field.text.length - (field.selectionEnd - field.selectionStart))) {
      field.pasteRefused("Not pasted: the clipboard text is too long for this box."); return
    }
    field.replaceRange(field.selectionStart, field.selectionEnd, content, false)
  }
  Process {
    id: boundedPaste
    command: ["sh", "-c", "command -v wl-paste >/dev/null || { printf '\\n127'; exit 0; }; "
              + "timeout 2 sh -c 'wl-paste --no-newline --type text 2>/dev/null | head -c \"$1\"' sh " + (root.pasteLimitBytes + 1)
              + "; printf '\\n%s' \"$?\""]
    stdout: StdioCollector { onStreamFinished: root.finishPaste(String(text)) }
  }

  component AcquisitionProgress: Column {
    id: acquisitionProgress
    required property var modelData
    readonly property var observation: root.acquisitionBlobProgress(modelData)
    visible: observation.visible === true
    width: parent ? parent.width : 0
    spacing: Style.spacing.xxs
    Text {
      textFormat: Text.PlainText
      width: parent.width
      text: acquisitionProgress.observation.label || ""
      color: root.accent
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      font.letterSpacing: 0.6
      wrapMode: Text.Wrap
    }
    Rectangle {
      id: acquisitionProgressTrack
      width: parent.width
      height: Style.space(4)
      color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)
      Rectangle {
        visible: acquisitionProgress.observation.determinate === true
        width: parent.width * Math.max(0, Math.min(1, Number(acquisitionProgress.observation.fraction) || 0))
        height: parent.height
        color: root.accent
      }
      Rectangle {
        id: acquisitionIndeterminateSegment
        visible: acquisitionProgress.observation.determinate !== true
        width: parent.width * 0.22
        height: parent.height
        x: 0
        color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.58)
        SequentialAnimation on x {
          running: acquisitionIndeterminateSegment.visible && root.opened
          loops: Animation.Infinite
          NumberAnimation { from: 0; to: Math.max(0, acquisitionProgressTrack.width - acquisitionIndeterminateSegment.width); duration: 900; easing.type: Easing.InOutSine }
          NumberAnimation { from: Math.max(0, acquisitionProgressTrack.width - acquisitionIndeterminateSegment.width); to: 0; duration: 900; easing.type: Easing.InOutSine }
        }
      }
    }
    Text {
      textFormat: Text.PlainText
      width: parent.width
      text: acquisitionProgress.observation.detail || ""
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
    }
  }

  // Telemetry (2026-10-07, btop-inspired; run 5): one framed MACHINE box, one row per
  // reading. "How busy" is a dot history, "how full" is a bar; colour only warms up
  // near the limit; readings this machine can't report are hidden, not boxed.
  function loadTint(fraction) {
    if (!isFinite(fraction)) return accent
    if (fraction >= 0.95) return Commons.Color.urgent
    if (fraction >= 0.85) return Qt.rgba((accent.r + Commons.Color.urgent.r) / 2, (accent.g + Commons.Color.urgent.g) / 2, (accent.b + Commons.Color.urgent.b) / 2, 1)
    return accent
  }
  function freeSpace(used, total) { return valid(used) && valid(total) && total >= used ? (total - used).toFixed(0) + " GiB free" : "—" }
  component TelemetryRow: Item {
    id: row
    property string label: ""
    property string value: "—"
    property string tooltipText: ""
    property real fraction: 0
    property var history: []
    property bool dots: false  // activity history instead of a fill bar
    readonly property color tint: root.loadTint(fraction)
    width: parent ? parent.width : implicitWidth
    implicitHeight: Math.max(rowLabel.implicitHeight, Style.space(14))
    Text { textFormat: Text.PlainText; id: rowLabel; width: Style.space(58); anchors.verticalCenter: parent.verticalCenter; text: row.label; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.72); font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.1 }
    // Fixed width, so a changing value never moves the graph (lesson of the brain bar).
    TextMetrics { id: valueMetrics; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; text: "00.0 / 00.0 GiB" }
    Text { textFormat: Text.PlainText; id: rowValue; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: valueMetrics.width; horizontalAlignment: Text.AlignRight; text: row.value; color: row.fraction >= 0.85 ? row.tint : root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
    Item {
      id: graph
      anchors.left: rowLabel.right; anchors.right: rowValue.left; anchors.rightMargin: Style.spacing.sm
      anchors.verticalCenter: parent.verticalCenter
      height: row.dots ? Style.space(12) : Math.max(3, Style.space(5))
      Rectangle { visible: !row.dots; anchors.fill: parent; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10) }
      Rectangle {
        visible: !row.dots
        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: parent.width * Math.max(0, Math.min(1, row.fraction))
        gradient: Gradient {
          orientation: Gradient.Horizontal
          GradientStop { position: 0.0; color: Qt.rgba(row.tint.r, row.tint.g, row.tint.b, 0.40) }
          GradientStop { position: 1.0; color: row.tint }
        }
        Behavior on width { NumberAnimation { duration: 360; easing.type: Easing.OutCubic } }
      }
      Canvas {
        id: dotCanvas
        visible: row.dots
        anchors.fill: parent
        property var samples: row.history
        property color tint: row.tint
        onSamplesChanged: requestPaint()
        onTintChanged: requestPaint()
        onWidthChanged: requestPaint()
        onPaint: {
          var ctx = getContext("2d"); ctx.reset()
          var pitch = 3, dot = 2, cols = Math.floor(width / pitch), rows = Math.max(1, Math.floor(height / pitch))
          var values = Array.isArray(samples) ? samples : []
          for (var c = 0; c < cols; c++) {
            // Newest sample on the right; older samples spread across the width.
            var index = values.length ? Math.floor(c * values.length / cols) : -1
            var raw = index >= 0 ? values[index] : null
            // A missing sample draws nothing; a real zero keeps the faint baseline.
            if (raw === null || typeof raw === "undefined" || !isFinite(Number(raw)) || Number(raw) < 0) continue
            var v = Number(raw)
            var lit = v > 0 ? Math.max(1, Math.round(Math.min(100, v) / 100 * rows)) : 0
            for (var r = 0; r < rows; r++) {
              var on = r < lit
              ctx.fillStyle = on ? Qt.rgba(tint.r, tint.g, tint.b, 0.55 + 0.45 * (r + 1) / rows)
                                 : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.08)
              ctx.fillRect(c * pitch, height - (r + 1) * pitch, dot, dot)
            }
          }
        }
      }
    }
    MouseArea {
      id: rowHelpTarget
      anchors.fill: parent
      hoverEnabled: row.tooltipText !== ""
      activeFocusOnTab: row.tooltipText !== ""
      acceptedButtons: Qt.NoButton
    }
    Ui.PanelToolTip {
      id: rowHelpTooltip
      visible: row.tooltipText !== "" && (rowHelpTarget.containsMouse || rowHelpTarget.activeFocus)
      text: row.tooltipText
      fontFamily: root.fontFamily
      width: Math.min(Style.space(300), Math.max(Style.space(180), row.width / 2))
      contentItem: Text {
        textFormat: Text.PlainText
        text: rowHelpTooltip.text
        color: rowHelpTooltip.panelForeground
        font.family: rowHelpTooltip.fontFamily
        font.pixelSize: rowHelpTooltip.fontSize
        width: rowHelpTooltip.width - Style.spacing.lg
        wrapMode: Text.WordWrap
        padding: Style.spacing.sm
      }
    }
  }

  component ChatLine: Item {
    id: chatLine
    property string role: "assistant"
    property string text: ""
    property var sources: []
    property string responseStatus: "complete"
    property bool streaming: false
    property bool copied: false
    // What the answer box shows. While an answer streams in, it catches up with `text` at
    // most every 80 ms (audit 2026-10-16): formatting and laying out the whole answer on
    // every token kept the shared shell busy on long answers. A finished answer shows at once.
    property string shownText: ""
    Component.onCompleted: shownText = text
    onTextChanged: {
      if (streaming && shownTextTimer.running) return
      shownText = text
      if (streaming) shownTextTimer.start()
    }
    onStreamingChanged: if (!streaming) { shownTextTimer.stop(); shownText = text }
    Timer {
      id: shownTextTimer
      interval: 80
      onTriggered: if (chatLine.shownText !== chatLine.text) { chatLine.shownText = chatLine.text; restart() }
    }
    width: parent ? parent.width : implicitWidth
    readonly property bool hasSources: role === "assistant" && Array.isArray(sources) && sources.length > 0
    readonly property bool canCopy: role === "assistant" && !streaming && text.length > 0
    function sourceSummary() {
      if (!Array.isArray(sources)) return ""
      return sources.map(function(item) {
        if (!item || typeof item.source !== "string") return ""
        var when = item.checkedAt ? new Date(item.checkedAt) : null
        var checked = when && !isNaN(when.getTime()) ? (" · checked " + ("0" + when.getHours()).slice(-2) + ":" + ("0" + when.getMinutes()).slice(-2)) : ""
        return item.source + checked
      }).filter(function(item) { return item !== "" }).join("  ·  ")
    }
    function copyAnswer() {
      if (!canCopy) return
      Quickshell.clipboardText = text
      copied = true
      copiedTimer.restart()
    }
    implicitHeight: header.height + (hasSources ? sourceLabel.implicitHeight + Style.spacing.xxs : 0) + Style.spacing.xs + messageText.contentHeight + Style.spacing.sm

    Timer {
      id: copiedTimer
      interval: 1400
      onTriggered: chatLine.copied = false
    }

    Item {
      id: header
      width: parent.width
      height: Math.max(roleLabel.implicitHeight, copyAnswerButton.visible ? copyAnswerButton.height : 0)

      Text {
        textFormat: Text.PlainText
        id: roleLabel
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: chatLine.role === "user" ? "YOU" : chatLine.role === "system" ? "NOVA · SYSTEM"
          : (chatLine.responseStatus === "incomplete" ? "NOVA · INCOMPLETE" : "NOVA")
        color: chatLine.role === "user" || chatLine.role === "system" ? root.muted
          : (chatLine.responseStatus === "incomplete" ? Commons.Color.urgent : root.accent)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
        font.letterSpacing: 1.1
      }

      Text {
        textFormat: Text.PlainText
        anchors.left: roleLabel.right
        anchors.leftMargin: Style.spacing.sm
        anchors.right: copyAnswerButton.left
        anchors.rightMargin: Style.spacing.xs
        anchors.verticalCenter: parent.verticalCenter
        visible: chatLine.copied
        text: "Copied"
        color: root.muted
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        horizontalAlignment: Text.AlignRight
        elide: Text.ElideRight
      }

      Ui.PanelActionButton {
        id: copyAnswerButton
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        visible: chatLine.role === "assistant"
        iconText: "󰆏"
        tooltipText: chatLine.copied ? "Copied" : "Copy answer"
        foreground: root.foreground
        hoverColor: root.accent
        bordered: true
        focusable: true
        enabled: chatLine.canCopy
        Accessible.name: "Copy answer"
        onClicked: chatLine.copyAnswer()
      }
    }

    Text {
      textFormat: Text.PlainText
      id: sourceLabel
      anchors.top: header.bottom
      anchors.topMargin: Style.spacing.xxs
      width: parent.width
      visible: chatLine.hasSources
      text: "Source: " + chatLine.sourceSummary()
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
    }

    TextEdit {
      id: messageText
      anchors.top: parent.top
      anchors.topMargin: header.height + (chatLine.hasSources ? sourceLabel.implicitHeight + Style.spacing.xxs : 0) + Style.spacing.xs
      width: parent.width
      height: contentHeight
      text: chatLine.role === "assistant" ? root.chatMarkdownHtml(chatLine.shownText) : chatLine.shownText
      readOnly: true
      selectByMouse: true
      persistentSelection: true
      wrapMode: TextEdit.Wrap
      textFormat: chatLine.role === "assistant" ? TextEdit.RichText : TextEdit.PlainText
      color: root.foreground
      selectionColor: Style.selectionFillFor(root.foreground, root.accent)
      selectedTextColor: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }

  Ui.KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(560))
    // The selector is real panel content. Grow the card by its readable height,
    // while KeyboardPanel keeps the result inside the active screen's safe area.
    contentHeight: panel.cappedContentHeight(root.basePanelHeight)

    Ui.PanelKeyCatcher {
      id: keyCatcher
      objectName: "panelKeyCatcher"
      anchors.fill: parent
      // When approval is pending, this catcher deliberately receives keys
      // before the chat field so Enter/Escape cannot submit chat or close the
      // panel instead of deciding the current server-owned request.
      blocked: chatInput.activeFocus && root.pendingApproval === null && root.cliConfirmModel === ""
      onCloseRequested: { if (!root.handleApprovalShortcut("deny") && !root.handleCliConfirmShortcut("cancel")) root.close() }
      onReturnRequested: { if (!root.handleApprovalShortcut("approve")) root.handleCliConfirmShortcut("launch") }
      onTabRequested: function(direction) {
        if (root.pendingApproval && (!root.pendingApproval.decision || root.pendingApproval.recovery)) root.focusApprovalControl(direction)
        else root.switchPanel(direction)
      }
      Keys.onReleased: function(event) { root.noteApprovalKeyReleased(event) }
      Shortcut { sequences: ["Alt+1"]; enabled: root.opened; onActivated: root.showView("chat") }
      Shortcut { sequences: ["Alt+2"]; enabled: root.opened; onActivated: root.showView("models") }
      Shortcut { sequences: ["Alt+3"]; enabled: root.opened; onActivated: root.showView("machine") }

      Item {
        id: layoutArea
        objectName: "layoutArea"
        anchors.fill: parent

        Column {
          id: fixedTop
          objectName: "fixedTop"
          anchors.top: parent.top
          anchors.left: parent.left
          anchors.right: parent.right
          spacing: Style.spacing.lg
  
          Item {
            id: statusHeader
            objectName: "statusHeader"
            width: parent.width
            height: Style.space(64)
            Column {
              width: parent.width
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.spacing.xxs
              Item {
                width: parent.width
                height: Math.max(novaRobot.height, novaTitle.implicitHeight, onlineIndicator.implicitHeight)
                NovaMark {
                  id: novaRobot
                  objectName: "novaRobot"
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  width: Math.round(Style.font.display * 1.15); height: width
                  accent: root.statusColor; foreground: root.foreground; background: Commons.Color.background
                  Behavior on accent { ColorAnimation { duration: 240 } }
                  // Slow breath at rest, quick pulse while NOVA works, still otherwise.
                  readonly property bool lively: root.opened && !root.coreReducedMotion
                  readonly property bool calm: lively && ["idle", "agent"].indexOf(root.coreMode) >= 0
                  readonly property bool busy: lively && ["preparing", "thinking", "writing", "working", "tool", "action", "switching"].indexOf(root.coreMode) >= 0
                  onCalmChanged: if (!calm && !busy) pulse = 1
                  onBusyChanged: if (!calm && !busy) pulse = 1
                  SequentialAnimation on pulse {
                    running: novaRobot.calm; loops: Animation.Infinite
                    NumberAnimation { to: 1.18; duration: 3000; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1; duration: 3000; easing.type: Easing.InOutSine }
                  }
                  SequentialAnimation on pulse {
                    running: novaRobot.busy; loops: Animation.Infinite
                    NumberAnimation { to: 1.35; duration: 500; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1; duration: 500; easing.type: Easing.InOutSine }
                  }
                }
                Text { textFormat: Text.PlainText; id: novaTitle; objectName: "novaTitle"; anchors.left: novaRobot.right; anchors.leftMargin: Style.spacing.md; anchors.verticalCenter: parent.verticalCenter; text: "NOVA"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.heading; font.bold: true; font.letterSpacing: 2.2 }
                Row {
                  id: onlineIndicator
                  objectName: "onlineIndicator"
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.spacing.sm
                  Rectangle {
                    width: Style.spacing.sm; height: width; radius: width / 2; color: root.statusColor; opacity: root.online ? 0.9 : 1
                    SequentialAnimation on opacity {
                      running: root.online
                      loops: Animation.Infinite
                      NumberAnimation { to: 0.35; duration: 1500; easing.type: Easing.InOutSine }
                      NumberAnimation { to: 0.9; duration: 1500; easing.type: Easing.InOutSine }
                    }
                  }
                  Text { textFormat: Text.PlainText; objectName: "onlineStatusText"; text: root.online ? "ONLINE" : (root.novaSetupPending ? "SETUP NEEDED" : "OFFLINE"); color: root.statusColor; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.2 }
                }
              }
              Item {
                width: parent.width
                height: Math.max(linkStatus.implicitHeight, uiBuildText.implicitHeight)
                Text {
                  textFormat: Text.PlainText
                  id: linkStatus
                  objectName: "linkStatus"
                  anchors.left: parent.left
                  width: Math.max(0, (updateLink.visible ? updateLink.x : uiBuildText.x) - Style.spacing.sm)
                  // One health signal (UX Phase 1): the header dot says ONLINE; this line speaks
                  // only when something is wrong. The core says what NOVA is doing.
                  text: root.online ? "" : (root.novaSetupPending ? "NOVA setup needed" : "NOVA is offline")
                  elide: Text.ElideRight
                  color: root.novaSetupPending ? root.muted : Commons.Color.urgent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.1
                }
                Text {
                  textFormat: Text.PlainText
                  id: updateLink
                  objectName: "updateLink"
                  anchors.right: uiBuildText.left
                  anchors.rightMargin: Style.spacing.sm
                  visible: text !== ""
                  text: root.updateLinkText()
                  color: root.updateStatus === "available" ? root.accent : root.muted
                  opacity: root.updateStatus !== "available" || root.updateAllowed ? 1 : 0.5
                  font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: root.updateStatus === "available"; font.letterSpacing: 1.1
                  MouseArea {
                    anchors.fill: parent
                    enabled: root.updateStatus === "available"
                    cursorShape: root.updateAllowed ? Qt.PointingHandCursor : Qt.ArrowCursor
                    Accessible.role: Accessible.Button
                    Accessible.name: "Update NOVA"
                    onClicked: root.runUpdate()
                  }
                }
                Text {
                  textFormat: Text.PlainText
                  id: uiBuildText
                  objectName: "uiBuildText"
                  anchors.right: parent.right
                  width: Math.min(implicitWidth, parent.width * 0.58)
                  text: root.displayUiIdentity
                  elide: Text.ElideLeft
                  color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption
                }
                MouseArea {
                  id: uiBuildHelp
                  anchors.fill: uiBuildText
                  hoverEnabled: true
                  acceptedButtons: root.updateSupported ? Qt.LeftButton : Qt.NoButton
                  cursorShape: root.updateSupported ? Qt.PointingHandCursor : Qt.ArrowCursor
                  activeFocusOnTab: true
                  Accessible.name: "NOVA UI " + root.displayUiIdentity
                  onClicked: root.checkForUpdates(true)
                  Keys.onReturnPressed: root.checkForUpdates(true)
                }
                Ui.PanelToolTip {
                  id: uiBuildTooltip
                  visible: uiBuildHelp.containsMouse || uiBuildHelp.activeFocus
                  text: root.displayUiIdentity + (root.displayUiIdentity.indexOf(" · ui-") >= 0 ? " · UI source fingerprint" : "")
                        + (root.updateSupported ? "\n" + root.updateTooltipText() : "")
                  width: Math.min(Style.space(320), Math.max(Style.space(180), statusHeader.width))
                  contentItem: Text {
                    textFormat: Text.PlainText
                    text: uiBuildTooltip.text
                    color: uiBuildTooltip.panelForeground
                    font.family: uiBuildTooltip.fontFamily
                    font.pixelSize: uiBuildTooltip.fontSize
                    width: uiBuildTooltip.width - Style.spacing.lg
                    wrapMode: Text.Wrap  // words first; a long build id still breaks
                    padding: Style.spacing.sm
                  }
                }
              }
              Item {
                width: parent.width
                height: brainLabel.implicitHeight + Style.space(4)
                Text {
                  textFormat: Text.PlainText
                  id: brainLabel
                  objectName: "brainLabel"
                  anchors.left: parent.left
                  width: parent.width  // full width, static: freshness lives in the footer and the core
                  anchors.verticalCenter: parent.verticalCenter
                  leftPadding: Style.space(5)
                  rightPadding: Style.space(17)
                  text: root.brainSummary()
                  elide: Text.ElideRight
                  color: root.accent
                  font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 0.8
                }
                Rectangle {
                  anchors.fill: brainLabel; anchors.topMargin: -Style.space(2); anchors.bottomMargin: -Style.space(2)
                  z: -1; radius: Style.space(2)
                  color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, brainHeaderTarget.containsMouse ? .16 : .06)
                  border.color: root.accent
                  border.width: brainHeaderTarget.activeFocus ? 2 : 1
                }
                Text {
                  textFormat: Text.PlainText
                  anchors.right: brainLabel.right; anchors.rightMargin: Style.space(4)
                  anchors.verticalCenter: brainLabel.verticalCenter
                  text: "›"
                  color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption
                }
                MouseArea {
                  id: brainHeaderTarget
                  objectName: "brainHeaderTarget"
                  anchors.fill: brainLabel
                  hoverEnabled: true
                  activeFocusOnTab: true
                  cursorShape: Qt.PointingHandCursor
                  Accessible.role: Accessible.Button
                  Accessible.name: root.brainIdentityText()
                  Accessible.description: "Show models"
                  onClicked: root.showView("models")
                  Keys.onReturnPressed: root.showView("models")
                  Keys.onEnterPressed: root.showView("models")
                  Keys.onSpacePressed: root.showView("models")
                }
                Ui.PanelToolTip {
                  id: brainIdentityTooltip
                  objectName: "brainIdentityTooltip"
                  visible: brainHeaderTarget.containsMouse || brainHeaderTarget.activeFocus
                  text: root.brainIdentityText()
                  width: Math.min(Style.space(320), Math.max(Style.space(180), statusHeader.width))
                  contentItem: Text {
                    textFormat: Text.PlainText
                    text: brainIdentityTooltip.text
                    color: brainIdentityTooltip.panelForeground
                    font.family: brainIdentityTooltip.fontFamily
                    font.pixelSize: brainIdentityTooltip.fontSize
                    width: brainIdentityTooltip.width - Style.spacing.lg
                    wrapMode: Text.WrapAnywhere
                    padding: Style.spacing.sm
                  }
                }
              }
            }
          }

          // UX Phase 2 tabs: words, clearer on first boot (Konstantin, 2026-10-09).
          Item {
            id: viewTabs
            objectName: "viewTabs"
            width: parent.width
            height: tabRow.implicitHeight
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) }
            Row {
              id: tabRow
              Repeater {
                objectName: "viewTabRepeater"
                model: [{ view: "chat", label: "CHAT", tip: "The conversation (Alt+1)" },
                        { view: "models", label: "MODELS", tip: "Installed models, downloads and online CLIs (Alt+2)" },
                        { view: "machine", label: "MACHINE", tip: "The core, GPU, memory and disk (Alt+3)" }]
                delegate: Item {
                  id: tab
                  objectName: "viewTab_" + modelData.view
                  readonly property bool current: root.shownView === modelData.view
                  readonly property string badge: modelData.view === "models" && root.newCatalogIds.length ? root.newCatalogIds.length + " NEW" : ""
                  readonly property bool warning: modelData.view === "machine" && root.machineWarning
                  readonly property bool updates: modelData.view === "models" && root.modelUpdatesAvailable > 0
                  implicitWidth: tabLabel.implicitWidth + (badge ? tabBadge.width + Style.spacing.xs : 0) + (warning || updates ? Style.space(10) : 0) + 2 * Style.space(14)
                  implicitHeight: tabLabel.implicitHeight + 2 * Style.spacing.sm
                  Text {
                    textFormat: Text.PlainText
                    id: tabLabel
                    x: Style.space(14); anchors.verticalCenter: parent.verticalCenter
                    text: modelData.label
                    color: tab.current ? root.accent : (tabArea.containsMouse ? root.foreground : root.muted)
                    font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.6
                  }
                  Rectangle {
                    id: tabBadge
                    visible: tab.badge !== ""
                    anchors.left: tabLabel.right; anchors.leftMargin: Style.spacing.xs; anchors.verticalCenter: tabLabel.verticalCenter
                    width: badgeText.implicitWidth + Style.space(6); height: badgeText.implicitHeight
                    color: root.accent
                    Text { textFormat: Text.PlainText; id: badgeText; anchors.centerIn: parent; text: tab.badge; color: Commons.Color.background; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.bold: true }
                  }
                  Rectangle {
                    visible: tab.warning
                    anchors.left: tabLabel.right; anchors.leftMargin: Style.space(5); anchors.verticalCenter: tabLabel.verticalCenter
                    width: Style.space(6); height: width; radius: width / 2; color: Commons.Color.urgent
                  }
                  Rectangle {
                    objectName: "modelsUpdateDot"
                    visible: tab.updates
                    anchors.left: tab.badge ? tabBadge.right : tabLabel.right; anchors.leftMargin: Style.space(5); anchors.verticalCenter: tabLabel.verticalCenter
                    width: Style.space(5); height: width; radius: width / 2; color: root.accent
                  }
                  Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 2; color: root.accent; visible: tab.current }
                  MouseArea {
                    id: tabArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    Accessible.role: Accessible.PageTab
                    Accessible.name: modelData.label
                    onClicked: root.showView(modelData.view)
                  }
                  Ui.PanelToolTip { visible: tabArea.containsMouse; text: modelData.tip + (tab.warning ? " · something is near its limit" : "") }
                }
              }
            }
          }

          Rectangle {
            id: previewNoticeCard
            visible: root.acceptancePreview
            width: parent.width
            height: visible ? previewNoticeContent.implicitHeight + Style.spacing.sm : 0
            color: Qt.rgba(Commons.Color.urgent.r, Commons.Color.urgent.g, Commons.Color.urgent.b, 0.09)
            border.width: 1
            border.color: Commons.Color.urgent
            Column {
              id: previewNoticeContent
              anchors.centerIn: parent
              width: parent.width - Style.spacing.md
              spacing: Style.spacing.xs
              Text {
                textFormat: Text.PlainText
                id: previewNotice
                width: parent.width
                text: root.acceptanceSimulation
                  ? "FIXTURE SIMULATION // NO LIVE ACTIONS // " + root.acceptanceSimulationStageLabel()
                  : "ACCEPTANCE PREVIEW // FIXTURE DATA // " + root.acceptancePreviewLabel.toUpperCase() + " // NO LIVE ACTIONS"
                color: Commons.Color.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 0.8
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
              }
              Row {
                visible: root.acceptanceSimulation
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Style.spacing.sm
                Ui.Button {
                  text: root.acceptanceSimulationFailure ? "OUTCOME: FAILURE" : "OUTCOME: SUCCESS"
                  tooltipText: root.acceptanceSimulationStage === "initial"
                    ? "Choose the simulated verification outcome"
                    : "Restart before changing the simulated outcome"
                  foreground: root.foreground
                  accent: root.acceptanceSimulationFailure ? Commons.Color.urgent : root.accent
                  bordered: true
                  focusable: true
                  enabled: root.acceptanceSimulationStage === "initial" && root.pendingApproval === null
                  Accessible.name: "Simulated acquisition outcome"
                  onClicked: root.toggleAcceptanceSimulationFailure()
                }
                Ui.Button {
                  text: "RESTART SIMULATION"
                  tooltipText: "Return to no installed model"
                  foreground: root.foreground
                  accent: root.accent
                  bordered: true
                  focusable: true
                  enabled: root.pendingApproval === null || !root.pendingApproval.decision
                  Accessible.name: "Restart onboarding simulation"
                  onClicked: root.resetAcceptanceSimulation()
                }
              }
            }
          }

          Rectangle {
            id: onboardingCard
            // Setup lives in CHAT where messages will be; once ready the MODELS tab replaces VIEW MODELS.
            visible: root.shownView === "chat" && root.onboardingState !== "READY"
            width: parent.width
            height: onboardingContent.implicitHeight + Style.spacing.md
            color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.055)
            border.width: 1
            border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.32)
            Column {
              id: onboardingContent
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.spacing.sm
              anchors.rightMargin: Style.spacing.sm
              spacing: Style.spacing.xxs
              Row {
                width: parent.width
                spacing: Style.spacing.sm
                Text { textFormat: Text.PlainText; id: onboardingTitleText; width: Math.max(0, parent.width - onboardingActionButton.width - parent.spacing); anchors.verticalCenter: parent.verticalCenter; text: root.onboardingTitle(); color: root.onboardingState === "READY" ? root.accent : (root.onboardingState.indexOf("FAILED") >= 0 || root.onboardingState.indexOf("RECOVERY") >= 0 ? Commons.Color.urgent : root.foreground); font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1; wrapMode: Text.Wrap }
                Ui.Button {
                  id: onboardingActionButton
                  objectName: "onboardingActionButton"
                  selected: true  // the one primary action of the card (UX Phase 1)
                  visible: root.onboardingActionLabel() !== ""
                  width: visible ? implicitWidth : 0
                  text: root.onboardingActionLabel()
                  tooltipText: text
                  foreground: root.foreground
                  accent: root.accent
                  bordered: true
                  focusable: true
                  enabled: visible && !(root.onboardingRequestInFlight && !root.onboardingRecheckQuiet) && !root.chatBusy && !root.modelTransitionActive && root.pendingApproval === null
                  Accessible.name: text
                  onClicked: root.performOnboardingAction()
                }
              }
              // The setup step below states the precise cause and remedy; the generic line would repeat it.
              Text { textFormat: Text.PlainText; objectName: "onboardingMessageText"; visible: setupStepBox.step === null; width: parent.width; text: root.onboardingMessage(); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
              Column {
                id: setupStepBox
                objectName: "setupStepBox"
                readonly property var step: root.setupStep()
                visible: step !== null
                width: parent.width
                spacing: Style.spacing.xxs
                Text { textFormat: Text.PlainText; objectName: "setupStepText"; width: parent.width; text: setupStepBox.step ? setupStepBox.step.text : ""; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                Ui.Button {
                  objectName: "setupStepButton"
                  text: setupStepBox.step ? setupStepBox.step.label : ""
                  foreground: root.foreground; accent: root.accent; bordered: true; focusable: setupStepBox.visible
                  Accessible.name: text
                  onClicked: root.runSetupStep()
                }
                Text {
                  textFormat: Text.PlainText
                  objectName: "setupStepFollow"
                  visible: root.setupLaunched !== "" && setupStepBox.step !== null && setupStepBox.step.id === root.setupLaunched
                  width: parent.width
                  text: "Follow the terminal window. When it shows Done, NOVA checks again by itself."
                  color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap
                }
              }
            }
          }

          Item {
            id: corePanel
            objectName: "corePanel"
            // UX Phase 2: a slim band above the conversation ("a little more alive"),
            // the big core in MACHINE. MODELS has none.
            visible: root.coreVisible
            width: parent.width
            // Short screens: the MACHINE core gives way first so the telemetry stays readable.
            readonly property real machineRoom: layoutArea.height - fixedBottom.height - Style.spacing.md - statusHeader.height - viewTabs.height
                                               - (previewNoticeCard.visible ? previewNoticeCard.height : 0) - telemetryFrame.implicitHeight - 4 * fixedTop.spacing
                                               - Style.space(96)  // leave a glimpse of IN GPU MEMORY below
            height: !visible ? 0 : root.shownView === "machine" ? Math.max(root.coreBandHeight, Math.min(Style.space(200), machineRoom)) : root.coreBandHeight
            Rectangle { anchors.fill: parent; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, root.online ? 0.045 : 0.025); border.width: 1; border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.38) }
            Text {
              textFormat: Text.PlainText
              id: coreStatus
              anchors.left: parent.left
              anchors.leftMargin: Style.spacing.md
              anchors.bottom: parent.bottom
              anchors.bottomMargin: Style.spacing.sm
              objectName: "coreStatus"
              width: Math.max(0, corePause.x - x - Style.spacing.sm)
              elide: Text.ElideRight
              text: "CORE // " + root.coreModeLabel
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1.2
            }
            Ui.Button {
              id: corePause
              objectName: "corePause"
              anchors.right: parent.right; anchors.rightMargin: Style.spacing.sm
              anchors.verticalCenter: coreStatus.verticalCenter
              text: root.coreMotionPaused ? "Resume" : "Pause"
              implicitHeight: Style.space(20)
              fontSize: Style.font.caption
              verticalPadding: 0
              visible: !root.coreReducedMotion && root.coreMode !== "offline"
              focusable: true
              tooltipText: (root.coreMotionPaused ? "Resume" : "Pause") + " decorative motion. Activity labels still update."
              onClicked: root.coreMotionPaused = !root.coreMotionPaused
            }
            Item {
              id: coreAnimationRegion
              objectName: "coreAnimationRegion"
              // The scene fills the band; inside it the dot field keeps root.coreAspect
              // (livingWave.uFieldW), so the band and MACHINE show the same wave.
              anchors.top: parent.top
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.bottom: coreStatus.top
              anchors.margins: Style.spacing.sm
              ShaderEffect {
                id: livingWave
                objectName: "livingWave"
                anchors.fill: parent
                // GPU living wave (UI-02C). Shares its body with previews/ui02c-wave.
                // Falls back to the Canvas wave below when shaders are unavailable.
                fragmentShader: Qt.resolvedUrl("wave.frag.qsb")
                property bool shaderFailed: GraphicsInfo.api === GraphicsInfo.Software
                readonly property bool active: !shaderFailed
                visible: active
                readonly property bool exposed: root.opened && root.coreVisible && active && width > 0 && height > 0
                readonly property bool frozen: ["error", "offline", "stale"].indexOf(root.coreMode) >= 0
                readonly property bool ticking: exposed && !root.coreMotionPaused && !root.coreReducedMotion
                                                && (!frozen || transitionAge < 1800)
                readonly property color themeAccent: root.accent
                readonly property color themeForeground: root.foreground
                // GPU load drives turbulence only while NOVA reports its own work.
                readonly property real gpuReading: {
                  root.clockTick
                  var sampledAt = root.hostWidget ? Number(root.hostWidget.gpuSampledAt) || 0 : 0
                  return root.gpuAvailable && root.valid(root.gpu) && Date.now() - sampledAt < 12000 ? root.ratio(root.gpu) : 0
                }
                property var current: null
                property var origin: null
                property var target: null
                property real transitionAge: 1800
                property real waveTime: 0
                property real gpuLevel: 0
                // UI-02D spikes (one per streamed chunk) and drops (idle/thinking): {t, x, z, s, echo}.
                property var drops: []
                property real lastSpike: -1
                // Konstantin's accepted playground settings (2026-10-05, previews/ui02d-wave).
                readonly property var tuning: ({maxSpikesPerSec: 25, spikeH: 30, spikeR: .1, attack: .2, decay: .8,
                                                writeEcho: .25, dropEcho: .7, echoSpeed: .45, dropGlow: 2,
                                                // Sunrise breath: a glow swells from the bottom centre and sinks back.
                                                lightI: .58, lightPerMinute: 3, lightW: 1, lightFar: .35, lightFlow: .5,
                                                dotDark: 1})
                property real lightAngle: 0
                // Light style in use (1 sunrise) and its level; a style change fades out, swaps, fades in.
                property int lightStyleNow: 1
                property real lightLevel: 1
                function wantedLightStyle() { return 1 }
                readonly property int dropSlots: 12
                property double lastTick: 0
                property int tickCount: 0
                property size uSize: Qt.size(width, height)
                property real uPhase: 0
                property real uTime: 0
                property vector3d uBase: Qt.vector3d(0, 0, 0)
                property vector3d uAccent: Qt.vector3d(0, 0, 0)
                property vector3d uCrest: Qt.vector3d(0, 0, 0)
                property real uOpacity: 1
                property real uEnergy: 0
                property real uCadence: 0
                property real uBreath: 0
                property real uBubbles: 0
                property real uSwell: 0
                property real uTurb: 0
                property vector3d uHorA: Qt.vector3d(0, 0, 0)
                property vector3d uHorB: Qt.vector3d(0, 0, 0)
                property real uHorI: 0
                property real uSweep: 0
                property real uPulse: 0
                property real uSpikeH: tuning.spikeH
                property real uSpikeR: tuning.spikeR
                property real uAttack: tuning.attack
                property real uDecay: tuning.decay
                property real uEcho: 0
                property real uEchoSpeed: tuning.echoSpeed
                property real uDropGlow: tuning.dropGlow
                property real uLightAngle: 0
                property real uLightI: 0
                property real uLightPath: 0
                property real uLightW: tuning.lightW
                property real uLightFar: tuning.lightFar
                property real uLightFlow: tuning.lightFlow
                property real uLightStyle: 1
                property real uDotGlow: 0
                property real uDotDark: tuning.dotDark
                property real uDotShift: 0
                property vector3d uDotShiftColor: Qt.vector3d(0, 0, 0)
                // The dots keep root.coreAspect; the horizon and light fill the width.
                property real uFieldW: Math.min(width, height * root.coreAspect)
                property vector4d uDA0: Qt.vector4d(-1, -1, -1, -1)
                property vector4d uDA1: Qt.vector4d(-1, -1, -1, -1)
                property vector4d uDA2: Qt.vector4d(-1, -1, -1, -1)
                property vector4d uDX0: Qt.vector4d(0, 0, 0, 0)
                property vector4d uDX1: Qt.vector4d(0, 0, 0, 0)
                property vector4d uDX2: Qt.vector4d(0, 0, 0, 0)
                property vector4d uDZ0: Qt.vector4d(0, 0, 0, 0)
                property vector4d uDZ1: Qt.vector4d(0, 0, 0, 0)
                property vector4d uDZ2: Qt.vector4d(0, 0, 0, 0)
                property vector4d uDS0: Qt.vector4d(0, 0, 0, 0)
                property vector4d uDS1: Qt.vector4d(0, 0, 0, 0)
                property vector4d uDS2: Qt.vector4d(0, 0, 0, 0)
                function mix(a, b, amount) {
                  if (!Array.isArray(a)) return a + (b - a) * amount
                  var out = []
                  for (var i = 0; i < a.length; i++) out.push(a[i] + (b[i] - a[i]) * amount)
                  return out
                }
                function rgb(c) { return [c.r * 255, c.g * 255, c.b * 255] }
                function modeTarget() {
                  // Mirrors modeTable() in previews/ui02c-wave/index.html; the idle, thinking and
                  // writing values for drops, spikes and surface come from previews/ui02d-wave.
                  var cool = mix([70,176,211], rgb(themeAccent), .15)
                  var violet = mix([157,115,209], rgb(themeForeground), .08)
                  var coral = mix([229,125,113], rgb(themeForeground), .08)
                  var magenta = mix([204,104,186], rgb(themeForeground), .06)
                  var amber = [240,182,96], peach = [249,207,163], red = [206,84,92]
                  var modes = {
                    idle: {accent:cool, crest:mix(cool,violet,.15), speed:.85, energy:.16, breath:1, bubbles:.32,
                           horA:cool, horB:mix(cool,violet,.35), horI:.2, dropRate:1.5, dropS:.1},
                    working: {accent:mix(cool,violet,.25), crest:violet, speed:1, energy:.42, breath:.6, bubbles:.1, swell:.15,
                              gpuGain:.8, horA:mix(cool,violet,.5), horB:violet, horI:.42},
                    agent: {accent:mix(cool,violet,.4), crest:mix(violet,magenta,.5), speed:.95, energy:.3, breath:.7, bubbles:.14, swell:.12,
                            gpuGain:1, horA:mix(cool,violet,.25), horB:mix(violet,magenta,.6), horI:.38},
                    preparing: {accent:mix(cool,violet,.25), crest:violet, speed:1.1, energy:.3, breath:.4, bubbles:.05, swell:1,
                                gpuGain:.4, horA:cool, horB:violet, horI:.5, sweep:1},
                    downloading: {accent:mix(cool,violet,.25), crest:violet, speed:1, energy:.26, breath:.5, bubbles:.05, swell:.8,
                                  horA:cool, horB:violet, horI:.45, sweep:1},
                    switching: {accent:violet, crest:mix(violet,coral,.25), speed:1.1, energy:.34, breath:.3, swell:.85,
                                gpuGain:.4, horA:violet, horB:mix(violet,coral,.35), horI:.5, sweep:1},
                    thinking: {accent:violet, crest:magenta, speed:1.3, energy:.6, breath:.3, bubbles:.08, swell:.2,
                               gpuGain:1, horA:violet, horB:magenta, horI:.5, dropRate:1.6, dropS:.2},
                    writing: {accent:coral, crest:mix(coral,peach,.35), speed:.9, energy:.36, breath:.2, swell:.1,
                              gpuGain:.25, horA:coral, horB:mix(coral,amber,.6), horI:.52, spikes:1},
                    tool: {accent:mix(cool,violet,.65), crest:violet, speed:1.1, cadence:.55, energy:.4, breath:.3,
                           gpuGain:.5, horA:mix(cool,violet,.6), horB:violet, horI:.4},
                    action: {accent:violet, crest:mix(violet,coral,.65), speed:1.2, cadence:.4, energy:.46, breath:.3,
                             gpuGain:.5, horA:violet, horB:coral, horI:.45},
                    waiting: {accent:cool, crest:cool, speed:0, energy:0, opacity:.65, horA:amber, horB:mix(amber,coral,.3),
                              horI:.48, pulse:1},
                    error: {accent:mix(cool,coral,.3), crest:coral, speed:0, energy:0, opacity:.75, horA:red,
                            horB:mix(red,coral,.4), horI:.3},
                    offline: {accent:cool, crest:cool, speed:0, energy:0, opacity:.38, horA:cool, horB:cool, horI:.04},
                    stale: {accent:cool, crest:mix(cool,violet,.2), speed:0, energy:0, opacity:.45, horA:cool,
                            horB:mix(cool,violet,.2), horI:.09}
                  }
                  var defaults = {base:cool, cadence:0, opacity:1, breath:0, bubbles:0, swell:0, gpuGain:0,
                                  horI:0, sweep:0, pulse:0, spikes:0, dropRate:0, dropS:0}
                  var m = modes[root.coreMode], out = {}
                  for (var key in defaults) out[key] = defaults[key]
                  for (key in m) out[key] = m[key]
                  return out
                }
                function retarget() {
                  origin = current
                  target = modeTarget()
                  transitionAge = 0
                  // Hidden panels wake directly in the latest state, without old work replay.
                  if (!current || !exposed || root.coreReducedMotion) {
                    current = target; transitionAge = 1800
                  }
                  push()
                }
                function advance(delta) {
                  transitionAge = Math.min(1800, transitionAge + delta)
                  if (transitionAge === 1800) { current = target; return }
                  var t = transitionAge / 1800
                  var ease = t*t*t*(t*(t*6-15)+10), next = {}
                  for (var key in target) next[key] = mix(origin[key], target[key], ease)
                  current = next
                }
                function addDrop(x, z, strength, echo, spike) {
                  var list = drops.slice()
                  if (list.length >= dropSlots) list.shift()
                  list.push({t: waveTime, x: x, z: z, s: strength, echo: echo, spike: spike})
                  drops = list
                }
                // One streamed chunk: a local spike at a random spot, rate-limited.
                function spawnSpike() {
                  if (!ticking || !target || !target.spikes) return
                  if (lastSpike >= 0 && waveTime - lastSpike < 1 / tuning.maxSpikesPerSec) return
                  lastSpike = waveTime
                  addDrop((Math.random() * 2 - 1) * .9, Math.random() * 2 - 1, .75 + Math.random() * .5, tuning.writeEcho, true)
                }
                // Idle and thinking: occasional random drops with an echo ring.
                function maybeDrop(delta) {
                  if (!target || !(target.dropRate > 0) || Math.random() >= target.dropRate * delta / 1000) return
                  addDrop((Math.random() * 2 - 1) * .85, Math.random() * 2 - 1, target.dropS * (.7 + Math.random() * .6), tuning.dropEcho, false)
                }
                function vec(c) { return Qt.vector3d(c[0] / 255, c[1] / 255, c[2] / 255) }
                function push() {
                  if (!current) return
                  var c = current, g = c.gpuGain * gpuLevel, A = [], X = [], Z = [], S = []
                  for (var i = 0; i < dropSlots; i++) {
                    var d = drops[i]
                    A.push(d ? waveTime - d.t : -1); X.push(d ? d.x : 0); Z.push(d ? d.z : 0); S.push(d ? d.s : 0)
                  }
                  uTime = waveTime
                  uBase = vec(c.base); uAccent = vec(c.accent); uCrest = vec(c.crest)
                  uOpacity = c.opacity; uEnergy = c.energy + g * .35; uCadence = c.cadence
                  uBreath = c.breath; uBubbles = c.bubbles; uSwell = c.swell; uTurb = g * .9
                  uHorA = vec(c.horA); uHorB = vec(c.horB); uHorI = c.horI; uPulse = c.pulse
                  uSweep = c.sweep
                  // The light breathes in every state; frozen states stop the clock, and
                  // dimmer states (offline, stale) dim it with their opacity.
                  uLightAngle = lightAngle; uLightStyle = lightStyleNow
                  uLightI = tuning.lightI * c.opacity * lightLevel
                  // The shader takes one echo strength: the newest drop's.
                  uEcho = drops.length ? drops[drops.length - 1].echo : 0
                  uDA0 = Qt.vector4d(A[0], A[1], A[2], A[3]); uDA1 = Qt.vector4d(A[4], A[5], A[6], A[7]); uDA2 = Qt.vector4d(A[8], A[9], A[10], A[11])
                  uDX0 = Qt.vector4d(X[0], X[1], X[2], X[3]); uDX1 = Qt.vector4d(X[4], X[5], X[6], X[7]); uDX2 = Qt.vector4d(X[8], X[9], X[10], X[11])
                  uDZ0 = Qt.vector4d(Z[0], Z[1], Z[2], Z[3]); uDZ1 = Qt.vector4d(Z[4], Z[5], Z[6], Z[7]); uDZ2 = Qt.vector4d(Z[8], Z[9], Z[10], Z[11])
                  uDS0 = Qt.vector4d(S[0], S[1], S[2], S[3]); uDS1 = Qt.vector4d(S[4], S[5], S[6], S[7]); uDS2 = Qt.vector4d(S[8], S[9], S[10], S[11])
                }
                onStatusChanged: if (status === ShaderEffect.Error) {
                  console.warn("NOVA living wave shader failed; using Canvas fallback: " + log)
                  shaderFailed = true
                }
                onExposedChanged: {
                  lastTick = 0
                  if (exposed) {
                    current = modeTarget(); target = current; transitionAge = 1800
                    lightStyleNow = wantedLightStyle(); lightLevel = 1
                    push(); compileGuard.restart()
                  }
                }
                onTickingChanged: lastTick = 0
                onThemeAccentChanged: if (current) retarget()
                onThemeForegroundChanged: if (current) retarget()
                Connections {
                  target: root
                  function onCoreModeChanged() { livingWave.retarget() }
                  function onCoreReducedMotionChanged() { livingWave.retarget() }
                  function onWaveChunk() { livingWave.spawnSpike() }
                }
                Component.onCompleted: retarget()
                Timer {
                  // A shader that never compiles must not leave an empty core.
                  id: compileGuard
                  interval: 2000
                  onTriggered: if (livingWave.exposed && livingWave.status !== ShaderEffect.Compiled) {
                    console.warn("NOVA living wave shader did not compile; using Canvas fallback")
                    livingWave.shaderFailed = true
                  }
                }
                Timer {
                  interval: 33; repeat: true; running: livingWave.ticking
                  onTriggered: {
                    var wave = livingWave, now = Date.now()
                    var delta = wave.lastTick ? Math.max(0, Math.min(60, now - wave.lastTick)) : 0
                    wave.lastTick = now
                    if (wave.transitionAge < 1800) wave.advance(delta)
                    wave.waveTime += delta / 1000
                    wave.gpuLevel += (wave.gpuReading - wave.gpuLevel) * (1 - Math.exp(-delta / 900))
                    wave.uPhase += delta / 1000 * wave.current.speed * (1 + wave.current.gpuGain * wave.gpuLevel * .5)
                    wave.maybeDrop(delta)
                    var style = wave.wantedLightStyle(), perMinute = wave.tuning.lightPerMinute
                    wave.lightAngle = (wave.lightAngle + delta / 1000 * perMinute / 60 * Math.PI * 2) % (Math.PI * 4)
                    wave.lightLevel += ((style === wave.lightStyleNow ? 1 : 0) - wave.lightLevel) * (1 - Math.exp(-delta / 350))
                    if (style !== wave.lightStyleNow && wave.lightLevel < .03) wave.lightStyleNow = style
                    var life = wave.tuning.attack + wave.tuning.decay * 6 + 1.2
                    if (wave.drops.length && wave.waveTime - wave.drops[0].t > life)
                      wave.drops = wave.drops.filter(function(d) { return wave.waveTime - d.t <= life })
                    wave.push()
                    wave.tickCount++
                  }
                }
              }
              Canvas {
                id: pixelWave
                objectName: "pixelWave"
                anchors.fill: parent
                visible: !livingWave.active
                // 319 marks, capped at 30 Hz. No timers while hidden/paused/still.
                readonly property bool exposed: root.opened && root.coreVisible && visible && width > 0 && height > 0
                readonly property bool stopped: ["error", "offline", "stale", "waiting"].indexOf(root.coreMode) >= 0
                readonly property bool ticking: exposed && !root.coreMotionPaused && !root.coreReducedMotion
                                                && (!stopped || transitionAge < 1800)
                readonly property color themeAccent: root.accent
                readonly property color themeForeground: root.foreground
                property var current: null
                property var origin: null
                property var target: null
                property real transitionAge: 1800
                property real phase: 0
                property double lastTick: 0
                property int paintCount: 0
                property int tickCount: 0
                function mix(a, b, amount) {
                  var out = []
                  for (var i = 0; i < a.length; i++) out.push(a[i] + (b[i] - a[i]) * amount)
                  return out
                }
                function rgb(c) { return [c.r * 255, c.g * 255, c.b * 255] }
                function modeTarget() {
                  // Approved decorative palette, gently adapted to the current theme.
                  var cool = mix([70,176,211], rgb(themeAccent), .15)
                  var violet = mix([157,115,209], rgb(themeForeground), .08)
                  var coral = mix([229,125,113], rgb(themeForeground), .08)
                  var modes = {
                    idle:[cool,mix(cool,violet,.15),.75,0,1,.07],
                    working:[mix(cool,violet,.25),violet,1,0,1,.42],
                    agent:[mix(cool,violet,.4),violet,1,0,1,.42],
                    preparing:[mix(cool,violet,.25),violet,1,0,1,.42],
                    downloading:[mix(cool,violet,.25),violet,1,0,1,.4],
                    switching:[violet,mix(violet,coral,.25),1.1,0,1,.53],
                    thinking:[violet,coral,1.3,0,1,.82],
                    writing:[coral,mix(coral,[249,207,163],.35),2.2,0,1,.67],
                    tool:[mix(cool,violet,.65),violet,1.1,.55,1,.4],
                    action:[violet,mix(violet,coral,.65),1.2,.4,1,.46],
                    waiting:[cool,cool,0,0,.65,0],
                    error:[mix(cool,coral,.3),coral,0,0,.75,0],
                    offline:[cool,cool,0,0,.38,0],
                    stale:[cool,mix(cool,violet,.2),0,0,.45,0]
                  }
                  var m = modes[root.coreMode]
                  return {base:cool,accent:m[0],crest:m[1],speed:m[2],cadence:m[3],opacity:m[4],energy:m[5]}
                }
                function retarget() {
                  origin = current
                  target = modeTarget()
                  transitionAge = 0
                  // Hidden panels wake directly in the latest state, without old work replay.
                  if (!current || !exposed || root.coreReducedMotion) {
                    current = target; transitionAge = 1800
                  }
                  if (exposed) requestPaint()
                }
                function advance(delta) {
                  transitionAge = Math.min(1800, transitionAge + delta)
                  if (transitionAge === 1800) { current = target; return }
                  var t = transitionAge / 1800
                  var ease = t*t*t*(t*(t*6-15)+10), next = {}
                  for (var key in target) next[key] = Array.isArray(target[key])
                    ? mix(origin[key],target[key],ease) : origin[key]+(target[key]-origin[key])*ease
                  current = next
                }
                onExposedChanged: {
                  lastTick = 0
                  if (exposed) { current = modeTarget(); target = current; transitionAge = 1800; requestPaint() }
                }
                onTickingChanged: lastTick = 0
                onThemeAccentChanged: if (current) retarget()
                onThemeForegroundChanged: if (current) retarget()
                onWidthChanged: if (exposed) requestPaint()
                onHeightChanged: if (exposed) requestPaint()
                Connections {
                  target: root
                  function onCoreModeChanged() { pixelWave.retarget() }
                  function onCoreReducedMotionChanged() { pixelWave.retarget() }
                }
                Component.onCompleted: retarget()
                Timer {
                  interval: 33; repeat: true; running: pixelWave.ticking
                  onTriggered: {
                    var now = Date.now()
                    var delta = pixelWave.lastTick ? Math.max(0, Math.min(60, now - pixelWave.lastTick)) : 0
                    pixelWave.lastTick = now
                    if (pixelWave.transitionAge < 1800) pixelWave.advance(delta)
                    if (!pixelWave.stopped) pixelWave.phase += delta / 1000 * pixelWave.current.speed
                    pixelWave.tickCount++
                    pixelWave.requestPaint()
                  }
                }
                onPaint: {
                  if (!exposed || !current) return
                  var ctx = getContext("2d")
                  ctx.clearRect(0,0,width,height)
                  var unit = Math.min((width-28)/28,18)
                  var scaleY = Math.min(1, height/100)
                  var base = height*.68, energy = current.energy
                  for (var row=0; row<11; row++) {
                    var depth=row/10, spread=.38+depth*.62
                    for (var col=0; col<29; col++) {
                      var x=(col-14)/14, z=depth*2-1
                      var distance=Math.sqrt(x*x*1.69+z*z*.7056)
                      var broad=Math.sin(distance*5.6-phase*.7)*1.5
                      var centre=.28+Math.sin(phase*.24)*.18
                      var dx=x-centre, dz=z+.08
                      var cluster=Math.exp(-(dx*dx/.15+dz*dz/.26))
                      var sx=x+.42+Math.sin(phase*.19)*.12, sz=z-.24
                      var shoulder=Math.exp(-(sx*sx/.13+sz*sz/.3))
                      var peak=Math.min(1,cluster+shoulder*.65)
                      var moving=Math.sin(x*8-phase+z*5)
                      var cadence=1-current.cadence*(.5-.5*Math.sin(phase*2))
                      var rise=broad*(.3+energy)+energy*cadence*(35*peak+4.5*moving)
                      var px=width/2+x*unit*14*spread
                      var py=base-(depth*39+rise)*scaleY
                      var slope=mix(current.base,current.accent,.12+peak*.75)
                      var tint=mix(slope,current.crest,peak*peak*energy*.9)
                      ctx.fillStyle=Qt.rgba(tint[0]/255,tint[1]/255,tint[2]/255,
                        current.opacity*(.22+depth*.18+energy*(.18+peak*.32)))
                      var mark=1.1+depth*.9
                      ctx.fillRect(Math.round(px),Math.round(py),mark*2.2,mark)
                    }
                  }
                  if (root.coreMode === "error") {
                    ctx.fillStyle = Qt.rgba(current.crest[0]/255,current.crest[1]/255,current.crest[2]/255,.68)
                    for (var i=0;i<9;i++) ctx.fillRect(width/2-30+i*8,base-7*scaleY,3,2)
                  }
                  paintCount++
                }
              }

            }
          }

          // Framed like btop: title and temperature sit in the top border line.
          Item {
            id: telemetryFrame
            objectName: "telemetryFrame"
            readonly property color line: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.22)
            readonly property real inset: Style.spacing.sm
            readonly property bool hasTemperature: root.valid(root.systemTemperature)
            visible: root.shownView === "machine"
            width: parent.width
            implicitHeight: telemetryTitle.implicitHeight + telemetryGrid.implicitHeight + inset
            height: visible ? implicitHeight : 0
            Rectangle { x: 0; y: telemetryTitle.implicitHeight / 2; width: telemetryFrame.inset; height: 1; color: telemetryFrame.line }
            Text { textFormat: Text.PlainText; id: telemetryTitle; objectName: "telemetryTitle"; x: telemetryFrame.inset * 1.5; text: "MACHINE"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.5 }
            Rectangle { x: telemetryTitle.x + telemetryTitle.implicitWidth + telemetryFrame.inset / 2; y: telemetryTitle.implicitHeight / 2; height: 1; color: telemetryFrame.line
                        width: Math.max(0, (telemetryFrame.hasTemperature ? telemetryTemperature.x - telemetryFrame.inset / 2 : telemetryFrame.width) - x) }
            Text {
              textFormat: Text.PlainText
              id: telemetryTemperature
              objectName: "telemetryTemperature"
              visible: telemetryFrame.hasTemperature
              x: telemetryFrame.width - implicitWidth - telemetryFrame.inset * 1.5
              text: (root.temperatureIsNvme() ? "SSD " : "TEMP ") + root.celsius(root.systemTemperature)
              color: root.systemTemperature >= 80 ? root.loadTint(root.systemTemperature >= 90 ? 1 : 0.9) : root.muted
              font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 0.8
            }
            Rectangle { visible: telemetryFrame.hasTemperature; x: telemetryTemperature.x + telemetryTemperature.implicitWidth + telemetryFrame.inset / 2; y: telemetryTitle.implicitHeight / 2; width: telemetryFrame.inset; height: 1; color: telemetryFrame.line }
            Rectangle { x: 0; y: telemetryTitle.implicitHeight / 2; width: 1; height: parent.height - y; color: telemetryFrame.line }
            Rectangle { x: parent.width - 1; y: telemetryTitle.implicitHeight / 2; width: 1; height: parent.height - y; color: telemetryFrame.line }
            Rectangle { x: 0; y: parent.height - 1; width: parent.width; height: 1; color: telemetryFrame.line }
            Column {
              id: telemetryGrid
              objectName: "telemetryGrid"
              x: telemetryFrame.inset * 1.5; y: telemetryTitle.implicitHeight + Style.spacing.xxs
              width: telemetryFrame.width - 3 * telemetryFrame.inset
              spacing: Style.spacing.xs
              TelemetryRow { objectName: "telemetryGpu"; visible: root.gpuAvailable && root.valid(root.gpu); label: "GPU"; dots: true; value: root.percent(root.gpu); fraction: root.ratio(root.gpu); history: root.gpuHistory; tooltipText: root.gpuActivityTooltip() }
              TelemetryRow { objectName: "telemetryGpuMemory"; visible: root.valid(root.vramUsed) && root.valid(root.vramTotal) && root.vramTotal > 0; label: "GPU MEM"; value: root.vramUsed.toFixed(1) + " / " + root.gib(root.vramTotal); fraction: visible ? root.vramUsed / root.vramTotal : 0; tooltipText: root.gpuMemoryTooltip() }
              TelemetryRow { objectName: "telemetryCpu"; visible: root.valid(root.cpu); label: "CPU"; dots: true; value: root.percent(root.cpu); fraction: root.ratio(root.cpu); history: root.cpuHistory; tooltipText: "How busy the processor is." }
              TelemetryRow { objectName: "telemetryMemory"; visible: root.valid(root.memoryUsed) && root.valid(root.memoryTotal); label: "RAM"; value: root.memoryUsed.toFixed(1) + " / " + root.gib(root.memoryTotal); fraction: root.ratio(root.memory); tooltipText: "System memory in use. Without a graphics card, models run here." }
              TelemetryRow { objectName: "telemetryDisk"; visible: root.valid(root.disk); label: "DISK"; value: root.freeSpace(root.diskUsed, root.diskTotal); fraction: root.ratio(root.disk); tooltipText: "Disk " + root.diskMount + ": " + root.gib(root.diskUsed) + " of " + root.gib(root.diskTotal) + " used. Models need free space to download." }
              TelemetryRow { objectName: "telemetryNetDown"; visible: root.valid(root.netDown); label: "NET ↓"; dots: true; value: root.rateLabel(root.netDown); fraction: 0; history: root.netDownRaw.map(function(v) { return 100 * v / root.netPeak }); tooltipText: "Download speed of this computer's network adapters (Wi-Fi and wired)." }
              TelemetryRow { objectName: "telemetryNetUp"; visible: root.valid(root.netUp); label: "NET ↑"; dots: true; value: root.rateLabel(root.netUp); fraction: 0; history: root.netUpRaw.map(function(v) { return 100 * v / root.netPeak }); tooltipText: "Upload speed of this computer's network adapters (Wi-Fi and wired)." }
              Text { textFormat: Text.PlainText; visible: !root.valid(root.cpu) && !root.valid(root.memory) && !root.valid(root.disk) && !root.gpuAvailable; text: "Machine readings unavailable"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
            }
          }


        }

        Item {
          id: approvalForegroundSpace
          anchors.top: fixedTop.bottom
          anchors.topMargin: Style.spacing.md
          anchors.bottom: fixedBottom.top
          anchors.bottomMargin: Style.spacing.md
          anchors.left: parent.left
          anchors.right: parent.right
          visible: root.approvalForeground
            Item {
              id: removeNovaCard
              objectName: "removeNovaCard"
              width: parent.width
              visible: root.removeNovaOpen && root.pendingApproval === null
              height: Math.min(parent.height, removeNovaColumn.implicitHeight + 2 * Style.spacing.sm)
              readonly property color red: root.destructiveColor
              readonly property string blocker: root.removeNovaBlocker()
              Keys.onEscapePressed: function(event) { root.removeNovaSure ? root.removeNovaSure = false : root.closeRemoveNova(); event.accepted = true }
              onVisibleChanged: if (visible) forceActiveFocus()
              Rectangle { anchors.fill: parent; color: Qt.rgba(removeNovaCard.red.r, removeNovaCard.red.g, removeNovaCard.red.b, 0.05); border.width: 1; border.color: removeNovaCard.red }
              Flickable {
                anchors.fill: parent; anchors.margins: Style.spacing.sm
                clip: true; contentWidth: width; contentHeight: removeNovaColumn.implicitHeight; boundsBehavior: Flickable.StopAtBounds; interactive: contentHeight > height
                Column {
                  id: removeNovaColumn
                  width: parent.width
                  spacing: Style.spacing.xs
                  Text { textFormat: Text.PlainText; objectName: "removeNovaTitle"; text: "REMOVE NOVA FROM THIS COMPUTER"; color: removeNovaCard.red; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.4 }
                  Text { textFormat: Text.PlainText; visible: !root.removeNovaSure; width: parent.width; wrapMode: Text.Wrap; text: "Choose what goes. Nothing is removed until you confirm."; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                  Repeater {
                    model: root.removeNovaSure ? root.removeNovaChoices.filter(function(c) { return c.id === root.removeNovaChoice }) : root.removeNovaChoices
                    Rectangle {
                      required property var modelData
                      objectName: "removeNovaChoice"
                      readonly property string choiceId: modelData.id
                      readonly property bool chosen: root.removeNovaChoice === modelData.id
                      width: removeNovaColumn.width; height: choiceText.implicitHeight + 2 * Style.spacing.xs
                      color: chosen ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.05) : "transparent"
                      border.width: 1; border.color: chosen ? root.foreground : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.18)
                      Rectangle {
                        x: Style.spacing.xs; y: Style.spacing.xs + 3; width: 11; height: 11; radius: 6
                        color: "transparent"; border.width: 1; border.color: parent.chosen ? root.foreground : root.muted
                        Rectangle { visible: parent.parent.chosen; anchors.centerIn: parent; width: 5; height: 5; radius: 3; color: root.foreground }
                      }
                      Column {
                        id: choiceText
                        x: Style.spacing.xs + 20; y: Style.spacing.xs; width: parent.width - x - choiceFrees.implicitWidth - Style.spacing.sm
                        Text { textFormat: Text.PlainText; text: modelData.title; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true }
                        Text { textFormat: Text.PlainText; visible: !root.removeNovaSure; width: parent.width; wrapMode: Text.Wrap; text: modelData.text; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                      }
                      Text { id: choiceFrees; textFormat: Text.PlainText; anchors.right: parent.right; anchors.rightMargin: Style.spacing.xs; y: Style.spacing.xs + 2; text: "frees " + root.removeNovaFrees(modelData.id); color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                      MouseArea { anchors.fill: parent; enabled: !root.removeNovaSure; cursorShape: Qt.PointingHandCursor
                                  Accessible.role: Accessible.RadioButton; Accessible.name: modelData.title + ": " + modelData.text
                                  onClicked: root.removeNovaChoice = modelData.id }
                    }
                  }
                  Column {
                    objectName: "removeNovaModels"
                    visible: !root.removeNovaSure && root.removeNovaChoice !== "nova" && root.discoveredModels.length > 0
                    width: parent.width; spacing: 1; leftPadding: Style.spacing.sm
                    Text { textFormat: Text.PlainText; text: "Models that go"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
                    Repeater {
                      model: root.discoveredModels
                      Row {
                        required property var modelData
                        spacing: Style.spacing.xs
                        Text { textFormat: Text.PlainText; text: root.modelPlainName(modelData.id); color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                        Text { textFormat: Text.PlainText; text: modelData.metadata && modelData.metadata.size_bytes > 0 ? "· " + root.bytesLabel(modelData.metadata.size_bytes) : ""; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                        FactWord { objectName: "removeNovaNotFromNova"; anchors.verticalCenter: parent.verticalCenter; text: root.modelFromNova(modelData) ? "" : "NOT FROM NOVA"; tone: root.cliTightColor }
                      }
                    }
                  }
                  Text { textFormat: Text.PlainText; objectName: "removeNovaNote"; visible: !root.removeNovaSure; width: parent.width; wrapMode: Text.Wrap
                         text: (root.removeNovaChoice === "everything" ? "Your password is asked once, in the terminal, to remove Ollama and its model folder." : "No password needed.")
                           + " OpenCode stays (Omarchy installed it); NOVA's settings for it go."
                         color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                  Text { textFormat: Text.PlainText; objectName: "removeNovaSureText"; visible: root.removeNovaSure; width: parent.width; wrapMode: Text.Wrap
                         text: "Are you sure? This removes " + (root.removeNovaChoice === "everything" ? "NOVA, " + root.discoveredModels.length + (root.discoveredModels.length === 1 ? " model" : " models") + " and Ollama"
                           : root.removeNovaChoice === "models" ? "NOVA and " + root.discoveredModels.length + (root.discoveredModels.length === 1 ? " model" : " models") : "NOVA") + " for good."
                           + (root.removeNovaChoice !== "nova" && root.discoveredModels.length ? " Getting the models back means downloading " + root.removeNovaFrees("models") + " again." : "")
                           + " A terminal opens and shows each step."
                         color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                  Text { textFormat: Text.PlainText; objectName: "removeNovaBlocker"; visible: removeNovaCard.blocker !== ""; width: parent.width; wrapMode: Text.Wrap; text: removeNovaCard.blocker; color: root.cliTightColor; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                  Item {
                    width: parent.width; height: removeNovaCancel.height + Style.spacing.xs
                    CompactModelButton { id: removeNovaCancel; objectName: "removeNovaCancel"; y: Style.spacing.xs; text: root.removeNovaSure ? "KEEP NOVA" : "CANCEL"; foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                                         onClicked: root.removeNovaSure ? root.removeNovaSure = false : root.closeRemoveNova() }
                    CompactModelButton {
                      objectName: "removeNovaGo"
                      anchors.right: parent.right; y: Style.spacing.xs
                      text: !root.removeNovaSure ? "REMOVE…" : (root.removeNovaChoice === "everything" ? "REMOVE EVERYTHING" : root.removeNovaChoice === "models" ? "REMOVE NOVA AND MODELS" : "REMOVE NOVA")
                      foreground: removeNovaCard.red; accent: removeNovaCard.red; bordered: true; focusable: true
                      enabled: removeNovaCard.blocker === ""
                      opacity: enabled ? 1 : 0.5
                      Accessible.name: text
                      onClicked: root.removeNovaSure ? root.launchRemoveNova() : root.removeNovaSure = true
                    }
                  }
                }
              }
            }
            Item {
              id: approvalCard
              objectName: "approvalCard"
              width: parent.width
              visible: root.pendingApproval !== null
              implicitHeight: approvalColumn.implicitHeight + approvalDecisionRow.implicitHeight + Style.spacing.sm * 4
              height: parent.height
              Rectangle { anchors.fill: parent; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.08); border.width: 1; border.color: root.accent }
              Flickable {
                id: approvalDetailsViewport
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: approvalDecisionRow.top
                anchors.margins: Style.spacing.sm
                contentWidth: width
                contentHeight: approvalColumn.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                interactive: contentHeight > height
                Column {
                id: approvalColumn; width: parent.width; spacing: Style.spacing.xs
                Text { textFormat: Text.PlainText; objectName: "approvalTitle"; text: root.pendingApproval && root.pendingApproval.recovery ? "RECOVERY REVIEW" : (root.pendingApproval && root.pendingApproval.coding_cli ? "CODING CLI APPROVAL" : "") || (root.pendingApproval && root.pendingApproval.removal ? "DELETE APPROVAL" : (root.pendingApproval && root.pendingApproval.update ? "UPDATE APPROVAL" : (root.pendingApproval && root.pendingApproval.acquisition ? "INSTALL APPROVAL" : "ACTION REQUEST"))); color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.2 }
                Text { textFormat: Text.PlainText; text: root.pendingApproval ? root.pendingApproval.summary : "ACTION"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true; width: parent.width; wrapMode: Text.Wrap }
                // 0.15: a coding CLI change lists exactly what changes, then how it fits.
                Text { textFormat: Text.PlainText; objectName: "approvalChanges"; visible: !!(root.pendingApproval && root.pendingApproval.coding_cli && Array.isArray(root.pendingApproval.changes) && root.pendingApproval.changes.length)
                       width: parent.width; wrapMode: Text.Wrap
                       text: visible ? root.pendingApproval.changes.map(function(line, index) { return (index + 1) + ". " + line }).join("\n") : ""
                       color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption; lineHeight: 1.15 }
                Text { textFormat: Text.PlainText; objectName: "approvalVerdict"; visible: !!(root.pendingApproval && root.pendingApproval.coding_cli && root.pendingApproval.verdict && root.pendingApproval.verdict.text)
                       width: parent.width; wrapMode: Text.Wrap; font.bold: true
                       text: visible ? root.pendingApproval.verdict.text : ""
                       color: visible ? root.verdictColor(root.pendingApproval.verdict.level, root.pendingApproval.verdict.past_native) : root.muted
                       font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Text { visible: !!(root.pendingApproval && root.pendingApproval.recovery === true); width: parent.width; text: root.recoveryAssessmentText(); textFormat: Text.PlainText; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; wrapMode: Text.Wrap }
                Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.recovery === true); width: parent.width; text: "Check other terminals, model clients and automation for downloads. Empty model residency does not establish download inactivity. Do not confirm if uncertain."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.target); text: "TARGET  " + (root.pendingApproval ? root.pendingApproval.target : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.process); text: "PROCESS  " + (root.pendingApproval ? root.pendingApproval.process : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.pid); text: "PID  " + (root.pendingApproval ? root.pendingApproval.pid : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.owner); text: "OWNER  " + (root.pendingApproval ? root.pendingApproval.owner : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.application); text: "APPLICATION  " + (root.pendingApproval ? root.pendingApproval.application : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.workspace !== undefined); text: "WORKSPACE: " + (root.pendingApproval ? root.pendingApproval.workspace : "") + " · STAY HERE"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                // UX Phase 1: what, how big and whether it fits come first; every fact of the
                // frozen preflight stays one click away (disclosed, not removed).
                Text { textFormat: Text.PlainText; objectName: "approvalSummaryLine"; visible: !!(root.pendingApproval && (root.pendingApproval.acquisition || root.pendingApproval.removal)); width: parent.width; wrapMode: Text.Wrap
                       text: !root.pendingApproval ? "" : root.pendingApproval.removal ? root.removalApprovalLine(root.pendingApproval) : root.pendingApproval.update ? "What changed: " + root.pendingApproval.whatChanged + " · download " + root.pendingApproval.download + " · " + root.approvalFitShort(root.pendingApproval.fit) + " · " + root.bytesLabel(root.pendingApproval.availableSpace) + " free" : root.bytesLabel(root.pendingApproval.size) + " on disk · " + root.approvalFitShort(root.pendingApproval.fit) + " · " + root.bytesLabel(root.pendingApproval.availableSpace) + " free"
                             + (root.pendingApproval.trust === "user_requested" ? " · not in NOVA's catalog" : "")
                       color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Text { textFormat: Text.PlainText; objectName: "approvalDetailsToggle"; visible: !!(root.pendingApproval && root.pendingApproval.acquisition); text: root.approvalDetailsOpen ? "[ HIDE DETAILS ]" : "[ DETAILS ]"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 0.7
                       MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.approvalDetailsOpen = !root.approvalDetailsOpen } }
                Column { objectName: "approvalDetails"; visible: root.approvalDetailsOpen || !(root.pendingApproval && root.pendingApproval.acquisition); width: parent.width; spacing: Style.spacing.xs
                  // Plain words for a first-time user; every fact of the frozen preflight stays disclosed.
                  Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.acquisition); text: "DOWNLOADED BY  " + (root.pendingApproval ? root.providerPlainName(root.pendingApproval.provider) : "unknown"); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                  Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.acquisition); text: "SIZE ON DISK  " + (root.pendingApproval ? root.bytesLabel(root.pendingApproval.size) : "unknown") + " (the download itself may differ a little)"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                  Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.acquisition); text: "FREE SPACE NEEDED  " + (root.pendingApproval ? root.bytesLabel(root.pendingApproval.requiredSpace) : "unknown") + ", including a safety margin"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                  Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.acquisition); text: "FREE SPACE NOW  " + (root.pendingApproval ? root.bytesLabel(root.pendingApproval.availableSpace) : "unknown") + " (measured when this request was made)"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                  Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.acquisition); text: "FIT  " + (root.pendingApproval ? root.approvalFitText(root.pendingApproval.fit) : "Not estimated"); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                  Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.acquisition); text: "SOURCE  " + root.approvalSourceText(root.pendingApproval ? root.pendingApproval.trust : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                  Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.acquisition && root.pendingApproval.provenance); text: "DETAILS  " + (root.pendingApproval ? root.pendingApproval.provenance : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                  Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.acquisition); text: "AFTER INSTALL  Nothing starts by itself; choose LOAD AND USE when you want it"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                  Text { textFormat: Text.PlainText; visible: !!(root.pendingApproval && root.pendingApproval.acquisition); text: "CANCEL  Not possible once the download has started"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                }
                Text { textFormat: Text.PlainText; text: "WHAT HAPPENS  " + (root.pendingApproval ? root.pendingApproval.impact : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; width: parent.width }
                }
              }
                Column {
                  id: approvalDecisionRow
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.bottom: parent.bottom
                  anchors.margins: Style.spacing.sm
                  spacing: Style.spacing.xs
                  Item {
                    id: recoveryConfirmationControl
                    objectName: "recoveryConfirmationControl"
                    width: parent.width
                    height: recoveryConfirmLabel.implicitHeight + Style.spacing.sm * 2
                    visible: !!(root.pendingApproval && root.pendingApproval.recovery === true && root.pendingApproval.assessment && root.pendingApproval.assessment.operator_attestation_required && !root.pendingApproval.decision)
                    activeFocusOnTab: true
                    Keys.onSpacePressed: function(event) { if (!event.isAutoRepeat) root.toggleRecoveryConfirmation(); event.accepted = true }
                    Keys.onReturnPressed: function(event) { root.approvalButtonKey(event) }
                    Keys.onEnterPressed: function(event) { root.approvalButtonKey(event) }
                    Keys.onEscapePressed: function(event) { root.approvalButtonKey(event) }
                    Keys.onReleased: function(event) { root.noteApprovalKeyReleased(event) }
                    Accessible.role: Accessible.CheckBox
                    Accessible.name: "I confirm no other model download is running"
                    Accessible.checked: root.pendingApproval && root.pendingApproval.operatorConfirmed === true
                    Rectangle { anchors.fill: parent; color: "transparent"; border.width: recoveryConfirmationControl.activeFocus ? 2 : 1; border.color: recoveryConfirmationControl.activeFocus ? root.accent : root.muted }
                    Text { textFormat: Text.PlainText; id: recoveryConfirmLabel; anchors.centerIn: parent; width: parent.width - Style.spacing.sm * 2; text: (root.pendingApproval && root.pendingApproval.operatorConfirmed === true ? "[x] " : "[ ] ") + "I confirm no other model download is running. (Space to toggle)"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                    MouseArea { anchors.fill: parent; objectName: "recoveryConfirmationHitTarget"; cursorShape: Qt.PointingHandCursor; onClicked: { recoveryConfirmationControl.forceActiveFocus(); root.toggleRecoveryConfirmation() } }
                  }
                  Text { textFormat: Text.PlainText; width: parent.width; visible: root.approvalFeedback !== ""; text: root.approvalFeedback; wrapMode: Text.Wrap; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                  Row {
                  spacing: Style.spacing.sm
                  Item {
                    id: cancelApprovalControl
                    objectName: "cancelApprovalControl"
                    width: cancelApprovalButton.implicitWidth
                    height: cancelApprovalButton.implicitHeight
                    activeFocusOnTab: true
                    enabled: root.pendingApproval && !root.pendingApproval.decision || (root.pendingApproval && root.pendingApproval.recovery === true && root.pendingApproval.decision === "checking")
                    visible: !!(root.pendingApproval && !root.pendingApproval.decision || (root.pendingApproval && root.pendingApproval.recovery === true && root.pendingApproval.decision === "checking"))
                    Keys.onReturnPressed: function(event) { root.approvalButtonKey(event) }
                    Keys.onEnterPressed: function(event) { root.approvalButtonKey(event) }
                    Keys.onEscapePressed: function(event) { root.approvalButtonKey(event) }
                    Keys.onReleased: function(event) { root.noteApprovalKeyReleased(event) }
                    Accessible.name: "Cancel approval"
                    Ui.Button {
                      id: cancelApprovalButton
                      anchors.fill: parent
                      text: "Cancel Esc"
                      foreground: root.foreground
                      accent: Commons.Color.urgent
                      bordered: true
                      focusable: false
                      hasCursor: cancelApprovalControl.activeFocus
                      enabled: cancelApprovalControl.enabled
                    }
                    MouseArea {
                      objectName: "cancelApprovalHitTarget"
                      anchors.fill: parent
                      enabled: cancelApprovalControl.enabled
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.decideApproval("deny")
                    }
                  }
                  Item {
                    id: approveApprovalControl
                    objectName: "approveApprovalControl"
                    width: approveApprovalButton.implicitWidth
                    height: approveApprovalButton.implicitHeight
                    activeFocusOnTab: true
                    enabled: root.pendingApproval && !root.pendingApproval.decision && (!root.pendingApproval.recovery || root.recoveryCanResolve())
                    visible: !!(root.pendingApproval && !root.pendingApproval.decision)
                    Keys.onReturnPressed: function(event) { root.approvalButtonKey(event) }
                    Keys.onEnterPressed: function(event) { root.approvalButtonKey(event) }
                    Keys.onEscapePressed: function(event) { root.approvalButtonKey(event) }
                    Keys.onReleased: function(event) { root.noteApprovalKeyReleased(event) }
                    Accessible.name: "Approve request"
                    Ui.Button {
                      id: approveApprovalButton
                      anchors.fill: parent
                      text: (root.pendingApproval && root.pendingApproval.approve_label ? root.pendingApproval.approve_label : "Approve") + " ↵"
                      selected: true
                      foreground: root.foreground
                      accent: root.accent
                      bordered: true
                      focusable: false
                      hasCursor: approveApprovalControl.activeFocus
                      enabled: approveApprovalControl.enabled
                    }
                    MouseArea {
                      objectName: "approveApprovalHitTarget"
                      anchors.fill: parent
                      enabled: approveApprovalControl.enabled
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.decideApproval("approve")
                    }
                  }
                  Ui.Button {
                    // 0.15: the CLI asked for more than fits; this is the most that does.
                    objectName: "alternativeApprovalButton"
                    text: root.pendingApproval && root.pendingApproval.alternative_label ? root.pendingApproval.alternative_label : ""
                    visible: !!(root.pendingApproval && root.pendingApproval.coding_cli && root.pendingApproval.alternative_context && !root.pendingApproval.decision)
                    focusable: true; foreground: root.foreground; accent: root.accent; bordered: true
                    onClicked: root.decideApproval("alternative")
                  }
                  Ui.Button {
                    id: reviewRecoveryAgainControl
                    objectName: "reviewRecoveryAgainControl"
                    text: "Check again"
                    visible: !!(root.pendingApproval && root.pendingApproval.recovery === true && root.pendingApproval.decision && ["sending", "checking", "resolved"].indexOf(root.pendingApproval.decision) < 0)
                    focusable: true; foreground: root.foreground; bordered: true
                    onClicked: root.checkRecoveryAssessment()
                  }
                  Ui.Button {
                    id: dismissApprovalControl
                    objectName: "dismissApprovalButton"
                    text: "Dismiss"
                    visible: !!(root.pendingApproval && root.pendingApproval.decision && (!root.pendingApproval.recovery || ["checking", "sending"].indexOf(root.pendingApproval.decision) < 0))
                    focusable: true
                    foreground: root.foreground
                    bordered: true
                    onClicked: { if (root.pendingApproval && root.pendingApproval.recovery) root.applyStreamState("idle", ""); root.clearPendingApproval(); root.restoreChatInputFocus(); root.refreshOnboarding() }
                  }
                  }
                }
              }
        }

        Flickable {
          id: chatViewport
          objectName: "chatViewport"
          visible: root.shownView === "chat" && !root.approvalForeground
          anchors.top: fixedTop.bottom
          anchors.topMargin: Style.spacing.md
          anchors.bottom: fixedBottom.top
          anchors.bottomMargin: Style.spacing.md
          anchors.left: parent.left
          anchors.right: parent.right
          contentWidth: width
          contentHeight: Math.max(height, chatLines.implicitHeight)
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          property bool followLatest: true
          property bool programmaticScroll: false
          function nearBottom() {
            return contentY >= Math.max(0, contentHeight - height - Style.space(12))
          }
          onContentYChanged: {
            if (!programmaticScroll) followLatest = nearBottom()
          }
          onContentHeightChanged: root.scrollChatToEnd()

          Column {
            id: chatLines
            objectName: "chatLines"
            width: chatViewport.width
            spacing: Style.spacing.sm
            // 0.15: NOVA's welcome (mock previews/v015-first-run, state 1).
            Item {
              objectName: "welcomeCard"
              visible: root.welcomeVisible
              width: parent.width
              height: visible ? welcomeColumn.implicitHeight + 2 * Style.spacing.sm : 0
              Rectangle { anchors.fill: parent; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.05); border.width: 1; border.color: root.accent }
              Column {
                id: welcomeColumn
                x: Style.spacing.sm; y: Style.spacing.sm; width: parent.width - 2 * Style.spacing.sm
                spacing: Style.spacing.xs
                readonly property var tool: root.welcomeData && Array.isArray(root.welcomeData.tools) ? root.welcomeData.tools.filter(function(t) { return t && t.id === "opencode" })[0] : null
                readonly property bool offerCli: !!(tool && tool.available === true) && root.openCodeProfile({ id: root.welcomeData && root.welcomeData.listed ? root.welcomeData.listed : root.activeModel }) === null
                Text { textFormat: Text.PlainText; text: "NOVA · WELCOME"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.4 }
                Text { textFormat: Text.PlainText; width: parent.width; wrapMode: Text.Wrap
                       text: "Hi, I'm NOVA. " + root.modelPlainName(root.activeModel) + " is ready" + (root.welcomeData && root.welcomeData.gpu ? " on your " + root.welcomeData.gpu : "") + ", and everything stays on this computer."
                       color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall }
                Text { textFormat: Text.PlainText; objectName: "welcomeContext"; visible: text !== ""; width: parent.width; wrapMode: Text.Wrap; text: root.welcomeContextLine()
                       color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.7); font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Text { textFormat: Text.PlainText; text: "Things to try:"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Repeater {
                  model: [["What's taking space on my disk?", "reads your disk usage"], ["Open a terminal on workspace 5", "asks your approval first"], ["Why is my CPU busy right now?", "reads the system monitor"]]
                  CompactModelButton {
                    required property var modelData
                    objectName: "welcomeTryButton"
                    text: modelData[0] + "  · " + modelData[1]
                    foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                    enabled: !root.chatBusy && !root.pendingApproval
                    onClicked: root.welcomeTry(modelData[0])
                  }
                }
                Text { textFormat: Text.PlainText; visible: welcomeColumn.offerCli; text: "CODE WITH IT IN A TERMINAL"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.2 }
                Text { textFormat: Text.PlainText; visible: welcomeColumn.offerCli; width: parent.width; wrapMode: Text.Wrap
                       text: "Omarchy installed OpenCode for you. I can connect it to " + root.modelPlainName(root.activeModel) + " so you can work on code with your own model: it reads and edits files in a project folder and asks before it runs anything."
                       color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Flow {
                  width: parent.width; spacing: Style.spacing.xxs
                  CompactModelButton { objectName: "welcomeSetUpCliButton"; visible: welcomeColumn.offerCli; text: "SET UP CODING CLI"; selected: true; foreground: root.foreground; accent: root.accent; bordered: true; focusable: visible; onClicked: root.welcomeSetUpCli() }
                  CompactModelButton { objectName: "welcomeDismissButton"; text: welcomeColumn.offerCli ? "NOT NOW" : "GOT IT"; foreground: root.muted; accent: root.accent; bordered: !welcomeColumn.offerCli; focusable: true; onClicked: root.markWelcomeSeen() }
                }
                Text { textFormat: Text.PlainText; visible: welcomeColumn.offerCli; width: parent.width; wrapMode: Text.Wrap; text: "Any time later: MODELS → " + root.modelPlainName(root.activeModel) + " → SET UP CODING CLI."
                       color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 1 }
              }
            }
            Repeater {
              objectName: "chatHistoryRepeater"
              model: chatHistoryModel
              ChatLine {
                role: model.role
                text: model.text
                sources: model.sources
                responseStatus: model.responseStatus
                streaming: model.role === "assistant" && root.chatBusy && index === chatHistoryModel.count - 1
              }
            }
            Item { width: parent.width; height: Style.spacing.md }
          }
        }

        Item {
          id: modelSelector
          objectName: "modelSelector"
          visible: root.selectorVisible
          // UX Phase 2: the MODELS view is this list at full height; no nested box.
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.top: fixedTop.bottom
          anchors.topMargin: Style.spacing.md
          anchors.bottom: fixedBottom.top
          anchors.bottomMargin: Style.spacing.md
          clip: true
          // UX Phase 2 step 2: the list sits directly in the panel (cards, no extra frame).
          Column {
            id: unavailableGroup
            objectName: "unavailableGroup"
            anchors.centerIn: modelList
            visible: root.modelsUnavailable && !root.catalogDownloadsExpanded
            spacing: Style.spacing.sm
            Text {
              textFormat: Text.PlainText
              id: unavailable
              anchors.horizontalCenter: parent.horizontalCenter
              // Before Ollama exists this is a setup step, not a failure.
              readonly property bool waitingForOllama: root.onboardingCodes.indexOf("PROVIDER_UNAVAILABLE") >= 0
              readonly property bool waitingForSetup: !root.online && root.novaSetupPending
              text: waitingForSetup ? "LOCAL MODELS // AFTER SETUP" : (waitingForOllama ? "LOCAL MODELS // WAITING FOR OLLAMA" : "LOCAL MODELS // UNAVAILABLE")
              color: waitingForOllama || waitingForSetup ? root.muted : Commons.Color.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1
            }
            // Run 6: the red line alone didn't say where the fix is.
            Text {
              textFormat: Text.PlainText
              objectName: "modelsUnavailableReason"
              anchors.horizontalCenter: parent.horizontalCenter
              visible: unavailable.waitingForSetup || unavailable.waitingForOllama
              text: unavailable.waitingForSetup ? "Finish SET UP NOVA in CHAT first." : "Install Ollama in CHAT first."
              color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption
            }
            CompactModelButton {
              id: modelsRefreshControl
              objectName: "modelsRefreshControl"
              anchors.horizontalCenter: parent.horizontalCenter
              text: "REFRESH"
              tooltipText: "Retry the local models request"
              foreground: root.foreground
              accent: root.accent
              bordered: true
              focusable: true
              enabled: !root.installedModelsLoading
              Accessible.name: "Refresh local models"
              onClicked: root.refreshModels()
            }
            CompactModelButton {
              objectName: "browseDownloadsWhileUnavailable"
              anchors.horizontalCenter: parent.horizontalCenter
              text: root.browseDownloadsLabel()
              tooltipText: "Show the models you can download once Ollama is ready"
              foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
              Accessible.name: text
              onClicked: root.toggleCatalogDownloads()
            }
          }
          Flickable {
            id: modelList; objectName: "modelList"
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.leftMargin: Style.spacing.sm; anchors.rightMargin: Style.spacing.sm + Style.spacing.xs
            anchors.topMargin: Style.spacing.xs; anchors.bottomMargin: Style.spacing.xs
            visible: !root.modelsUnavailable || root.catalogDownloadsExpanded
            clip: true; contentWidth: width; contentHeight: modelColumn.implicitHeight; boundsBehavior: Flickable.StopAtBounds; interactive: contentHeight > height
            // A card closing shortens the list; snap back into bounds at once, so the next
            // click lands on its button instead of only stopping the spring-back.
            rebound: Transition { NumberAnimation { properties: "x,y"; duration: 0 } }
            onContentHeightChanged: if (contentY > Math.max(0, contentHeight - height)) contentY = Math.max(0, contentHeight - height)
            Column { id: modelColumn; objectName: "modelColumn"; width: modelList.width; spacing: Style.spacing.xs
              Column {
                id: installedModelsSection
                objectName: "installedModelsSection"
                width: parent.width
                spacing: Style.spacing.xs
                // UX Phase 2 step 2 (mock): section title, a short note, REFRESH at the right.
                Item {
                  width: parent.width
                  height: Math.max(installedTitle.implicitHeight, refreshInstalledRow.height)
                  Text { textFormat: Text.PlainText; id: installedTitle; anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "INSTALLED · " + root.discoveredModels.length; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.4 }
                  Text { textFormat: Text.PlainText; anchors.left: installedTitle.right; anchors.leftMargin: Style.spacing.sm; anchors.right: refreshInstalledRow.left; anchors.rightMargin: Style.spacing.sm; anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight
                         text: root.modelUpdatesAvailable > 0 ? root.modelUpdatesAvailable + (root.modelUpdatesAvailable === 1 ? " update" : " updates") : ""; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                  Row { id: refreshInstalledRow; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: Style.spacing.xxs
                    CompactModelButton { objectName: "refreshInstalledModelsButton"; text: root.modelUpdatesChecking ? "CHECKING…" : "REFRESH"; tooltipText: "Re-read the installed models and ask Ollama's library for newer builds"; foreground: root.foreground; accent: root.accent; bordered: true; focusable: true; enabled: !root.installedModelsLoading; Accessible.name: "Refresh installed models and check for updates"; onClicked: { root.refreshModels(); root.refreshModelUpdates(true) } }
                    CompactModelButton {
                      id: browseModelDownloadsButton
                      objectName: "browseModelDownloadsButton"
                      text: root.browseDownloadsLabel()
                      tooltipText: root.catalogDownloadsExpanded ? "Hide the download catalog"
                        : (root.catalogAttention() ? root.catalogAttention().label + " for " + root.catalogAttention().target
                        : "Show the models you can download, installation progress and recovery actions")
                      foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                      selected: !root.catalogDownloadsExpanded
                      Accessible.name: text
                      onClicked: root.toggleCatalogDownloads()
                    }
                  }
                }
                Text { textFormat: Text.PlainText; width: parent.width; visible: root.installedModelsLoading; text: "REFRESHING INSTALLED INVENTORY…"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 1 }
                Text { textFormat: Text.PlainText; width: parent.width; visible: !root.modelsUnavailable && !root.installedModelsLoading && root.discoveredModels.length === 0; text: "NO MODELS INSTALLED YET"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 1 }
                Text { textFormat: Text.PlainText; width: parent.width; visible: root.modelsUnavailable; text: "INSTALLED INVENTORY UNAVAILABLE · RETRY REFRESH"; color: Commons.Color.urgent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                Repeater { model: root.discoveredModels; id: installedModelsRepeater; objectName: "installedModelsRepeater"
                  Item {
                    required property var modelData
                    required property int index
                    width: parent.width
                    height: installedModelRow.implicitHeight + 2 * Style.spacing.sm
                    visible: !root.installedModelsLoading && modelData !== null
                    // UX Phase 2 step 2 (mock): each installed model is a card; the one in use is outlined.
                    Rectangle {
                      anchors.fill: parent
                      color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.03)
                      border.width: 1
                      border.color: root.installedModelSelected(modelData) ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.55)
                                                                         : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14)
                    }
                    Column {
                      id: installedModelRow
                      readonly property var catalogMatch: root.matchedCatalogEntryFor(modelData)
                      readonly property bool loadable: modelData !== null && typeof modelData !== "undefined" && modelData.provider === "ollama"
                        && modelData.available === true && !root.installedModelReady(modelData) && !root.cliLoadingModel(modelData.id)
                      x: Style.spacing.sm; y: Style.spacing.sm
                      width: parent.width - 2 * Style.spacing.sm
                      spacing: 3
                      Item {
                        width: parent.width
                        height: Math.max(installedModelId.height, inUseTag.height, installedModelSize.implicitHeight)
                        readonly property real nameRoom: width - (inUseTag.visible ? inUseTag.width + Style.spacing.sm : 0) - (updateTag.visible ? updateTag.width + Style.spacing.sm : 0) - installedModelSize.implicitWidth - Style.spacing.sm
                        TextMetrics { id: installedNameMetrics; font: installedModelId.font; text: installedModelId.text }
                        Text { textFormat: Text.PlainText; id: installedModelId; objectName: "installedModelId"; width: Math.min(installedNameMetrics.advanceWidth + 2, Math.max(Style.space(60), parent.nameRoom)); wrapMode: Text.Wrap; font.bold: true; text: root.modelPlainName(modelData.id); color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; font.letterSpacing: 0.4 }
                        Rectangle {
                          id: inUseTag
                          objectName: "inUseTag"
                          visible: root.installedModelSelected(modelData)
                          anchors.left: installedModelId.right; anchors.leftMargin: Style.spacing.sm; y: Style.space(2)
                          width: inUseText.implicitWidth + Style.space(8); height: inUseText.implicitHeight + Style.space(2)
                          color: "transparent"; border.width: 1; border.color: root.accent
                          Text { textFormat: Text.PlainText; id: inUseText; anchors.centerIn: parent; text: "IN USE"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.bold: true; font.letterSpacing: 1 }
                        }
                        Rectangle {
                          id: updateTag
                          objectName: "modelUpdateTag"
                          visible: root.modelUpdateOffered(modelData) || root.modelUpdateInProgress(modelData)
                          anchors.left: inUseTag.visible ? inUseTag.right : installedModelId.right; anchors.leftMargin: Style.spacing.sm; y: Style.space(2)
                          width: updateTagText.implicitWidth + Style.space(8); height: updateTagText.implicitHeight + Style.space(2)
                          color: "transparent"; border.width: 1; border.color: root.accent
                          Text { textFormat: Text.PlainText; id: updateTagText; anchors.centerIn: parent; text: "UPDATE"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.bold: true; font.letterSpacing: 1 }
                        }
                        Text { textFormat: Text.PlainText; id: installedModelSize; anchors.right: parent.right; y: Style.space(1); text: modelData.metadata && typeof modelData.metadata.size_bytes === "number" ? root.bytesLabel(modelData.metadata.size_bytes) : ""; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                      }
                      // MODELS v2 (mock previews/models-v2): the facts as words, then DETAILS and the icons.
                      Item {
                        width: parent.width
                        height: Math.max(installedFacts.height, installedModelEnd.height)
                        Row {
                          id: installedFacts
                          anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                          width: parent.width - installedModelEnd.width - Style.spacing.sm
                          spacing: Style.spacing.xs
                          FactWord { id: installedFitWord; anchors.verticalCenter: parent.verticalCenter; text: root.fitWord(root.installedFit(modelData)); tone: root.fitTone(root.installedFit(modelData)) }
                          Text {
                            textFormat: Text.PlainText
                            objectName: "installedModelStatusText"
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - (installedFitWord.visible ? installedFitWord.width + parent.spacing : 0)
                            wrapMode: Text.Wrap
                            text: root.installedFacts(modelData)
                            color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.55)
                            font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 0.4
                          }
                        }
                        Row {
                          id: installedModelEnd
                          anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                          spacing: Style.spacing.xxs
                          CompactModelButton {
                            id: installedModelDetailsButton
                            objectName: "installedModelDetailsButton"
                            text: root.installedModelDetailsOpen(modelData) ? "LESS" : "DETAILS"
                            tooltipText: "The exact name, the fit and the catalog's facts for this model"
                            foreground: root.cardSoftText; accent: root.accent; bordered: false; focusable: true
                            opacity: enabled ? 1 : 0.46
                            Accessible.name: (root.installedModelDetailsOpen(modelData) ? "Hide details of " : "Details of ") + modelData.id
                            onClicked: root.toggleInstalledModelDetails(modelData)
                          }
                          Row {
                            id: installedModelIcons
                            objectName: "installedModelIcons"
                            spacing: Style.spacing.xxs
                            Ui.PanelActionButton {
                              objectName: "unloadModelButton"
                              visible: modelData !== null && typeof modelData !== "undefined" && modelData.resident === true
                              iconText: "⏏"
                              tooltipText: root.unloadTooltip(modelData)
                              height: installedModelDetailsButton.height
                              width: height
                              fontSize: Math.round(height * 0.55)
                              foreground: root.foreground
                              hoverColor: root.accent
                              bordered: true
                              focusable: visible
                              enabled: !root.removalBusy
                              opacity: enabled ? 1 : 0.46
                              Accessible.name: "Free " + root.memoryWord + " used by " + modelData.id
                              onClicked: root.unloadModel(modelData)
                            }
                            Ui.PanelActionButton {
                              objectName: "deleteModelButton"
                              visible: modelData !== null && typeof modelData !== "undefined" && modelData.provider === "ollama"
                              iconText: "󰩹"
                              tooltipText: root.removalTooltip(modelData)
                              height: installedModelDetailsButton.height
                              width: height
                              fontSize: Math.round(height * 0.55)
                              foreground: root.destructiveColor
                              hoverColor: root.destructiveColor
                              bordered: true
                              focusable: visible
                              enabled: !root.removalBusy && root.pendingApproval === null
                              opacity: enabled ? 1 : 0.46
                              Accessible.name: "Delete " + modelData.id + " from this computer"
                              onClicked: root.proposeModelRemoval(modelData)
                            }
                          }
                        }
                      }
                      Flow {
                        id: installedModelActions
                        objectName: "installedModelActions"
                        visible: installedModelRow.loadable
                        width: parent.width
                        topPadding: Style.space(3)
                        spacing: Style.spacing.xxs
                        CompactModelButton {
                          id: loadInstalledModelButton
                          objectName: "loadInstalledModelButton"
                          visible: installedModelRow.loadable
                          text: root.isSwitchDestination(modelData) ? "LOADING…"
                            : (root.installedModelSelected(modelData) ? "LOAD AND VERIFY" : "LOAD AND USE")
                          tooltipText: root.isSwitchDestination(modelData) ? "Loading and verifying " + modelData.id
                            : "Load and verify " + modelData.id + " through the existing model activation path"
                          foreground: root.foreground; accent: root.accent; bordered: true; focusable: visible
                          selected: true  // the row's one primary action
                          enabled: visible && root.canLoadInstalledModel(modelData)
                          Accessible.name: text + " " + modelData.id
                          onClicked: root.loadInstalledModel(modelData)
                        }
                      }
                      // 0.14.0: Ollama's newer build of this model, offered only after the user asks.
                      Column {
                        id: modelUpdateBlock
                        objectName: "modelUpdateBlock"
                        readonly property var update: root.modelUpdateFor(modelData)
                        readonly property bool offered: root.modelUpdateOffered(modelData)
                        readonly property bool updating: root.modelUpdateInProgress(modelData)
                        readonly property string quiet: root.modelUpdateQuietLine(modelData)
                        visible: offered || updating || quiet !== ""
                        width: parent.width
                        spacing: Style.spacing.xs
                        topPadding: Style.spacing.xs
                        Rectangle { width: parent.width; height: 1; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) }
                        Text {
                          objectName: "modelUpdateWhat"
                          visible: modelUpdateBlock.offered && !modelUpdateBlock.updating
                          width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                          text: root.modelUpdateWhat(modelUpdateBlock.update)
                          color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption
                        }
                        Text {
                          objectName: "modelUpdateFacts"
                          visible: modelUpdateBlock.offered && !modelUpdateBlock.updating
                          width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                          text: root.modelUpdateFacts(modelUpdateBlock.update)
                          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.55); font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 0.6
                        }
                        Text {
                          objectName: "modelUpdateProgress"
                          visible: modelUpdateBlock.updating
                          width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                          readonly property var blob: root.acquisitionBlobProgress({ catalog_id: modelUpdateBlock.update ? modelUpdateBlock.update.catalog_id : "" })
                          text: "Updating to the newer build" + (blob.visible && blob.determinate ? " · " + blob.detail : "…") + " The current build keeps working until this finishes."
                          color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption
                        }
                        Row {
                          visible: modelUpdateBlock.offered && !modelUpdateBlock.updating
                          spacing: Style.spacing.xs
                          topPadding: Style.space(2)
                          CompactModelButton {
                            objectName: "modelUpdateButton"
                            text: root.modelUpdateBusy !== "" && modelUpdateBlock.update && root.modelUpdateBusy === modelUpdateBlock.update.catalog_id ? "CHECKING…" : "UPDATE"
                            tooltipText: "Review this update; nothing downloads until you approve"
                            foreground: root.foreground; accent: root.accent; bordered: true; focusable: visible
                            selected: true
                            enabled: visible && root.modelUpdateBusy === "" && root.pendingApproval === null && !root.chatBusy
                                     && !root.modelTransitionActive && root.trackedAcquisitionId === ""
                            Accessible.name: "Update " + modelData.id
                            onClicked: root.proposeModelUpdate(modelData)
                          }
                          Text {
                            textFormat: Text.PlainText
                            objectName: "modelUpdateSkip"
                            anchors.verticalCenter: parent.verticalCenter
                            text: "skip this version"; font.underline: true
                            color: skipArea.containsMouse ? root.foreground : root.muted
                            font.family: root.fontFamily; font.pixelSize: Style.font.caption
                            MouseArea { id: skipArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        Accessible.role: Accessible.Button; Accessible.name: "Skip this version of " + modelData.id
                                        onClicked: root.setModelUpdateSkipped(modelData, true) }
                          }
                        }
                        Row {
                          visible: modelUpdateBlock.quiet !== "" && !modelUpdateBlock.updating
                          width: parent.width
                          spacing: Style.spacing.xs
                          Text {
                            objectName: "modelUpdateQuiet"
                            width: Math.min(implicitWidth, parent.width - (showAgain.visible ? showAgain.width + Style.spacing.xs : 0))
                            wrapMode: Text.Wrap; textFormat: Text.PlainText
                            text: modelUpdateBlock.quiet
                            color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.55); font.family: root.fontFamily; font.pixelSize: Style.font.caption
                          }
                          Text {
                            textFormat: Text.PlainText
                            id: showAgain
                            objectName: "modelUpdateShowAgain"
                            visible: !!(modelUpdateBlock.update && modelUpdateBlock.update.skipped)
                            text: "show again"; font.underline: true
                            color: showAgainArea.containsMouse ? root.foreground : root.muted
                            font.family: root.fontFamily; font.pixelSize: Style.font.caption
                            MouseArea { id: showAgainArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        Accessible.role: Accessible.Button; Accessible.name: "Show the skipped update of " + modelData.id
                                        onClicked: root.setModelUpdateSkipped(modelData, false) }
                          }
                        }
                      }
                      Text {
                        textFormat: Text.PlainText
                        objectName: "removalStatusText"
                        visible: root.removalStatus !== "" && root.removalStatusModel === modelData.id
                        width: parent.width; wrapMode: Text.Wrap
                        text: root.removalStatus
                        color: root.removalFailed ? Commons.Color.urgent : root.accent
                        font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 0.6
                      }
                      // MODELS v2: the model's coding CLIs, under a dashed line, each row with its own buttons.
                      Column {
                        id: codingCliRows
                        objectName: "codingCliRows"
                        readonly property var profiles: root.cliProfilesFor(modelData)
                        readonly property var openCode: root.openCodeProfile(modelData)
                        // Run 7: a fresh install has no profile file, so OPEN CLI could only fail; it shows
                        // once this model has a CLI, or when NOVA can't tell (the reason is on the button).
                        readonly property bool hasCli: modelData !== null && typeof modelData !== "undefined" && modelData.provider === "ollama"
                          && (profiles.length > 0 || root.cliState === null) && !(root.cliState && root.cliState.configured === false)
                        readonly property bool canSetUp: modelData !== null && typeof modelData !== "undefined" && modelData.provider === "ollama"
                          && modelData.available === true && root.cliState !== null && openCode === null
                        visible: hasCli || canSetUp
                        width: parent.width
                        topPadding: Style.space(4)
                        spacing: Style.spacing.xxs
                        Row {
                          width: parent.width; spacing: Style.space(3); clip: true
                          Repeater { model: Math.ceil(parent.width / 7); Rectangle { width: 4; height: 1; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.18) } }
                        }
                        Item {
                          visible: codingCliRows.hasCli
                          width: parent.width; height: Math.max(cliRowLabel.implicitHeight, cliRowButtons.height)
                          Text { id: cliRowLabel; textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; width: Style.space(80)
                                 text: "CODING CLI"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.bold: true; font.letterSpacing: 1.2 }
                          Text {
                            textFormat: Text.PlainText
                            objectName: "codingCliLine"
                            anchors.left: cliRowLabel.right; anchors.right: cliRowButtons.left; anchors.rightMargin: Style.spacing.sm; anchors.verticalCenter: parent.verticalCenter
                            elide: Text.ElideRight
                            text: root.codingCliSummary(modelData, codingCliRows.profiles)
                            color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption
                          }
                          Row {
                            id: cliRowButtons
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            spacing: Style.spacing.xxs
                            CompactModelButton {
                              objectName: "openCliButton"
                              text: root.cliRunning(modelData) ? "CLI RUNNING" : "OPEN CLI"
                              tooltipText: root.cliCanLaunch(modelData)
                                ? "Open the configured coding CLI for " + modelData.id + " in a new terminal"
                                : root.cliUnavailableReason(modelData)
                              // Stays clickable when unavailable so the reason can be shown, not hidden.
                              readonly property bool launchable: root.cliCanLaunch(modelData)
                              foreground: launchable ? root.foreground : root.muted
                              accent: root.accent; bordered: true; focusable: visible
                              selected: launchable && codingCliRows.openCode !== null
                              opacity: launchable ? 1 : 0.6
                              enabled: visible && !root.cliRequesting
                              Accessible.name: text + " " + modelData.id
                              Accessible.description: tooltipText
                              onClicked: launchable ? root.beginCliLaunch(modelData) : root.explainCliUnavailable(modelData)
                            }
                            CompactModelButton {
                              objectName: "tuneCodingCliButton"
                              visible: codingCliRows.openCode !== null
                              text: root.cliTuneModel === modelData.id ? "CLOSE TUNE" : "TUNE"
                              tooltipText: "The coding CLI's working context, with what fits your graphics card"
                              foreground: root.foreground; accent: root.accent; bordered: true; focusable: visible
                              Accessible.name: text + " " + modelData.id
                              onClicked: root.cliTuneModel === modelData.id ? root.cliTuneModel = "" : root.openCliTune(modelData)
                            }
                          }
                        }
                        Item {
                          visible: codingCliRows.canSetUp
                          width: parent.width; height: Math.max(setUpRowText.implicitHeight, setUpCodingCliButton.height)
                          Text { id: setUpRowLabel; textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; width: Style.space(80)
                                 text: codingCliRows.hasCli ? "" : "CODING CLI"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.bold: true; font.letterSpacing: 1.2 }
                          Text { id: setUpRowText; textFormat: Text.PlainText; anchors.left: setUpRowLabel.right; anchors.verticalCenter: parent.verticalCenter
                                 text: codingCliRows.hasCli ? "OpenCode · not set up" : "Not set up"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          CompactModelButton {
                            id: setUpCodingCliButton
                            objectName: "setUpCodingCliButton"
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            text: root.cliSetupModel === modelData.id ? "CLOSE" : "SET UP"
                            tooltipText: "Connect OpenCode (Omarchy installs it) to " + modelData.id + ": one approval, nothing downloaded by NOVA"
                            foreground: root.foreground; accent: root.accent; bordered: true; focusable: visible
                            Accessible.name: (root.cliSetupModel === modelData.id ? "Close the coding CLI setup for " : "Set up a coding CLI for ") + modelData.id
                            onClicked: root.cliSetupModel === modelData.id ? root.cliSetupModel = "" : root.openCliSetup(modelData)
                          }
                        }
                      }
                      Rectangle {
                        id: cliConfirmCard
                        objectName: "cliConfirmCard"
                        readonly property var profile: root.cliProfileFor(modelData)
                        visible: root.cliConfirmModel === modelData.id && profile !== null
                        // The card takes keyboard focus so Enter/Escape decide it (focus would
                        // otherwise stay on OPEN CLI, and Enter would just reopen the card).
                        onVisibleChanged: if (visible) Qt.callLater(function() { root.revealInModelList(cliConfirmCard); cliConfirmCard.forceActiveFocus() })
                        Keys.onReturnPressed: function(event) { root.handleCliConfirmShortcut("launch"); event.accepted = true }
                        Keys.onEnterPressed: function(event) { root.handleCliConfirmShortcut("launch"); event.accepted = true }
                        Keys.onEscapePressed: function(event) { root.handleCliConfirmShortcut("cancel"); event.accepted = true }
                        Keys.onUpPressed: function(event) { root.moveCliChoice(modelData, -1); event.accepted = true }
                        Keys.onDownPressed: function(event) { root.moveCliChoice(modelData, 1); event.accepted = true }
                        readonly property var projects: visible ? root.cliProfilesFor(modelData) : []
                        onHeightChanged: if (visible) Qt.callLater(function() { root.revealInModelList(cliConfirmCard) })
                        width: parent.width
                        height: visible ? cliConfirmColumn.implicitHeight + Style.spacing.md : 0
                        color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.07)
                        border.width: 1; border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.5)
                        Column {
                          id: cliConfirmColumn
                          anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                          anchors.margins: Style.spacing.sm
                          spacing: Style.spacing.xxs
                          Text { textFormat: Text.PlainText; text: "OPEN CODING CLI"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.1 }
                          Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: cliConfirmCard.profile ? cliConfirmCard.profile.label : ""; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall }
                          Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: "MODEL  " + modelData.id; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          Text { visible: cliConfirmCard.projects.length < 2; width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: "PROJECT  " + (cliConfirmCard.profile ? cliConfirmCard.profile.project_label : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          Text { textFormat: Text.PlainText; objectName: "cliSyncText"; visible: cliConfirmCard.projects.length < 2; width: parent.width; wrapMode: Text.Wrap; text: root.cliSyncSummary(cliConfirmCard.profile); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          // Several projects for this model: choose one (click, or Up/Down; Enter launches).
                          Column {
                            objectName: "cliProjectPicker"
                            visible: cliConfirmCard.projects.length > 1
                            width: parent.width; spacing: Style.spacing.xxs
                            Text { textFormat: Text.PlainText; width: parent.width; text: "PROJECT · " + cliConfirmCard.projects.length + " set up for this model"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 0.6 }
                            Rectangle {
                              width: parent.width; height: cliProjectRows.implicitHeight + 2
                              color: "transparent"; border.width: 1; border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.16)
                              Column {
                                id: cliProjectRows
                                x: 1; y: 1; width: parent.width - 2
                                Repeater {
                                  objectName: "cliProjectRepeater"
                                  model: cliConfirmCard.projects
                                  Rectangle {
                                    objectName: "cliProjectRow"
                                    readonly property bool chosen: !!cliConfirmCard.profile && cliConfirmCard.profile.id === modelData.id
                                    width: parent.width; height: cliProjectRowText.implicitHeight + Style.spacing.xs * 2
                                    color: chosen ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.12) : "transparent"
                                    Rectangle { visible: parent.chosen; width: 2; height: parent.height; color: root.accent }
                                    Rectangle {
                                      x: Style.spacing.sm; y: Style.spacing.xs + 3; width: 10; height: 10; radius: 5
                                      color: "transparent"; border.width: 1; border.color: parent.chosen ? root.accent : root.muted
                                      Rectangle { visible: parent.parent.chosen; anchors.centerIn: parent; width: 4; height: 4; radius: 2; color: root.accent }
                                    }
                                    Column {
                                      id: cliProjectRowText
                                      x: Style.spacing.sm + 18; y: Style.spacing.xs; width: parent.width - x - Style.spacing.sm
                                      Text { objectName: "cliProjectName"; width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                                             text: modelData.project_label + (modelData.running === true ? "  · RUNNING" : "")
                                             color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall }
                                      Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                                             text: (modelData.project_place ? modelData.project_place + " · " : "") + root.cliAgo(modelData.last_used)
                                             color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                                      Text { textFormat: Text.PlainText; width: parent.width; wrapMode: Text.Wrap
                                             text: modelData.available === true || modelData.running === true ? root.cliSyncShort(modelData)
                                                   : root.cliReasonFor(modelData)
                                             color: modelData.available !== true && modelData.running !== true ? Commons.Color.urgent
                                                    : modelData.project_sync === "will_update" ? root.foreground : root.muted
                                             font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                                    }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                Accessible.role: Accessible.RadioButton; Accessible.name: "Project " + modelData.project_label
                                                onClicked: { root.cliChosenProfileId = modelData.id; cliConfirmCard.forceActiveFocus() } }
                                  }
                                }
                              }
                            }
                            Text { textFormat: Text.PlainText; width: parent.width; text: "↑ ↓ CHOOSE · ENTER LAUNCH · ESC CANCEL"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 1 }
                          }
                          Text { textFormat: Text.PlainText; width: parent.width; wrapMode: Text.Wrap; text: "New interactive session: no earlier task is resumed. Opening the CLI does not change NOVA's chat model."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          Row {
                            spacing: Style.spacing.xxs
                            CompactModelButton { objectName: "cliConfirmLaunch"; selected: true; text: "LAUNCH [ENTER]"; foreground: root.foreground; accent: root.accent; bordered: true; focusable: cliConfirmCard.visible; enabled: cliConfirmCard.visible && root.cliChosenCanLaunch(modelData); Accessible.name: "Launch coding CLI for " + modelData.id; onClicked: root.confirmCliLaunch(modelData) }
                            CompactModelButton { objectName: "cliConfirmCancel"; text: "CANCEL [ESC]"; foreground: root.foreground; accent: root.accent; bordered: true; focusable: cliConfirmCard.visible; Accessible.name: "Cancel opening the coding CLI"; onClicked: root.cancelCliLaunch() }
                          }
                        }
                      }
                      // SET UP CODING CLI (MODELS v2: three labelled choices, the fit as a word).
                      Item {
                        objectName: "codingCliSetupCard"
                        visible: root.cliSetupModel === modelData.id
                        width: parent.width
                        height: visible ? setupColumn.implicitHeight + 2 * Style.spacing.sm : 0
                        Rectangle { anchors.fill: parent; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.05); border.width: 1; border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.35) }
                        Column {
                          id: setupColumn
                          x: Style.spacing.sm; y: Style.spacing.sm; width: parent.width - 2 * Style.spacing.sm
                          spacing: Style.spacing.xs
                          readonly property var tool: root.cliSetupState ? root.cliSetupTool() : null
                          readonly property var verdict: root.cliSetupState ? root.cliVerdict(root.cliSetupState.numbers, root.cliSetupState.context) : null
                          readonly property real labelWidth: Style.space(78)
                          Row {
                            width: parent.width; spacing: Style.spacing.sm
                            Text { textFormat: Text.PlainText; text: "SET UP OPENCODE"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 1; font.bold: true; font.letterSpacing: 1.2 }
                            Text { textFormat: Text.PlainText; width: parent.width - x; elide: Text.ElideRight; text: "a coding agent that edits files and runs tests in one folder"; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 1 }
                          }
                          Text { textFormat: Text.PlainText; visible: !root.cliSetupState && root.cliSetupError === ""; text: "Checking this computer…"; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          Text { textFormat: Text.PlainText; visible: !!(setupColumn.tool && setupColumn.tool.available !== true); width: parent.width; wrapMode: Text.Wrap
                                 text: "OpenCode isn't on this computer. Omarchy installs it; run Omarchy's update, then open this again."
                                 color: root.destructiveColor; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          Item {
                            visible: !!root.cliSetupState; width: parent.width; height: Math.max(folderLabel.implicitHeight, folderChoices.height)
                            Text { id: folderLabel; textFormat: Text.PlainText; y: Style.space(4); width: setupColumn.labelWidth; text: "FOLDER"; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.letterSpacing: 1.2 }
                            Flow {
                              id: folderChoices
                              x: setupColumn.labelWidth; width: parent.width - x; spacing: Style.spacing.xxs
                              Repeater {
                                model: root.cliSetupState && Array.isArray(root.cliSetupState.projects) ? root.cliSetupState.projects : []
                                CompactModelButton {
                                  required property var modelData
                                  objectName: "codingCliProjectButton"
                                  text: modelData; selected: root.cliSetupProject === modelData
                                  tooltipText: "OpenCode works only inside " + modelData
                                  foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                                  onClicked: root.cliSetupProject = modelData
                                }
                              }
                              Text { textFormat: Text.PlainText; visible: !!root.cliSetupState && !(root.cliSetupState.projects && root.cliSetupState.projects.length); width: parent.width; wrapMode: Text.Wrap
                                     text: "No folder in your home folder yet. Make one for your code (for example ~/Code), then open this again."; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                            }
                          }
                          Item {
                            visible: !!root.cliSetupState; width: parent.width; height: Math.max(asksLabel.implicitHeight, asksChoices.height)
                            Text { id: asksLabel; textFormat: Text.PlainText; y: Style.space(4); width: setupColumn.labelWidth; text: "ASKS"; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.letterSpacing: 1.2 }
                            Flow {
                              id: asksChoices
                              x: setupColumn.labelWidth; width: parent.width - x; spacing: Style.spacing.xxs
                              CompactModelButton { objectName: "codingCliCareAsk"; text: "before edits and commands"; tooltipText: "You see each change and command first. Recommended to start."; selected: root.cliSetupCare === "ask"; foreground: root.foreground; accent: root.accent; bordered: true; focusable: true; onClicked: root.cliSetupCare = "ask" }
                              CompactModelButton { objectName: "codingCliCareEdits"; text: "before commands only"; tooltipText: "It edits files freely; commands still need your yes."; selected: root.cliSetupCare === "edits"; foreground: root.foreground; accent: root.accent; bordered: true; focusable: true; onClicked: root.cliSetupCare = "edits" }
                            }
                          }
                          Item {
                            visible: !!root.cliSetupState; width: parent.width; height: setupContextRow.height
                            Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; width: setupColumn.labelWidth; text: "CONTEXT"; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.letterSpacing: 1.2 }
                            Row {
                              id: setupContextRow
                              x: setupColumn.labelWidth; width: parent.width - x; spacing: Style.spacing.xs
                              Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; text: root.cliSetupState ? root.contextLabel(root.cliSetupState.context) : ""; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                              FactWord { objectName: "codingCliSetupWord"; anchors.verticalCenter: parent.verticalCenter; text: setupColumn.verdict ? setupColumn.verdict.word : ""; tone: setupColumn.verdict ? root.verdictColor(setupColumn.verdict.level, false) : root.accent }
                              Text { textFormat: Text.PlainText; objectName: "codingCliSetupVerdict"; anchors.verticalCenter: parent.verticalCenter; width: parent.width - x; elide: Text.ElideRight
                                     text: setupColumn.verdict ? setupColumn.verdict.short + " · change it later with TUNE" : (root.cliSetupState && root.cliSetupState.verdict ? root.cliSetupState.verdict.text : "NOVA can't estimate this model's memory; TUNE changes it later.")
                                     color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                            }
                          }
                          Text { textFormat: Text.PlainText; visible: root.cliSetupError !== ""; width: parent.width; wrapMode: Text.Wrap; text: root.cliSetupError; color: root.destructiveColor; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          Item {
                            width: parent.width; height: setupActions.height
                            Row {
                              id: setupActions
                              spacing: Style.spacing.xxs
                              CompactModelButton {
                                objectName: "codingCliSetUpButton"
                                text: root.cliSetupBusy ? "ASKING…" : "SET UP"
                                tooltipText: "One approval lists every change before anything happens"
                                selected: true; foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                                enabled: !!(setupColumn.tool && setupColumn.tool.available === true) && root.cliSetupProject !== "" && !root.cliSetupBusy && !root.pendingApproval
                                opacity: enabled ? 1 : 0.5
                                onClicked: root.proposeCliSetup()
                              }
                              CompactModelButton { text: "CANCEL"; foreground: root.cardSoftText; accent: root.accent; bordered: false; focusable: true; onClicked: root.cliSetupModel = "" }
                            }
                            Text { textFormat: Text.PlainText; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                   visible: !!(setupColumn.tool && setupColumn.tool.available === true && setupColumn.tool.downloads_on_first_start) && width < parent.width - setupActions.width
                                   text: "its first start downloads OpenCode"; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 1 }
                          }
                        }
                      }
                      // TUNE (MODELS v2): one number, one word, one bar; marks only where they mean something.
                      Item {
                        objectName: "codingCliTuneCard"
                        visible: root.cliTuneModel === modelData.id
                        width: parent.width
                        height: visible ? tuneColumn.implicitHeight + 2 * Style.spacing.sm : 0
                        Rectangle { anchors.fill: parent; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.05); border.width: 1; border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.35) }
                        Column {
                          id: tuneColumn
                          x: Style.spacing.sm; y: Style.spacing.sm; width: parent.width - 2 * Style.spacing.sm
                          spacing: Style.spacing.xs
                          readonly property var numbers: root.cliTuneState ? root.cliTuneState.numbers : null
                          readonly property int tokens: root.cliTuneTokens()
                          readonly property var verdict: root.cliTuneVerdict()
                          readonly property var steps: root.cliTuneSteps()
                          readonly property bool risky: !verdict || verdict.level !== "fits" || verdict.past
                          Row {
                            width: parent.width; spacing: Style.spacing.sm
                            Text { textFormat: Text.PlainText; text: "WORKING CONTEXT"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 1; font.bold: true; font.letterSpacing: 1.2 }
                            Text { textFormat: Text.PlainText; width: parent.width - x; elide: Text.ElideRight; text: "how much code and conversation it keeps in mind"; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 1 }
                          }
                          Text { textFormat: Text.PlainText; visible: !root.cliTuneState && root.cliTuneError === ""; text: "Reading its settings…"; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          Row {
                            visible: !!root.cliTuneState; spacing: Style.spacing.sm
                            Text { textFormat: Text.PlainText; id: tuneValueText; objectName: "codingCliTuneValue"; text: root.contextLabel(tuneColumn.tokens); color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall + 8; font.bold: true }
                            FactWord { objectName: "codingCliTuneWord"; anchors.verticalCenter: tuneValueText.verticalCenter; text: tuneColumn.verdict ? tuneColumn.verdict.word : "NOT CHECKED"; tone: tuneColumn.verdict ? root.verdictColor(tuneColumn.verdict.level, false) : root.cliTightColor }
                            FactWord { objectName: "codingCliTunePast"; anchors.verticalCenter: tuneValueText.verticalCenter; text: tuneColumn.verdict && tuneColumn.verdict.past ? "PAST TRAINED" : ""; tone: root.cliPastColor }
                          }
                          Ui.PanelSlider {
                            id: tuneSlider
                            objectName: "codingCliTuneSlider"
                            visible: !!root.cliTuneState
                            width: parent.width
                            minimum: 0; maximum: Math.max(1, tuneColumn.steps.length - 1); step: 1; integer: true
                            tickCount: tuneColumn.steps.length
                            value: root.cliTuneIndex
                            fillColor: tuneColumn.verdict ? root.verdictColor(tuneColumn.verdict.level, tuneColumn.verdict.past) : root.accent
                            knobColor: root.foreground
                            onMoved: function(value) { root.cliTuneIndex = Math.round(value); root.cliTuneConfirm = false }
                            onReleased: function(value) { root.cliTuneIndex = Math.round(value) }
                          }
                          // Labels only at the ends, the recommended step and what the model was trained for.
                          Item {
                            id: tuneMarks
                            objectName: "codingCliTuneMarks"
                            visible: !!root.cliTuneState
                            width: parent.width; height: visible ? Style.font.caption + 2 : 0
                            readonly property int last: Math.max(1, tuneColumn.steps.length - 1)
                            readonly property int recommended: root.cliTuneState ? root.cliTuneState.recommended_context : 0
                            readonly property int trained: tuneColumn.numbers ? tuneColumn.numbers.native_context : 0
                            function at(tokens) { var i = tuneColumn.steps.indexOf(tokens); return i < 0 ? -1 : width * i / last }
                            Text { id: tuneMin; textFormat: Text.PlainText; x: 0; visible: tuneColumn.steps.length > 0 && tuneRec.x > implicitWidth + Style.spacing.sm
                                   text: tuneColumn.steps.length ? root.contextLabel(tuneColumn.steps[0]) : ""; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2 }
                            Text { id: tuneRec; textFormat: Text.PlainText; visible: tuneMarks.at(tuneMarks.recommended) >= 0
                                   x: Math.max(0, tuneMarks.at(tuneMarks.recommended) - implicitWidth + 4)
                                   text: "recommended " + root.contextLabel(tuneMarks.recommended) + (tuneMarks.trained === tuneMarks.recommended ? " = trained" : "") + " ▲"
                                   color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2 }
                            Text { id: tuneNative; textFormat: Text.PlainText; visible: tuneMarks.trained !== tuneMarks.recommended && tuneMarks.at(tuneMarks.trained) >= 0
                                   x: Math.max(tuneRec.x + tuneRec.implicitWidth + Style.spacing.sm, Math.min(tuneMarks.width - implicitWidth, tuneMarks.at(tuneMarks.trained) - 4))
                                   text: "▲ " + root.contextLabel(tuneMarks.trained) + " trained"; color: root.cliPastColor; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2 }
                            Text { textFormat: Text.PlainText; anchors.right: parent.right; visible: tuneColumn.steps.length > 1 && (!tuneNative.visible || tuneNative.x + tuneNative.implicitWidth < parent.width - implicitWidth - Style.spacing.sm)
                                   text: tuneColumn.steps.length ? root.contextLabel(tuneColumn.steps[tuneColumn.steps.length - 1]) : ""; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2 }
                          }
                          // The meter: model, context cache and runtime against what the card can give; stripes spill.
                          Item {
                            objectName: "codingCliMeter"
                            visible: !!(root.cliTuneState && tuneColumn.numbers && tuneColumn.verdict)
                            width: parent.width; height: Style.space(9)
                            readonly property real total: visible ? Math.max(tuneColumn.numbers.budget_bytes, tuneColumn.verdict.need) : 1
                            function part(bytes) { return width * Math.max(0, bytes) / total }
                            Rectangle { anchors.fill: parent; color: "transparent"; border.width: 1; border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.25) }
                            Row {
                              anchors.fill: parent; anchors.margins: 1
                              Rectangle { height: parent.height; width: parent.parent.visible ? parent.parent.part(tuneColumn.numbers.weights_bytes) : 0; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.45) }
                              Rectangle { height: parent.height; width: parent.parent.visible ? parent.parent.part(tuneColumn.tokens * tuneColumn.numbers.kv_bytes_per_token + tuneColumn.numbers.fixed_cache_bytes) : 0
                                          color: tuneColumn.verdict ? root.verdictColor(tuneColumn.verdict.level, tuneColumn.verdict.past) : root.accent }
                              Rectangle { height: parent.height; width: parent.parent.visible ? parent.parent.part(tuneColumn.numbers.runtime_bytes) : 0; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.3) }
                            }
                            Rectangle { visible: parent.visible && tuneColumn.verdict.level === "spills"; x: parent.visible ? parent.part(tuneColumn.numbers.budget_bytes) : 0; y: -2; width: 2; height: parent.height + 4; color: root.foreground }
                          }
                          Item {
                            visible: !!(tuneColumn.numbers && tuneColumn.verdict); width: parent.width; height: tuneKey.implicitHeight
                            Text { id: tuneKey; textFormat: Text.PlainText
                                   text: visible ? "model " + root.gibLabel(tuneColumn.numbers.weights_bytes).replace(" GiB", "") + " · context " + root.gibLabel(tuneColumn.tokens * tuneColumn.numbers.kv_bytes_per_token).replace(" GiB", "")
                                     + " · runtime " + root.gibLabel(tuneColumn.numbers.runtime_bytes).replace(" GiB", "") + " GiB" : ""
                                   color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2 }
                            Text { textFormat: Text.PlainText; anchors.right: parent.right; visible: tuneKey.implicitWidth + implicitWidth + Style.spacing.sm < parent.width
                                   text: root.cliTuneState ? (root.cliTuneState.gpu || "your graphics card") + " gives " + root.gibLabel(tuneColumn.numbers ? tuneColumn.numbers.budget_bytes : 0) : ""
                                   color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2 }
                          }
                          Text { textFormat: Text.PlainText; objectName: "codingCliTuneVerdict"; visible: !!root.cliTuneState; width: parent.width; wrapMode: Text.Wrap
                                 text: tuneColumn.verdict ? tuneColumn.verdict.short + "." : "NOVA can't estimate graphics memory for this model, so it can't warn you. Change it only if you know it fits."
                                 color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          // Are you sure? (risky settings only)
                          Item {
                            objectName: "codingCliTuneConfirm"
                            visible: root.cliTuneConfirm
                            width: parent.width; height: visible ? confirmColumn.implicitHeight + 2 * Style.spacing.xs : 0
                            Rectangle { anchors.fill: parent; color: "transparent"; border.width: 1; border.color: tuneColumn.verdict && tuneColumn.verdict.level === "spills" ? root.destructiveColor : root.cliTightColor }
                            Column {
                              id: confirmColumn
                              x: Style.spacing.xs; y: Style.spacing.xs; width: parent.width - 2 * Style.spacing.xs; spacing: Style.spacing.xxs
                              Text { textFormat: Text.PlainText; width: parent.width; wrapMode: Text.Wrap; font.bold: true
                                     text: "ARE YOU SURE? · " + root.contextLabel(tuneColumn.tokens) + " " + (!tuneColumn.verdict ? "can't be checked on this model" : tuneColumn.verdict.level === "spills" ? "doesn't fit your graphics card" : tuneColumn.verdict.level === "tight" ? "leaves very little graphics memory" : "goes past what the model was trained for")
                                     color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                              Text { textFormat: Text.PlainText; width: parent.width; wrapMode: Text.Wrap; text: "You can always go back: TUNE → BACK TO " + root.contextLabel(tuneMarks.recommended) + ", or ask the CLI to undo it."; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                              Flow {
                                width: parent.width; spacing: Style.spacing.xxs
                                CompactModelButton { objectName: "codingCliKeepButton"; text: "KEEP " + root.contextLabel(root.cliTuneState ? root.cliTuneState.context : 0); selected: true; foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                                                     onClicked: { root.cliTuneIndex = Math.max(0, tuneColumn.steps.indexOf(root.cliTuneState.context)); root.cliTuneConfirm = false } }
                                CompactModelButton { objectName: "codingCliAnywayButton"; text: "USE " + root.contextLabel(tuneColumn.tokens) + " ANYWAY"; foreground: tuneColumn.verdict && tuneColumn.verdict.level === "spills" ? root.destructiveColor : root.cliTightColor
                                                     accent: root.accent; bordered: true; focusable: true; onClicked: root.saveCliTune(true) }
                              }
                            }
                          }
                          Text { textFormat: Text.PlainText; visible: root.cliTuneError !== ""; width: parent.width; wrapMode: Text.Wrap; text: root.cliTuneError; color: root.destructiveColor; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                          Flow {
                            visible: !!root.cliTuneState && !root.cliTuneConfirm; width: parent.width; spacing: Style.spacing.xxs
                            CompactModelButton {
                              objectName: "codingCliSaveButton"
                              text: root.cliTuneBusy ? "SAVING…" : "SAVE " + root.contextLabel(tuneColumn.tokens) + (tuneColumn.risky ? " ANYWAY…" : "")
                              tooltipText: "Rebuilds the CLI's model in a few seconds (no download); NOVA's chat follows it"
                              selected: !tuneColumn.risky; foreground: !tuneColumn.risky ? root.foreground : (tuneColumn.verdict && tuneColumn.verdict.level === "spills" ? root.destructiveColor : root.cliTightColor)
                              accent: root.accent; bordered: true; focusable: true
                              enabled: !root.cliTuneBusy && !!root.cliTuneState && tuneColumn.tokens !== root.cliTuneState.context
                              opacity: enabled ? 1 : 0.5
                              onClicked: root.saveCliTune(false)
                            }
                            CompactModelButton { objectName: "codingCliRecommendedButton"; text: "BACK TO " + root.contextLabel(tuneMarks.recommended); foreground: root.cardSoftText; accent: root.accent; bordered: false; focusable: true; onClicked: root.cliTuneRecommended() }
                            CompactModelButton { text: "CANCEL"; foreground: root.cardSoftText; accent: root.accent; bordered: false; focusable: true; onClicked: root.cliTuneModel = "" }
                          }
                        }
                      }
                      Text {
                        textFormat: Text.PlainText
                        objectName: "cliStatusText"
                        visible: root.cliStatus !== "" && root.cliStatusModel === modelData.id
                        width: parent.width; wrapMode: Text.Wrap
                        text: root.cliStatus
                        color: root.cliStatusFailed ? Commons.Color.urgent : root.accent
                        font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 1
                      }
                      Column { visible: root.installedModelDetailsOpen(modelData); width: parent.width; spacing: Style.spacing.xxs
                        Text { objectName: "installedModelExactName"; width: parent.width; text: "Exact name in Ollama: " + modelData.id; textFormat: Text.PlainText; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                        Text { textFormat: Text.PlainText; width: parent.width; text: installedModelRow.catalogMatch ? root.fitSummary(modelData.id) : "Hardware fit hasn’t been verified yet."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                        Text { textFormat: Text.PlainText; width: parent.width; text: installedModelRow.catalogMatch ? root.workingContextSummary(modelData.id) : "Working context hasn’t been estimated yet."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                        Text { textFormat: Text.PlainText; visible: installedModelRow.catalogMatch !== null; width: parent.width; text: "Working context is how much conversation and reference material the model can work with at once."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                        Text { textFormat: Text.PlainText; visible: installedModelRow.catalogMatch !== null; width: parent.width; text: root.catalogPropertiesSummary(installedModelRow.catalogMatch); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                        Text { textFormat: Text.PlainText; visible: installedModelRow.catalogMatch !== null; width: parent.width; text: root.catalogFitConfidenceSummary(installedModelRow.catalogMatch); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                        Text { textFormat: Text.PlainText; visible: root.installedMatchedDetail(modelData) !== ""; width: parent.width; text: root.installedMatchedDetail(modelData); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                        Text { visible: root.installedMatchedHistory(modelData) !== ""; width: parent.width; text: root.installedMatchedHistory(modelData); textFormat: Text.PlainText; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                      }
                    }
                  }
                }
              }
              Column {
                id: onlineCliSection
                objectName: "onlineCliSection"
                width: parent.width
                visible: root.onlineCliItems.length > 0
                spacing: Style.spacing.xs
                Rectangle { width: parent.width; height: 1; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.16) }
                Row {
                  width: parent.width; spacing: Style.spacing.sm
                  Text { textFormat: Text.PlainText; objectName: "onlineCliTitle"; text: "ONLINE CLIS · " + root.onlineCliItems.length; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.4 }
                  Text { textFormat: Text.PlainText; width: parent.width - x; elide: Text.ElideRight; text: "your own account · NOVA keeps no keys"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                }
                Repeater { model: root.onlineCliItems; id: onlineCliRepeater; objectName: "onlineCliRepeater"
                  Column {
                    id: onlineCliRow
                    required property var modelData
                    readonly property var profile: root.cliProfileFor(modelData)
                    width: parent.width
                    spacing: 2
                    // MODELS v2: one line per online CLI: name, project, OPEN CLI.
                    Item {
                      width: parent.width; height: Math.max(onlineCliName.implicitHeight, onlineCliOpenButton.height)
                      Text { id: onlineCliName; objectName: "onlineCliName"; anchors.verticalCenter: parent.verticalCenter; width: Math.min(implicitWidth, parent.width * 0.55); elide: Text.ElideRight; textFormat: Text.PlainText; text: onlineCliRow.profile ? onlineCliRow.profile.label : ""; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; font.letterSpacing: 0.4 }
                      Text { anchors.left: onlineCliName.right; anchors.leftMargin: Style.spacing.md; anchors.right: onlineCliOpenButton.left; anchors.rightMargin: Style.spacing.sm; anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight; textFormat: Text.PlainText; text: onlineCliRow.profile ? onlineCliRow.profile.project_label : ""; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                    CompactModelButton {
                      id: onlineCliOpenButton
                      anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                      objectName: "onlineCliOpenButton"
                      readonly property bool launchable: root.cliCanLaunch(modelData)
                      text: "OPEN CLI"
                      tooltipText: launchable ? "Open " + (onlineCliRow.profile ? onlineCliRow.profile.label : "this CLI") + " in a new terminal" : root.cliUnavailableReason(modelData)
                      foreground: launchable ? root.foreground : root.muted
                      accent: root.accent; bordered: true; focusable: true
                      opacity: launchable ? 1 : 0.6
                      enabled: !root.cliRequesting
                      Accessible.name: "Open CLI " + (onlineCliRow.profile ? onlineCliRow.profile.label : "")
                      Accessible.description: tooltipText
                      onClicked: launchable ? root.beginCliLaunch(modelData) : root.explainCliUnavailable(modelData)
                    }
                    }
                    Rectangle {
                      id: onlineCliConfirmCard
                      objectName: "onlineCliConfirmCard"
                      visible: root.cliConfirmModel === modelData.id && onlineCliRow.profile !== null
                      onVisibleChanged: if (visible) Qt.callLater(function() { root.revealInModelList(onlineCliConfirmCard); onlineCliConfirmCard.forceActiveFocus() })
                      Keys.onReturnPressed: function(event) { root.handleCliConfirmShortcut("launch"); event.accepted = true }
                      Keys.onEnterPressed: function(event) { root.handleCliConfirmShortcut("launch"); event.accepted = true }
                      Keys.onEscapePressed: function(event) { root.handleCliConfirmShortcut("cancel"); event.accepted = true }
                      width: parent.width
                      height: visible ? onlineCliConfirmColumn.implicitHeight + Style.spacing.md : 0
                      color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.07)
                      border.width: 1; border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.5)
                      Column {
                        id: onlineCliConfirmColumn
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: Style.spacing.sm
                        spacing: Style.spacing.xxs
                        Text { textFormat: Text.PlainText; text: "OPEN CODING CLI"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.1 }
                        Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: onlineCliRow.profile ? onlineCliRow.profile.label : ""; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall }
                        Text { objectName: "onlineCliProject"; width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: "PROJECT  " + (onlineCliRow.profile ? onlineCliRow.profile.project_label : ""); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                        Text { textFormat: Text.PlainText; width: parent.width; wrapMode: Text.Wrap; text: "Runs online: what you type goes to that service under your own account and sign-in. Opening it does not change NOVA's chat model."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                        Row {
                          spacing: Style.spacing.xxs
                          CompactModelButton { objectName: "onlineCliConfirmLaunch"; selected: true; text: "LAUNCH [ENTER]"; foreground: root.foreground; accent: root.accent; bordered: true; focusable: onlineCliConfirmCard.visible; enabled: onlineCliConfirmCard.visible && root.cliChosenCanLaunch(modelData); Accessible.name: "Launch " + (onlineCliRow.profile ? onlineCliRow.profile.label : ""); onClicked: root.confirmCliLaunch(modelData) }
                          CompactModelButton { objectName: "onlineCliConfirmCancel"; text: "CANCEL [ESC]"; foreground: root.foreground; accent: root.accent; bordered: true; focusable: onlineCliConfirmCard.visible; Accessible.name: "Cancel opening the coding CLI"; onClicked: root.cancelCliLaunch() }
                        }
                      }
                    }
                    Text {
                      textFormat: Text.PlainText
                      objectName: "onlineCliStatusText"
                      visible: root.cliStatus !== "" && root.cliStatusModel === modelData.id
                      width: parent.width; wrapMode: Text.Wrap
                      text: root.cliStatus
                      color: root.cliStatusFailed ? Commons.Color.urgent : root.accent
                      font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 1
                    }
                  }
                }
              }
              Text { textFormat: Text.PlainText; objectName: "modelSwitchStatusText"; visible: root.modelSwitchStatus !== ""; text: root.modelSwitchStatus; color: root.modelSwitchStatus.indexOf("FAILED") >= 0 || root.modelSwitchStatus === "MODEL UNAVAILABLE" ? Commons.Color.urgent : root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.1 }
              Rectangle {
                id: globalAcquisitionStatusBadge
                objectName: "globalAcquisitionStatusBadge"
                visible: root.acquisitionStatus !== ""
                width: parent.width
                height: visible ? globalAcquisitionStatusText.implicitHeight + Style.spacing.sm : 0
                color: Qt.rgba((root.acquisitionStatus.indexOf("FAILED") >= 0 || root.acquisitionStatus.indexOf("UNAVAILABLE") >= 0 ? Commons.Color.urgent : root.accent).r,
                               (root.acquisitionStatus.indexOf("FAILED") >= 0 || root.acquisitionStatus.indexOf("UNAVAILABLE") >= 0 ? Commons.Color.urgent : root.accent).g,
                               (root.acquisitionStatus.indexOf("FAILED") >= 0 || root.acquisitionStatus.indexOf("UNAVAILABLE") >= 0 ? Commons.Color.urgent : root.accent).b, 0.08)
                border.width: 1
                border.color: root.acquisitionStatus.indexOf("FAILED") >= 0 || root.acquisitionStatus.indexOf("UNAVAILABLE") >= 0 ? Commons.Color.urgent : Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.52)
                Text { textFormat: Text.PlainText; id: globalAcquisitionStatusText; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.leftMargin: Style.spacing.xs; anchors.rightMargin: Style.spacing.xs; text: root.acquisitionStatus; color: root.acquisitionStatus.indexOf("FAILED") >= 0 || root.acquisitionStatus.indexOf("UNAVAILABLE") >= 0 ? Commons.Color.urgent : root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 0.7; wrapMode: Text.Wrap }
              }
              Column {
                id: downloadCatalog
                objectName: "downloadCatalog"
                width: parent.width
                visible: root.catalogDownloadsExpanded
                spacing: Style.spacing.xs
                // MODELS v2 (mock previews/models-v2): SUGGESTED / FITS / ALL, two lines per model,
                // the long facts behind DETAILS, and any Ollama model at the end.
                Item { width: 1; height: Style.spacing.md }
                Item {
                  width: parent.width; height: Math.max(downloadsTitle.implicitHeight, hideDownloadsButton.height)
                  Text { textFormat: Text.PlainText; id: downloadsTitle; objectName: "downloadsTitle"; anchors.verticalCenter: parent.verticalCenter; text: "DOWNLOADS"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.4 }
                  Text { textFormat: Text.PlainText; anchors.left: downloadsTitle.right; anchors.leftMargin: Style.spacing.sm; anchors.right: hideDownloadsButton.left; anchors.rightMargin: Style.spacing.sm; anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight
                         text: "nothing downloads until you approve"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                  CompactModelButton { id: hideDownloadsButton; objectName: "hideDownloadsButton"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "HIDE"; tooltipText: "Hide the download catalog"; foreground: root.cardSoftText; accent: root.accent; bordered: false; focusable: true; onClicked: root.toggleCatalogDownloads() }
                }
                Item {
                  width: parent.width; height: Math.max(catalogFilters.height, catalogDetailsToggle.height)
                  Row {
                    id: catalogFilters
                    objectName: "catalogFilters"
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.spacing.xxs
                    Repeater {
                      model: root.catalogFilterChoices()
                      CompactModelButton {
                        required property var modelData
                        objectName: "catalogFilter"
                        readonly property string filterId: modelData.id
                        text: modelData.label + " · " + modelData.count
                        tooltipText: modelData.tip
                        selected: root.effectiveCatalogFilter === modelData.id
                        foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                        Accessible.name: "Show " + modelData.label.toLowerCase() + " models"
                        onClicked: root.catalogFilter = modelData.id
                      }
                    }
                  }
                  CompactModelButton { id: catalogDetailsToggle; objectName: "catalogDetailsToggle"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                       text: root.selectorDetailsExpanded ? "CATALOG ‹" : "CATALOG ›"; tooltipText: "Where the list comes from and what NOVA knows about this computer"
                                       foreground: root.cardSoftText; accent: root.accent; bordered: false; focusable: true; onClicked: root.selectorDetailsExpanded = !root.selectorDetailsExpanded }
                }
                Column { visible: root.selectorDetailsExpanded; width: parent.width; spacing: Style.spacing.xxs
                  Text { textFormat: Text.PlainText; width: parent.width; text: root.catalogSummary; color: root.catalogSummary.indexOf("UNAVAILABLE") >= 0 || root.catalogSummary.indexOf("MISMATCH") >= 0 ? Commons.Color.urgent : root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 0.5; wrapMode: Text.Wrap }
                  Text { textFormat: Text.PlainText; width: parent.width; text: root.hardwareSummary; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                  Text { textFormat: Text.PlainText; width: parent.width; text: root.recommendationSummary === "RECOMMEND // INSUFFICIENT EVIDENCE" ? "Recommendations need more evidence before NOVA can suggest a model." : root.recommendationSummary; color: root.recommendationSummary.indexOf("UNAVAILABLE") >= 0 ? root.muted : root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                }
                Text { textFormat: Text.PlainText; objectName: "catalogFilterHint"; width: parent.width; wrapMode: Text.Wrap; text: root.catalogFilterHint(); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                Text { textFormat: Text.PlainText; objectName: "budgetNote"; visible: root.budgetNote !== ""; width: parent.width; text: root.budgetNote; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
              Repeater { model: root.orderedCatalogModels; id: catalogModelRepeater; objectName: "catalogModelRepeater"
                Item {
                  required property var modelData
                  width: parent.width
                  visible: root.catalogRowVisible(modelData)
                  height: root.catalogRowVisible(modelData) ? modelEntry.implicitHeight + 2 * Style.spacing.xs + Style.spacing.xs : 0
                  // A card like the installed models (the bottom spacing separates the cards).
                  Rectangle {
                    width: parent.width; height: parent.height - Style.spacing.xs
                    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.03)
                    border.width: 1
                    border.color: root.isExplicitlySelected(modelData) ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.55)
                                                                       : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14)
                  }
                  Column {
                    id: modelEntry
                    x: Style.spacing.sm; y: Style.spacing.xs
                    width: parent.width - 2 * Style.spacing.sm
                    spacing: 3
                    // Two lines: name (and tags) over the facts; size, DETAILS and the action on the right.
                    Item {
                      width: parent.width
                      height: Math.max(catalogWho.height, catalogEnd.height)
                      Column {
                        id: catalogWho
                        anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - catalogEnd.width - Style.spacing.sm
                        spacing: 3
                        Flow {
                          id: catalogTitleFlow
                          width: parent.width
                          spacing: Style.spacing.sm
                          Text { textFormat: Text.PlainText; objectName: "catalogRowTitle"; text: root.friendlyModelName(modelData.catalog_id); color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true; wrapMode: Text.Wrap; width: Math.min(implicitWidth, catalogTitleFlow.width) }
                          Repeater {
                            model: [root.isSwitchDestination(modelData) ? "SWITCHING…" : "", root.isExplicitlySelected(modelData) ? "IN USE" : "", root.catalogIsNew(modelData.catalog_id) ? "NEW" : "",
                                    root.suggestionFor(modelData.catalog_id) && root.effectiveCatalogFilter !== "suggested" ? "SUGGESTED" : "", modelData.user_requested === true ? "YOU ADDED" : ""].filter(function(t) { return t !== "" })
                            delegate: Rectangle {
                              required property string modelData
                              objectName: "catalogTag"
                              readonly property bool filled: modelData === "NEW"
                              width: catalogTagText.implicitWidth + Style.space(8); height: catalogTagText.implicitHeight + Style.space(2)
                              y: Style.space(2)
                              color: filled ? root.accent : "transparent"
                              border.width: 1; border.color: filled || modelData === "IN USE" || modelData === "SUGGESTED" ? root.accent : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.35)
                              Text { textFormat: Text.PlainText; id: catalogTagText; anchors.centerIn: parent; text: parent.modelData; color: parent.filled ? Commons.Color.background : (parent.modelData === "IN USE" || parent.modelData === "SUGGESTED" ? root.accent : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.7)); font.family: root.fontFamily; font.pixelSize: Style.font.caption - 2; font.bold: true; font.letterSpacing: 1 }
                            }
                          }
                        }
                        Row {
                          width: parent.width; spacing: Style.spacing.xs
                          FactWord { id: catalogFitWord; objectName: "catalogFitWord"; anchors.verticalCenter: parent.verticalCenter; text: root.fitWord(root.rowFit(modelData)); tone: root.fitTone(root.rowFit(modelData)) }
                          Text { textFormat: Text.PlainText; objectName: "catalogFacts"; anchors.verticalCenter: parent.verticalCenter; width: parent.width - (catalogFitWord.visible ? catalogFitWord.width + parent.spacing : 0); elide: Text.ElideRight
                                 text: root.catalogFacts(modelData); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                        }
                      }
                      // Right to left: the action at the edge, then DETAILS, then the size.
                      Row {
                        id: catalogEnd
                        objectName: "catalogActions"
                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        layoutDirection: Qt.RightToLeft
                        spacing: Style.spacing.xxs
                        CompactModelButton {
                          objectName: "installModelButton"
                          visible: root.acquisitionState(modelData) === "AVAILABLE TO INSTALL"
                          text: "INSTALL"
                          tooltipText: "Create a new installation proposal for " + root.catalogRuntimeId(modelData) + "; nothing downloads until you approve"
                          foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                          selected: true  // the card's one primary action
                          enabled: !root.modelsUnavailable && !root.chatBusy && !root.modelTransitionActive && root.pendingApproval === null && root.trackedAcquisitionId === ""
                          Accessible.name: "Install " + root.catalogRuntimeId(modelData)
                          onClicked: root.proposeAcquisition(modelData)
                        }
                        CompactModelButton {
                          objectName: "activateModelButton"
                          visible: root.catalogIsInstalled(modelData) && !root.isExplicitlySelected(modelData)
                          text: "LOAD AND USE " + root.catalogDisplayName(modelData.catalog_id).toUpperCase()
                          tooltipText: "Load and verify " + root.catalogRuntimeId(modelData) + ", then make it the selected model"
                          foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                          enabled: !root.modelsUnavailable && !root.modelTransitionActive && !root.chatBusy && root.pendingApproval === null && root.trackedAcquisitionId === ""
                          Accessible.name: text
                          onClicked: root.selectModel(root.catalogRuntimeId(modelData), true)
                        }
                        CompactModelButton {
                          id: modelDetailsButton
                          objectName: "modelDetailsButton"
                          text: root.modelDetailsOpen(modelData.catalog_id) ? "LESS" : "DETAILS"
                          tooltipText: root.modelDetailsOpen(modelData.catalog_id) ? "Hide this model's details" : "Who suggests it, how sure NOVA is, and the catalog's facts"
                          foreground: enabled ? root.cardSoftText : root.muted
                          accent: root.accent; bordered: false; focusable: true
                          opacity: enabled ? 1 : 0.46
                          Accessible.name: (root.modelDetailsOpen(modelData.catalog_id) ? "Hide details of " : "Details of ") + root.friendlyModelName(modelData.catalog_id)
                          onClicked: root.toggleModelDetails(modelData.catalog_id)
                        }
                        Text { textFormat: Text.PlainText; id: catalogSize; anchors.verticalCenter: parent.verticalCenter; rightPadding: Style.spacing.xs; text: root.catalogSizeLabel(modelData); color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                      }
                    }
                    Rectangle {
                      id: acquisitionStatusBadge
                      objectName: "acquisitionStatusBadge"
                      readonly property bool warning: root.acquisitionState(modelData) === "FAILED" || root.acquisitionState(modelData) === "RECOVERY_REQUIRED"
                      // Only when there is something to say ("Available to install." is what INSTALL says).
                      visible: root.acquisitionPlainLabel(modelData) !== "" && root.acquisitionState(modelData) !== "AVAILABLE TO INSTALL"
                      width: parent.width
                      height: visible ? acquisitionStatusBadgeText.implicitHeight + Style.spacing.sm : 0
                      color: Qt.rgba((warning ? Commons.Color.urgent : root.foreground).r, (warning ? Commons.Color.urgent : root.foreground).g, (warning ? Commons.Color.urgent : root.foreground).b, 0.045)
                      border.width: 1
                      border.color: warning ? Qt.rgba(Commons.Color.urgent.r, Commons.Color.urgent.g, Commons.Color.urgent.b, 0.65) : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.18)
                      Text { textFormat: Text.PlainText; id: acquisitionStatusBadgeText; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.leftMargin: Style.spacing.xs; anchors.rightMargin: Style.spacing.xs; text: root.acquisitionPlainLabel(modelData); color: acquisitionStatusBadge.warning ? Commons.Color.urgent : root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                    }
                    Text { textFormat: Text.PlainText; visible: root.recoveryAllowsRetry(root.acquisitionFor(modelData.catalog_id)); width: parent.width; text: "Previous attempt interrupted · recovery reviewed · fresh approval required"; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                    CompactModelButton {
                      objectName: "reviewRecoveryButton"
                      visible: { var record = root.acquisitionFor(modelData.catalog_id); return record && record.state === "RECOVERY_REQUIRED" && !record.recovery_resolution }
                      text: "Review recovery"
                      foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                      enabled: !root.chatBusy && !root.modelTransitionActive && root.pendingApproval === null && root.trackedAcquisitionId === ""
                      onClicked: root.reviewRecovery(modelData)
                    }
                    AcquisitionProgress { modelData: modelEntry.parent.modelData }
                    Column { visible: root.modelDetailsOpen(modelData.catalog_id); width: parent.width; spacing: Style.spacing.xxs; topPadding: Style.space(2)
                      Rectangle { width: parent.width; height: 1; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.1) }
                      Text { objectName: "catalogSuggestion"; visible: root.suggestionLine(modelData.catalog_id) !== ""; width: parent.width; text: root.suggestionLine(modelData.catalog_id); textFormat: Text.PlainText; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                      Text { textFormat: Text.PlainText; objectName: "catalogFitText"; width: parent.width; text: root.rowFitSummary(modelData); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                      Text { textFormat: Text.PlainText; width: parent.width; text: root.rowContextSummary(modelData); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                      Text { textFormat: Text.PlainText; width: parent.width; text: "Working context is how much conversation and reference material the model can work with at once."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                      Text { textFormat: Text.PlainText; width: parent.width; text: root.catalogPropertiesSummary(modelData); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                      Text { textFormat: Text.PlainText; width: parent.width; text: root.catalogFitConfidenceSummary(modelData); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                      Text { textFormat: Text.PlainText; visible: root.acquisitionDetail(modelData) !== ""; width: parent.width; text: root.acquisitionDetail(modelData); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                      Text { visible: root.acquisitionHistory(modelData) !== ""; width: parent.width; text: root.acquisitionHistory(modelData); textFormat: Text.PlainText; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
                      CompactModelButton {
                        objectName: "removeRequestedButton"
                        visible: modelData.user_requested === true
                        text: "REMOVE FROM LIST"
                        tooltipText: "Remove this model you added from the list. An installed copy stays installed."
                        foreground: enabled ? root.foreground : root.muted
                        accent: root.accent; bordered: true; focusable: visible
                        enabled: root.canRemoveRequested(modelData)
                        opacity: enabled ? 1 : 0.46
                        Accessible.name: "Remove " + root.friendlyModelName(modelData.catalog_id) + " from the list"
                        onClicked: root.removeRequestedModel(modelData)
                      }
                    }
                  }
                }
              }
                // Not in the list: any Ollama model, looked up first (nothing downloads until approved).
                Item { width: 1; height: Style.spacing.xs }
                Rectangle { width: parent.width; height: 1; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12) }
                Item {
                  objectName: "addModelRow"
                  width: parent.width; height: Math.max(addModelInput.height, addModelButton.height)
                  Text { textFormat: Text.PlainText; id: addModelTitle; objectName: "addModelTitle"; anchors.verticalCenter: parent.verticalCenter; text: "NOT LISTED?"; color: root.cardSoftText; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 1; font.letterSpacing: 1.2 }
                  GuardedTextField {
                    id: addModelInput
                    objectName: "addModelInput"
                    anchors.left: addModelTitle.right; anchors.leftMargin: Style.spacing.sm; anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - addModelTitle.width - addModelButton.implicitWidth - 2 * Style.spacing.sm
                    placeholderText: "any Ollama name or ollama.com link, e.g. gemma4:12b"
                    enabled: !root.addModelBusy
                    foreground: root.foreground
                    accent: root.accent
                    onAccepted: root.addRequestedModel(text)
                    onPasteRefused: function(reason) { root.addModelFailed = true; root.addModelStatus = reason }
                  }
                  CompactModelButton { id: addModelButton; objectName: "addModelButton"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.addModelBusy ? "LOOKING UP…" : "ADD"; tooltipText: "Look up this model in Ollama's library; nothing downloads"; foreground: root.foreground; accent: root.accent; bordered: true; focusable: true; enabled: !root.addModelBusy && addModelInput.text.trim() !== ""; onClicked: root.addRequestedModel(addModelInput.text) }
                }
                Text { textFormat: Text.PlainText; objectName: "addModelStatusText"; visible: root.addModelStatus !== ""; width: parent.width; wrapMode: Text.Wrap; text: root.addModelStatus; color: root.addModelFailed ? Commons.Color.urgent : root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              }
            }
          }
          Rectangle {
            id: modelScrollCue
            objectName: "modelScrollCue"
            anchors.top: modelList.top
            anchors.right: parent.right
            anchors.rightMargin: Style.spacing.sm
            width: Math.max(2, Style.normalBorderWidth)
            height: modelList.height
            visible: modelList.visible && modelList.height > 0 && modelList.contentHeight > modelList.height + 1
            color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14)
            readonly property real scrollRange: Math.max(0, modelList.contentHeight - modelList.height)
            Rectangle {
              id: modelScrollThumb
              objectName: "modelScrollThumb"
              width: parent.width
              height: Math.min(parent.height, Math.max(8, parent.height * modelList.height / Math.max(modelList.height, modelList.contentHeight)))
              y: modelScrollCue.scrollRange > 0 ? (parent.height - height) * modelList.contentY / modelScrollCue.scrollRange : 0
              color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.72)
            }
          }
        }

        // UX Phase 2 step 2b (mock): what's in GPU memory and the versions scroll below the
        // telemetry, so the MACHINE core keeps its size on short screens.
        Flickable {
          id: machineDetailsViewport
          objectName: "machineDetailsViewport"
          visible: root.shownView === "machine"
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.top: fixedTop.bottom
          anchors.topMargin: Style.spacing.md
          anchors.bottom: fixedBottom.top
          anchors.bottomMargin: Style.spacing.md
          clip: true
          contentWidth: width
          contentHeight: machineDetails.implicitHeight
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
            Column {
              id: machineDetails
              objectName: "machineDetails"
              width: machineDetailsViewport.width
              spacing: Style.spacing.xs
              Text { textFormat: Text.PlainText; text: "IN " + root.memoryWord.toUpperCase(); color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.4 }
              Text {
                textFormat: Text.PlainText
                objectName: "gpuMemoryEmpty"
                visible: root.residentModels.length === 0
                width: parent.width; wrapMode: Text.Wrap
                text: "Nothing is in " + root.memoryWord + "." + (root.onboardingExplicitSelection ? " The selected model loads with your next message." : "")
                color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption
              }
              Repeater {
                objectName: "gpuMemoryRepeater"
                model: root.residentModels
                delegate: Item {
                  required property var modelData
                  width: parent.width
                  height: gpuMemoryRow.implicitHeight + 2 * Style.spacing.sm
                  Rectangle { anchors.fill: parent; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.03); border.width: 1; border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) }
                  Column {
                    id: gpuMemoryRow
                    x: Style.spacing.sm; y: Style.spacing.sm
                    width: parent.width - 2 * Style.spacing.sm
                    spacing: 3
                    Item {
                      width: parent.width
                      height: Math.max(gpuMemoryName.implicitHeight, gpuMemorySize.implicitHeight)
                      Text { textFormat: Text.PlainText; id: gpuMemoryName; objectName: "gpuMemoryModelName"; width: parent.width - gpuMemorySize.implicitWidth - Style.spacing.sm; elide: Text.ElideRight; text: root.modelPlainName(modelData.id); color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; font.bold: true }
                      Text { textFormat: Text.PlainText; id: gpuMemorySize; objectName: "gpuMemoryModelSize"; anchors.right: parent.right; text: root.residentSizeLabel(modelData); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                    }
                    Item {
                      width: parent.width
                      height: Math.max(gpuMemoryNote.implicitHeight, freeGpuMemoryButton.height)
                      Text { textFormat: Text.PlainText; id: gpuMemoryNote; anchors.left: parent.left; anchors.right: freeGpuMemoryButton.left; anchors.rightMargin: Style.spacing.sm; anchors.verticalCenter: parent.verticalCenter; wrapMode: Text.Wrap
                             text: root.removalStatusModel === modelData.id && root.removalStatus !== "" ? root.removalStatus : "unloads by itself 10 min after last use"
                             color: root.removalStatusModel === modelData.id && root.removalFailed ? Commons.Color.urgent : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.55); font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                      CompactModelButton {
                        id: freeGpuMemoryButton
                        objectName: "freeGpuMemoryButton"
                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        text: "FREE NOW"
                        tooltipText: root.unloadTooltip(modelData)
                        foreground: root.foreground; accent: root.accent; bordered: true; focusable: true
                        enabled: !root.removalBusy
                        Accessible.name: "Free " + root.memoryWord + " used by " + modelData.id
                        onClicked: root.unloadModel(modelData)
                      }
                    }
                  }
                }
              }
              Item { width: 1; height: Style.spacing.xs }
              Text { textFormat: Text.PlainText; text: "VERSIONS"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.4 }
              Text {
                textFormat: Text.PlainText
                objectName: "machineVersions"
                width: parent.width; wrapMode: Text.Wrap; lineHeight: 1.3
                text: "NOVA " + root.displayUiIdentity
                  + "\nOllama " + (root.providerVersion !== "" ? root.providerVersion : (root.ollamaInstalled ? "version unknown" : "not installed"))
                  + "\n" + root.graphicsLine()
                  + (root.modelListLine() !== "" ? "\n" + root.modelListLine() : "")
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.7); font.family: root.fontFamily; font.pixelSize: Style.font.caption
              }
              // REMOVE-01: removing NOVA lives at the very bottom of MACHINE (and in the bar icon's menu).
              Item { width: 1; height: Style.spacing.sm }
              Rectangle { width: parent.width; height: 1; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12) }
              Item {
                width: parent.width; height: Math.max(removeNovaButton.height, removeNovaHint.implicitHeight)
                CompactModelButton { id: removeNovaButton; objectName: "removeNovaButton"; anchors.verticalCenter: parent.verticalCenter; text: "REMOVE NOVA…"
                                     tooltipText: "Shows what goes and asks first"; foreground: root.destructiveColor; accent: root.destructiveColor; bordered: true; focusable: true
                                     onClicked: root.openRemoveNova() }
                Text { id: removeNovaHint; textFormat: Text.PlainText; anchors.left: removeNovaButton.right; anchors.leftMargin: Style.spacing.sm; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; wrapMode: Text.Wrap
                       text: "Removes NOVA, and if you want its models and Ollama too. It shows what goes and asks first."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption - 1 }
              }
            }
        }

        Column {
          id: fixedBottom
          objectName: "fixedBottom"
            anchors.left: parent.left
          anchors.right: parent.right
          anchors.bottom: parent.bottom
          spacing: Style.spacing.sm

          Text { textFormat: Text.PlainText; visible: root.approvalFeedback !== "" && !root.pendingApproval; width: parent.width; text: root.approvalFeedback; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
          Text { textFormat: Text.PlainText; visible: root.chatContextNotice !== ""; width: parent.width; text: root.chatContextNotice; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
          Text { textFormat: Text.PlainText; visible: root.chatSessionSetupInFlight || root.chatRetentionPending; width: parent.width; text: root.chatSessionSetupInFlight ? "STARTING CONVERSATION CONTEXT…" : "CHECKING CONVERSATION CONTEXT…"; color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 0.8; wrapMode: Text.Wrap }
          Text { textFormat: Text.PlainText; visible: root.chatBusy; text: root.effectiveState === "tool_call" ? "INSPECTING // " + (root.currentTool || "DIAGNOSTICS").replace(/_/g, " ").toUpperCase() : (root.effectiveState === "approval_required" ? "AWAITING APPROVAL // " + (root.currentTool || "ACTION").replace(/_/g, " ").toUpperCase() : "NOVA // " + root.stateWords(root.effectiveState)); color: root.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1 }
          Text { textFormat: Text.PlainText; objectName: "chatWaitNotice"; visible: root.chatWaitNotice !== "" && !root.chatBusy && root.pendingApproval === null; width: parent.width; text: root.chatWaitNotice; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
          Text { textFormat: Text.PlainText; objectName: "modelLoadingHint"; visible: root.chatBusy && root.chatLoadsModel && root.effectiveState === "loading_model"; width: parent.width; text: "Loading " + root.modelPlainName(root.activeModel) + " into " + root.memoryWord + ": it was unloaded after 10 idle minutes. This usually takes 10–30 seconds, then NOVA replies."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
          Text { textFormat: Text.PlainText; objectName: "slowFirstReplyHint"; visible: root.slowFirstReply; width: parent.width; text: "Reading your message. The first reply can take a minute, longer on computers without a graphics card."; color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap }
          Text { textFormat: Text.PlainText; visible: root.chatError !== ""; text: root.chatError.toUpperCase(); color: Commons.Color.urgent; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 0.8; wrapMode: Text.WordWrap; width: parent.width }
          Row {
            objectName: "chatInputRow"
            visible: root.shownView === "chat"  // the message box lives in CHAT only (2026-10-09)
            width: parent.width
            spacing: Style.spacing.sm
            GuardedTextField {
              id: chatInput
              objectName: "chatInput"
              width: parent.width - newChatButton.implicitWidth - sendButton.implicitWidth - parent.spacing * 2
              placeholderText: root.chatPlaceholder()
              enabled: root.online && !root.chatBusy && !root.chatContextOperationActive && !root.chatResetInFlight && !root.modelTransitionActive && root.pendingApproval === null
              foreground: root.foreground
              accent: root.accent
              onAccepted: root.sendChat()
              onPasteRefused: function(reason) { root.chatError = reason }
              Keys.onPressed: function(event) { root.noteChatKeyPressed(event) }
              Keys.onReleased: function(event) { root.noteApprovalKeyReleased(event) }
              Keys.onEscapePressed: { if (!root.handleApprovalShortcut("deny")) root.close() }
            }
            Ui.PanelActionButton {
              id: newChatButton
              anchors.verticalCenter: parent.verticalCenter
              iconText: "󰐕"
              tooltipText: root.chatResetInFlight ? "Starting a new chat conversation"
                : (root.chatBusy || root.chatContextOperationActive
                   ? "Wait for the current conversation operation" : "Start a new chat conversation")
              foreground: root.foreground
              hoverColor: root.accent
              bordered: true
              enabled: root.online && !root.chatBusy && !root.chatContextOperationActive && !root.chatResetInFlight
                       && !root.modelTransitionActive && root.pendingApproval === null
              onClicked: root.startChatReset()
            }
            Ui.PanelActionButton {
              id: sendButton
              anchors.verticalCenter: parent.verticalCenter
              iconText: root.chatBusy || root.chatContextOperationActive ? "󰑓" : "󰒭"
              tooltipText: root.chatBusy ? "NOVA is busy"
                : (root.chatSessionSetupInFlight ? "Starting conversation context"
                : (root.chatRetentionPending ? "Checking conversation context"
                : (root.onboardingCanSend
                   ? (root.acceptanceSimulation ? "Show canned fixture reply" : "Send message")
                   : (root.acceptanceSimulation && root.acceptanceSimulationReplyShown
                      ? "Canned first reply already shown" : "Local model is not ready"))))
              foreground: root.foreground
              hoverColor: root.accent
              bordered: true
              enabled: root.online && root.onboardingCanSend && !root.chatBusy && !root.chatContextOperationActive && !root.chatResetInFlight && !root.modelTransitionActive && chatInput.text.trim() !== ""
                       && root.pendingApproval === null
              onClicked: root.sendChat()
            }
          }
          Rectangle { width: parent.width; height: 1; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) }
          Row {
            width: parent.width
            Text { textFormat: Text.PlainText; id: refreshHint; objectName: "refreshHint"; text: root.refreshAge(); color: root.muted; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.letterSpacing: 0.7 }
          }
        }
      }
    }
  }
}
