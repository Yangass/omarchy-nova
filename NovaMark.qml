import QtQuick

// NOVA's mark: a shockwave ring, an off-centre core and a faint outer blast arc.
// Konstantin chose "Shockwave" and tuned it in previews/logo (2026-10-06); these
// are his exact settings. Drawn rather than a font glyph so it follows the theme
// and can breathe with NOVA's state (pulse > 1 brightens the core).
Canvas {
  id: mark
  property color accent: "#7aa2f7"
  property color foreground: "#c0caf5"
  property color background: "#1a1b26"
  property real pulse: 1
  readonly property var settings: ({ ring: 0.77, weight: 0.51, coreSize: 0.93, coreOffset: 0.54, coreAngle: 225,
                                     glow: 0.8, shockRadius: 1.27, shockSpan: 167, shockAlpha: 0.44 })
  implicitWidth: 16
  implicitHeight: 16
  antialiasing: true

  function luminance(c) { return 0.299 * c.r + 0.587 * c.g + 0.114 * c.b }
  function blend(a, b, t) { return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t, 1) }
  function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, Math.max(0, Math.min(1, a))) }

  onAccentChanged: requestPaint()
  onForegroundChanged: requestPaint()
  onBackgroundChanged: requestPaint()
  onPulseChanged: requestPaint()
  onWidthChanged: requestPaint()
  onHeightChanged: requestPaint()

  onPaint: {
    var ctx = getContext("2d"), v = settings
    var s = Math.min(width, height), c = s / 2
    ctx.reset()
    if (s <= 0) return
    ctx.translate((width - s) / 2, (height - s) / 2)
    var R = c * v.ring * 0.72, a = v.coreAngle * Math.PI / 180
    ctx.lineCap = "round"
    // Ring
    ctx.strokeStyle = accent
    ctx.lineWidth = Math.max(1, s * 0.06 * v.weight)
    ctx.beginPath(); ctx.arc(c, c, R, 0, Math.PI * 2); ctx.stroke()
    // Shockwave arc on the far side from the core
    var span = v.shockSpan * Math.PI / 180, back = a + Math.PI
    ctx.strokeStyle = alpha(accent, v.shockAlpha)
    ctx.lineWidth = Math.max(0.8, s * 0.035 * v.weight)
    ctx.beginPath(); ctx.arc(c, c, Math.min(c * 0.97, R * v.shockRadius), back - span / 2, back + span / 2); ctx.stroke()
    // Core with glow: bright on dark themes, deep on light ones
    var x = c + Math.cos(a) * R * v.coreOffset, y = c + Math.sin(a) * R * v.coreOffset
    var r = Math.max(0.9, s * 0.075 * v.coreSize * (0.92 + 0.08 * pulse))
    var glowR = r + s * 0.32 * v.glow * pulse
    var g = ctx.createRadialGradient(x, y, r * 0.5, x, y, glowR)
    g.addColorStop(0, alpha(accent, 0.55 * Math.min(1, v.glow) * pulse))
    g.addColorStop(1, alpha(accent, 0))
    ctx.fillStyle = g
    ctx.beginPath(); ctx.arc(x, y, glowR, 0, Math.PI * 2); ctx.fill()
    ctx.fillStyle = luminance(background) < 0.5 ? blend(foreground, Qt.rgba(1, 1, 1, 1), 0.55) : blend(foreground, Qt.rgba(0, 0, 0, 1), 0.25)
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill()
  }
}
