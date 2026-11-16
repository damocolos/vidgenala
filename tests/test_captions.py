"""Phrase captions: chunking, script alignment, drawing, and that nothing changes when they are off."""
import numpy as np
from PIL import Image

from nalarka.core import captions as cap
from nalarka.core.frames import render_frame


def W(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_chunks_are_two_to_four_words_and_split_at_pauses_and_punctuation():
    words = [W("Pas", 1.0, 1.2), W("tsunami", 1.2, 1.6), W("dateng,", 1.6, 2.0), W("jadi", 2.05, 2.3), W("kamu", 2.3, 2.5),
             W("bawa", 3.0, 3.2), W("perahu", 3.2, 3.6), W("ya", 3.6, 3.7), W("sayang", 3.7, 4.0), W("kalau", 4.0, 4.2)]
    cues = cap.chunk_words(words)
    assert [c["text"] for c in cues] == ["Pas tsunami dateng,", "jadi kamu", "bawa perahu ya sayang", "kalau"]
    assert all(len(c["text"].split()) <= cap.MAX_WORDS for c in cues)


def test_long_words_never_make_a_line_wider_than_the_limit():
    words = [W("pembangunan", 1, 1.3), W("infrastruktur", 1.3, 1.7), W("berkelanjutan", 1.7, 2.1)]
    assert all(len(c["text"]) <= cap.MAX_CHARS for c in cap.chunk_words(words))


def test_cues_do_not_overlap_and_tail_is_short():
    words = [W("a", 1.0, 1.2), W("b", 1.2, 1.4), W("c", 1.5, 1.7), W("d", 1.7, 1.9)]
    cues = cap.chunk_words(words, max_words=2)
    for a, b in zip(cues, cues[1:]):
        assert a["end"] <= b["start"] + 1e-9
    assert cues[-1]["end"] == round(1.9 + cap.TAIL, 3)


def test_script_wording_wins_over_a_misheard_word_and_keeps_timing():
    script = "Pas tsunami datang, jadi kamu ganti beton."
    heard = [W("pas", 1.0, 1.2), W("tsunami", 1.2, 1.7), W("dateng", 1.7, 2.0), W("jadi", 2.1, 2.3), W("kamu", 2.3, 2.5),
             W("ganti", 2.5, 2.8), W("betong", 2.8, 3.2)]
    cues, rep = cap.build_cues(script, heard)
    text = " ".join(c["text"] for c in cues)
    assert "datang," in text and "beton." in text and "dateng" not in text and "betong" not in text
    assert rep["source"] == "script" and rep["matched"] >= 0.7
    assert cues[0]["start"] == 1.0                                   # still the time Whisper heard the first word


def test_script_word_whisper_missed_is_timed_between_its_neighbours():
    timed, ratio = cap.align_script_to_words("satu dua tiga empat", [W("satu", 1.0, 1.4), W("tiga", 2.0, 2.4), W("empat", 2.4, 2.8)])
    by = {w["word"]: w for w in timed}
    assert 1.4 <= by["dua"]["start"] < by["dua"]["end"] <= 2.0 and ratio == 0.75


def test_falls_back_to_whisper_words_when_script_does_not_match():
    heard = [W("halo", 1, 1.3), W("semua", 1.3, 1.7)]
    cues, rep = cap.build_cues("sama sekali berbeda teks naskah panjang", heard)
    assert rep["source"] == "whisper" and cues[0]["text"] == "halo semua"
    cues2, rep2 = cap.build_cues("", heard)
    assert rep2["source"] == "whisper"
    assert cap.build_cues("x", [])[0] == []


def test_active_cue_is_half_open():
    cues = [{"start": 1.0, "end": 2.0, "text": "a"}, {"start": 2.0, "end": 3.0, "text": "b"}]
    assert cap.active_cue(cues, 0.99) is None and cap.active_cue(cues, 1.0)["text"] == "a"
    assert cap.active_cue(cues, 2.0)["text"] == "b" and cap.active_cue(cues, 3.0) is None


def test_caption_is_drawn_white_in_the_lower_third_and_nowhere_else():
    base = Image.new("RGB", (540, 960), (30, 80, 160))
    cues = [{"start": 1.0, "end": 2.0, "text": "pas tsunami dateng"}]
    out = cap.apply_caption(base, 1.5, cues)
    diff = np.abs(np.asarray(out, dtype=int) - np.asarray(base, dtype=int)).sum(axis=2)
    ys, xs = np.nonzero(diff)
    assert len(ys) > 200                                              # something was drawn
    assert 0.55 * 960 < ys.min() and ys.max() < 0.75 * 960             # around 65% of the height
    assert xs.min() > 0.05 * 540 and xs.max() < 0.95 * 540             # inside the side margins
    assert (np.asarray(out)[diff > 0].max(axis=0) >= 250).all()        # there is pure white text
    assert out.mode == base.mode and out.size == base.size
    assert cap.apply_caption(base, 5.0, cues) is base                   # no cue -> the very same image


def test_wide_phrase_wraps_to_two_lines_then_shrinks_and_stays_inside_the_screen():
    one = cap._caption_layer("pas tsunami", 1080, 1920)
    two = cap._caption_layer("pembangunan infrastruktur kuat", 1080, 1920)
    assert two.width <= 1080 and two.height > one.height * 1.25                    # wrapped, not shrunk to a sliver
    assert cap._caption_layer("WWWWWWWWWWWWWWWWWWWWWWWW", 300, 600).width <= 300


def test_render_frame_without_captions_is_identical_and_with_captions_only_adds_text(monkeypatch):
    plan = {"palette": {}, "scenes": [{"start": 0.0, "end": 5.0, "transition": "cut", "bg_color": "cream"}]}
    ns = {}

    import nalarka.core.frames as frames
    monkeypatch.setattr(frames, "render_single_beat_canvas", lambda t, beat, idx, w, h, pal, words, ns_: (Image.new("RGB", (w, h), (240, 230, 210)), None, None))
    off = np.asarray(render_frame(1.5, 270, 480, plan, [], ns)).copy()
    plan_empty = {**plan, "captions": []}
    assert np.array_equal(off, np.asarray(render_frame(1.5, 270, 480, plan_empty, [], ns)))      # empty list = off
    plan_on = {**plan, "captions": [{"start": 1.0, "end": 2.0, "text": "halo"}]}
    on = np.asarray(render_frame(1.5, 270, 480, plan_on, [], ns))
    assert not np.array_equal(off, on)
    assert np.array_equal(off, np.asarray(render_frame(2.5, 270, 480, plan_on, [], ns)))          # outside the cue: untouched
