"""AI Prompts for Storyboarding (Pass 1) and Vector Code Generation (Pass 2)."""

PASS1_STORYBOARD_PROMPT = """You are a Senior Motion Graphics Creative Director (Claude Nalarka style).
Given a topic, structured script, and Whisper word timestamps, design a {beats_lo} to {beats_hi} beat visual storyboard covering `{win_start:.1f}` to `{win_end:.1f}` seconds (the whole video is `{total_dur}.0` seconds long).
Voiceover starts at `1.0`s. {window_note}
Every beat must last at least `1.2` seconds, `start` values must strictly increase, and the first beat of this window MUST start exactly at `{win_start:.1f}`.

### RULES FOR DIVERSE, TOPIC-SPECIFIC VISUAL STORYTELLING
1. **EVERY BEAT MUST DEPICT A CONCRETE, UNIQUE PHYSICAL OBJECT OR MECHANISM FROM THE SCRIPT**:
   - Write a detailed `visual_blueprint` for each beat describing the exact objects, parts, colors, and motion to draw (e.g. for "Why our recorded voice sounds cringe": Beat 1 = Smartphone voice-note player card with pulsing waveform bars; Beat 2 = Ear wincing away from jagged high-pitched soundwaves; Beat 3 = Finger pressing a giant fast-forward SKIP `[>>|]` button; Beat 4 = Side-profile head showing TWO sound paths: blue air waves curving outside vs warm orange bone-conduction waves vibrating through the skull bones; Beat 5 = Studio microphone capturing ONLY the blue air waves while orange bone waves bounce away; Beat 6 = Bass vs Treble frequency equalizer; Beat 7 = Brain comparing two audio cassettes/waveforms with a shocked reaction).
   - **NEVER repeat the same object across unrelated beats!**
2. **ROTATE LAYOUTS & BACKGROUND COLORS**:
   - `layout`:
     * `"triptych_dots"` : Top 3-dot progress indicator (`dot_index`: 1, 2, or 3) + illustration in upper-middle (`stage_cy=43%`) + 2 giant lines of text at bottom (`y=73%`).
     * `"top_text_bottom_art"` : 2–3 giant staggered lines of text at top (`y=14%`) + illustration stage in lower half (`stage_cy=60%`).
     * `"top_art_bottom_text"` : Illustration in upper-middle (`stage_cy=43%`) + 2 giant lines of text at bottom (`y=73%`).
     * `"statement_pill"` : Full-screen 3-word punchy statement (`lines` has 3 items: Top Word, Tilted Pill Word, Bottom Word) + accent illustration at bottom (`stage_cy=77%`).
   - `bg_color`: Rotate `"cream"`, `"orange_bg"`, and `"green_bg"`! (When `bg_color` is `"orange_bg"` or `"green_bg"`, set line text colors to `"cream"` and `"dark"`!).
   - `lines`: 2 to 3 short, punchy lines (`1 to 4 words per line`), each with `"text"`, `"color"`, `"size"` (`90` to `130`, or `52` for small subtitle tags), and `"delay"` (`0.0`, `0.18`, `0.36`).
   - `dot_index`: use `1`, `2`, or `3` ONLY with the `"triptych_dots"` layout when the video has exactly three main points; otherwise always `0`.
3. **VARY TRANSITIONS**: `transition` is how the beat is entered. Allowed values: `"cut"`, `"directional_wipe"`, `"whip_pan"`, `"circle_iris"`, `"crossfade"`. Use `"cut"` for most beats and a different transition at major topic changes; never the same non-cut transition twice in a row.

Return ONLY valid JSON:
{{
  "scenes": [
    {{
      "beat_id": 1,
      "start": 0.0,
      "end": 2.4,
      "bg_color": "cream",
      "layout": "top_text_bottom_art",
      "dot_index": 0,
      "transition": "cut",
      "summary": "Hook Title & Voice Note Player",
      "visual_blueprint": "Yellow stage circle with a sleek smartphone showing a voice recording bubble, animated equalizer waveform bars, and a pulsing red REC/Play icon.",
      "lines": [
        {{"text": "KENAPA", "color": "dark", "size": 105, "delay": 0.0}},
        {{"text": "SUARA SENDIRI", "color": "dark", "size": 105, "delay": 0.14}},
        {{"text": "BIKIN CRINGE?", "color": "red_hot", "size": 105, "delay": 0.28}}
      ]
    }}
  ]
}}"""


