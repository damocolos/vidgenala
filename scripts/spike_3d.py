"""Phase 0 speed test for a possible "Scene 3D" video type. Changes nothing in the app.

    pip install playwright && playwright install chromium     # once
    python scripts/spike_3d.py                                  # 1080x1920, 5 shots x 24 frames
    python scripts/spike_3d.py --width 720 --height 1280 --frames 12

It answers three questions on THIS computer:
  1. Does Chromium get a real GPU, or software rendering (the slow case)?
  2. How long does one 1080x1920 frame take with a plain WebGL scene (lower bound)?
  3. How long with a small three.js scene (about 150 meshes, shadows, fog, a moving water surface and a
     moving camera: roughly what one shot of the reference video needs)? Needs internet once, to cache
     three.js r149 next to the report.
Then it estimates the render time of an 82-second video (2,475 frames) and writes a report plus the first frame
of each shot to data/spike_3d/ (or --out).
"""
import argparse
import base64
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.149.0/build/three.min.js"
REFERENCE_FRAMES = 2475          # the reference video: 82.5 s at 30 fps
SOFTWARE_RE = re.compile(r"swiftshader|llvmpipe|software|basic render|softpipe|mesa offscreen", re.I)

# ---- pure helpers (unit-tested without a browser) ----------------------------------------------


def is_software_renderer(name: str) -> bool:
    return bool(SOFTWARE_RE.search(name or ""))


def estimate_minutes(seconds_per_frame: float, frames: int = REFERENCE_FRAMES) -> float:
    return seconds_per_frame * frames / 60.0


def verdict(seconds_per_frame: float) -> str:
    """Plain-language reading of the three.js timing for a full-size video."""
    mins = estimate_minutes(seconds_per_frame)
    if mins <= 20:
        return f"GOOD: about {mins:.0f} min for an 82 s video. Scene 3D is practical here."
    if mins <= 60:
        return (f"USABLE: about {mins:.0f} min for an 82 s video. Fine for final renders; "
                f"use 720x1280 for drafts.")
    return (f"TOO SLOW: about {mins:.0f} min for an 82 s video. Prefer the 2.5D path, or a computer with a GPU.")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--frames", type=int, default=24, help="frames timed per shot (default 24)")
    ap.add_argument("--shots", type=int, default=5, help="camera moves to time, 1-5 (default 5)")
    ap.add_argument("--out", default="data/spike_3d")
    ap.add_argument("--no-three", action="store_true", help="only probe the GPU and time the plain WebGL scene")
    ap.add_argument("--three-url", default=THREE_URL)
    a = ap.parse_args(argv)
    a.width, a.height = max(64, min(a.width, 2160)), max(64, min(a.height, 3840))
    a.frames, a.shots = max(2, min(a.frames, 300)), max(1, min(a.shots, 5))
    return a


# ---- browser side -------------------------------------------------------------------------------

PROBE_JS = """() => {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl2') || c.getContext('webgl');
  if (!gl) return {ok: false};
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  return {ok: true, webgl2: (typeof WebGL2RenderingContext !== 'undefined') && gl instanceof WebGL2RenderingContext,
          renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
          vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR)};
}"""

