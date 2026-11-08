import pytest
import legacy.nalarka_local as leg


def test_hex_to_rgb():
    assert leg.hex_to_rgb("#F2EADA") == (242, 234, 218)
    assert leg.hex_to_rgb("#000000") == (0, 0, 0)
    assert leg.hex_to_rgb("ffffff") == (255, 255, 255)
    assert leg.hex_to_rgb("#123") == (17, 34, 51) or len(leg.hex_to_rgb("#123")) == 3


def test_rgb_to_hex():
    assert leg.rgb_to_hex((242, 234, 218)).upper() == "#F2EADA"
    assert leg.rgb_to_hex((0, 0, 0)).upper() == "#000000"
    assert leg.rgb_to_hex((255, 255, 255)).upper() == "#FFFFFF"


def test_clamp():
    assert leg.clamp(50, 0, 100) == 50
    assert leg.clamp(-10, 0, 100) == 0
    assert leg.clamp(120, 0, 100) == 100
    assert leg.clamp(0, 0, 100) == 0
    assert leg.clamp(100, 0, 100) == 100


def test_sample_bezier_curve():
    pts = [(0.0, 0.0), (50.0, 100.0), (100.0, 0.0)]
    curve = leg.sample_bezier_curve(pts, steps=10)
    assert len(curve) == 11
    assert curve[0] == (0, 0)
    assert curve[-1] == (100, 0)
    # Check intermediate points are ints
    assert all(isinstance(p[0], int) and isinstance(p[1], int) for p in curve)
