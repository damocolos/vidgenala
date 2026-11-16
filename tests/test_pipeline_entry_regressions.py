"""Guards for mistakes that only show up when execute_full_render runs for real (it needs no AI to reach them)."""
import ast
import shutil
import subprocess
from pathlib import Path

import pytest

SERVICES = Path(__file__).parents[1] / "nalarka" / "services"


@pytest.mark.parametrize("module", ["pipeline.py", "pipeline3d.py"])
def test_no_function_reimports_a_module_level_name(module):
    """`from x import name` inside a function makes `name` local for the WHOLE function, so an earlier use fails with
    UnboundLocalError. This broke every render once; the rule is checked statically now."""
    tree = ast.parse((SERVICES / module).read_text(encoding="utf-8"))
    top = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            top |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            top.add(n.name)
    clashes = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    for a in n.names:
                        name = (a.asname or a.name).split(".")[0]
                        if name in top:
                            clashes.append(f"{fn.name}: {name}")
    assert not clashes, clashes


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg not installed")
@pytest.mark.parametrize("captions", [False, True])
def test_flat_full_render_gets_past_the_setup_and_reaches_the_ai(tmp_path, monkeypatch, captions):
    from nalarka.services import pipeline as pl
    from nalarka.services.context import RunContext

    vo = tmp_path / "vo.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", str(vo)], check=True)

    class Reached(Exception):
        pass

    def stop_here(*a, **k):
        raise Reached()

    monkeypatch.setattr(pl, "extract_word_timestamps", lambda *a, **k: [{"word": "halo", "start": 1.0, "end": 1.4}])
    monkeypatch.setattr(pl, "generate_two_pass_storyboard_and_code", stop_here)
    with pytest.raises(Reached):
        pl.execute_full_render(RunContext(workdir=tmp_path, log_fn=lambda m: None), "u", "k", "m", "Topik", "[Hook] halo dunia", vo,
                               check_script_fingerprint=False, captions=captions)