RAW_JS = """([w, h, n]) => {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const gl = c.getContext('webgl2', {preserveDrawingBuffer: true}); if (!gl) return null;
  const vs = `#version 300 es
  in vec3 p; in vec3 nrm; uniform mat4 m; uniform float t; out float l;
  void main(){ vec3 q = p; q.x += float(gl_InstanceID % 20) * 2.2 - 22.0; q.z += float(gl_InstanceID / 20) * 2.2 - 8.0;
    q.y += 0.5 + 0.3 * sin(t + float(gl_InstanceID));
    l = max(dot(normalize(nrm), normalize(vec3(0.4, 1.0, 0.3))), 0.0) * 0.8 + 0.2; gl_Position = m * vec4(q, 1.0); }`;
  const fs = `#version 300 es
  precision mediump float; in float l; out vec4 o; void main(){ o = vec4(vec3(0.9, 0.8, 0.6) * l, 1.0); }`;
  const sh = (t, s) => { const x = gl.createShader(t); gl.shaderSource(x, s); gl.compileShader(x); return x; };
  const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(pr); if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) return {error: gl.getProgramInfoLog(pr)};
  const P = [[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1],[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1]];
  const F = [[0,1,2,3,0,0,1],[5,4,7,6,0,0,-1],[3,2,6,7,0,1,0],[4,5,1,0,0,-1,0],[1,5,6,2,1,0,0],[4,0,3,7,-1,0,0]];
  const pos = [], nr = [];
  for (const f of F) for (const i of [0,1,2,0,2,3]) { pos.push(...P[f[i]]); nr.push(f[4], f[5], f[6]); }
  const bind = (name, data) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(pr, name); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0); };
  gl.useProgram(pr); bind('p', pos); bind('nrm', nr);
  gl.enable(gl.DEPTH_TEST); gl.viewport(0, 0, w, h);
  const persp = (fov, a, n0, f0) => { const t = 1 / Math.tan(fov / 2);
    return [t / a,0,0,0, 0,t,0,0, 0,0,(f0+n0)/(n0-f0),-1, 0,0,2*f0*n0/(n0-f0),0]; };
  const P4 = persp(1.0, w / h, 0.1, 200);
  const frameMs = [];
  for (let k = 0; k < n; k++) {
    const t0 = performance.now();
    const ang = k * 0.02, ca = Math.cos(ang), sa = Math.sin(ang);
    // camera 12 units back and up, slowly orbiting: view = translate * rotY
    const V = [ca,0,-sa,0, 0,1,0,0, sa,0,ca,0, 0,-6,-24,1];
    const M = new Float32Array(16);
    for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) { let s = 0; for (let q = 0; q < 4; q++) s += P4[q*4+j] * V[i*4+q]; M[i*4+j] = s; }
    gl.uniformMatrix4fv(gl.getUniformLocation(pr, 'm'), false, M);
    gl.uniform1f(gl.getUniformLocation(pr, 't'), k * 0.1);
    gl.clearColor(0.45, 0.7, 0.9, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 36, 400);
    const px = new Uint8Array(4); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);   // forces the GPU to finish
    c.toDataURL('image/jpeg', 0.9);                                                            // what a frame grab costs
    frameMs.push(performance.now() - t0);
  }
  return {frameMs};
}"""

