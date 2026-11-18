"""Prompts for Scene 3D. The API text below must match v3_core.js (a test checks every `v.name`)."""

EXAMPLE_BEAT = """function beat_01(v) {
  v.sky('sky', 'cream'); v.fog('cream', 60, 220); v.sun({ dir: [30, 50, 20] });
  v.ground({ color: 'sand' });
  v.water({ x: 0, z: -70, size: [220, 90] });
  v.pilotiBuilding({ floors: 3, w: 24, d: 14, trim: 'blue' });
  for (let i = 0; i < 5; i++) {
    const p = v.person({ x: -7 + i * 3.5, z: 13, ry: Math.PI });
    p.anim((t) => ({ y: Math.abs(Math.sin(t * 6 + i)) * 0.12 }));
    p.armR.anim((t) => ({ rz: 2.4 + Math.sin(t * 8 + i) * 0.5 }));
  }
  v.palm({ x: -17, z: 6 }); v.palm({ x: 17, z: 8, s: 1.2 });
  v.sign('ZONA EVAKUASI', { x: 0, y: 3, z: 19, w: 7, color: 'orange' });
  v.cam.dolly({ from: [-28, 7, 42], to: [-10, 9, 30], look: [0, 9, 0], fov: 50 });
}"""

V3_API_DOC = """## The `v` API (3D scene description; units are metres, y is up, +z points toward the camera's default side)
Colours: a palette name (cream, sky, sand, concrete, stone, blue, blue_soft, orange, red, white, dark, green, brown, wood, roof, skin, water, mud, yellow) or "#rrggbb".
Every shape takes an options object. Common options: x, y, z (position), rx, ry, rz (rotation in radians), sx, sy, sz or s (scale), color, opacity (0-1), visible.
Shapes (each returns a node):
  v.box(w, h, d, o)        v.cylinder(rTop, rBottom, h, o)     v.sphere(r, o)      v.cone(r, h, o)
  v.capsule(r, len, o)     v.plane(w, d, o) (flat floor)        v.group(o)          v.label(text, { size, fg, bg, ...o }) (text of 40 characters or fewer)
Nodes: node.add(child, ...) nests objects (returns the node). node.anim((t, p) => ({ y, ry, sx, opacity, ... })) moves it; t is seconds since the beat started, p is progress 0..1. Return only the fields that change; they replace the base value. The function must be pure: the same t gives the same result.
Scene: v.sky(top, bottom)   v.fog(color, near, far)   v.sun({ dir: [x,y,z], intensity })
Camera (REQUIRED, call exactly one): 
  v.cam.dolly({ from: [x,y,z], to: [x,y,z], look: [x,y,z] or { from, to }, fov })   straight move, eased
  v.cam.orbit({ center: [x,y,z], radius, height, from: degrees, to: degrees, look, fov })   circle around a point
  v.cam.still({ pos, look, fov })
  v.camera((t, p) => ({ pos: [x,y,z], look: [x,y,z], fov }))   free-form
Ready-made pieces (use them, then add your own detail):
  v.ground({ color, w, d })   v.water({ x, z, size: [w, d], y, color, opacity })
  v.person({ x, y, z, ry, s, color, skin })   has .armL and .armR groups you can animate with rz
  v.palm({ x, z, s })   v.stiltHouse({ x, z, ry, s, color })   v.pilotiBuilding({ floors, w, d, floorH, columns, trim, x, z, ry })   (open ground floor, columns only)
  v.sign(text, { x, y, z, ry, w, h, color, fg, size })
Helpers: v.dur (beat length in seconds), v.lerp(a,b,u), v.clamp(x,a,b), v.ease(p), v.step(t,t0,t1), v.wave(t,freq,amp), v.palette.
"""

_RULES = """## Hard rules (the code is run in a sandbox and rejected if it breaks them)
- Plain JavaScript only. No `while`, `do`, `async`, `await`, `import`, `require`, `eval`, `Function`, `Date`, `Math.random`, timers, `window`, `document`, `fetch`. Use `for` loops with fixed counts (use sin/cos for variety, never random).
- Each beat is ONE self-contained function `function beat_NN(v) {...}`. Do not rely on other functions or on variables outside it.
- Every beat must set a camera, and it must MOVE (dolly, orbit or a free-form function). Static cameras look dead.
- Use at least 8 objects per beat. A beat lasts 3.5-6.5 seconds, so choreograph it: things appear one after another (use v.step(t, t0, t1) for scale or opacity from 0 to 1 at staggered times), move along paths, rotate, pulse; the story of the beat happens in time. Static scenery plus a moving camera is not enough.
- Never build sea or ground from many flat boxes: use v.water(...) and v.ground(...). Keep every object small enough that it never fills the frame (a typical object is under 10 m; the camera is 15-45 m away).
- Stay inside the video's world (given below): same place, same palette, same light as the neighbouring beats. Show what the narration of the beat says.
- The scene must be in front of the camera: at least two non-ground objects visible. The video is portrait 9:16, so frame the subject in the upper two thirds; the bottom third is reserved for captions.
- At most 600 objects and 60000 triangles per beat; a label has at most 40 characters.
- Output ONLY code (no prose). No markdown fences needed."""

