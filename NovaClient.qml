import QtQuick
import Quickshell
import Quickshell.Io

// NOVA's private socket, for the bar and the panel (BarWidget.novaRequest).
//
// NOVA's services listen only on Unix sockets in this user's runtime directory
// (local_socket.py). Marketplace review 2026-10-14: while NOVA was off, another account
// could take its TCP port and receive the key the bar sent there. Only this account can
// enter $XDG_RUNTIME_DIR, so whatever answers on novad.sock is this user's novad, and
// nothing secret is sent at all. request() returns an XMLHttpRequest look-alike over that
// socket: HTTP/1.1 with "Connection: close", read line by line (NOVA ends every body with
// a newline; Quickshell drops an unfinished last line when the socket closes). Callbacks
// never run inside send(), as with XMLHttpRequest.
Scope {
  id: client
  property string socketPath: Quickshell.env("XDG_RUNTIME_DIR") ? Quickshell.env("XDG_RUNTIME_DIR") + "/nova/novad.sock" : ""
  property var pending: []
  Component {
    id: novaSocketComponent
    Socket {
      id: novaSocket
      property var request: null
      parser: SplitParser { splitMarker: "\n"; onRead: function(data) { if (novaSocket.request) novaSocket.request._line(data) } }
      onConnectionStateChanged: if (request) request._connection(connected)
      onError: function(code) { if (request) request._failed(code) }
    }
  }
  Timer {
    interval: 200; repeat: true; running: client.pending.length > 0
    onTriggered: {
      var now = Date.now()
      client.pending.forEach(function(request) { if (request._deadline && now >= request._deadline) request._expire() })
    }
  }
  function utf8Length(text) {
    var bytes = 0
    for (var index = 0; index < text.length; index++) {
      var code = text.charCodeAt(index)
      if (code < 0x80) bytes += 1
      else if (code < 0x800) bytes += 2
      else if (code >= 0xD800 && code <= 0xDBFF && index + 1 < text.length) { bytes += 4; index++ }
      else bytes += 3
    }
    return bytes
  }
  function request() {
    var r = { readyState: 0, status: 0, statusText: "", responseText: "", timeout: 0,
              onreadystatechange: null, onload: null, onerror: null, ontimeout: null,
              _method: "GET", _path: "/", _headers: [], _body: "", _socket: null, _deadline: 0,
              _connected: false, _sent: false, _done: false, _timedOut: false,
              _statusSeen: false, _head: true, _length: -1, _received: 0 }
    function changed() { if (typeof r.onreadystatechange === "function") r.onreadystatechange() }
    function clean(text) { return String(text).replace(/[\r\n]/g, "") }
    r.open = function(method, url) {
      r._method = clean(method).toUpperCase()
      r._path = clean(url).replace(/^[a-z]+:\/\/[^\/]*/i, "") || "/"
      r.readyState = 1; changed()
    }
    r.setRequestHeader = function(name, value) {
      if (!/^(host|connection|content-length|transfer-encoding)$/i.test(name)) r._headers.push(clean(name) + ": " + clean(value))
    }
    r.abort = function() { if (!r._done) { r._done = true; r._drop(); r.readyState = 0 } }
    r.send = function(body) {
      r._body = body === undefined || body === null ? "" : String(body)
      r._deadline = r.timeout > 0 ? Date.now() + r.timeout : 0
      client.pending = client.pending.concat([r])
      if (!client.socketPath) { Qt.callLater(function() { r._finish(true) }); return }
      r._socket = novaSocketComponent.createObject(client, { request: r, path: client.socketPath })
      r._socket.connected = true
    }
    r._connection = function(up) {
      if (r._done) return
      if (up && !r._sent) {
        r._connected = true; r._sent = true
        var head = r._method + " " + r._path + " HTTP/1.1\r\nHost: novad\r\nConnection: close\r\n"
        for (var index = 0; index < r._headers.length; index++) head += r._headers[index] + "\r\n"
        if (r._body.length > 0 || r._method !== "GET") head += "Content-Length: " + client.utf8Length(r._body) + "\r\n"
        r._socket.write(head + "\r\n" + r._body)
        r._socket.flush()
      } else if (!up && r._connected) r._finish(!r._statusSeen)  // the end of an answer without a length (a stream)
    }
    r._line = function(text) {
      if (r._done) return
      if (!r._statusSeen) {
        var match = /^HTTP\/1\.[01] (\d{3}) ?([^\r]*)\r?$/.exec(text)
        if (!match) { r._finish(true); return }
        r.status = Number(match[1]); r.statusText = match[2]; r._statusSeen = true
        return
      }
      if (r._head) {
        text = text.replace(/\r$/, "")
        if (text !== "") {
          var colon = text.indexOf(":")
          if (colon > 0 && text.slice(0, colon).trim().toLowerCase() === "content-length") r._length = Number(text.slice(colon + 1).trim())
          return
        }
        r._head = false; r.readyState = 2; changed()
        if (r._length === 0) r._finish(false)
        return
      }
      r.responseText += text + "\n"
      r._received += client.utf8Length(text) + 1
      r.readyState = 3; changed()
      if (r._length >= 0 && r._received >= r._length) r._finish(false)
    }
    // A closed answer also reports PeerClosedError (1); the disconnect that follows ends it.
    r._failed = function(code) { if (!r._done && !(r._connected && code === 1)) Qt.callLater(function() { r._finish(true) }) }
    r._expire = function() { r._timedOut = true; r._finish(true) }
    r._drop = function() {
      client.pending = client.pending.filter(function(other) { return other !== r })
      var socket = r._socket; r._socket = null
      if (socket) { socket.request = null; socket.connected = false; socket.destroy() }
    }
    r._finish = function(failed) {
      if (r._done) return
      r._done = true
      r._drop()
      if (failed) { r.status = 0; r.responseText = "" }
      r.readyState = 4; changed()
      if (!failed && typeof r.onload === "function") r.onload()
      else if (failed && r._timedOut && typeof r.ontimeout === "function") r.ontimeout()
      else if (failed && !r._timedOut && typeof r.onerror === "function") r.onerror()
    }
    return r
  }
}