THREE_SCENE_JS = """
window.spike = (function () {
  const W = %(w)d, H = %(h)d;
  const renderer = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
  renderer.setSize(W, H); renderer.setPixelRatio(1);
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  document.body.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x8fc4ee); scene.fog = new THREE.Fog(0x8fc4ee, 40, 140);
  scene.add(new THREE.HemisphereLight(0xcfe6ff, 0xc9b27c, 0.9));
  const sun = new THREE.DirectionalLight(0xffffff, 1.1); sun.position.set(30, 50, 20); sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048); Object.assign(sun.shadow.camera, {left: -40, right: 40, top: 40, bottom: -40, near: 1, far: 140});
  if (sun.shadow.camera.updateProjectionMatrix) sun.shadow.camera.updateProjectionMatrix();
  scene.add(sun);
  const mat = (c) => new THREE.MeshStandardMaterial({color: c, roughness: 0.85, metalness: 0.0});
  const add = (geo, m, x, y, z) => { const o = new THREE.Mesh(geo, m); o.position.set(x, y, z); o.castShadow = o.receiveShadow = true; scene.add(o); return o; };
  const ground = add(new THREE.PlaneGeometry(260, 260), mat(0xd8c08e), 0, 0, 0); ground.rotation.x = -Math.PI / 2; ground.castShadow = false;
  // water: a displaced plane whose level can rise
  const wgeo = new THREE.PlaneGeometry(120, 120, 64, 64); wgeo.rotateX(-Math.PI / 2);
  const water = new THREE.Mesh(wgeo, new THREE.MeshStandardMaterial({color: 0x6b5a3c, roughness: 0.35, transparent: true, opacity: 0.9}));
  water.position.set(0, -0.4, -30); water.receiveShadow = true; scene.add(water);
  const wbase = wgeo.attributes.position.array.slice();
  // evacuation building on columns: 3 floors, 12 columns, railings
  const concrete = mat(0xb9b6ae), blue = mat(0x2f6fd6), white = mat(0xf4f4f2);
  for (let f = 0; f < 3; f++) {
    add(new THREE.BoxGeometry(22, 0.6, 14), concrete, 0, 5 + f * 5, 0);
    add(new THREE.BoxGeometry(22.2, 0.25, 14.2), blue, 0, 5.4 + f * 5, 0);
    for (let i = 0; i < 12; i++) add(new THREE.BoxGeometry(0.15, 1.0, 0.15), white, -10.5 + i * 1.9, 6.1 + f * 5, 6.9);
  }
  for (let i = 0; i < 12; i++) add(new THREE.CylinderGeometry(0.55, 0.55, 15, 14), concrete, -9 + (i %% 6) * 3.6, 7.5, i < 6 ? -5 : 5);
  add(new THREE.BoxGeometry(6, 4, 0.4), mat(0xd9a64a), 0, 2.2, -5.2);                      // breakaway wall
  add(new THREE.BoxGeometry(5, 2.2, 0.3), mat(0xee5a2b), 0, 21, 7.2);                        // sign
  // stilt houses
  for (let i = 0; i < 6; i++) { const x = -34 + i * 5.5, z = 18 + (i %% 2) * 6;
    for (const [dx, dz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) add(new THREE.CylinderGeometry(0.15, 0.15, 2.4, 8), mat(0x6b4a2b), x + dx, 1.2, z + dz);
    add(new THREE.BoxGeometry(3.4, 2, 3.4), mat(0xc77a4a), x, 3.4, z);
    add(new THREE.ConeGeometry(2.9, 1.6, 4), mat(0xb53b2a), x, 5.2, z).rotation.y = Math.PI / 4; }
  // people
  for (let i = 0; i < 24; i++) { const x = -12 + (i %% 8) * 3, z = 9 + Math.floor(i / 8) * 2;
    add(new THREE.CapsuleGeometry(0.28, 0.9, 4, 8), mat([0x2f6fd6, 0xee8631, 0xf4f4f2, 0x38844c][i %% 4]), x, 0.8, z);
    add(new THREE.SphereGeometry(0.26, 12, 10), mat(0xf0c8a0), x, 1.78, z); }
  // palms
  for (let i = 0; i < 10; i++) { const x = 20 + i * 4, z = 10 + (i %% 3) * 4;
    const trunk = add(new THREE.CylinderGeometry(0.2, 0.3, 7, 8), mat(0x7a5a3a), x, 3.5, z);
    for (let k = 0; k < 6; k++) { const leaf = add(new THREE.ConeGeometry(0.5, 3.4, 4), mat(0x3f8a4a), x + Math.cos(k) * 1.4, 7, z + Math.sin(k) * 1.4); leaf.rotation.set(Math.sin(k) * 1.2, 0, Math.cos(k) * 1.2); } }
  const cam = new THREE.PerspectiveCamera(55, W / H, 0.1, 300);
  const move = [   // [from, to, look-at] per shot: dolly in, orbit, crane up, side pan, low angle
    (u) => [[-30 + 20 * u, 4, 36 - 10 * u], [0, 6, 0]],
    (u) => [[Math.sin(u * 1.6) * 38, 9, Math.cos(u * 1.6) * 38], [0, 8, 0]],
    (u) => [[-6, 2 + 30 * u, 30 - 6 * u], [0, 6 + 4 * u, 0]],
    (u) => [[-40 + 80 * u, 5, 34], [0, 6, 0]],
    (u) => [[8 - 4 * u, 0.8, 22 - 8 * u], [0, 9, 0]],
  ];
  return {
    frame(shot, k, n) {
      const u = k / Math.max(1, n - 1), [p, l] = move[shot](u);
      cam.position.set(...p); cam.lookAt(...l);
      const a = wgeo.attributes.position.array, t = (shot * n + k) * 0.08, level = shot === 3 ? 4 * u : 0;
      for (let i = 0; i < a.length; i += 3) a[i + 1] = wbase[i + 1] + level + 0.25 * Math.sin(wbase[i] * 0.3 + t) * Math.cos(wbase[i + 2] * 0.25 + t);
      wgeo.attributes.position.needsUpdate = true; wgeo.computeVertexNormals();
      renderer.render(scene, cam);
      return renderer.domElement.toDataURL('image/jpeg', 0.9);
    },
    info() { return {meshes: scene.children.length, triangles: renderer.info.render.triangles, calls: renderer.info.render.calls}; },
  };
})();
"""


