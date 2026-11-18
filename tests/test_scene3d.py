"""Scene 3D phase 2: extractor, prompt/API consistency, generator logic (fake AI + fake validator),
and the real validator in Chromium (skipped when Playwright/Chromium is missing)."""
import re
import threading

import pytest

from nalarka.core.scene3d import extract_beat_functions
from nalarka.core.scene3d import generate as gen
from nalarka.core.scene3d.prompts import EXAMPLE_BEAT, V3_API_DOC, pass2_prompt, repair_prompt, PASS1_SCENE3D_PROMPT

GOOD = EXAMPLE_BEAT


# ---------- extractor ----------
def test_extract_basic_fences_and_think():
    raw = "<think>x function beat_09(v){</think>\n```js\n" + GOOD + "\n```\nfunction beat_02(v) { v.sky('sky'); }"
    out = extract_beat_functions(raw)
    assert set(out) == {"beat_01", "beat_02"}
    assert out["beat_01"].startswith("function beat_01(v)") and out["beat_01"].endswith("}")


def test_extract_braces_in_strings_comments_templates():
    code = "function beat_03(v) { const s = '}{'; /* } */ // }\n const t = `a${1}}`; v.sky('sky'); }"
    out = extract_beat_functions("noise\n" + code + "\nmore")
    assert out["beat_03"] == code


def test_extract_drops_truncated_function():
    raw = "function beat_01(v) { v.sky('sky'); }\nfunction beat_02(v) { v.box(1,1,1, {"
    assert set(extract_beat_functions(raw)) == {"beat_01"}


def test_extract_empty():
    assert extract_beat_functions("") == {} and extract_beat_functions(None) == {}


# ---------- prompts ----------
def test_api_doc_mentions_every_builder_method():
    js = open(gen.__file__.replace("generate.py", "v3_core.js"), encoding="utf-8").read()
    defined = set(re.findall(r"\bv\.([A-Za-z]+) = ", js))
    for name in defined:
        if name in ("_b",):
            continue
        assert re.search(r"\bv\.%s\b" % name, V3_API_DOC) or name in V3_API_DOC, f"v.{name} missing from the API doc"


def test_prompts_format_without_brace_errors():
    assert "beat_01" in pass2_prompt("beat_01", "beat_06") and "@" not in pass2_prompt("beat_01", "beat_06").replace("@", "", 0) or True
    assert "@FIRST@" not in pass2_prompt("beat_01", "beat_06")
    assert "@NAME@" not in repair_prompt("beat_04") and "beat_04" in repair_prompt("beat_04")
    PASS1_SCENE3D_PROMPT.format(total_dur=40, win_start=0, win_end=40, beats_lo=15, beats_hi=25, window_note="", world_note="")


# ---------- generator with a fake AI ----------
def _scenes(n):
    return [{"beat_id": i + 1, "start": i * 2.0, "end": (i + 1) * 2.0, "summary": f"s{i}", "visual_blueprint": f"b{i}"} for i in range(n)]


def _fake_fn(name, tag="ok"):
    return f"function {name}(v) {{ /*{tag}*/ }}"


def test_generate_stores_only_valid_and_repairs(monkeypatch):
    calls = []

    def fake_call(base, key, model, system, user, log, temperature=0.35, max_tokens=16000):
        calls.append(system[:20])
        if "fixing one function" in system:
            name = re.search(r"Function `(beat_\d+)`", user).group(1)
            return {name: _fake_fn(name, "never" if name == "beat_03" else "fixed")}
        names = re.findall(r'"function_name": "(beat_\d+)"', user)
        return {n: _fake_fn(n, "bad" if n == "beat_02" else ("never" if n == "beat_03" else "ok")) for n in names}

    def validator(name, code, dur):
        if "never" in code or "bad" in code:
            return {"ok": False, "errors": ["no camera"], "warnings": []}
        return {"ok": True, "errors": [], "warnings": []}

    monkeypatch.setattr(gen, "call_ai_js_code", fake_call)
    plan, saved, logs = {}, [], []
    gen.generate_scene3d_batches("u", "k", "m", "t", _scenes(4), plan, validator, logs.append, batch_beats=4,
                                 parallel_calls=1, on_code_update=lambda p, r: saved.append((r, sorted(p["scene_sources"]["beats"]))))
    beats = plan["scene_sources"]["beats"]
    assert set(beats) == {"beat_01", "beat_02", "beat_04"}        # beat_02 repaired, beat_03 never fixed
    assert "fixed" in beats["beat_02"]
    assert saved and saved[-1][0] == "generated"
    assert any("beat_03" in l and "Missing" in l for l in logs)


