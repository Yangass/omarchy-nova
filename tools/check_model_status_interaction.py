#!/usr/bin/env python3
"""Exercise model status and activation controls in canonical Panel.qml."""
import json
from pathlib import Path
import subprocess
import tempfile

import onboarding_preview as preview


QML_CHECK = r'''
  TestCase {
    name: "IsolatedModelStatusInteraction"
    when: novaPanel.opened && novaPanel.catalogModels.length > 0
    function check(value, message) { if (!value) throw new Error(message) }
    function child(name) {
      var item=findChild(novaPanel,name)
      if (item===null) {
        var inst=findChild(novaPanel,"installedModelsRepeater")
        if (inst && inst.count) item=findChild(inst.itemAt(0),name)
      }
      if (item===null) {
        var rows=findChild(novaPanel,"catalogModelRepeater")
        if (rows && rows.count) item=findChild(rows.itemAt(0),name)
      }
      check(item!==null,"Missing "+name); return item
    }
    function click(item) {
      var catcher=child("panelKeyCatcher"), point=item.mapToItem(catcher,item.width/2,item.height/2)
      mouseClick(catcher,point.x,point.y,Qt.LeftButton)
    }
    function record(state, verification) {
      return {id:"fixture-terminal",state:state,verification:verification||"pending",activation:"not_performed",
        preflight:{descriptor:{catalog_id:"qwen3-8b-q4-k-m",artifact_id:"qwen3:8b"}}}
    }
    function test_status_and_actions() {
      var results=[]
      function test(name,fn){try{fn();results.push({name:name,passed:true})}catch(e){results.push({name:name,passed:false,error:String(e)})}}
      var model=shell.fixture.catalog_models[0]
      test("active acquisition becomes truthful installed terminal status",function(){
        novaPanel.approvalFeedback="Approval accepted. Installation status is being checked."
        novaPanel.trackedAcquisitionId="fixture-terminal"
        novaPanel.applyTrackedAcquisition(record("DOWNLOADING"))
        check(novaPanel.acquisitionStatus==="INSTALLING // QWEN 3 8B (Q4_K_M) // DOWNLOADING","active wording wrong")
        novaPanel.applyTrackedAcquisition(record("INSTALLED","matched"))
        check(novaPanel.acquisitionStatus==="INSTALLED AND CHECKED // QWEN 3 8B (Q4_K_M) // NOT IN USE YET","terminal wording wrong")
        check(novaPanel.approvalFeedback.indexOf("Download finished and checked")>=0,"stale approval feedback remains")
        check(novaPanel.trackedAcquisitionId==="","terminal attempt still tracked")
        check(child("globalAcquisitionStatusBadge").visible,"global terminal badge hidden")
      })
      test("failure replaces accepted feedback",function(){
        novaPanel.approvalFeedback="Approval accepted. Installation status is being checked."
        novaPanel.applyTrackedAcquisition(record("FAILED"))
        check(novaPanel.acquisitionStatus==="INSTALLATION FAILED // QWEN 3 8B (Q4_K_M)","failure wording wrong")
        check(novaPanel.approvalFeedback==="Installation failed. Nothing was started.","failure feedback stale")
      })
      test("installed without matched verification does not claim verified",function(){
        novaPanel.applyTrackedAcquisition(record("INSTALLED","pending"))
        check(novaPanel.acquisitionStatus.indexOf("CHECK NOT CONFIRMED")>=0,"unknown verification was promoted")
        check(novaPanel.approvalFeedback.indexOf("couldn't confirm its check")>=0,"unknown verification limitation hidden")
      })
      test("status and details are information while activation is explicit",function(){
        novaPanel.applyTrackedAcquisition(record("INSTALLED","matched")); wait(100)
        if (!novaPanel.catalogDownloadsExpanded) {
          var browse=child("browseModelDownloadsButton"), browseList=child("modelList")
          var browseTop=browse.mapToItem(browseList.contentItem,0,0).y
          browseList.contentY=Math.max(0,Math.min(browseList.contentHeight-browseList.height,browseTop-12))
          wait(100); click(browse); wait(100)
          check(novaPanel.catalogDownloadsExpanded,"download catalog did not open")
        }
        // The exact-installed identity must not repeat as a separate catalog
        // activation row; its load and details live on the installed/discovered row.
        var catalog=child("catalogModelRepeater")
        check(catalog.itemAt(0).visible===false,"installed identity repeated as a catalog row")
        var load=child("loadInstalledModelButton"), details=child("installedModelDetailsButton")
        check(load.visible,"installed load control hidden")
        check(details.visible,"installed details control hidden")
        check(Math.abs(load.y-details.y)<=1 && load.x+load.width<=details.x+1,
              "installed load and details are not grouped without overlap")
        check(child("globalAcquisitionStatusBadge").visible,"terminal status badge hidden")
        var prior=novaPanel.onboardingState
        var list=child("modelList"), top=details.mapToItem(list.contentItem,0,0).y
        list.contentY=Math.max(0,Math.min(list.contentHeight-list.height,top+details.height-list.height+2)); wait(100)
        click(details); wait(100)
        check(novaPanel.installedModelDetailsOpen({provider:"ollama",id:"qwen3:8b"})
              && !novaPanel.installedModelDetailsOpen({provider:"other-provider",id:"qwen3:8b"}),
              "installed details did not expand for exactly the chosen identity")
        check(novaPanel.onboardingState===prior,"details triggered activation")
        top=load.mapToItem(list.contentItem,0,0).y
        list.contentY=Math.max(0,Math.min(list.contentHeight-list.height,top+load.height-list.height+2)); wait(100)
        click(load); wait(100)
        check(novaPanel.onboardingState==="ACTIVATING" && novaPanel.onboardingTarget==="qwen3:8b","activation button used wrong target")
      })
      test("onboarding load action names its target",function(){
        novaPanel.onboardingState="NEEDS_LOAD"; novaPanel.onboardingActions=["LOAD_AND_VERIFY"]
        novaPanel.onboardingTarget="qwen3:30b"; wait(100)
        check(novaPanel.onboardingActionLabel()==="LOAD AND VERIFY QWEN3:30B","load target omitted")
        check(child("onboardingActionButton").text==="LOAD AND VERIFY QWEN3:30B","rendered load target omitted")
      })
      console.warn("MODEL_STATUS_RESULTS "+JSON.stringify(results))
    }
  }
'''


def main():
    fixture = preview.scenarios()["verified-awaiting-activation"]
    with tempfile.TemporaryDirectory(prefix="nova-model-status-", dir="/tmp") as raw:
        target = Path(raw)
        shell = preview.prepare_config(target, fixture)
        text = shell.read_text().replace("import QtQuick\n", "import QtQuick\nimport QtTest\n", 1)
        text = text.rsplit("\n}", 1)[0] + QML_CHECK + "\n}\n"
        shell.write_text(text)
        result = subprocess.run(["quickshell", "--path", str(target)], capture_output=True,
                                text=True, timeout=45)
        output = result.stdout + result.stderr
    marker = "MODEL_STATUS_RESULTS "
    line = next((row.split(marker, 1)[1] for row in output.splitlines() if marker in row), None)
    if line is None:
        print(output)
        raise SystemExit("Model status interaction harness did not complete")
    rows = json.loads(line)
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] +
              ((": " + row["error"]) if not row["passed"] else ""))
    if any(not row["passed"] for row in rows):
        print(output)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
