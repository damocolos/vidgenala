"""The Scene 3D speed test: its pure parts (the browser parts need Chromium and are run by hand)."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("spike_3d", Path(__file__).parent.parent / "scripts" / "spike_3d.py")
spike = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spike)


@pytest.mark.parametrize("name,software", [
    ("ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)", True),
    ("llvmpipe (LLVM 15.0.7, 256 bits)", True),
    ("Microsoft Basic Render Driver", True),
    ("ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)", False),
    ("ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11)", False),
    ("ANGLE (Apple, Apple M2, OpenGL 4.1)", False),
    ("", False),
])
def test_software_renderer_detection(name, software):
    assert spike.is_software_renderer(name) is software


def test_estimate_and_verdict_thresholds():
    assert spike.estimate_minutes(1.0) == pytest.approx(2475 / 60)
    assert spike.verdict(0.2).startswith("GOOD")          # 8 min
    assert spike.verdict(0.48).startswith("GOOD")         # 19.8 min
    assert spike.verdict(0.5).startswith("USABLE")        # 20.6 min is just over the 20 min line
    v = spike.verdict(1.0)
    assert v.startswith("USABLE") and "720x1280" in v     # 41 min
    assert spike.verdict(2.0).startswith("TOO SLOW") and "2.5D" in spike.verdict(2.0)


def test_args_are_clamped_and_defaults_match_the_target_video():
    a = spike.parse_args([])
    assert (a.width, a.height, a.frames, a.shots) == (1080, 1920, 24, 5)
    b = spike.parse_args(["--width", "99999", "--height", "1", "--frames", "100000", "--shots", "0"])
    assert (b.width, b.height, b.frames, b.shots) == (2160, 64, 300, 1)


def test_scene_template_has_no_stray_percent_signs():
    out = spike.THREE_SCENE_JS % {"w": 1080, "h": 1920}      # a lone % would raise
    assert "1080" in out and "window.spike" in out


def test_three_download_refuses_a_file_that_is_not_three(tmp_path, monkeypatch):
    class R:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(spike.urllib.request, "urlopen", lambda *a, **k: R(b"<html>blocked</html>"))
    with pytest.raises(ValueError):
        spike._download_three("https://x/three.js", tmp_path / "t.js", lambda m: None)
    assert not (tmp_path / "t.js").exists()                   # nothing cached from a bad download
    monkeypatch.setattr(spike.urllib.request, "urlopen", lambda *a, **k: R(b"var REVISION='149';"))
    assert "REVISION" in spike._download_three("https://x/three.js", tmp_path / "t.js", lambda m: None)
    monkeypatch.setattr(spike.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cached")))
    assert "REVISION" in spike._download_three("https://x/three.js", tmp_path / "t.js", lambda m: None)   # second run: no network