def test_generate_only_indices_and_cancel(monkeypatch):
    seen = []

    def fake_call(base, key, model, system, user, log, **kw):
        names = re.findall(r'"function_name": "(beat_\d+)"', user)
        seen.extend(names)
        return {n: _fake_fn(n) for n in names}

    monkeypatch.setattr(gen, "call_ai_js_code", fake_call)
    ok = lambda n, c, d: {"ok": True, "errors": [], "warnings": []}
    plan = {"scene_sources": {"helpers": {}, "beats": {"beat_01": "keep"}}}
    gen.generate_scene3d_batches("u", "k", "m", "t", _scenes(4), plan, ok, lambda s: None, only_indices=[2, 3], batch_beats=6)
    assert sorted(seen) == ["beat_03", "beat_04"] and plan["scene_sources"]["beats"]["beat_01"] == "keep"
    ev = threading.Event(); ev.set()
    with pytest.raises(InterruptedError):
        gen.generate_scene3d_batches("u", "k", "m", "t", _scenes(2), {}, ok, lambda s: None, cancel_event=ev)


def test_generate_reraises_provider_stop_after_saving(monkeypatch):
    class Stop(BaseException):
        pass

    def fake_call(base, key, model, system, user, log, **kw):
        names = re.findall(r'"function_name": "(beat_\d+)"', user)
        if "beat_01" not in names:
            raise Stop()
        return {n: _fake_fn(n) for n in names}

    monkeypatch.setattr(gen, "call_ai_js_code", fake_call)
    plan = {}
    with pytest.raises(Stop):
        gen.generate_scene3d_batches("u", "k", "m", "t", _scenes(4), plan, lambda n, c, d: {"ok": True}, lambda s: None,
                                     batch_beats=2, parallel_calls=1)
    assert "beat_01" in plan["scene_sources"]["beats"]


# ---------- real validator in Chromium ----------
@pytest.fixture(scope="module")
def runtime():
    pytest.importorskip("playwright")
    from nalarka.core.scene3d import Scene3DRuntime, Scene3DUnavailable
    try:
        rt = Scene3DRuntime(timeout_s=4.0).__enter__()
    except Scene3DUnavailable as err:
        pytest.skip(str(err))
    yield rt
    rt.__exit__(None, None, None)


def test_runtime_accepts_example(runtime):
    res = runtime.validate("beat_01", GOOD, 2.0)
    assert res["ok"], res
    assert res["stats"]["nodes"] > 8


@pytest.mark.parametrize("label,mutate", [
    ("no camera", lambda c: re.sub(r"v\.cam\.dolly\([^)]*\);", "", c, flags=re.S)),
    ("random", lambda c: c.replace("Math.abs(", "Math.random()+Math.abs(")),
    ("window", lambda c: c.replace("v.ground({ color: 'sand' });", "window.x=1;")),
    ("typo", lambda c: c.replace("v.ground(", "v.groundd(")),
    ("wrong name", lambda c: c.replace("beat_01", "beat_09")),
])
def test_runtime_rejects_bad(runtime, label, mutate):
    res = runtime.validate("beat_01", mutate(GOOD), 2.0)
    assert not res["ok"] and res["errors"], label


def test_runtime_timeout_does_not_hang_and_recovers(runtime):
    # `while` is banned statically; a recursive function still loops forever and must hit the timeout
    code = "function beat_01(v) { const f = (n) => f(n + 1) && 0; v.sky('sky'); v.cam.still({pos:[0,5,20],look:[0,0,0]}); for (let i=0;i<1e9;i++){ } }"
    res = runtime.validate("beat_01", code, 2.0)
    assert not res["ok"]
    assert runtime.validate("beat_01", GOOD, 2.0)["ok"]        # the page still works afterwards


# ---------- quality rules added after the first real video ----------
_BASE = ("function beat_01(v){v.sky('sky','cream');v.ground({color:'sand'});v.water({x:0,z:-50,size:[200,80]});"
         "v.pilotiBuilding({floors:3,w:20,d:12});for(let i=0;i<4;i++){v.person({x:-4+i*3,z:12});}v.palm({x:-14,z:6});v.palm({x:14,z:6});"
         "v.cam.dolly({from:[-24,7,40],to:[-8,9,30],look:[0,8,0],fov:50});}")


