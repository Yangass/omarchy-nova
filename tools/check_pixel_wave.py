#!/usr/bin/env python3
"""Exercise the production QML living wave (GPU shader + Canvas fallback) on an isolated Wayland display.

Run under a private compositor, never against the production backend. Optional
NOVA_WAVE_EVIDENCE_DIR saves rendered captures and the fixture log.
"""
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time

import onboarding_preview as preview

CHECKS = r'''
  property bool keepActivityFresh: true
  Timer { interval: 500; running: shell.keepActivityFresh; repeat: true; onTriggered: previewHost.runtimeUpdatedAt = Date.now() }
  TestCase {
    name: "PixelWave"
    parent: findChild(novaPanel, "statusHeader")
    when: novaPanel.opened
    function check(value, message) { if (!value) throw new Error(message) }
    function item(name) { var result=findChild(novaPanel,name); check(result!==null,"missing "+name); return result }
    function state(value) { previewHost.runtimeUpdatedAt=Date.now(); previewHost.runtimeState=value }
    function capture(name) {
      if (!"@CAPTURE@") return
      var done=false, saved=false
      item("panelKeyCatcher").parent.parent.grabToImage(function(image) { saved=image.saveToFile("@CAPTURE@/"+name+".png"); done=true })
      for (var i=0; !done && i<100; i++) wait(20)
      check(done && saved,"capture failed")
    }
    function test_wave() {
      Color.foreground="#eee4d4";Color.background="#10191d";Color.accent="#7fc6c4";Color.muted="#a8b8b5"
      var results=[], metrics={}
      function test(name, fn) {
        try { fn(); results.push({name:name,passed:true}) }
        catch (e) { results.push({name:name,passed:false,error:String(e)}) }
      }
      var wave=item("livingWave"), fallback=item("pixelWave"), core=item("corePanel")
      novaPanel.panelView = "chat"
      test("GPU shader compiles, ticks and fits the CHAT band and the MACHINE core",function() {
        wait(500)
        check(wave.status===ShaderEffect.Compiled,"shader status "+wave.status+" "+wave.log)
        check(wave.active && wave.visible && !fallback.visible,"shader not primary")
        check(wave.tickCount>0 && wave.ticking && fallback.tickCount===0,"wave did not start or fallback also ran")
        check(wave.height>0 && wave.width>200,"wave has no drawing space")
        check(wave.mapToItem(core,0,wave.height).y<=item("coreStatus").y,"wave/footer overlap")
        // UX Phase 2: a band in CHAT (104 px, less on short screens), a 200 px core in MACHINE.
        check(Math.abs(core.height-novaPanel.coreBandHeight)<1,"CHAT band: "+core.height)
        capture("compact")
        novaPanel.panelView = "machine"; wait(100)
        check(core.height>=Style.space(72)-1 && core.height<=Style.space(200)+1 && wave.height>0 && core.height>novaPanel.coreBandHeight-1,"MACHINE core: core "+core.height+" wave "+wave.height)
        check(wave.mapToItem(core,0,wave.height).y<=item("coreStatus").y,"wave/footer overlap in MACHINE")
        capture("machine"); novaPanel.panelView = "chat"
      })
      test("reported modes are distinct and generic chat fallback stays Working",function() {
        var modes={loading_model:"preparing",model_switching:"switching",thinking:"thinking",generating:"writing",tool_call:"tool",executing_action:"action",verifying_action:"action",approval_required:"waiting",error:"error",model_switch_error:"error"}
        for (var mode in modes) { state(mode); check(novaPanel.coreMode===modes[mode],"wrong "+mode) }
        state("idle"); novaPanel.chatBusy=true; check(novaPanel.coreMode==="working","busy fallback claims thinking")
        novaPanel.chatBusy=false; check(novaPanel.coreMode==="idle","idle wrong")
      })
      test("1.8 second gradient fade and rapid retarget preserve current appearance and phase",function() {
        state("thinking"); wait(2000); capture("thinking")
        var before=JSON.stringify(wave.current), phase=wave.uPhase
        state("generating")
        check(JSON.stringify(wave.current)===before && wave.uPhase===phase,"mode change jumped")
        var previous=wave.current.accent.slice(), maxStep=0
        for (var i=0; i<65; i++) {
          wait(33)
          for (var c=0;c<3;c++) maxStep=Math.max(maxStep,Math.abs(wave.current.accent[c]-previous[c]))
          previous=wave.current.accent.slice()
        }
        check(maxStep<12 && wave.transitionAge===1800,"fade stepped or did not finish: "+maxStep)
        check(wave.current.accent[0]>210,"writing not warm")
        check(wave.current.base[2]>wave.current.accent[2],"spatial gradient missing")
        metrics.maxChannelStep=maxStep;capture("writing")
        state("tool_call");wait(300);before=JSON.stringify(wave.current);phase=wave.uPhase
        state("loading_model")
        check(JSON.stringify(wave.current)===before && wave.uPhase===phase,"rapid retarget jumped")
        wait(2000)
        // Loading sweeps the horizon and swells (the orbit that took over the sweep is retired).
        check(wave.uSweep===1 && wave.uSwell===1,"loading lacks swell/horizon sweep: "+wave.uSweep)
        state("idle");wait(2000);check(wave.current.energy===.16 && wave.uBubbles>0 && wave.uBreath===1,"completion failed to settle")
      })
      test("pause freezes an active fade; resume continues",function() {
        state("thinking");wait(400)
        item("corePause").clicked(); wait(50)
        var before=JSON.stringify(wave.current), phase=wave.uPhase, ticks=wave.tickCount
        wait(350)
        check(wave.tickCount===ticks && wave.uPhase===phase && JSON.stringify(wave.current)===before,"pause still animates")
        item("corePause").clicked();wait(2000);check(wave.current.energy===.6,"resume failed")
      })
      test("streamed chunks spike only while writing, rate-limited and bounded",function() {
        state("generating");wait(1900);wave.drops=[];wave.lastSpike=-1
        function spikes() { return wave.drops.filter(function(d){return d.spike}).length }
        novaPanel.waveChunk();novaPanel.waveChunk();novaPanel.waveChunk()
        check(spikes()===1,"chunk burst not rate-limited: "+spikes())
        for (var i=0;i<16;i++) { wait(90); novaPanel.waveChunk() }
        check(wave.drops.length===12 && spikes()===12,"spikes unbounded or missing: "+wave.drops.length)
        check(wave.uDA0.x>0 && wave.uDA2.w>=0 && wave.uDS2.w>0 && Math.abs(wave.uDX1.y)<=.9,"spike slots not passed to shader")
        check(wave.uEcho===wave.tuning.writeEcho,"spike echo not passed: "+wave.uEcho)
        wait(6600);check(wave.drops.length===0,"old spikes kept: "+wave.drops.length)
        state("thinking");wait(50);novaPanel.waveChunk()
        check(spikes()===0,"thinking accepted a chunk spike")
      })
      test("idle and thinking drop softly; writing does not drop",function() {
        state("idle");wait(1900);wave.drops=[]
        wave.maybeDrop(1000)
        check(wave.drops.length===1 && !wave.drops[0].spike && wave.drops[0].s<=.13 && wave.drops[0].s>0,"idle drop: "+JSON.stringify(wave.drops))
        state("generating");wait(1900);wave.drops=[]
        wave.maybeDrop(1000);check(wave.drops.length===0,"writing dropped")
      })
      test("sunrise breath advances while ticking and freezes with pause",function() {
        state("idle");wait(1900)
        check(wave.uLightI>.5 && wave.uLightStyle===1 && wave.uDotDark===1,"light not configured: "+wave.uLightI)
        var a0=wave.uLightAngle;wait(400)
        check(wave.uLightAngle>a0,"light did not advance")
        item("corePause").clicked();wait(50);var a1=wave.uLightAngle;wait(300)
        check(wave.uLightAngle===a1,"light moved while paused")
        item("corePause").clicked();wait(50)
      })
      test("loading and switching keep the sunrise (orbit retired 2026-10-08)",function() {
        state("idle");wait(1900);check(wave.uLightStyle===1,"idle not sunrise")
        state("loading_model");wait(2600)
        check(wave.uLightStyle===1 && wave.lightLevel>.95,"loading changed the light: "+wave.uLightStyle+" "+wave.lightLevel)
        check(Math.abs(wave.uLightI-wave.tuning.lightI*wave.current.opacity*wave.lightLevel)<1e-6,"loading light not the sunrise: "+wave.uLightI)
        check(item("coreStatus").text==="CORE // PREPARING","loading label: "+item("coreStatus").text)
        capture("loading-sunrise")
        state("model_switching");wait(400);check(wave.uLightStyle===1,"switching changed the light")
        state("idle");wait(60);check(wave.uLightStyle===1,"idle not sunrise")
      })
      test("a model download shows its progress with the sunrise, then returns to idle",function() {
        state("idle");wait(2600)
        var dl=function(st,done,total){var r={};r.m1={state:st,progress:{current_blob_completed_bytes:done,current_blob_total_bytes:total},preflight:{descriptor:{catalog_id:"m1"}}};return r}
        novaPanel.acquisitionByCatalog=dl("DOWNLOADING",42,100);wait(60)
        check(novaPanel.coreMode==="downloading" && item("coreStatus").text==="CORE // DOWNLOADING · 42%","download not shown: "+novaPanel.coreMode+" "+item("coreStatus").text)
        wait(2600);check(wave.uLightStyle===1,"download changed the light: "+wave.uLightStyle)
        capture("downloading")
        novaPanel.acquisitionByCatalog=dl("DOWNLOADING",null,null);wait(30)
        check(item("coreStatus").text==="CORE // DOWNLOADING","unknown progress invented a number: "+item("coreStatus").text)
        novaPanel.acquisitionByCatalog=dl("VERIFYING",100,100);wait(30)
        check(item("coreStatus").text==="CORE // CHECKING DOWNLOAD","verify label: "+item("coreStatus").text)
        novaPanel.chatBusy=true;check(novaPanel.coreMode==="working","chat work must outrank a background download");novaPanel.chatBusy=false
        novaPanel.acquisitionByCatalog=dl("INSTALLED",100,100);wait(60)
        check(novaPanel.coreMode==="idle","finished download kept the core busy: "+novaPanel.coreMode)
        wait(2600);check(wave.uLightStyle===1,"idle did not return to sunrise after the download")
        novaPanel.acquisitionByCatalog=({})
      })
      test("fresh GPU load drives work turbulence but never idle",function() {
        previewHost.gpuAvailable=true;previewHost.gpu=95;previewHost.gpuSampledAt=Date.now()
        state("thinking");wait(2600)
        check(wave.gpuLevel>.8 && wave.uTurb>.7 && wave.uEnergy>.85,"GPU did not drive thinking: "+wave.gpuLevel+" "+wave.uEnergy)
        state("idle");wait(2000)
        check(wave.uTurb===0 && Math.abs(wave.uEnergy-.16)<1e-9,"idle followed the GPU")
        previewHost.gpuSampledAt=Date.now()-20000;novaPanel.clockTick++
        check(wave.gpuReading===0,"stale GPU sample still used")
        previewHost.gpu=7
      })
      test("a running NOVA-launched coding CLI shows CODING AGENT and follows GPU load",function() {
        previewHost.gpuAvailable=true;previewHost.gpu=90;previewHost.gpuSampledAt=Date.now()
        state("idle");novaPanel.cliState={profiles:[{id:"qwen-nova-dev",running:true}]}
        check(novaPanel.coreMode==="agent" && item("coreStatus").text==="CORE // CODING AGENT","agent mode missing: "+novaPanel.coreMode)
        wait(2600)
        check(wave.current.gpuGain===1 && wave.uTurb>.7,"agent did not follow GPU: "+wave.uTurb)
        novaPanel.chatBusy=true;check(novaPanel.coreMode==="working","chat work must outrank agent");novaPanel.chatBusy=false
        novaPanel.cliState={profiles:[{id:"qwen-nova-dev",running:false}]}
        check(novaPanel.coreMode==="idle","stopped agent still shown")
        novaPanel.cliState=null;check(novaPanel.coreMode==="idle","unknown CLI state claimed agent")
        previewHost.gpu=7
      })
      test("Canvas fallback takes over when the shader is unavailable",function() {
        state("thinking");wave.shaderFailed=true;wait(400)
        check(!wave.visible && !wave.ticking && fallback.visible && fallback.ticking && fallback.paintCount>0,"fallback did not take over")
        capture("fallback-thinking")
        wave.shaderFailed=false;wait(200)
        check(wave.ticking && !fallback.visible && !fallback.ticking,"shader did not resume")
      })
      test("closed, expanded chat and approval stop work; reopen uses latest activity",function() {
        var start=wave.tickCount
        console.warn("WAVE_COST visible start");wait(2000);console.warn("WAVE_COST visible end")
        metrics.visibleTicks=wave.tickCount-start;check(metrics.visibleTicks<70,"frame cap exceeded")
        novaPanel.close();wait(150);start=wave.tickCount
        console.warn("WAVE_COST hidden start");wait(2000);console.warn("WAVE_COST hidden end")
        check(wave.tickCount===start,"closed wave still ticks")
        state("generating");novaPanel.open();wait(100)
        check(wave.current.energy===.36,"reopened old activity")
        novaPanel.panelView="models";wait(50);start=wave.tickCount;wait(300)
        check(wave.tickCount===start && !core.visible,"MODELS kept motion")
        novaPanel.panelView="chat"
        novaPanel.beginApproval({id:"fixture-only",action:"fixture",summary:"Fixture approval",reason:"Test only",target:"",process:"",pid:0,owner:"",application:"",acquisition:false,recovery:false,decision:"",expires_at:9999999999});wait(50);start=wave.tickCount;wait(300)
        // The approved Phase 2 mock keeps the slim band above an approval (it shows AWAITING APPROVAL).
        check(core.visible && core.height<=novaPanel.coreBandHeight+1,"approval grew the core")
        novaPanel.clearPendingApproval()
      })
      test("stale and offline stop; terminal colour fades finish",function() {
        shell.keepActivityFresh=false;previewHost.runtimeUpdatedAt=Date.now()-7000;novaPanel.clockTick++
        check(novaPanel.coreMode==="stale","stale activity portrayed as work")
        wait(2000);check(!wave.ticking,"stale never stopped")
        previewHost.online=false;check(novaPanel.coreMode==="offline","offline lost")
        wait(2000);check(!wave.ticking,"offline never stopped")
        previewHost.online=true;shell.keepActivityFresh=true;state("error");wait(2000)
        check(!wave.ticking && item("coreStatus").text.indexOf("ERROR")>=0,"error still animates")
      })
      test("reduced motion and theme changes update static colours",function() {
        novaPanel.settings={reducedMotion:true};state("thinking");wait(50)
        var phase=wave.uPhase,ticks=wave.tickCount
        check(wave.current.energy===.6 && !wave.ticking,"reduced state not applied")
        wait(250);check(wave.uPhase===phase && wave.tickCount===ticks,"reduced state animates")
        Color.foreground="#e8f3f8";Color.background="#0b1728";Color.accent="#79cee0"
        wait(50);capture("blue-reduced")
        novaPanel.settings={reducedMotion:false};wait(100)
        var before=JSON.stringify(wave.current)
        Color.accent="#b1e38b"
        check(JSON.stringify(wave.current)===before,"theme colour jumped")
        wait(2000);check(wave.transitionAge===1800,"theme fade failed")
      })
      test("BRAIN pointer, Enter, Space and literal identity",function() {
        var button=item("brainHeaderTarget")
        novaPanel.panelView = "chat"
        mouseClick(button);wait(30);check(novaPanel.panelView==="models","pointer failed")
        novaPanel.panelView = "chat"
        button.forceActiveFocus();keyClick(Qt.Key_Return);wait(30);check(novaPanel.panelView==="models","Enter failed")
        novaPanel.panelView = "chat"
        button.forceActiveFocus();keyClick(Qt.Key_Space);wait(30);check(novaPanel.panelView==="models","Space failed")
        check(button.Accessible.description==="Show models","disclosure name wrong")
        check(button.Accessible.name===novaPanel.brainIdentityText(),"identity changed")
      })
      console.warn("WAVE_RESULTS "+JSON.stringify({cases:results,metrics:metrics}))
    }
  }
'''


