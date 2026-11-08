import pytest
from PIL import Image
import legacy.nalarka_local as leg


def test_composite_transition_cut():
    im1 = Image.new("RGB", (100, 100), (255, 0, 0))
    im2 = Image.new("RGB", (100, 100), (0, 255, 0))

    # A "cut" transition always returns img_next immediately
    res0 = leg.composite_transition(im1, im2, 0.0, "cut")
    assert res0.getpixel((50, 50)) == (0, 255, 0)

    res1 = leg.composite_transition(im1, im2, 1.0, "cut")
    assert res1.getpixel((50, 50)) == (0, 255, 0)


def test_composite_transition_fade():
    im1 = Image.new("RGB", (100, 100), (0, 0, 0))
    im2 = Image.new("RGB", (100, 100), (200, 200, 200))

    res_mid = leg.composite_transition(im1, im2, 0.5, "fade")
    pixel = res_mid.getpixel((50, 50))
    # Should blend around (100, 100, 100)
    assert 90 <= pixel[0] <= 110


def test_composite_transition_types():
    im1 = Image.new("RGB", (100, 100), (255, 0, 0))
    im2 = Image.new("RGB", (100, 100), (0, 255, 0))

    for trans in ["whip_pan", "slide_left", "slide_right", "zoom_punch"]:
        res = leg.composite_transition(im1, im2, 0.5, trans)
        assert res.size == (100, 100)
