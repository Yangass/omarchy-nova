#version 440
// NOVA living wave (UI-02C, UI-02D spikes, drops and sunrise breath). Compile: tools/build_wave_shader.py
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 uSize;
    float uPhase;
    float uTime;
    vec3 uBase;
    vec3 uAccent;
    vec3 uCrest;
    float uOpacity;
    float uEnergy;
    float uCadence;
    float uBreath;
    float uBubbles;
    float uSwell;
    float uTurb;
    vec3 uHorA;
    vec3 uHorB;
    float uHorI;
    float uSweep;
    float uPulse;
    float uSpikeH;
    float uSpikeR;
    float uAttack;
    float uDecay;
    float uEcho;
    float uEchoSpeed;
    float uDropGlow;
    vec4 uDA0;
    vec4 uDA1;
    vec4 uDA2;
    vec4 uDX0;
    vec4 uDX1;
    vec4 uDX2;
    vec4 uDZ0;
    vec4 uDZ1;
    vec4 uDZ2;
    vec4 uDS0;
    vec4 uDS1;
    vec4 uDS2;
    float uLightAngle;
    float uLightI;
    float uLightPath;
    float uLightW;
    float uLightFar;
    float uLightFlow;
    float uLightStyle;
    float uDotGlow;
    float uDotDark;
    float uDotShift;
    vec3 uDotShiftColor;
    float uFieldW;
};