def main():
    evidence = Path(os.environ['NOVA_WAVE_EVIDENCE_DIR']) if os.environ.get('NOVA_WAVE_EVIDENCE_DIR') else None
    if evidence: evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nova-pixel-wave-',dir='/tmp') as temporary:
        folder=Path(temporary)
        fixture=preview.scenarios()['installed-no-selection']
        shell=preview.prepare_config(folder, fixture)
        source=shell.read_text().replace('import QtQuick\n','import QtQuick\nimport QtTest\n',1)
        source=source.replace('property double updatedAt: Date.now()', 'property double updatedAt: Date.now()\n    property double runtimeUpdatedAt: Date.now()\n    property double gpuSampledAt: Date.now()')
        shell.write_text(source.rsplit('\n}',1)[0]+CHECKS.replace('@CAPTURE@',str(evidence) if evidence else '')+'\n}\n')
        proc=subprocess.Popen(['quickshell','--path',str(folder)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        lines=[]; costs={}; deadline=time.monotonic()+100
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout,selectors.EVENT_READ)
                while proc.poll() is None:
                    if time.monotonic()>deadline: raise RuntimeError('wave checks timed out')
                    for key,_ in selector.select(.2):
                        line=key.fileobj.readline();lines.append(line)
                        if 'WAVE_COST ' in line:
                            mode,edge=line.split('WAVE_COST ',1)[1].strip().split()
                            fields=Path(f'/proc/{proc.pid}/stat').read_text().split()
                            costs.setdefault(mode,{})[edge]=(time.monotonic(),(int(fields[13])+int(fields[14]))/os.sysconf('SC_CLK_TCK'))
            lines.append(proc.stdout.read())
        finally:
            if proc.poll() is None: proc.kill();proc.wait()
        output=''.join(lines)
        if evidence: (evidence/'fixture.log').write_text(output)
        report=next((json.loads(line.split('WAVE_RESULTS ',1)[1]) for line in output.splitlines() if 'WAVE_RESULTS ' in line),None)
        if report is None: print(output);raise RuntimeError('No pixel-wave result')
        for case in report['cases']:
            print(('PASS ' if case['passed'] else 'FAIL ')+case['name']+(': '+case['error'] if not case['passed'] else ''))
        print('METRICS '+json.dumps(report['metrics']))
        for mode,edges in costs.items():
            if 'start' in edges and 'end' in edges:
                wall=edges['end'][0]-edges['start'][0];cpu=edges['end'][1]-edges['start'][1]
                print(f'COST {mode}: {100*cpu/wall:.2f}% of one CPU over {wall:.2f}s (whole isolated panel; shader on the nested compositor GPU)')
        errors=[line for line in output.splitlines() if any(word in line for word in ['ReferenceError','TypeError','Binding loop'])]
        if proc.returncode or errors or any(not c['passed'] for c in report['cases']):
            print(output);raise RuntimeError('Pixel-wave checks failed')

if __name__=='__main__': main()
