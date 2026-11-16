// Scene 3D core: the `v` object AI-written beats use, plus the dry-run validator.
// Pure JavaScript with no DOM and no three.js, so it runs in a Worker for validation and
// the renderer later turns the same node tree into three.js objects.
'use strict';
var V3 = (function () {
  const PALETTE = {
    cream: '#f5ecd7', sky: '#8fc4ee', sand: '#d8c08e', concrete: '#b9b6ae', stone: '#8d8a84', blue: '#2f6fd6',
    blue_soft: '#a9c8f0', orange: '#ee8631', red: '#d64535', white: '#f4f4f2', dark: '#222a35', green: '#3f8a4a',
    brown: '#7a5a3a', wood: '#c77a4a', roof: '#b53b2a', skin: '#f0c8a0', water: '#4f8fc4', mud: '#6b5a3c', yellow: '#f2c230',
  };
  const LIMITS = { nodes: 600, anims: 600, triangles: 60000, codeChars: 24000, labelChars: 40 };
  const TRIS = { box: 12, cylinder: 64, sphere: 192, cone: 48, capsule: 160, plane: 2, water: 8192, label: 2, group: 0 };
  const FIELDS = ['x', 'y', 'z', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz', 'opacity', 'visible', 'color'];
  const BANNED = [
    'eval', 'Function', 'constructor', 'prototype', '__proto__', 'import', 'require', 'process', 'fetch', 'XMLHttpRequest',
    'WebSocket', 'window', 'document', 'globalThis', 'self', 'importScripts', 'postMessage', 'localStorage', 'indexedDB',
    'navigator', 'location', 'Worker', 'setTimeout', 'setInterval', 'requestAnimationFrame', 'Date', 'performance', 'async', 'await',
    'while', 'do',
  ];
  const SHADOWED = ['window', 'document', 'globalThis', 'self', 'fetch', 'XMLHttpRequest', 'WebSocket', 'Function', 'importScripts',
    'postMessage', 'navigator', 'location', 'Date', 'performance', 'setTimeout', 'setInterval', 'requestAnimationFrame', 'Worker',
    'localStorage', 'indexedDB'];

  const fail = (msg) => { throw new Error(msg); };
  const isNum = (x) => typeof x === 'number' && Number.isFinite(x);
  const num = (x, what) => (isNum(x) ? x : fail(`${what} must be a finite number, got ${typeof x === 'number' ? x : JSON.stringify(x)}`));
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, u) => a + (b - a) * u;
  const ease = (p) => { p = clamp(p, 0, 1); return p * p * (3 - 2 * p); };
  const color = (c) => {
    if (typeof c === 'string') {
      if (PALETTE[c]) return PALETTE[c];
      if (/^#[0-9a-fA-F]{6}$/.test(c)) return c.toLowerCase();
    }
    return fail(`unknown color ${JSON.stringify(c)}; use a palette name (${Object.keys(PALETTE).join(', ')}) or "#rrggbb"`);
  };
  const vec3 = (a, what) => (Array.isArray(a) && a.length === 3 ? a.map((n, i) => num(n, `${what}[${i}]`)) : fail(`${what} must be [x, y, z]`));

  class Node {
    constructor(b, kind, params, o) {
      if (b.nodes.length >= LIMITS.nodes) fail(`too many objects (max ${LIMITS.nodes}); use fewer, larger shapes or shorter loops`);
      this._b = b; this.id = b.nodes.length; this.kind = kind; this.params = params;
      this.parent = null; this.children = []; this.anims = [];
      this.base = { x: 0, y: 0, z: 0, rx: 0, ry: 0, rz: 0, sx: 1, sy: 1, sz: 1, color: null, opacity: 1, visible: true };
      b.nodes.push(this);
      b.tris += TRIS[kind] || 0;
      if (b.tris > LIMITS.triangles) fail(`scene too heavy (about ${b.tris} triangles, max ${LIMITS.triangles}); use fewer objects`);
      o = o || {};
      for (const k of Object.keys(o)) {
        if (o[k] === undefined) continue;          // helpers pass optional fields straight through
        if (k === 's') { this.base.sx = this.base.sy = this.base.sz = num(o.s, 's'); }
        else if (k === 'color') { this.base.color = color(o.color); }
        else if (k === 'visible') { this.base.visible = !!o.visible; }
        else if (FIELDS.indexOf(k) >= 0) { this.base[k] = num(o[k], k); }
        else if (this.optionKeys.indexOf(k) < 0) fail(`unknown option "${k}" for ${kind}`);
      }
    }
    get optionKeys() { return this.params._opts || []; }
    add() {
      for (const k of arguments) {
        if (!(k instanceof Node)) fail('add() needs objects made with v.box, v.group, ...');
        if (k.parent) fail('this object already has a parent');
        if (k === this) fail('an object cannot contain itself');
        k.parent = this; this.children.push(k);
      }
      return this;
    }
    anim(fn) {
      if (typeof fn !== 'function') fail('anim() needs a function (t, p) => ({ ...fields })');
      if (this._b.animCount >= LIMITS.anims) fail(`too many animations (max ${LIMITS.anims})`);
      this._b.animCount++; this.anims.push(fn); return this;
    }
  }

  function makeBuilder(dur) {
    const b = { nodes: [], animCount: 0, tris: 0, camera: null, bg: null, fog: null, sun: null, dur };
    const mk = (kind, params, o) => new Node(b, kind, params, o);
    const v = { dur: dur, palette: Object.keys(PALETTE), lerp, clamp, ease };
    v.step = (t, t0, t1) => ease((t - t0) / Math.max(1e-6, t1 - t0));          // 0 before t0, eases to 1 at t1
    v.wave = (t, freq, amp) => (amp === undefined ? 1 : amp) * Math.sin(2 * Math.PI * (freq === undefined ? 1 : freq) * t);
    v.box = (w, h, d, o) => mk('box', { w: num(w, 'w'), h: num(h, 'h'), d: num(d, 'd') }, o);
    v.cylinder = (rt, rb, h, o) => mk('cylinder', { rt: num(rt, 'rt'), rb: num(rb, 'rb'), h: num(h, 'h') }, o);
    v.sphere = (r, o) => mk('sphere', { r: num(r, 'r') }, o);
    v.cone = (r, h, o) => mk('cone', { r: num(r, 'r'), h: num(h, 'h') }, o);
    v.capsule = (r, len, o) => mk('capsule', { r: num(r, 'r'), len: num(len, 'len') }, o);
    v.plane = (w, d, o) => mk('plane', { w: num(w, 'w'), d: num(d, 'd') }, o);          // lies flat (floor), normal up
    v.group = (o) => mk('group', {}, o);
    v.label = (text, o) => {
      if (typeof text !== 'string' || !text.length) fail('label() needs text');
      if (text.length > LIMITS.labelChars) fail(`label text is too long (max ${LIMITS.labelChars} characters)`);
      o = Object.assign({}, o);
      const size = o.size === undefined ? 2 : num(o.size, 'size'); delete o.size;
      const fg = o.fg === undefined ? 'white' : o.fg; delete o.fg;
      const bg = o.bg === undefined ? null : o.bg; delete o.bg;
      return mk('label', { text, size, fg: color(fg), bg: bg === null ? null : color(bg) }, o);
    };
    v.sky = (top, bottom) => { b.bg = { top: color(top), bottom: color(bottom === undefined ? top : bottom) }; };
    v.fog = (c, near, far) => { b.fog = { color: color(c), near: num(near, 'near'), far: num(far, 'far') }; };
    v.sun = (o) => { o = o || {}; b.sun = { dir: vec3(o.dir || [30, 50, 20], 'dir'), intensity: o.intensity === undefined ? 1.1 : num(o.intensity, 'intensity') }; };
    v.camera = (fn) => {
      if (typeof fn !== 'function') fail('camera() needs a function (t, p) => ({ pos: [x,y,z], look: [x,y,z], fov })');
      if (b.camera) fail('the camera is already set; call it once');
      b.camera = fn;
    };
    const at = (a, bb, u) => a.map((x, i) => lerp(x, bb[i], u));
    const lookOf = (look, u) => (look && look.from ? at(vec3(look.from, 'look.from'), vec3(look.to, 'look.to'), u) : vec3(look, 'look'));
    v.cam = {
      // Straight move (dolly, pan or crane) from one position to another, eased.
      dolly(o) {
        const from = vec3(o.from, 'from'), to = vec3(o.to, 'to'), fov = o.fov === undefined ? 55 : num(o.fov, 'fov');
        v.camera((t, p) => ({ pos: at(from, to, ease(p)), look: lookOf(o.look, ease(p)), fov }));
      },
      // Circle around a centre; angles in degrees (0 = looking from +z).
      orbit(o) {
        const c = vec3(o.center, 'center'), r = num(o.radius, 'radius'), h = num(o.height === undefined ? 6 : o.height, 'height');
        const a0 = num(o.from === undefined ? 0 : o.from, 'from'), a1 = num(o.to === undefined ? 60 : o.to, 'to'), fov = o.fov === undefined ? 55 : num(o.fov, 'fov');
        v.camera((t, p) => { const a = lerp(a0, a1, ease(p)) * Math.PI / 180; return { pos: [c[0] + Math.sin(a) * r, c[1] + h, c[2] + Math.cos(a) * r], look: o.look ? vec3(o.look, 'look') : c, fov }; });
      },
      still(o) { const pos = vec3(o.pos, 'pos'), look = vec3(o.look, 'look'), fov = o.fov === undefined ? 55 : num(o.fov, 'fov'); v.camera(() => ({ pos, look, fov })); },
    };

    // ---- ready-made pieces (built from the shapes above) ----
    v.ground = (o) => { o = o || {}; return v.plane(o.w || 400, o.d || 400, { color: o.color || 'sand', y: o.y || 0 }); };
    v.water = (o) => {
      o = o || {}; const size = o.size || [160, 120];
      return mk('water', { w: num(size[0], 'size[0]'), d: num(size[1], 'size[1]'), _opts: ['size'] },
        { y: o.y === undefined ? -0.3 : o.y, x: o.x || 0, z: o.z || 0, color: o.color || 'water', opacity: o.opacity === undefined ? 0.88 : o.opacity });
    };
    v.person = (o) => {
      o = o || {}; const col = o.color || 'blue';
      const g = v.group({ x: o.x, y: o.y, z: o.z, ry: o.ry, s: o.s });
      g.add(v.capsule(0.28, 0.9, { y: 0.75, color: col }), v.sphere(0.26, { y: 1.78, color: o.skin || 'skin' }));
      g.armL = v.group({ x: -0.38, y: 1.3 }).add(v.capsule(0.09, 0.5, { y: -0.3, color: col }));
      g.armR = v.group({ x: 0.38, y: 1.3 }).add(v.capsule(0.09, 0.5, { y: -0.3, color: col }));
      return g.add(g.armL, g.armR);                      // animate arms with person.armL.anim(t => ({ rz: ... }))
    };
    v.palm = (o) => {
      o = o || {}; const g = v.group({ x: o.x, y: o.y, z: o.z, s: o.s });
      g.add(v.cylinder(0.2, 0.3, 7, { y: 3.5, color: 'brown' }));
      for (let k = 0; k < 5; k++) g.add(v.cone(0.5, 3.4, { x: Math.cos(k * 1.3) * 1.4, y: 7, z: Math.sin(k * 1.3) * 1.4, rx: Math.sin(k * 1.3) * 1.2, rz: Math.cos(k * 1.3) * 1.2, color: 'green' }));
      return g;
    };
    v.stiltHouse = (o) => {
      o = o || {}; const g = v.group({ x: o.x, y: o.y, z: o.z, ry: o.ry, s: o.s });
      for (const dx of [-1, 1]) for (const dz of [-1, 1]) g.add(v.cylinder(0.15, 0.15, 2.4, { x: dx, z: dz, y: 1.2, color: 'brown' }));
      g.add(v.box(3.4, 2, 3.4, { y: 3.4, color: o.color || 'wood' }), v.cone(2.9, 1.6, { y: 5.2, ry: Math.PI / 4, color: 'roof' }));
      return g;
    };
    // A building whose ground floor is open (columns only), so water can pass through.
    v.pilotiBuilding = (o) => {
      o = o || {}; const floors = Math.max(1, Math.min(8, o.floors || 3)), w = o.w || 22, d = o.d || 14, fh = o.floorH || 5;
      const g = v.group({ x: o.x, y: o.y, z: o.z, ry: o.ry, s: o.s });
      const cols = Math.max(2, Math.min(8, o.columns || 6));
      for (let r = 0; r < 2; r++) for (let i = 0; i < cols; i++) g.add(v.cylinder(0.55, 0.55, floors * fh, { x: -w / 2 + 1 + i * ((w - 2) / (cols - 1)), y: floors * fh / 2, z: r ? d / 2 - 1 : -d / 2 + 1, color: 'concrete' }));
      for (let f = 0; f < floors; f++) {
        g.add(v.box(w, 0.6, d, { y: fh * (f + 1), color: 'concrete' }), v.box(w + 0.2, 0.25, d + 0.2, { y: fh * (f + 1) + 0.4, color: o.trim || 'blue' }),
          v.box(w, 1.0, 0.15, { y: fh * (f + 1) + 1.1, z: d / 2 - 0.1, color: 'white' }));
      }
      return g;
    };
    v.sign = (text, o) => { o = o || {}; const g = v.group({ x: o.x, y: o.y, z: o.z, ry: o.ry }); g.add(v.box(o.w || 6, o.h || 2.4, 0.3, { color: o.color || 'orange' }), v.label(text, { z: 0.25, size: o.size || 1.6, fg: o.fg || 'white' })); return g; };
    v._b = b;
    return v;
  }

  // ---- evaluation ----
  const mulM = (a, c) => { const r = new Array(16).fill(0); for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) { let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + j] * c[i * 4 + k]; r[i * 4 + j] = s; } return r; };
  function composeM(x, y, z, rx, ry, rz, sx, sy, sz) {            // three.js Euler 'XYZ' order, column-major
    const a = Math.cos(rx), bb = Math.sin(rx), c = Math.cos(ry), d = Math.sin(ry), e = Math.cos(rz), f = Math.sin(rz);
    const ae = a * e, af = a * f, be = bb * e, bf = bb * f;
    return [c * e * sx, (af + be * d) * sx, (bf - ae * d) * sx, 0, -c * f * sy, (ae - bf * d) * sy, (be + af * d) * sy, 0, d * sz, -bb * c * sz, a * c * sz, 0, x, y, z, 1];
  }
  function resolve(node, t, p) {
    const s = Object.assign({}, node.base);
    for (const fn of node.anims) {
      const r = fn(t, p);
      if (r === undefined || r === null) continue;
      if (typeof r !== 'object') fail(`an animation must return an object like { y: 1 }, got ${typeof r}`);
      for (const k of Object.keys(r)) {
        if (k === 's') { s.sx = s.sy = s.sz = r.s; }
        else if (FIELDS.indexOf(k) >= 0) s[k] = r[k];
        else fail(`an animation returned the unknown field "${k}" (allowed: ${FIELDS.join(', ')}, s)`);
      }
    }
    for (const k of ['x', 'y', 'z', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz', 'opacity']) if (!isNum(s[k])) fail(`${node.kind} #${node.id}: "${k}" is ${s[k]} at t=${t.toFixed(2)}s (not a finite number)`);
    if (s.color !== null && typeof s.color === 'string') s.color = color(s.color);
    return s;
  }
  function evalScene(b, t) {
    const p = clamp(t / Math.max(1e-6, b.dur), 0, 1), out = new Array(b.nodes.length);
    const walk = (n, parentM, parentVisible) => {
      const s = resolve(n, t, p), m = mulM(parentM, composeM(s.x, s.y, s.z, s.rx, s.ry, s.rz, s.sx, s.sy, s.sz)), vis = parentVisible && !!s.visible;
      out[n.id] = { id: n.id, kind: n.kind, params: n.params, state: s, world: m, pos: [m[12], m[13], m[14]], visible: vis };
      for (const c of n.children) walk(c, m, vis);
    };
    const I = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    for (const n of b.nodes) if (!n.parent) walk(n, I, true);
    return out;
  }
  function evalCamera(b, t) {
    const p = clamp(t / Math.max(1e-6, b.dur), 0, 1), c = b.camera(t, p);
    if (!c || typeof c !== 'object') fail('the camera function must return { pos: [x,y,z], look: [x,y,z], fov }');
    const pos = vec3(c.pos, 'camera pos'), look = vec3(c.look, 'camera look'), fov = c.fov === undefined ? 55 : num(c.fov, 'camera fov');
    return { pos, look, fov };
  }
  // true when world point q is inside the camera's view (portrait aspect = width / height).
  function inView(cam, q, aspect, margin) {
    const f = cam.look.map((x, i) => x - cam.pos[i]), fl = Math.hypot(f[0], f[1], f[2]) || 1, fw = f.map((x) => x / fl);
    let r = [fw[2] * 0 - fw[1] * 0, 0, 0];                    // right = forward x up(0,1,0)
    r = [fw[1] * 0 - fw[2] * 1, fw[2] * 0 - fw[0] * 0, fw[0] * 1 - fw[1] * 0];
    const rl = Math.hypot(r[0], r[1], r[2]) || 1; r = r.map((x) => x / rl);
    const u = [r[1] * fw[2] - r[2] * fw[1], r[2] * fw[0] - r[0] * fw[2], r[0] * fw[1] - r[1] * fw[0]];
    const d = q.map((x, i) => x - cam.pos[i]);
    const z = d[0] * fw[0] + d[1] * fw[1] + d[2] * fw[2];
    if (z < 0.3) return false;
    const th = Math.tan(cam.fov * Math.PI / 360);
    const x = (d[0] * r[0] + d[1] * r[1] + d[2] * r[2]) / z, y = (d[0] * u[0] + d[1] * u[1] + d[2] * u[2]) / z;
    return Math.abs(x) <= th * aspect * margin && Math.abs(y) <= th * margin;
  }

  function stripLiterals(code) {          // blank out comments and string contents so the ban list only sees real code
    let out = '', i = 0; const n = code.length;
    while (i < n) {
      const ch = code[i], nx = code[i + 1];
      if (ch === '/' && nx === '/') { while (i < n && code[i] !== '\n') { out += ' '; i++; } continue; }
      if (ch === '/' && nx === '*') { out += '  '; i += 2; while (i < n && !(code[i] === '*' && code[i + 1] === '/')) { out += code[i] === '\n' ? '\n' : ' '; i++; } out += '  '; i += 2; continue; }
      if (ch === '"' || ch === "'" || ch === '`') {
        const q = ch; out += q; i++;
        while (i < n && code[i] !== q) { if (code[i] === '\\') { out += '  '; i += 2; continue; } out += code[i] === '\n' ? '\n' : ' '; i++; }
        out += q; i++; continue;
      }
      out += ch; i++;
    }
    return out;
  }

  // Turns beat source into its function, with browser globals shadowed. Throws on syntax errors.
  function compile(code, name) {
    return new Function(...SHADOWED, 'Math', '"use strict";\n' + code + '\nreturn ' + name + ';')(...SHADOWED.map(() => undefined), Math);
  }

  // Dry-run one beat. Returns { ok, errors[], warnings[], stats }. Never throws.
  function validate(code, name, opts) {
    opts = opts || {}; const dur = opts.dur || 2, aspect = opts.aspect || 9 / 16;
    const errors = [], warnings = [], stats = { nodes: 0, anims: 0, triangles: 0, minVisible: null };
    const done = () => ({ ok: errors.length === 0, errors, warnings, stats });
    if (typeof code !== 'string' || !code.trim()) { errors.push('the beat has no code'); return done(); }
    if (code.length > LIMITS.codeChars) { errors.push(`the code is too long (${code.length} characters, max ${LIMITS.codeChars}); simplify it`); return done(); }
    const bare = stripLiterals(code);
    for (const w of BANNED) if (new RegExp('(^|[^\\w$.])' + w + '(?![\\w$])').test(bare)) errors.push(`"${w}" is not allowed in a beat (no loops without a fixed count, no network, no clock, no browser objects)`);
    if (/for\s*\(\s*;\s*;/.test(bare)) errors.push('an endless for(;;) loop is not allowed');
    if (/Math\s*\.\s*random/.test(bare)) errors.push('Math.random is not allowed: the video must look the same on every render. Derive variety from the loop index.');
    if (!new RegExp('function\\s+' + name + '\\s*\\(').test(bare)) errors.push(`the code must define function ${name}(v)`);
    if (errors.length) return done();
    let fn;
    try { fn = compile(code, name); } catch (e) { errors.push('syntax or load error: ' + (e && e.message)); return done(); }
    const v = makeBuilder(dur);
    try { fn(v); } catch (e) { errors.push(`${name}(v) failed: ${e && e.message}`); return done(); }
    const b = v._b; stats.nodes = b.nodes.length; stats.anims = b.animCount; stats.triangles = b.tris;
    if (!b.camera) { errors.push('no camera: call v.cam.dolly(...), v.cam.orbit(...) or v.camera(fn)'); return done(); }
    if (b.nodes.length < 4) errors.push(`only ${b.nodes.length} objects: build a recognisable scene (at least 8 objects)`);
    const times = [0, 0.25, 0.5, 0.75, 1].map((u) => Math.min(dur - 1 / 30, u * dur));
    let low = 0, moved = false, first = null;
    for (const t of times) {
      let scene, cam, again;
      try { scene = evalScene(b, t); cam = evalCamera(b, t); again = evalScene(b, t); } catch (e) { errors.push(`at t=${t.toFixed(2)}s: ${e && e.message}`); return done(); }
      if (JSON.stringify(scene.map((s) => s.pos)) !== JSON.stringify(again.map((s) => s.pos))) { errors.push('the animation changes between two evaluations at the same time: it must depend only on t and p'); return done(); }
      if (Math.hypot(cam.pos[0] - cam.look[0], cam.pos[1] - cam.look[1], cam.pos[2] - cam.look[2]) < 0.5) { errors.push(`at t=${t.toFixed(2)}s the camera is on top of its target (pos and look are the same point)`); return done(); }
      if (cam.fov < 20 || cam.fov > 100) { errors.push(`camera fov ${cam.fov} is outside 20..100`); return done(); }
      const seen = scene.filter((s) => s.visible && s.kind !== 'plane' && s.kind !== 'water' && s.kind !== 'group' && inView(cam, s.pos, aspect, 1.05)).length;
      stats.minVisible = stats.minVisible === null ? seen : Math.min(stats.minVisible, seen);
      if (seen < 2) low++;
      const sig = JSON.stringify([cam.pos, cam.look]); if (first === null) first = sig; else if (sig !== first) moved = true;
    }
    if (low >= 3) errors.push(`the camera looks at empty space: fewer than 2 objects are in view at ${low} of 5 sample times. Aim v.cam at your objects (look at the middle of the subject) and keep it close enough to see them.`);
    if (!moved && b.animCount === 0) warnings.push('nothing moves: add a camera move (dolly/orbit) or .anim(...) on objects');
    else if (!moved) warnings.push('the camera does not move; a slow dolly or orbit makes the shot feel 3D');
    return done();
  }

  return { PALETTE, LIMITS, makeBuilder, evalScene, evalCamera, inView, validate, stripLiterals, compile, composeM, mulM };
})();
if (typeof module !== 'undefined') module.exports = V3;