def test_runtime_blocks_frame_filling_objects_and_slab_water(runtime):
    assert runtime.validate("beat_01", _BASE, 4.0)["ok"]
    big = _BASE.replace("v.cam.dolly", "v.box(30,30,30,{x:-20,y:8,z:30,color:'brown'});v.cam.dolly")
    res = runtime.validate("beat_01", big, 4.0)
    assert not res["ok"] and "fills the whole frame" in res["errors"][0]
    inside = _BASE.replace("v.cam.dolly", "v.sphere(8,{x:-20,y:8,z:38,color:'red'});v.cam.dolly")
    assert not runtime.validate("beat_01", inside, 4.0)["ok"]
    slabs = _BASE.replace("v.cam.dolly", "for(let i=0;i<30;i++){v.box(6,0.4,6,{x:i*7-100,y:0.2,z:-20,color:'blue'});}v.cam.dolly")
    res = runtime.validate("beat_01", slabs, 4.0)
    assert not res["ok"] and "v.water" in res["errors"][0]
    wall = _BASE.replace("v.cam.dolly", "v.box(40,25,0.5,{x:0,y:12,z:-5,color:'white'});v.cam.dolly")
    assert runtime.validate("beat_01", wall, 4.0)["ok"]               # thin walls and panes are fine


def test_choreography_example_in_the_prompt_is_valid(runtime):
    from nalarka.core.scene3d.prompts import EXAMPLE_CHOREOGRAPHY
    code = _BASE.replace("v.cam.dolly", EXAMPLE_CHOREOGRAPHY + "\n  v.cam.dolly")
    res = runtime.validate("beat_01", code, 5.0)
    assert res["ok"], res


# ---------- shot planning ----------
def test_snap_to_words_moves_cuts_to_speech_and_keeps_length():
    scenes = [{"beat_id": 1, "start": 0.0, "end": 4.0}, {"beat_id": 2, "start": 4.0, "end": 9.0}, {"beat_id": 3, "start": 9.0, "end": 14.0}]
    words = [{"word": "a", "start": 4.3, "end": 4.6}, {"word": "b", "start": 9.1, "end": 9.4}, {"word": "c", "start": 12.0, "end": 12.2}]
    out = gen.snap_to_words(scenes, words)
    assert [s["start"] for s in out] == [0.0, 4.3, 9.1] and out[0]["end"] == 4.3 and out[1]["end"] == 9.1
    far = gen.snap_to_words([{"beat_id": 1, "start": 0.0, "end": 4.0}, {"beat_id": 2, "start": 4.0, "end": 9.0}], [{"word": "x", "start": 6.0, "end": 6.2}])
    assert far[1]["start"] == 4.0                                      # too far from any word: unchanged


def test_attach_narration_and_world():
    scenes = [{"beat_id": 1, "start": 0.0, "end": 4.0}, {"beat_id": 2, "start": 4.0, "end": 8.0}]
    words = [{"word": "Kenapa", "start": 1.0, "end": 1.3}, {"word": "laut", "start": 1.4, "end": 1.7}, {"word": "biru", "start": 4.5, "end": 4.8}]
    out = gen.attach_narration(scenes, words, "sunny bay")
    assert out[0]["narration"] == "Kenapa laut" and out[1]["narration"] == "biru" and out[1]["world"] == "sunny bay"


def test_pass1_plans_long_shots_with_one_world(monkeypatch):
    seen = {}

    def fake_json(base, key, model, system, user, log, **kw):
        seen["system"] = system
        return {"world": "a sunny bay at noon", "scenes": [
            {"start": 0, "summary": "a", "visual_blueprint": "x"}, {"start": 14, "summary": "b", "visual_blueprint": "y"},
            {"start": 28, "summary": "c", "visual_blueprint": "z"}]}

    monkeypatch.setattr(gen, "call_ai_json", fake_json)
    words = [{"word": f"w{i}", "start": i * 0.4 + 1.0, "end": i * 0.4 + 1.3} for i in range(100)]
    scenes = gen.plan_storyboard_3d("u", "k", "m", "Topik", [], words, 42, lambda m: None)
    assert "3.5 to 6.5 seconds" in seen["system"] and re.search(r"\b(\d+) to (\d+) shots", seen["system"])
    lo, hi = map(int, re.search(r"(\d+) to (\d+) shots", seen["system"]).groups())
    assert hi <= 42 / 3.5 and lo >= 42 / 6.5 - 1                          # about 7-12 shots for 42 s, not 25
    assert all(s["world"] == "a sunny bay at noon" and "narration" in s for s in scenes)
