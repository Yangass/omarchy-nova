import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.BarWidget {
  id: root
  moduleName: "io.github.yangass.nova"
  // These remain false/null in the deployed shell. A standalone repository
  // preview host sets them explicitly when rendering isolated fixture states.
  property bool acceptancePreview: false
  property var acceptancePreviewFixture: null
  // NOVA's private socket (NovaClient.qml, local_socket.py): the bar and the panel make
  // every request with novaRequest().
  readonly property NovaClient novaClient: NovaClient {}
  function novaRequest() { return novaClient.request() }
  readonly property int refreshMs: Math.max(5000, Number(setting("refreshSec", 10)) * 1000)
  property bool online: false
  property bool probing: false
  property real cpu: -1
  property real memory: -1
  property real memoryUsed: -1
  property real memoryTotal: -1
  property bool gpuAvailable: false
  property string gpuName: "GPU unavailable"
  property string gpuDriver: ""
  // Network speed of physical interfaces in bytes/s (QWEN-14 /telemetry/network); -1 unknown.
  property real netDown: -1
  property real netUp: -1
  property var netDownHistory: []
  property var netUpHistory: []
  property real gpu: -1
  property real vramUsed: -1
  property real vramTotal: -1
  property real gpuTemperature: -1
  property real gpuPower: -1
  property real disk: -1
  property real diskUsed: -1
  property real diskTotal: -1
  property string diskMount: "/"
  property real systemTemperature: -1
  property string temperatureLabel: "Thermals"
  property string runtimeState: "idle"
  property string currentTool: ""
  property string activeProvider: ""
  property string activeModel: ""
  property bool activeModelAvailable: false
  property bool activeModelRuntimeReady: false
  property string modelTarget: ""
  property string modelPhase: ""
  property bool statePolling: false
  property var cpuHistory: []
  property var memoryHistory: []
  property var gpuHistory: []
  property var vramHistory: []
  property string updated: "Not connected"
  property double updatedAt: 0
  // Freshness of actual activity, independent of the slower health/telemetry poll.
  property double runtimeUpdatedAt: 0
  property double gpuSampledAt: 0
  property bool gpuSampling: false
  // Sample GPU load faster only while the panel is open and NOVA reports model work.
  readonly property bool gpuFastSampling: opened && online
    && (["thinking", "generating", "loading_model", "model_switching", "tool_call"].indexOf(runtimeState) >= 0
        || (panelLoader.item && panelLoader.item.cliAgentActive === true))

  function percent(value) { return isFinite(value) && value >= 0 ? Math.round(value) + "%" : "—" }
  function number(value) {
    var parsed = Number(value)
    return isFinite(parsed) ? parsed : -1
  }
  function resetTelemetry() {
    cpu = memory = memoryUsed = memoryTotal = -1
    gpuAvailable = false
    gpuName = "GPU unavailable"
    gpuDriver = ""
    netDown = netUp = -1
    gpu = vramUsed = vramTotal = gpuTemperature = gpuPower = -1
    disk = diskUsed = diskTotal = -1
    diskMount = "/"
    systemTemperature = -1
    temperatureLabel = "Thermals"
  }
  // Raw values (bytes/s); the panel scales them to the recent peak.
  function appendRaw(history, value) {
    if (!isFinite(value) || value < 0) return history
    var next = history.slice(Math.max(0, history.length - 23))
    next.push(value)
    return next
  }
  function appendHistory(history, value) {
    if (!isFinite(value) || value < 0) return history
    var next = history.slice(Math.max(0, history.length - 23))
    next.push(Math.max(0, Math.min(100, value)))
    return next
  }
  function request(path, callback) {
    var xhr = novaRequest()
    var finished = false
    function finish(status, text) {
      if (finished) return
      finished = true
      callback(status, text)
    }
    xhr.onreadystatechange = function() { if (xhr.readyState === XMLHttpRequest.DONE) finish(xhr.status, xhr.responseText) }
    xhr.onerror = function() { finish(0, "") }
    xhr.ontimeout = function() { finish(0, "") }
    xhr.open("GET", path)
    xhr.timeout = 2000
    xhr.send()
  }
  function refreshState() {
    if (statePolling) return
    statePolling = true
    request("/state", function(status, text) {
      statePolling = false
      if (status !== 200) { root.runtimeState = "error"; root.currentTool = ""; root.modelTarget = ""; root.modelPhase = ""; return }
      try {
        var payload = JSON.parse(text)
        var state = payload.state
        root.runtimeUpdatedAt = Date.now()
        root.runtimeState = ["idle", "loading_model", "model_switching", "model_switch_error", "thinking", "tool_call", "approval_required", "executing_action", "verifying_action", "generating", "error"].indexOf(state) >= 0 ? state : "error"
        root.currentTool = root.runtimeState === "tool_call" && typeof payload.tool === "string" ? payload.tool : (typeof payload.action === "string" ? payload.action : "")
        root.modelTarget = (root.runtimeState === "model_switching" || root.runtimeState === "model_switch_error") && typeof payload.model === "string" ? payload.model : ""
        root.modelPhase = typeof payload.phase === "string" ? payload.phase : ""
      } catch (error) { root.runtimeState = "error"; root.currentTool = ""; root.modelTarget = ""; root.modelPhase = "" }
    })
  }
  function refreshModelState() {
    request("/model/active", function(status, text) {
      if (status !== 200) { activeModelAvailable = false; activeModelRuntimeReady = false; return }
      try {
        var payload = JSON.parse(text)
        activeProvider = typeof payload.provider === "string" ? payload.provider : ""
        activeModel = typeof payload.model === "string" ? payload.model : ""
        activeModelAvailable = payload.available === true
        activeModelRuntimeReady = payload.runtime_ready === true
      } catch (error) { activeModelAvailable = false; activeModelRuntimeReady = false }
    })
  }
  function applyGpu(status, text, recordHistory) {
    if (status !== 200) return
    try {
      var data = JSON.parse(text)
      var device = data.gpus && data.gpus.length ? data.gpus[0] : null
      gpuAvailable = data.available === true && !!device
      if (!gpuAvailable) { gpuName = data.reason || "GPU unavailable"; return }
      gpuName = device.name || "GPU"
      gpuDriver = typeof device.driver_version === "string" ? device.driver_version : ""
      gpu = number(device.usage_percent)
      vramUsed = number(device.memory_used_mib) / 1024
      vramTotal = number(device.memory_total_mib) / 1024
      gpuTemperature = number(device.temperature_c)
      gpuPower = number(device.power_w)
      gpuSampledAt = Date.now()
      // Fast samples feed the core wave only; history keeps its refresh spacing.
      if (!recordHistory) return
      gpuHistory = appendHistory(gpuHistory, gpu)
      vramHistory = appendHistory(vramHistory, vramTotal > 0 ? 100 * vramUsed / vramTotal : -1)
    } catch (error) { gpuAvailable = false; gpuName = "GPU unavailable" }
  }
  function refreshGpuSample() {
    if (gpuSampling) return
    gpuSampling = true
    request("/telemetry/gpu", function(status, text) { root.gpuSampling = false; root.applyGpu(status, text, false) })
  }
  function refresh() {
    if (probing) return
    probing = true
    refreshModelState()
    request("/health", function(status, text) {
      root.online = status === 200
      root.probing = false
      if (!root.online) {
        root.updated = "novad unavailable"
        root.resetTelemetry()
        return
      }
      root.updated = "Updated just now"
      root.updatedAt = Date.now()
      request("/telemetry/cpu", function(cpuStatus, cpuText) {
        if (cpuStatus !== 200) return
        try {
          root.cpu = root.number(JSON.parse(cpuText).usage_percent)
          root.cpuHistory = root.appendHistory(root.cpuHistory, root.cpu)
        } catch (error) { root.cpu = -1 }
      })
      request("/telemetry/memory", function(memoryStatus, memoryText) {
        if (memoryStatus !== 200) return
        try {
          var data = JSON.parse(memoryText).memory
          root.memory = root.number(data.usage_percent)
          root.memoryUsed = root.number(data.used_mib) / 1024
          root.memoryTotal = root.number(data.total_mib) / 1024
          root.memoryHistory = root.appendHistory(root.memoryHistory, root.memory)
        } catch (error) { root.memory = root.memoryUsed = root.memoryTotal = -1 }
      })
      request("/telemetry/gpu", function(status, text) { root.applyGpu(status, text, true) })
      request("/telemetry/network", function(status, text) {
        if (status !== 200) return
        try {
          var data = JSON.parse(text)
          var down = data.rx_bytes_per_s, up = data.tx_bytes_per_s
          root.netDown = typeof down === "number" ? down : -1
          root.netUp = typeof up === "number" ? up : -1
          root.netDownHistory = root.appendRaw(root.netDownHistory, root.netDown)
          root.netUpHistory = root.appendRaw(root.netUpHistory, root.netUp)
        } catch (error) { root.netDown = root.netUp = -1 }
      })
      request("/telemetry/disks", function(status, text) {
        if (status !== 200) return
        try {
          var items = JSON.parse(text).filesystems || []
          var item = items.find(function(candidate) { return candidate.mountpoint === "/" }) || items[0]
          if (!item) return
          root.diskMount = item.mountpoint || "/"
          root.disk = root.number(item.usage_percent)
          root.diskUsed = root.number(item.used_gib)
          root.diskTotal = root.number(item.total_gib)
        } catch (error) { root.disk = root.diskUsed = root.diskTotal = -1 }
      })
      request("/telemetry/temperatures", function(status, text) {
        if (status !== 200) return
        try {
          var items = JSON.parse(text).temperatures || []
          if (!items.length) return
          var hottest = items[0]
          for (var index = 1; index < items.length; index++)
            if (root.number(items[index].temperature_c) > root.number(hottest.temperature_c)) hottest = items[index]
          root.systemTemperature = root.number(hottest.temperature_c)
          root.temperatureLabel = hottest.sensor || "Thermals"
        } catch (error) { root.systemTemperature = -1; root.temperatureLabel = "Thermals" }
      })
    })
  }
  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  Component.onCompleted: { refresh(); refreshState(); refreshModelState() }
  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()
  Timer { interval: root.refreshMs; running: true; repeat: true; onTriggered: root.refresh() }
  Timer { interval: 750; running: true; repeat: true; onTriggered: root.refreshState() }
  Timer { interval: 1500; running: root.gpuFastSampling; repeat: true; triggeredOnStart: true; onTriggered: root.refreshGpuSample() }
  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
  }
  IpcHandler {
    target: root.moduleName
    function refresh(): void { root.refresh() }
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
  }
  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // NOVA's own mark (NovaMark.qml), still in the bar; the panel header breathes.
    iconComponent: Component {
      NovaMark { accent: root.online ? Color.accent : Color.urgent; foreground: Color.bar.text; background: Color.bar.background }
    }
    active: root.online
    activeColor: root.online ? Color.accent : Color.urgent
    tooltipText: root.online ? "NOVA online · CPU " + root.percent(root.cpu) + " · RAM " + root.percent(root.memory) : "NOVA offline · click for details"
    onPressed: function(mouseButton) {
      if (mouseButton === Qt.RightButton) root.refresh()
      else if (mouseButton === Qt.LeftButton) root.togglePanel()
    }
  }
}
