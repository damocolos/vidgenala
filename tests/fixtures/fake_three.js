// A tiny stand-in for three.js (only what renderer.js uses). render() projects mesh positions with a pinhole
// camera onto a 2D canvas, so frames really change with the camera and the scene matrices. Test-only.
(function () {
  class Vec { constructor() { this.x = 0; this.y = 0; this.z = 0; } set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; } }
  class Color { constructor(c) { this.c = c; } set(c) { this.c = c; return this; } }
  class Mat4 { constructor() { this.e = new Array(16).fill(0); } fromArray(a) { this.e = a.slice(); return this; } }
  const geo = (kind) => class { constructor(...a) { this.kind = kind; this.args = a; const n = 65 * 65; this.attributes = { position: { array: new Float32Array(n * 3), needsUpdate: false } }; for (let i = 0; i < n; i++) { this.attributes.position.array[i * 3] = (i % 65) - 32; this.attributes.position.array[i * 3 + 2] = Math.floor(i / 65) - 32; } } rotateX() { return this; } dispose() { window.__disposed = (window.__disposed || 0) + 1; } };
  const mat = () => class { constructor(o) { o = o || {}; this.color = new Color(o.color || '#ffffff'); this.opacity = 1; this.transparent = !!o.transparent; this.map = o.map || null; } dispose() {} };
  class Mesh { constructor(g, m) { this.geometry = g; this.material = m; this.matrix = new Mat4(); this.visible = true; this.matrixAutoUpdate = true; } }
  class Scene { constructor() { this.children = []; this.background = null; this.fog = null; } add(o) { this.children.push(o); } remove(o) { this.children = this.children.filter((c) => c !== o); } }
  class Cam { constructor(fov, aspect) { this.fov = fov; this.aspect = aspect; this.position = new Vec(); this.target = [0, 0, 0]; } lookAt(x, y, z) { this.target = [x, y, z]; } updateProjectionMatrix() {} }
  class Renderer {
    constructor() { this.domElement = document.createElement('canvas'); this.info = { render: { calls: 0, triangles: 0 } }; }
    setPixelRatio() {} setSize(w, h) { this.domElement.width = w; this.domElement.height = h; }
    render(scene, cam) {
      const c = this.domElement, g = c.getContext('2d'), W = c.width, H = c.height;
      if (scene.background && scene.background.image) g.drawImage(scene.background.image, 0, 0, W, H); else { g.fillStyle = '#888'; g.fillRect(0, 0, W, H); }
      const p = [cam.position.x, cam.position.y, cam.position.z], f = cam.target.map((x, i) => x - p[i]), fl = Math.hypot(...f), fw = f.map((x) => x / fl);
      let r = [-fw[2], 0, fw[0]]; const rl = Math.hypot(...r) || 1; r = r.map((x) => x / rl);
      const u = [r[1] * fw[2] - r[2] * fw[1], r[2] * fw[0] - r[0] * fw[2], r[0] * fw[1] - r[1] * fw[0]];
      const th = Math.tan(cam.fov * Math.PI / 360); let drawn = 0;
      const items = scene.children.filter((o) => o.matrix && o.visible).map((o) => { const e = o.matrix.e, d = [e[12] - p[0], e[13] - p[1], e[14] - p[2]]; return { o, d, z: d[0] * fw[0] + d[1] * fw[1] + d[2] * fw[2] }; }).filter((i) => i.z > 0.3).sort((a, b) => b.z - a.z);
      for (const it of items) {
        const x = (it.d[0] * r[0] + it.d[1] * r[1] + it.d[2] * r[2]) / it.z, y = (it.d[0] * u[0] + it.d[1] * u[1] + it.d[2] * u[2]) / it.z;
        const sx = W / 2 + x / (th * cam.aspect) * W / 2, sy = H / 2 - y / th * H / 2, s = Math.max(6, 900 / it.z);
        g.globalAlpha = it.o.material.opacity; g.fillStyle = it.o.material.color.c; g.fillRect(sx - s / 2, sy - s / 2, s, s); drawn++;
      }
      g.globalAlpha = 1; this.info.render.calls = drawn;
    }
  }
  window.THREE = {
    WebGLRenderer: Renderer, PerspectiveCamera: Cam, Scene, Mesh, Color, DoubleSide: 2,
    Fog: class { constructor(c, n, f) { this.color = c; this.near = n; this.far = f; } },
    HemisphereLight: class {}, DirectionalLight: class { constructor() { this.position = new Vec(); } },
    CanvasTexture: class { constructor(c) { this.image = c; } dispose() {} },
    BoxGeometry: geo('box'), CylinderGeometry: geo('cylinder'), SphereGeometry: geo('sphere'), ConeGeometry: geo('cone'),
    CapsuleGeometry: geo('capsule'), PlaneGeometry: geo('plane'),
    MeshLambertMaterial: mat(), MeshBasicMaterial: mat(),
  };
})();
