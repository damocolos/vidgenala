"""Golden frame hashes depend on the installed fonts (Arial Black / Impact vs the DejaVu fallback)
and the FreeType build, so they are only meaningful on a machine with the reference fonts.
Set NALARKA_FORCE_GOLDEN=1 to compare anyway."""
import os
import pytest


def skip_unless_reference_fonts():
    if os.environ.get("NALARKA_FORCE_GOLDEN") == "1":
        return
    from nalarka.core.vectorkit import load_font
    path = (getattr(load_font(40), "path", "") or "").lower()
    if not path or os.path.basename(path).startswith("dejavu"):
        pytest.skip("golden hashes need the reference fonts (Arial Black/Impact); fallback font in use")
