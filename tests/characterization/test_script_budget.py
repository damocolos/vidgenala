import pytest
import legacy.nalarka_local as leg


def test_script_word_budget():
    lo_60, hi_60 = leg.script_word_budget(60)
    assert 70 <= lo_60 <= 80
    assert 120 <= hi_60 <= 130

    lo_120, hi_120 = leg.script_word_budget(120)
    assert lo_120 > lo_60
    assert hi_120 > hi_60


def test_script_section_count():
    assert leg.script_section_count(60) == 3
    assert leg.script_section_count(90) >= 4
    assert leg.script_section_count(120) == 6
    assert leg.script_section_count(300) <= 12


def test_estimate_video_seconds():
    # 50 words / 2.5 = 20s voiceover + 2.5 = 22.5 -> ceil = 23s
    vo, vid = leg.estimate_video_seconds(50)
    assert vo == 20.0
    assert vid == 23


def test_check_script_fits():
    # 50 words is ~23s video -> fits easily inside 60s
    short_script = "[Hook] " + " ".join(["kata"] * 50)
    est_vid = leg.check_script_fits(short_script, 60, lambda msg: None)
    assert est_vid <= 60

    # 400 words is ~162.5s video -> exceeds 60s * 1.12 = 67.2s -> ValueError
    long_script = "[Hook] " + " ".join(["kata"] * 400)
    with pytest.raises(ValueError) as excinfo:
        leg.check_script_fits(long_script, 60, lambda msg: None)
    assert "over the 60s cap" in str(excinfo.value)


def test_split_text_for_tts():
    # Short text returns single chunk
    short_text = "Halo ini naskah singkat.\n\nParagraf kedua."
    chunks = leg.split_text_for_tts(short_text, max_chars=100)
    assert len(chunks) == 1 or len(chunks) == 2
    assert "Halo ini naskah singkat." in "".join(chunks)

    # Long text splits on max_chars
    long_para = " ".join(["kata"] * 60)  # ~300 chars
    split_chunks = leg.split_text_for_tts(long_para, max_chars=80)
    assert len(split_chunks) > 1
    assert all(len(c) <= 80 for c in split_chunks)


def test_compress_words():
    raw_words = [
        {"word": f"w_{i}", "start": float(i), "end": float(i + 0.8)}
        for i in range(12)
    ]
    grouped = leg.compress_words(raw_words, group=4)
    assert len(grouped) == 3
    assert grouped[0]["start"] == 0.0
    assert grouped[0]["end"] == 3.8
    assert "w_0 w_1 w_2 w_3" == grouped[0]["text"]
