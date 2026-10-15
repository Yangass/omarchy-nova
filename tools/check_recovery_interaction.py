#!/usr/bin/env python3
"""Exercise P1-01E.5 through canonical Panel.qml with isolated HTTP fixtures."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Thread

import fake_nova
import onboarding_preview as preview


QML_CHECK = r'''
  NovaClient { id: fakeNova; socketPath: "@SOCKET@" }
  TestCase {
    name: "IsolatedRecoveryInteraction"
    when: novaPanel.opened && novaPanel.catalogModels.length > 0
    function check(value, message) { if (!value) throw new Error(message) }
    function child(name) {
      var item=findChild(novaPanel,name)
      if (item===null) {
        var rows=findChild(novaPanel,"catalogModelRepeater")
        if (rows && rows.count) item=findChild(rows.itemAt(0),name)
      }
      check(item!==null,"Missing "+name); return item
    }
    function click(item) { var c=child("panelKeyCatcher"), p=item.mapToItem(c,item.width/2,item.height/2); mouseClick(c,p.x,p.y,Qt.LeftButton) }
    function clickModelControl(name) {
      if (!novaPanel.catalogDownloadsExpanded) {
        var browse=child("browseModelDownloadsButton"), browserList=child("modelList")
        var browseTop=browse.mapToItem(browserList.contentItem,0,0).y
        browserList.contentY=Math.max(0,Math.min(browserList.contentHeight-browserList.height,browseTop-12))
        wait(100); click(browse); wait(100)
        check(novaPanel.catalogDownloadsExpanded,"download catalog did not open")
      }
      var item=child(name), list=child("modelList"), top=item.mapToItem(list.contentItem,0,0).y
      list.contentY=Math.max(0,Math.min(list.contentHeight-list.height,top+item.height-list.height+2)); wait(100)
      click(item)
    }
    function setRecord(id, resolution) {
      var model=shell.fixture.catalog_models[0], records={}
      records[model.catalog_id]={id:id,state:"RECOVERY_REQUIRED",error:"provider_failure",updated_at:1789498367,
        recovery_resolution:resolution||null,historical_progress:null,failure_detail:null}
      novaPanel.acquisitionByCatalog=records
      wait(150)
      return model
    }
    function waitAssessment() {
      tryVerify(function(){ return novaPanel.pendingApproval && novaPanel.pendingApproval.assessment !== null },3000)
    }
    function test_recovery() {
      var results=[]
      function test(name,fn){try{fn();results.push({name:name,passed:true})}catch(e){results.push({name:name,passed:false,error:String(e)})}}
      previewHost.novaClient = fakeNova; previewHost.acceptancePreview=false
      var model=shell.fixture.catalog_models[0]
      test("resolved retry history permits eligible install",function(){
        setRecord("fixture-resolved",{outcome:"RETRY_ALLOWED",retry_allowed:true,basis:"OPERATOR_ATTESTATION"})
        var p={}; p[model.catalog_id]={status:"ELIGIBLE"}; novaPanel.preflightByCatalog=p
        check(novaPanel.acquisitionState(model)==="AVAILABLE TO INSTALL","resolved history masks eligible preflight: "+novaPanel.acquisitionState(model))
        check(novaPanel.acquisitionHistory(model).indexOf("Recovery reviewed")>=0,"history missing")
      })
      test("unresolved review uses foreground and preserves layout transcript draft",function(){
        setRecord("fixture-keyboard",null); novaPanel.panelView = "models"
        wait(200)
        novaPanel.addChat("assistant","preserved transcript"); var input=child("chatInput"); input.text="preserved draft"
        clickModelControl("reviewRecoveryButton"); waitAssessment()
        check(novaPanel.approvalForeground && novaPanel.shownView==="chat" && !child("modelSelector").visible,"review lacks foreground")
        check(novaPanel.chatHistoryCount>0 && input.text==="preserved draft","review lost transcript or draft")
        keyClick(Qt.Key_Return); wait(100)
        check(novaPanel.pendingApproval.decision===null||novaPanel.pendingApproval.decision==="","unconfirmed Enter submitted")
        var box=child("recoveryConfirmationControl"); box.forceActiveFocus(); keyClick(Qt.Key_Space)
        check(novaPanel.pendingApproval.operatorConfirmed===true,"Space did not explicitly confirm")
        keyClick(Qt.Key_Return); wait(500)
        check(novaPanel.pendingApproval.decision==="resolved","fresh Enter did not resolve")
        check(novaPanel.approvalFeedback.indexOf("No download started")>=0,"resolution implies a download")
        click(child("dismissApprovalButton")); wait(100)
        check(novaPanel.pendingApproval===null && novaPanel.panelView==="chat","NOVA left CHAT after the review")
        check(input.text==="preserved draft","draft lost after recovery")
      })
      test("mouse confirmation and resolve share the same once-only path",function(){
        setRecord("fixture-pointer",null); novaPanel.panelView = "models"; clickModelControl("reviewRecoveryButton"); waitAssessment()
        click(child("recoveryConfirmationHitTarget")); check(novaPanel.pendingApproval.operatorConfirmed,"mouse confirmation failed")
        click(child("approveApprovalHitTarget")); click(child("approveApprovalHitTarget")); wait(500)
        check(novaPanel.pendingApproval.decision==="resolved","mouse resolve failed")
        click(child("dismissApprovalButton")); wait(100)
      })
      test("Escape closes unresolved review without resolution",function(){
        setRecord("fixture-cancel",null); novaPanel.panelView = "models"; clickModelControl("reviewRecoveryButton"); waitAssessment()
        keyClick(Qt.Key_Escape); wait(100)
        check(novaPanel.pendingApproval===null,"Escape did not close review")
        check(novaPanel.approvalFeedback.indexOf("No download started")>=0,"Escape feedback missing")
      })
      test("stale assessment shows error and cannot duplicate",function(){
        setRecord("fixture-stale",null); novaPanel.panelView = "models"; clickModelControl("reviewRecoveryButton"); waitAssessment()
        click(child("recoveryConfirmationHitTarget")); keyClick(Qt.Key_Return); wait(400)
        check(novaPanel.approvalFeedback.indexOf("assessment changed")>=0,"stale error hidden")
        keyClick(Qt.Key_Return); wait(100)
        check(novaPanel.pendingApproval.decision==="unavailable","stale result replayed")
        check(child("reviewRecoveryAgainControl").visible,"check-again action missing")
        click(child("dismissApprovalButton")); wait(100)
      })
      test("blocked mismatch exposes facts without resolve",function(){
        setRecord("fixture-mismatch",null); novaPanel.panelView = "models"; clickModelControl("reviewRecoveryButton"); waitAssessment()
        check(novaPanel.pendingApproval.assessment.artifact_state==="MISMATCH","mismatch lost")
        check(!child("approveApprovalControl").visible,"blocked assessment offers resolve")
        check(novaPanel.approvalFeedback.indexOf("cannot be resolved")>=0,"blocked explanation missing")
        keyClick(Qt.Key_Escape); wait(100)
      })
      test("exact installed resolution remains installed",function(){
        var installed=JSON.parse(JSON.stringify(model)); installed.installed_match={installed:true,match_status:"exact",installed_model_id:"qwen3:8b"}
        setRecord("fixture-exact",{outcome:"EXACT_INSTALLED",retry_allowed:false,basis:"MACHINE_VERIFIED_IDENTITY"})
        check(novaPanel.acquisitionState(installed)==="INSTALLED","exact resolution not installed")
      })
      console.warn("RECOVERY_INTERACTION_RESULTS "+JSON.stringify(results))
    }
  }
'''


def main():
    requests, resolved = [], set()
    base_assessment = {
        "artifact_state": "ABSENT", "artifact_detail": None,
        "provider_activity": "UNKNOWN", "resolution_id": None,
        "resolution_status": "AVAILABLE", "operator_attestation_required": True,
        "retry_allowed": False, "limitations": ["PROVIDER_ACTIVITY_NOT_MACHINE_VERIFIED"],
    }

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def send(self, status, body):
            raw=json.dumps(body).encode(); self.send_response(status)
            self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
        def do_GET(self):
            requests.append(("GET",self.path,None))
            if self.path.startswith("/acquisitions/") and self.path.endswith("/recovery"):
                identifier=self.path.split("/")[2]; body=dict(base_assessment,acquisition_id=identifier,assessment_id=(identifier.encode().hex()+"0"*64)[:64])
                if identifier=="fixture-mismatch": body.update(artifact_state="MISMATCH",resolution_status="BLOCKED",operator_attestation_required=False)
                self.send(200,body); return
            if self.path=="/acquisitions":
                rows=[]
                for identifier in resolved:
                    rows.append({"id":identifier,"state":"RECOVERY_REQUIRED","error":"provider_failure","updated_at":1789498367,
                                 "preflight":{"descriptor":{"catalog_id":preview.CATALOG_ID}},
                                 "recovery_resolution":{"outcome":"RETRY_ALLOWED","retry_allowed":True,"basis":"OPERATOR_ATTESTATION"}})
                self.send(200,{"acquisitions":rows}); return
            self.send(404,{"error":"fixture_not_found"})
        def do_POST(self):
            raw=self.rfile.read(int(self.headers.get("Content-Length",0))); body=json.loads(raw)
            requests.append(("POST",self.path,body)); identifier=self.path.split("/")[2]
            if identifier=="fixture-stale": self.send(409,{"error":"stale_recovery_assessment"}); return
            assert body=={"assessment_id":(identifier.encode().hex()+"0"*64)[:64],"operator_attestation":"NO_PROVIDER_ACQUISITION_ACTIVE"}
            resolved.add(identifier)
            self.send(201,{"outcome":"RETRY_ALLOWED","retry_allowed":True,"basis":"OPERATOR_ATTESTATION"})

    with fake_nova.server(Stub) as server:
        Thread(target=server.serve_forever,daemon=True).start()
        with tempfile.TemporaryDirectory(prefix="nova-recovery-check-",dir="/tmp") as raw:
            target=Path(raw); fixture=preview.scenarios()["recovery-unresolved"]
            shell=preview.prepare_config(target,fixture)
            text=shell.read_text().replace("import QtQuick\n","import QtQuick\nimport QtTest\n",1)
            text=text.rsplit("\n}",1)[0]+QML_CHECK.replace("@SOCKET@",server.server_address)+"\n}\n"
            shell.write_text(text)
            result=subprocess.run(["quickshell","--path",str(target)],capture_output=True,text=True,timeout=45)
            output=result.stdout+result.stderr
        server.shutdown()
    marker="RECOVERY_INTERACTION_RESULTS "
    line=next((x.split(marker,1)[1] for x in output.splitlines() if marker in x),None)
    if line is None: print(output); raise SystemExit("Recovery interaction harness did not complete")
    results=json.loads(line)
    for row in results: print(("PASS " if row["passed"] else "FAIL ")+row["name"]+(": "+row["error"] if not row["passed"] else ""))
    if any(not row["passed"] for row in results):
        print(output)
    posts=[r for r in requests if r[0]=="POST"]
    if [p[1] for p in posts].count("/acquisitions/fixture-keyboard/resolve-recovery")!=1 or [p[1] for p in posts].count("/acquisitions/fixture-pointer/resolve-recovery")!=1 or [p[1] for p in posts].count("/acquisitions/fixture-stale/resolve-recovery")!=1:
        raise SystemExit("Unexpected recovery POST cardinality: "+repr(posts))
    print("PASS isolated resolution requests were submitted exactly once with assessment and explicit attestation")
    if any(not row["passed"] for row in results): raise SystemExit(1)


if __name__ == "__main__": main()
