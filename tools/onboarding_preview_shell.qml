import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons

ShellRoot {
  id: shell
  property var fixture: @FIXTURE_JSON@

  QtObject {
    id: previewBar
    property string position: "top"
    property real barSize: 36
    property color foreground: Commons.Color.foreground
    property color barForeground: Commons.Color.foreground
    property string fontFamily: Style.font.family
    property var activePopout: null
    property var clickTargets: []
    function requestPopout(item) { activePopout = item }
    function releasePopout(item) { if (activePopout === item) activePopout = null }
    function targetBelongsToWindow(item, window) { return false }
    function switchPanelFrom(item, direction) { return false }
  }

  QtObject {
    id: previewHost
    property bool acceptancePreview: true
    property var acceptancePreviewFixture: shell.fixture
    // No NOVA behind the preview: the panel's requests fail like an offline NOVA. A check
    // can set a NovaClient here that points at its own fake novad socket.
    property var novaClient: null
    property bool online: shell.fixture.host.online === true
    property real cpu: 18
    property real memory: 42
    property real memoryUsed: 13.4
    property real memoryTotal: 31.8
    property bool gpuAvailable: true
    property string gpuName: "Fixture GPU"
    property real gpu: 7
    property real vramUsed: 4.2
    property real vramTotal: 16
    property real gpuTemperature: 44
    property real gpuTemperatureLimit: 90
    property real cpuTemperature: 52
    property real cpuTemperatureLimit: 95
    property string cpuTemperatureSource: "AMD Ryzen limit"
    property real gpuPower: 38
    property real disk: 51
    property real diskUsed: 214
    property real diskTotal: 420
    property string diskMount: "/"
    property real systemTemperature: 39
    property string temperatureLabel: "nvme:fixture sensor"
    property string runtimeState: shell.fixture.host.runtime_state || "idle"
    property string currentTool: ""
    property string activeProvider: shell.fixture.host.active_provider || ""
    property string activeModel: shell.fixture.host.active_model || ""
    property bool activeModelAvailable: shell.fixture.host.active_available === true
    property bool activeModelRuntimeReady: shell.fixture.host.runtime_ready === true
    property string modelTarget: ""
    property string modelPhase: ""
    property var cpuHistory: [8, 12, 10, 16, 18]
    property var memoryHistory: [39, 40, 40, 41, 42]
    property var gpuHistory: [0, 4, 8, 3, 7]
    property var vramHistory: [24, 25, 26, 26, 26]
    property string gpuDriver: "fixture-driver"
    property real netDown: 2048
    property real netUp: 512
    property var netDownHistory: [0, 1024, 4096, 2048, 2048]
    property var netUpHistory: [0, 256, 512, 512, 512]
    property double updatedAt: Date.now()
    property bool popoutSwitchClosing: false
    function refresh() { }
    function refreshModelState() { }
  }

  PanelWindow {
    id: previewAnchorWindow
    visible: true
    screen: Quickshell.screens.length ? Quickshell.screens[0] : null
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "nova-onboarding-acceptance-preview-anchor"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    anchors { top: true; left: true; right: true }
    implicitHeight: 36

    Item {
      id: previewAnchor
      anchors.horizontalCenter: parent.horizontalCenter
      width: 36
      height: 36
    }
  }

  Panel {
    id: novaPanel
    anchorItem: previewAnchor
    bar: previewBar
    hostWidget: previewHost
    Component.onCompleted: Qt.callLater(function() { novaPanel.open() })
  }

}