EXAMPLE_CHOREOGRAPHY = """  // sequence inside a 5 s beat: the sun appears, a ray travels down to the sea, the water glows where it lands
  const sun = v.sphere(3, { x: -14, y: 26, z: -30, color: 'yellow' });
  sun.anim((t) => ({ sx: 1 + 0.25 * v.step(t, 0.2, 1.0), sy: 1 + 0.25 * v.step(t, 0.2, 1.0), sz: 1 + 0.25 * v.step(t, 0.2, 1.0) }));
  const ray = v.cylinder(0.6, 0.6, 30, { x: -7, y: 10, z: -30, rz: 0.5, color: 'yellow', opacity: 0 });
  ray.anim((t) => ({ opacity: 0.55 * v.step(t, 1.0, 1.8), sy: 0.2 + 0.8 * v.step(t, 1.0, 2.4) }));
  const glow = v.sphere(5, { x: 0, y: 0.2, z: -30, sy: 0.05, color: 'white', opacity: 0 });
  glow.anim((t) => ({ opacity: 0.6 * v.step(t, 2.2, 3.0) }));"""

PASS1_SCENE3D_PROMPT = """You are the creative director of a short 3D explainer video (portrait 9:16). The viewer must always understand WHAT is being explained, so the pictures stay inside one world and follow the narration literally.
Plan the shots for the window {win_start:.0f}s-{win_end:.0f}s of a {total_dur}s video: {beats_lo} to {beats_hi} shots. {window_note}
Return JSON only: {{"world": "one paragraph", "scenes": [ {{"start": seconds, "summary": "what this shot explains", "visual_blueprint": "...", "transition": "dissolve"}} ]}}
Rules:
- `world`: ONE consistent setting for the whole video (place, time of day, colour mood, the 3-5 recurring objects such as the sun, the sea, a pier). Every shot happens in this world. {world_note}
- Each shot lasts 3.5 to 6.5 seconds. A shot is one idea told with ANIMATION, not a slideshow: objects enter, move and change during the shot while the camera glides. Never plan a shot that is just one static object.
- Start a shot where a sentence or a phrase of the narration starts (use `word_timestamps`), so the picture changes only when the idea changes.
- Show what the narration says, literally. If it says sunlight enters the sea, show the sun, rays and the sea surface, not a glass of water or an unrelated prop. If it needs a metaphor, keep it inside the same world and use it for the whole shot.
- `visual_blueprint` (2-4 sentences) lists the objects and where they are, the sequence of events in time ("first ... then ... finally ..."), and the camera move (dolly in, orbit, crane up, pan along). Keep it buildable from simple shapes (boxes, cylinders, spheres, cones, plus the sea, sky, people, palms and buildings that the toolkit has).
- At most ONE short label (max 3 words) per shot, for a key term. Spoken sentences appear as captions at the bottom, so never repeat them in the scene.
- Camera and angle should differ between neighbouring shots, but the world, palette and light stay the same."""

PASS2_SCENE3D_PROMPT = """You are a 3D scene programmer. Write JavaScript functions `@FIRST@(v)` through `@LAST@(v)`, one per beat, that build each beat's 3D scene with the `v` API below.

""" + V3_API_DOC + "\n" + _RULES + """

## Example of one good beat
""" + EXAMPLE_BEAT + """

## Example of choreography (animation over time inside one beat)
""" + EXAMPLE_CHOREOGRAPHY + """

Write all requested functions, in order, nothing else."""

REPAIR_SCENE3D_PROMPT = """You are a 3D scene programmer fixing one function. The code below failed the sandbox check. Return the COMPLETE corrected function `@NAME@(v)` and nothing else.

""" + V3_API_DOC + "\n" + _RULES + """

Keep the same visual idea; change only what is needed to fix the listed problems."""


def pass2_prompt(first_fn: str, last_fn: str) -> str:
    return PASS2_SCENE3D_PROMPT.replace("@FIRST@", first_fn).replace("@LAST@", last_fn)


def repair_prompt(name: str) -> str:
    return REPAIR_SCENE3D_PROMPT.replace("@NAME@", name)
