// Scene 3D renderer: turns the node tree made by v3_core.js into three.js objects and draws frames.
// Runs in the page of headless Chromium after three.js (r149) and v3_core.js are loaded.
'use strict';
var R3 = (function () {
  function create(THREE, opts) {
    opts = opts || {};
    const W = opts.width || 1080, H = opts.height || 1920;
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(1); renderer.setSize(W, H);
    const canvas = renderer.domElement;
    if (document.body) document.body.appendChild(canvas);
    const camera = new THREE.PerspectiveCamera(55, W / H, 0.1, 600);
    let cur = null;        // { b, scene, objs, water[], dispose() }

    const GEO = {
      box: (p) => new THREE.BoxGeometry(p.w, p.h, p.d),
      cylinder: (p) => new THREE.CylinderGeometry(p.rt, p.rb, p.h, 16),
      sphere: (p) => new THREE.SphereGeometry(p.r, 16, 12),
      cone: (p) => new THREE.ConeGeometry(p.r, p.h, 16),
      capsule: (p) => new THREE.CapsuleGeometry(p.r, p.len, 4, 10),
      plane: (p) => { const g = new THREE.PlaneGeometry(p.w, p.d); g.rotateX(-Math.PI / 2); return g; },
      water: (p) => { const g = new THREE.PlaneGeometry(p.w, p.d, 64, 64); g.rotateX(-Math.PI / 2); return g; },
    };

    function labelMesh(p) {
      const c = document.createElement('canvas'), g = c.getContext('2d'), px = 96;
      g.font = 'bold ' + px + 'px sans-serif';
      const tw = Math.ceil(g.measureText(p.text).width), pad = Math.round(px * 0.35);
      c.width = tw + pad * 2; c.height = Math.round(px * 1.5);
      g.font = 'bold ' + px + 'px sans-serif'; g.textBaseline = 'middle'; g.textAlign = 'center';
      if (p.bg) { g.fillStyle = p.bg; g.beginPath(); g.roundRect ? g.roundRect(0, 0, c.width, c.height, px * 0.25) : g.rect(0, 0, c.width, c.height); g.fill(); }
      g.fillStyle = p.fg; g.fillText(p.text, c.width / 2, c.height / 2 + px * 0.04);
      const tex = new THREE.CanvasTexture(c);
      const h = p.size, w = h * c.width / c.height;
      const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide });
      return new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
    }

    function gradientTexture(top, bottom) {
      const c = document.createElement('canvas'); c.width = 2; c.height = 256;
      const g = c.getContext('2d'), grad = g.createLinearGradient(0, 0, 0, 256);
      grad.addColorStop(0, top); grad.addColorStop(1, bottom); g.fillStyle = grad; g.fillRect(0, 0, 2, 256);
      return new THREE.CanvasTexture(c);
    }

    function unload() {
      if (!cur) return;
      for (const o of cur.objs) {
        if (!o) continue;
        cur.scene.remove(o);
        if (o.geometry && o.geometry.dispose) o.geometry.dispose();
        if (o.material) { if (o.material.map && o.material.map.dispose) o.material.map.dispose(); if (o.material.dispose) o.material.dispose(); }
      }
      if (cur.scene.background && cur.scene.background.dispose) cur.scene.background.dispose();
      cur = null;
    }

    // Builds the scene of one beat. Throws with the validator's message when the code is not acceptable.
    function loadBeat(code, name, dur) {
      const res = V3.validate(code, name, { dur: dur, aspect: W / H });
      if (!res.ok) throw new Error(name + ' failed the check: ' + res.errors.join('; '));
      unload();
      const v = V3.makeBuilder(dur);
      V3.compile(code, name)(v);
      const b = v._b, scene = new THREE.Scene(), objs = new Array(b.nodes.length).fill(null), water = [];
      const bg = b.bg || { top: V3.PALETTE.sky, bottom: V3.PALETTE.cream };
      scene.background = gradientTexture(bg.top, bg.bottom);
      if (b.fog) scene.fog = new THREE.Fog(b.fog.color, b.fog.near, b.fog.far);
      scene.add(new THREE.HemisphereLight(0xdfeeff, 0xc9b27c, 0.75));
      const sun = b.sun || { dir: [30, 50, 20], intensity: 1.0 };
      const dl = new THREE.DirectionalLight(0xffffff, sun.intensity); dl.position.set(sun.dir[0], sun.dir[1], sun.dir[2]); scene.add(dl);
      for (const n of b.nodes) {
        if (n.kind === 'group') continue;
        let mesh;
        if (n.kind === 'label') mesh = labelMesh(n.params);
        else {
          const mat = new THREE.MeshLambertMaterial({ color: n.base.color || '#c8c8c8' });
          mesh = new THREE.Mesh(GEO[n.kind](n.params), mat);
          if (n.kind === 'water') water.push(mesh);
        }
        mesh.matrixAutoUpdate = false;
        mesh._color = null;
        objs[n.id] = mesh; scene.add(mesh);
      }
      cur = { b: b, scene: scene, objs: objs, water: water, dur: dur };
      return { nodes: b.nodes.length, triangles: b.tris };
    }

    function frame(t) {
      if (!cur) throw new Error('no beat loaded');
      const b = cur.b, st = V3.evalScene(b, t), cam = V3.evalCamera(b, t);
      for (const s of st) {
        const o = cur.objs[s.id]; if (!o) continue;
        o.visible = s.visible;
        o.matrix.fromArray(s.world); o.matrixWorldNeedsUpdate = true;
        const m = o.material, op = s.state.opacity;
        if (m.opacity !== op) { m.opacity = op; m.transparent = op < 1 || s.kind === 'label'; }
        if (s.kind !== 'label' && s.state.color && o._color !== s.state.color) { o._color = s.state.color; m.color.set(s.state.color); }
      }
      for (const w of cur.water) {                                  // gentle swell
        const a = w.geometry.attributes.position, arr = a.array;
        for (let i = 0; i < arr.length; i += 3) arr[i + 1] = Math.sin(arr[i] * 0.15 + t * 1.2) * 0.15 + Math.cos(arr[i + 2] * 0.12 + t * 0.9) * 0.12;
        a.needsUpdate = true;
      }
      if (camera.fov !== cam.fov) { camera.fov = cam.fov; camera.updateProjectionMatrix(); }
      camera.position.set(cam.pos[0], cam.pos[1], cam.pos[2]); camera.lookAt(cam.look[0], cam.look[1], cam.look[2]);
      renderer.render(cur.scene, camera);
    }

    function jpeg(quality) { return canvas.toDataURL('image/jpeg', quality === undefined ? 0.92 : quality).split(',', 2)[1]; }
    function info() { const i = renderer.info && renderer.info.render ? renderer.info.render : {}; return { calls: i.calls, triangles: i.triangles, nodes: cur ? cur.b.nodes.length : 0 }; }
    return { loadBeat: loadBeat, frame: frame, jpeg: jpeg, info: info, unload: unload, size: [W, H] };
  }
  return { create: create };
})();
