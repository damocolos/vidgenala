import pytest
import legacy.nalarka_local as leg


def test_format_sections_to_tagged_text():
    script_data = {
        "hook": "Kenapa suara sendiri bikin cringe?",
        "inti_1": "Suara merambat lewat udara dan tulang.",
        "inti_2": "Tulang menambah getaran rendah.",
        "penutup": "Jadi suara rekaman adalah suaramu yang asli."
    }
    tagged = leg.format_sections_to_tagged_text(script_data)
    assert "[Hook] Kenapa suara sendiri bikin cringe?" in tagged
    assert "[Inti 1] Suara merambat lewat udara dan tulang." in tagged
    assert "[Inti 2] Tulang menambah getaran rendah." in tagged
    assert "[Penutup] Jadi suara rekaman adalah suaramu yang asli." in tagged


def test_format_sections_fallback_full_text():
    script_data = {"full_text": "Hanya teks biasa tanpa hook"}
    tagged = leg.format_sections_to_tagged_text(script_data)
    assert tagged == "Hanya teks biasa tanpa hook"


def test_parse_tagged_script():
    raw_text = """[Hook] Kenapa suara rekaman aneh?
    
[Inti 1] Ada dua jalan perambatan suara.

[Penutup] Terimalah suaramu apa adanya."""
    clean, sections = leg.parse_tagged_script(raw_text)
    assert len(sections) == 3
    assert sections[0]["tag"] == "Hook"
    assert sections[0]["text"] == "Kenapa suara rekaman aneh?"
    assert sections[1]["tag"] == "Inti 1"
    assert sections[2]["tag"] == "Penutup"
    assert "[Hook]" not in clean
    assert "[Inti 1]" not in clean
    assert "[Penutup]" not in clean


def test_parse_tagged_script_untagged_fallback():
    raw_text = "Paragraf satu tanpa tag sama sekali.\n\nParagraf dua."
    clean, sections = leg.parse_tagged_script(raw_text)
    assert len(sections) == 2
    assert sections[0]["tag"] == "Narasi"
    assert sections[0]["text"] == "Paragraf satu tanpa tag sama sekali."
    assert sections[1]["tag"] == "Narasi"
    assert sections[1]["text"] == "Paragraf dua."


def test_prepare_text_for_elevenlabs():
    raw_text = "[Hook] Kalimat pertama.\n\n[Inti 1] Kalimat kedua."
    clean_no_ssml = leg.prepare_text_for_elevenlabs(raw_text, add_ssml_breaks=False)
    assert '<break time="0.4s" />' not in clean_no_ssml
    assert "Kalimat pertama.\n\nKalimat kedua." == clean_no_ssml

    clean_ssml = leg.prepare_text_for_elevenlabs(raw_text, add_ssml_breaks=True)
    assert '<break time="0.4s" />' in clean_ssml


def test_script_fingerprint():
    t1 = "[Hook] Kenapa suara rekaman aneh?  \n\n  [Inti 1] Dua jalur."
    t2 = "  [Hook]   Kenapa suara   rekaman aneh?\n\n[Inti 1]   Dua jalur.  "
    t3 = "[Hook] Berbeda isi."
    fp1 = leg.script_fingerprint(t1)
    fp2 = leg.script_fingerprint(t2)
    fp3 = leg.script_fingerprint(t3)
    assert fp1 == fp2
    assert fp1 != fp3
    assert len(fp1) == 40  # SHA1 hexdigest length