def _stats(ms):
    ms = sorted(ms)
    return {"first_s": round(ms[0] / 1000, 3), "median_s": round(ms[len(ms) // 2] / 1000, 3),
            "mean_s": round(sum(ms) / len(ms) / 1000, 3), "max_s": round(ms[-1] / 1000, 3)}


def _download_three(url: str, cache: Path, log) -> str:
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    log(f"Downloading three.js once from {url} ...")
    with urllib.request.urlopen(url, timeout=30) as r:        # noqa: S310 - pinned https URL, cached afterwards
        text = r.read().decode("utf-8")
    if "REVISION" not in text:
        raise ValueError("the downloaded file does not look like three.js")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def run(args, log=print) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("Playwright is not installed. Run:  pip install playwright && playwright install chromium")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"size": [args.width, args.height], "frames_per_shot": args.frames}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        page.set_content("<!doctype html><html><body style='margin:0'></body></html>")
        probe = page.evaluate(PROBE_JS)
        if not probe.get("ok"):
            browser.close()
            raise SystemExit("Chromium could not create a WebGL context here, so Scene 3D cannot run on this computer.")
        probe["software"] = is_software_renderer(probe.get("renderer", ""))
        report["gpu"] = probe
        log(f"WebGL{'2' if probe['webgl2'] else '1'} renderer: {probe['renderer']} -> "
            + ("SOFTWARE rendering (no GPU in use): expect slow frames" if probe["software"] else "hardware GPU"))

        raw = page.evaluate(RAW_JS, [args.width, args.height, args.frames])
        if not raw or raw.get("error"):
            report["raw_error"] = (raw or {}).get("error", "no WebGL2")
            log(f"Plain WebGL test failed: {report['raw_error']}")
        else:
            report["plain_webgl"] = _stats(raw["frameMs"])
            log(f"Plain WebGL (400 lit boxes + JPEG grab): median {report['plain_webgl']['median_s']} s/frame")

        if not args.no_three:
            try:
                three = _download_three(args.three_url, out / "three-0.149.0.min.js", log)
            except Exception as err:   # noqa: BLE001
                report["three_error"] = f"{type(err).__name__}: {err}"
                log(f"Skipping the three.js test: {report['three_error']}")
                three = None
            if three:
                page.evaluate("() => { document.body.innerHTML = ''; }")
                page.add_script_tag(content=three)
                page.evaluate("() => { window.__err = null; window.addEventListener('error', e => window.__err = String(e.message)); }")
                page.add_script_tag(content=THREE_SCENE_JS % {"w": args.width, "h": args.height})
                shots = []
                for s in range(args.shots):
                    ms = []
                    for k in range(args.frames):
                        t0 = time.perf_counter()
                        url = page.evaluate("([s, k, n]) => window.spike.frame(s, k, n)", [s, k, args.frames])
                        data = base64.b64decode(url.split(",", 1)[1])        # the frame as the pipeline would receive it
                        ms.append((time.perf_counter() - t0) * 1000)
                        if k == 0:
                            (out / f"shot{s + 1}_first.jpg").write_bytes(data)
                    st = _stats(ms[1:] or ms)                                # the first frame pays one-time shader compile
                    st["first_frame_s"] = round(ms[0] / 1000, 3)
                    shots.append(st)
                    log(f"  shot {s + 1}: median {st['median_s']} s/frame (first frame {st['first_frame_s']} s)")
                report["three_shots"] = shots
                report["three_info"] = page.evaluate("() => window.spike.info()")
                per = sum(x["median_s"] for x in shots) / len(shots)
                report["three_median_s_per_frame"] = round(per, 3)
                report["estimate_minutes_82s_video"] = round(estimate_minutes(per), 1)
                report["verdict"] = verdict(per)
        browser.close()
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv=None) -> int:
    args = parse_args(argv)
    rep = run(args)
    print()
    if "verdict" in rep:
        print(rep["verdict"])
        print(f"Scene: {rep['three_info']['meshes']} objects. First frame of each shot and report.json are in {args.out}/")
    else:
        print("Only the GPU probe and the plain WebGL test ran" + (" (three.js was not available)." if "three_error" in rep else "."))
        if "plain_webgl" in rep:
            print(f"Plain scene estimate for 82 s: about {estimate_minutes(rep['plain_webgl']['median_s']):.0f} min "
                  "(a lower bound: a real scene is slower).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
