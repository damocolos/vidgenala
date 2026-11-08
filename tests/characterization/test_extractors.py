import pytest
import legacy.nalarka_local as leg


def test_extract_json_safely_clean():
    raw = '{"status": "ok", "count": 10}'
    data = leg.extract_json_safely(raw, lambda msg: None)
    assert data == {"status": "ok", "count": 10}


def test_extract_json_safely_markdown_fences():
    raw = '```json\n{"scenes": [{"beat_id": 1}]}\n```'
    data = leg.extract_json_safely(raw, lambda msg: None)
    assert data == {"scenes": [{"beat_id": 1}]}


def test_extract_json_safely_strips_think_tags():
    raw = '<think>I am reasoning about the JSON format</think>\n{"topic": "science"}'
    data = leg.extract_json_safely(raw, lambda msg: None)
    assert data == {"topic": "science"}


def test_extract_json_safely_embedded_in_text():
    raw = 'Here is your plan: {"title": "Test Plan"} Hope you like it!'
    data = leg.extract_json_safely(raw, lambda msg: None)
    assert data == {"title": "Test Plan"}


def test_extract_json_safely_empty_raises():
    with pytest.raises(ValueError):
        leg.extract_json_safely("", lambda msg: None)


def test_extract_python_code_safely():
    raw = '```python\ndef beat_01(v):\n    v.rect(0, 0, 100, 100)\n```'
    code = leg.extract_python_code_safely(raw)
    assert code == "def beat_01(v):\n    v.rect(0, 0, 100, 100)"


def test_extract_python_code_strips_think_tags():
    raw = '<think>Let us write beat 01</think>\n```python\ndef beat_01(v):\n    pass\n```'
    code = leg.extract_python_code_safely(raw)
    assert code == "def beat_01(v):\n    pass"


def test_extract_python_code_empty_raises():
    with pytest.raises(ValueError):
        leg.extract_python_code_safely("   ")
