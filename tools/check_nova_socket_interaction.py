#!/usr/bin/env python3
"""The bar and the panel reach NOVA only over its private socket (2026-10-15).

Marketplace review #11195 (2026-10-14): while NOVA was off, another local account could
take 127.0.0.1:8790 and receive the key the bar sent there with every poll. NOVA's
services now listen only on Unix sockets in this user's runtime directory
(local_socket.py) and NovaClient.qml speaks HTTP over that socket, so no other account
can answer and there is no key.

Run under a private compositor (tools/run_qml_checks.sh). A real OwnerOnlyHTTPServer
(local_socket.py) serves test routes in a throwaway runtime directory; the production
NovaClient.qml makes the requests. Checks: JSON with non-ASCII text, a UTF-8 body with
the right Content-Length, a stream read while it is still coming, a large answer, an
error page, a timeout, an offline NOVA (no socket) failing like XMLHttpRequest and never
inside send(), 40 requests at once with nothing left behind, and, when 127.0.0.1:8790 is
free, a stand-in listening there that nobody ever connects to.
"""
import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "novad"))
TEXT = "héllo ąž привет \U0001F680"
BIG = "x" * 300_000


class Routes(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode() + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/json": self.reply(200, {"text": TEXT})
        elif self.path == "/big": self.reply(200, {"big": BIG})
        elif self.path == "/slow": time.sleep(3); self.reply(200, {"late": True})
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            for index in range(3):
                self.wfile.write(f"event: token\ndata: {json.dumps({'n': index, 'text': TEXT}, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.4)
        else: self.send_error(404, "Unknown")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.reply(200, {"length": int(self.headers.get("Content-Length") or 0), "bytes": len(raw),
                         "got": json.loads(raw.decode()), "type": self.headers.get("Content-Type")})


QML = r'''import QtQuick
import QtTest
import Quickshell

ShellRoot {
  NovaClient { id: client; socketPath: "@SOCKET@" }
  NovaClient { id: offline; socketPath: "@SOCKET@.missing" }
  TestCase {
    name: "NovaSocket"
    when: true
    function run(target, method, path, body, timeout) {
      var r = target.request(), result = {done: false, states: [], events: [], firstChunkAt: -1, text: ""}
      var started = Date.now()
      r.onreadystatechange = function() {
        result.states.push(r.readyState)
        if (r.readyState === 3 && result.firstChunkAt < 0) result.firstChunkAt = Date.now() - started
        if (r.readyState === XMLHttpRequest.DONE) { result.done = true; result.status = r.status; result.text = r.responseText; result.elapsed = Date.now() - started }
      }
      r.onerror = function() { result.events.push("error") }
      r.ontimeout = function() { result.events.push("timeout") }
      r.open(method, path)
      if (body !== undefined) r.setRequestHeader("Content-Type", "application/json")
      if (timeout) r.timeout = timeout
      r.send(body)
      result.doneInsideSend = result.done || result.events.length > 0
      var n = 0; while (!result.done && n++ < 800) wait(10)
      return result
    }
    function test_socket() {
      var results = []
      function test(name, fn) {
        try { fn(); results.push({name: name, passed: true}) }
        catch (e) { results.push({name: name, passed: false, error: String(e)}) }
      }
      function check(value, message) { if (!value) throw new Error(message) }
      test("JSON with non-ASCII text arrives whole", function() {
        var r = run(client, "GET", "/json")
        check(r.status === 200, "status " + r.status)
        check(JSON.parse(r.text).text === "@TEXT@", "text " + r.text)
        check(!r.doneInsideSend, "a callback ran inside send()")
      })
      test("a full URL is reduced to its path", function() {
        var r = run(client, "GET", "http://127.0.0.1:8790/json")
        check(r.status === 200 && JSON.parse(r.text).text === "@TEXT@", "status " + r.status)
      })
      test("a UTF-8 body goes out with its byte length", function() {
        var body = JSON.stringify({message: "@TEXT@"})
        var r = run(client, "POST", "/echo", body)
        var got = JSON.parse(r.text)
        check(r.status === 200, "status " + r.status)
        check(got.length === got.bytes && got.got.message === "@TEXT@", "echo " + r.text)
        check(got.type === "application/json", "header " + got.type)
      })
      test("a stream is read while it is still coming", function() {
        var r = run(client, "GET", "/stream")
        check(r.status === 200, "status " + r.status)
        check((r.text.match(/event: token/g) || []).length === 3, "events " + r.text)
        check(r.firstChunkAt >= 0 && r.firstChunkAt < 350 && r.elapsed > 1000, "first chunk at " + r.firstChunkAt + " ms, done at " + r.elapsed)
      })
      test("a large answer arrives whole", function() {
        var r = run(client, "GET", "/big")
        check(r.status === 200 && JSON.parse(r.text).big.length === 300000, "status " + r.status + ", " + r.text.length + " chars")
      })
      test("an error page keeps its status", function() {
        var r = run(client, "GET", "/nothing")
        check(r.status === 404 && r.events.length === 0, "status " + r.status + " " + r.events)
      })
      test("a timeout ends the request like XMLHttpRequest", function() {
        var r = run(client, "GET", "/slow", undefined, 500)
        check(r.status === 0 && r.events.join() === "timeout" && r.elapsed < 1500, "status " + r.status + " " + r.events + " " + r.elapsed)
      })
      test("an offline NOVA fails like XMLHttpRequest, never inside send()", function() {
        var r = run(offline, "GET", "/json")
        check(r.status === 0 && r.events.join() === "error" && r.text === "", "status " + r.status + " " + r.events)
        check(!r.doneInsideSend, "a callback ran inside send()")
      })
      test("40 requests at once all finish and nothing is left behind", function() {
        var done = 0, ok = 0
        for (var i = 0; i < 40; i++) {
          var r = client.request()
          r.onreadystatechange = (function(req) { return function() { if (req.readyState === 4) { done++; if (req.status === 200) ok++ } } })(r)
          r.open("GET", "/json"); r.send()
        }
        var n = 0; while (done < 40 && n++ < 500) wait(10)
        wait(300)
        check(done === 40 && ok === 40, done + " done, " + ok + " ok")
        check(client.pending.length === 0, client.pending.length + " still pending")
      })
      console.warn("SOCKET_RESULTS " + JSON.stringify(results))
    }
  }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix="nova-socket-", dir="/tmp") as raw:
        base = Path(raw)
        runtime = base / "run"; runtime.mkdir(mode=0o700)
        os.environ["XDG_RUNTIME_DIR"] = str(runtime)
        import local_socket
        server = local_socket.OwnerOnlyHTTPServer("novad", Routes)
        Thread(target=server.serve_forever, daemon=True).start()
        squatter = None
        stand_in = False
        touched = []
        try:
            squatter = socket.create_server(("127.0.0.1", 8790))
            stand_in = True
            squatter.settimeout(0.2)
            def watch():
                while squatter is not None:
                    try: connection, _ = squatter.accept(); touched.append(1); connection.close()
                    except OSError: continue
            Thread(target=watch, daemon=True).start()
        except OSError:
            print("NOTE 127.0.0.1:8790 is in use (an older NOVA?); stand-in check skipped")
        config = base / "config"; config.mkdir()
        (config / "NovaClient.qml").symlink_to(ROOT / "NovaClient.qml")
        (config / "shell.qml").write_text(QML.replace("@SOCKET@", server.server_address).replace("@TEXT@", TEXT))
        result = subprocess.run(["quickshell", "--path", str(config)], capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr
        server.shutdown()
        if squatter is not None:
            sock, squatter = squatter, None
            sock.close()
    line = next((row.split("SOCKET_RESULTS ", 1)[1] for row in output.splitlines() if "SOCKET_RESULTS " in row), None)
    if line is None:
        print(output[-4000:])
        raise SystemExit("Socket check did not complete")
    rows = json.loads(line)
    if stand_in:
        rows.append({"name": "nobody connects to a stand-in on 127.0.0.1:8790", "passed": not touched,
                     "error": f"{len(touched)} connection(s)"})
    for row in rows:
        print(("PASS " if row["passed"] else "FAIL ") + row["name"] + ((": " + row["error"]) if not row["passed"] else ""))
    errors = [r for r in output.splitlines() if any(w in r for w in ("ReferenceError", "TypeError", "Binding loop"))]
    if errors or any(not row["passed"] for row in rows):
        print("\n".join(errors) or output[-3000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