// ---- shared body: identical in previews/ui02d-wave and assets/shaders/wave.frag ----
float hash12(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

// One drop: a local spike at (px, pz) that rises, glows and falls, plus an optional
// small echo ring that spreads a little and fades. s = strength (0 = empty slot).
float drop1(float age, float px, float pz, float s, float x, float z) {
  if (age < 0.0 || s <= 0.0) return 0.0;
  float dx = (x - px) * 1.3, dz = (z - pz) * 0.84;
  float d = sqrt(dx * dx + dz * dz);
  float env = smoothstep(0.0, uAttack, age) * exp(-max(0.0, age - uAttack) / uDecay);
  float spike = exp(-(d * d) / (uSpikeR * uSpikeR)) * env;
  float rd = (d - age * uEchoSpeed) / 0.055;
  float ring = uEcho * exp(-rd * rd) * exp(-age * 2.8) * smoothstep(0.0, 0.05, age);
  return s * (spike + ring);
}
float drop4(vec4 a, vec4 px, vec4 pz, vec4 s, float x, float z) {
  return drop1(a.x, px.x, pz.x, s.x, x, z) + drop1(a.y, px.y, pz.y, s.y, x, z)
       + drop1(a.z, px.z, pz.z, s.z, x, z) + drop1(a.w, px.w, pz.w, s.w, x, z);
}

// x: rise in px, y: crest weight, z: glow
vec3 waveAt(float x, float z, float depth, vec2 cell, float energy) {
  float dist = sqrt(x * x * 1.69 + z * z * 0.7056);
  float broad = sin(dist * 5.6 - uPhase * 0.7) * 1.5;
  float centre = 0.28 + sin(uPhase * 0.24) * 0.18;
  float dx = x - centre, dz = z + 0.08;
  float cluster = exp(-(dx * dx / 0.15 + dz * dz / 0.26));
  float sx = x + 0.42 + sin(uPhase * 0.19) * 0.12, sz = z - 0.24;
  float shoulder = exp(-(sx * sx / 0.13 + sz * sz / 0.3));
  float peak = min(1.0, cluster + shoulder * 0.65);
  float moving = sin(x * 8.0 - uPhase + z * 5.0);
  float cad = 1.0 - uCadence * (0.5 - 0.5 * sin(uPhase * 2.0));
  float rise = broad * (0.3 + energy) + energy * cad * (35.0 * peak + 4.5 * moving);
  rise += uBreath * (sin(uTime * 0.9 + x * 1.7 - z * 1.1) * 0.6 + sin(uTime * 0.53 - x * 2.3 + z * 0.8) * 0.4) * 6.0;
  rise += uSwell * sin(z * 7.0 - uPhase * 1.9 + sin(x * 2.0 + uPhase * 0.3) * 0.7) * 9.0 * (0.55 + 0.45 * depth);
  rise += uTurb * (sin(x * 9.0 + uPhase * 2.6) * sin(z * 7.0 - uPhase * 2.0)
                   + 0.4 * sin(x * 5.0 - z * 6.0 + uPhase * 3.3)) * 6.0;
  float glow = 0.0;
  if (uBubbles > 0.0) {
    vec2 csz = vec2(4.0, 3.0);
    vec2 cid = floor(cell / csz);
    float t = uTime / 3.4 + hash12(cid) * 7.0;
    float k = floor(t), f = fract(t);
    float on = step(hash12(cid + k * vec2(13.1, 7.7)), uBubbles);
    vec2 c0 = cid * csz + vec2(1.5, 1.0) + (vec2(hash12(cid + k), hash12(cid - k)) - 0.5) * 1.5;
    float w = clamp(1.0 - length((cell - c0) / vec2(2.0, 1.4)), 0.0, 1.0);
    float bub = on * smoothstep(0.0, 0.22, f) * (1.0 - smoothstep(0.22, 0.95, f)) * w * w;
    rise += bub * 13.0;
    glow += bub * 0.9;
  }
  float drops = drop4(uDA0, uDX0, uDZ0, uDS0, x, z) + drop4(uDA1, uDX1, uDZ1, uDS1, x, z)
              + drop4(uDA2, uDX2, uDZ2, uDS2, x, z);
  rise += drops * uSpikeH;
  glow += drops * uDropGlow;
  return vec3(rise, peak, glow);
}

// Horizon light (UI-02D). Two styles:
//  uLightStyle 0: a travelling glow; uLightPath blends an orbit (near pass left→right,
//                 far pass right→left) with breathing (far → near → far).
//  uLightStyle 1: sunrise breath; a small glow low in the centre swells into a dome that
//                 wraps the field, then sinks back.
vec2 lightPos() {
  float a = uLightAngle;
  vec2 orbit = vec2(-cos(a), 0.5 + 0.5 * sin(a));
  vec2 breathe = vec2(0.35 * sin(a * 0.5), 0.5 - 0.5 * cos(a));
  return mix(orbit, breathe, uLightPath);
}
// Light amount (0..1) at a point in logical pixels.
float lightAmount(vec2 p, float W, float H, float base, float scaleY) {
  if (uLightI <= 0.0) return 0.0;
  if (uLightStyle > 0.5) {
    float b = 0.5 - 0.5 * cos(uLightAngle);
    float r = mix(0.07, 1.15, b);
    float dx = (p.x - W * 0.5) / (r * W * 0.5 * uLightW), dy = (p.y - H * 1.02) / (r * H * 0.95);
    return clamp(exp(-(dx * dx + dy * dy) * 1.6) * uLightI * mix(uLightFar, 1.0, b), 0.0, 1.0);
  }
  vec2 lp = lightPos();
  float nearL = lp.y, sizeL = mix(0.45, 1.0, nearL);
  float lx = W * 0.5 + lp.x * W * 0.45 * mix(0.62, 1.0, nearL);
  float ly = base - mix(39.0 * scaleY + 14.0, 2.0, nearL);
  float gx = (p.x - lx) / (W * 0.22 * uLightW * sizeL), gy = (p.y - ly) / (H * 0.2 * sizeL);
  return clamp(exp(-(gx * gx + gy * gy)) * uLightI * mix(uLightFar, 1.0, nearL), 0.0, 1.0);
}

// p: logical pixels, origin top-left. Returns premultiplied colour.
vec4 shade(vec2 p) {
  float W = uSize.x, H = uSize.y;
  // The dot field may be narrower than the canvas (CHAT band): the horizon and light
  // fill the whole width, the dots keep their proportions, centred (uFieldW 0 = full).
  float FW = uFieldW > 0.0 ? min(uFieldW, W) : W;
  float unit = min((FW - 28.0) / 28.0, 18.0);
  float scaleY = min(1.0, H / 100.0);
  float base = H * 0.68;
  float energy = uEnergy;

  float xn = (p.x - W * 0.5) / (W * 0.5);
  float dy = p.y - (base - 39.0 * scaleY - 4.0);
  float spreadY = dy < 0.0 ? H * 0.16 : H * 0.42;
  float vfall = exp(-(dy / spreadY) * (dy / spreadY));
  float flow = 0.5 + 0.5 * sin(xn * 2.3 + uTime * 0.33 + uLightAngle * uLightFlow) * cos(xn * 1.1 - uTime * 0.21);
  vec3 hcol = mix(uHorA, uHorB, flow);
  float vign = clamp(1.0 - pow(abs(xn), 2.2) * 0.75, 0.0, 1.0);
  float sweepPos = mod(uTime * 0.32, 1.7) - 0.35;
  float sb = ((xn * 0.5 + 0.5) - sweepPos) / 0.22;
  float inten = uHorI * mix(1.0, 0.3 + 1.1 * exp(-sb * sb), uSweep);
  inten *= 1.0 - uPulse * 0.6 * (0.5 + 0.5 * cos(uTime * 2.2));
  float ha = clamp(vfall * vign * inten, 0.0, 1.0);
  vec4 acc = vec4(hcol * ha, ha);
  vec3 lcol = mix(uHorB, vec3(1.0), 0.25);
  float la = lightAmount(p, W, H, base, scaleY);
  if (la > 0.0) acc = vec4(lcol * la, la) + acc * (1.0 - la);

  for (int r = 0; r < 11; r++) {
    float depth = float(r) / 10.0;
    float rowY = base - depth * 39.0 * scaleY;
    if (abs(p.y - rowY) > 95.0 * scaleY + 6.0) continue;
    float spread = 0.38 + depth * 0.62;
    float spacing = unit * spread;
    float mark = 1.1 + depth * 0.9;
    float mw = mark * 2.2;
    float c0 = floor((p.x - W * 0.5) / spacing + 14.0);
    for (int k = 0; k < 2; k++) {
      float c = c0 - float(k);
      if (c < 0.0 || c > 28.0) continue;
      float x = (c - 14.0) / 14.0;
      float px = W * 0.5 + x * spacing * 14.0;
      if (p.x < px - 0.5 || p.x > px + mw + 0.5) continue;
      vec3 w = waveAt(x, depth * 2.0 - 1.0, depth, vec2(c, float(r)), energy);
      float py = rowY - w.x * scaleY;
      float cov = clamp(min(p.x - px, px + mw - p.x) + 0.5, 0.0, 1.0)
                * clamp(min(p.y - py, py + mark - p.y) + 0.5, 0.0, 1.0);
      if (cov <= 0.0) continue;
      // Marks in the light: catch it (glow), stand out as silhouettes (dark) or shift colour.
      float lm, lit = uDotGlow;
      lm = lightAmount(vec2(px + mw * 0.5, py), W, H, base, scaleY);
      float glow = clamp(w.z + lm * lit, 0.0, 1.0);
      vec3 slope = mix(uBase, uAccent, 0.12 + w.y * 0.75);
      vec3 tint = mix(slope, uCrest, w.y * w.y * energy * 0.9);
      tint = mix(tint, min(uCrest * 1.25 + 0.08, vec3(1.0)), glow * 0.65);
      float lk = clamp(lm * 2.5, 0.0, 1.0);
      tint = mix(tint, uDotShiftColor, lk * uDotShift);
      tint *= 1.0 - lk * uDotDark * 0.85;
      float a = clamp(uOpacity * (0.22 + depth * 0.18 + energy * (0.18 + w.y * 0.32)) + glow * 0.4
                      + lk * uDotDark * 0.5, 0.0, 1.0) * cov;
      acc = vec4(tint * a, a) + acc * (1.0 - a);
    }
  }
  return acc;
}
// ---- end shared body ----

void main() {
    fragColor = shade(qt_TexCoord0 * uSize) * qt_Opacity;
}