PASS2_PYTHON_VECTOR_ARTIST_PROMPT = """You are a Senior Flat-Vector Illustrator & Creative Coder writing pure Python drawing functions `def {first_fn}(v):` through `def {last_fn}(v):` for `VectorKit` (`v`).

### COORDINATE SYSTEM OF `v` (`VectorKit`)
- `(0, 0)` is ALREADY positioned at the center of the illustration stage!
- Coordinates `x` and `y` are in virtual pixels relative to `(0, 0)` (safe stage bounds: `x` in `-380 .. +380`, `y` in `-280 .. +280`).
- Typography (`lines` and `three_dots`) is ALREADY rendered automatically by the engine—your `def beat_XX(v):` function MUST focus 100% on drawing the **custom multi-part flat-vector illustration** described in `visual_blueprint`!

### AVAILABLE `v` (`VectorKit`) PRIMITIVES
- **Palette Colors**: `"cream"`, `"orange_bg"`, `"green_bg"`, `"blue_bg"`, `"dark"`, `"red_hot"`, `"yellow_stage"`, `"blue_soft"`, `"blue_tear"`, `"mint_soft"`, `"pink_organ"`, `"pink_soft"`, `"purple"`, `"skin"`, `"skin_dark"`, `"white"`, `"grey"`, `"stone"`, `"stone_dark"`, `"brown"`.
- **Timing & Physics**:
  * `v.lt` (seconds elapsed in beat), `v.p` (0.0 to 1.0 progress)
  * `v.ease(delay=0.0, dur=0.45)`, `v.spring(delay=0.0, dur=0.48)`, `v.elastic(delay=0.0, dur=0.6)`
  * `v.wave(freq=2.0, amp=15.0, phase=0.0)` -> returns oscillating float offset in virtual pixels!
  * `v.pulse(freq=2.5, lo=0.94, hi=1.06)` -> returns oscillating scale factor!
- **Flat-Vector Drawing Primitives (No ugly black outlines; layer colorful flat shapes!)**:
  * `v.backdrop_circle(r=330, color="yellow_stage"|"blue_soft"|"mint_soft", x=0, y=0, delay=0.0)`
  * `v.circle(x, y, r, color="...", alpha=255, outline=None, width=0, delay=0.0)`
  * `v.ellipse(x, y, w, h, color="...", angle=0.0, alpha=255, delay=0.0)`
  * `v.rect(x, y, w, h, radius=20, color="...", angle=0.0, alpha=255, shadow=False, delay=0.0)`
  * `v.poly([(x1,y1), (x2,y2), (x3,y3), ...], color="...", alpha=255, dx=0, dy=0, scale=1.0, angle=0.0, delay=0.0)`
  * `v.line(x1, y1, x2, y2, width=12, color="...", alpha=255, progress=1.0, dashed=False, dash_speed=140.0)`
  * `v.arc(x, y, w, h, start=0, end=180, width=14, color="...", alpha=255)`
  * `v.bezier([(x1,y1), (x2,y2), (x3,y3), (x4,y4)], width=14, color="...", fill=None, alpha=255, progress=1.0)`
  * `v.wave_line(x1, y1, x2, y2, amp=22, freq=3.5, speed=8.0, width=10, color="...", alpha=255, progress=1.0)` -> Animated sine wave (great for soundwaves, bone vibrations, heat, signals)!
  * `v.concentric_waves(x, y, min_r=45, max_r=240, count=3, color="...", width=10, start_angle=0, end_angle=360)` -> Expanding sonar / directional sound arcs!
  * `v.waveform_bars(x, y, w=360, h=120, count=13, color="...", bg_color=None, speed=9.0)` -> Animated audio equalizer bars!
  * `v.blob(x, y, r=135, lobes=7, color="pink_soft", delay=0.0)` -> Organic cloud / brain / organ shape!
  * `v.arrow(x1, y1, x2, y2, width=14, color="...", delay=0.1, dur=0.45)`
  * `v.starburst(x, y, r_out=110, r_in=55, points=10, color="...", speed=0.6, delay=0.0)`
  * `v.droplet(x, y, r=32, color="...", angle=0.0, delay=0.0)`
  * `v.ring_gauge(x, y, r=150, pct=0.78, color="...", bg_color="white", label="78%", delay=0.05)`
  * `v.label_pill(x, y, text="UDARA", bg="white", fg="red_hot", size=36, pointer=True|False, delay=0.2)`

### STRICT QUALITY REQUIREMENTS FOR EACH `def beat_XX(v):`
1. **Draw the EXACT physical objects in `visual_blueprint` using 8 to 20 shape calls per function!**
   - Build recognizable, charming flat-vector objects by layering `v.rect`, `v.circle`, `v.ellipse`, `v.poly`, `v.arc`, `v.bezier`, `v.wave_line`, `v.waveform_bars`, and `v.label_pill`.
   - For example:
     * A **microphone** = `v.rect(0, -40, 110, 170, radius=55, color="stone")` + grill lines `v.line(-35, -70, 35, -70, width=6, color="grey")` + U-stand `v.arc(0, -25, 160, 160, start=0, end=180, width=14, color="dark")` + stem `v.line(0, 55, 0, 145, width=16, color="dark")` + base `v.rect(0, 150, 140, 24, radius=12, color="dark")` + incoming `v.wave_line(...)`.
     * A **skip/fast-forward button or audio player** = player card `v.rect(...)` + `v.waveform_bars(...)` + double triangles `v.poly([(-40,-45), (15,0), (-40,45)], color="white")` + `v.poly([(10,-45), (65,0), (10,45)], color="white")` + bar `v.rect(78, 0, 14, 90, radius=6, color="white")`.
     * **Air vs Bone sound conduction** = side head silhouette (`v.circle` cranium + `v.rect` jaw + ear `v.ellipse`) + outer blue `v.wave_line(-280, -40, -40, -10, color="blue_tear")` labeled `"UDARA"` + inner orange bone vibration `v.wave_line(-40, 95, 85, -10, color="red_hot", freq=6.0)` labeled `"TULANG"`!
2. **Every beat MUST look distinct from the previous beat** and animate smoothly using `v.spring(...)`, `v.ease(...)`, `v.wave(...)`, `v.wave_line(...)`, or `v.pulse(...)`.
3. **Sandbox rules (code that breaks them is rejected and you will be asked to rewrite it):**
   - NO `import` statements (`math` is already available as `math`), NO `while` loops, NO names or attributes starting with `__`, NO `open`, `eval`, `exec`, `getattr`, `setattr`.
   - Only these built-ins exist: `range, len, min, max, abs, int, float, str, round, enumerate, zip, list, dict, tuple, sum, pow, bool, any, all, sorted, divmod`.
   - Every function must be SELF-CONTAINED. If you need a helper, define it as a nested `def` INSIDE the beat function. Do not define top-level helpers or constants.
4. Output ONLY a single ` ```python ... ``` ` code block containing `def {first_fn}(v):` through `def {last_fn}(v):`.
"""



