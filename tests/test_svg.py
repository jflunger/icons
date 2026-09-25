import xml.etree.ElementTree as ET

import pytest

from iconsearch import svg

STROKE_BODY = '<path fill="none" stroke="currentColor" stroke-width="2" d="M1 1h2"/>'
NO_WIDTH_BODY = '<g fill="none" stroke="currentColor"><path d="M1 1h2"/></g>'


def test_no_current_color_and_explicit_size():
    out = svg.build(STROKE_BODY, 24, 24, color="FF0000", size=128)
    assert "currentColor" not in out
    assert 'stroke="#ff0000"' in out
    root = ET.fromstring(out)
    assert root.get("width") == "128" and root.get("height") == "128"
    assert root.get("viewBox") == "0 0 24 24"


def test_aspect_ratio_kept():
    root = ET.fromstring(svg.build("<path/>", 20, 10, size=200))
    assert (root.get("width"), root.get("height")) == ("200", "100")


def test_stroke_rewrite_scaled_to_viewbox():
    assert 'stroke-width="1.5"' in svg.build(STROKE_BODY, 24, 24, stroke=1.5, is_stroke=True)
    # 256-unit grid: 1.5 on a 24 grid = 16 units
    assert 'stroke-width="16"' in svg.build(STROKE_BODY, 256, 256, stroke=1.5, is_stroke=True)
    # ignored for fill icons
    assert 'stroke-width="2"' in svg.build(STROKE_BODY, 24, 24, stroke=1.5, is_stroke=False)


def test_stroke_added_on_root_when_absent():
    root = ET.fromstring(svg.build(NO_WIDTH_BODY, 24, 24, stroke=1, is_stroke=True))
    assert root.get("stroke-width") == "1"


def test_background_rect():
    out = svg.build("<path/>", 24, 24, bg="ffffff")
    assert '<rect width="24" height="24" fill="#ffffff"/>' in out


@pytest.mark.parametrize("bad", ["red", "12345", "1234567", "#12345g", "fff"])
def test_colour_validation(bad):
    with pytest.raises(svg.SvgParamError):
        svg.build("<path/>", 24, 24, color=bad)


def test_size_clamped():
    assert ET.fromstring(svg.build("<path/>", 24, 24, size=1)).get("width") == "16"
    assert ET.fromstring(svg.build("<path/>", 24, 24, size=99999)).get("width") == "2048"


def test_no_style_blocks():
    out = svg.build(STROKE_BODY, 24, 24)
    assert "<style" not in out and "var(" not in out
