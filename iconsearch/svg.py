"""Build standalone, PowerPoint-safe SVG files from Iconify bodies."""
from __future__ import annotations

import re
import struct
import zlib

HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")
MIN_SIZE, MAX_SIZE = 16, 2048
STROKE_WIDTH_RE = re.compile(r'stroke-width="[^"]*"')
CURRENT_COLOR_RE = re.compile(r"currentColor", re.I)


class SvgParamError(ValueError):
    pass


def valid_hex(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    value = value.lstrip("#")
    if not HEX_RE.match(value):
        raise SvgParamError(f"{name} must be a 6-digit hex colour like 1f2937")
    return value.lower()


def clamp_size(size: int) -> int:
    return max(MIN_SIZE, min(MAX_SIZE, int(size)))


def _num(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".")


def build(
    body: str,
    width: float,
    height: float,
    *,
    color: str = "ffffff",
    size: int = 256,
    stroke: float | None = None,
    is_stroke: bool = False,
    bg: str | None = None,
) -> str:
    """Return a full SVG document.

    - currentColor is replaced by the literal hex (PowerPoint doesn't resolve it).
    - width/height are explicit so PowerPoint inserts it at a sensible size; the
      longer side equals `size` and the aspect ratio is kept.
    - `stroke` is in px on a 24-unit grid and scaled to the icon's viewBox, so
      2 means "Lucide default weight" whatever the set's native grid.
    - Presentation attributes only: no <style>, no CSS variables.
    """
    color = valid_hex(color, "color") or "ffffff"
    bg = valid_hex(bg, "bg")
    size = clamp_size(size)
    scale = size / max(width, height)
    out_w, out_h = round(width * scale), round(height * scale)

    inner = CURRENT_COLOR_RE.sub(f"#{color}", body)
    root_extra = ""
    if stroke is not None and is_stroke:
        if not (0 < stroke <= 10):
            raise SvgParamError("stroke must be between 0 and 10")
        sw = _num(stroke * width / 24.0)
        if STROKE_WIDTH_RE.search(inner):
            inner = STROKE_WIDTH_RE.sub(f'stroke-width="{sw}"', inner)
        else:
            root_extra = f' stroke-width="{sw}"'
    if bg:
        inner = f'<rect width="{_num(width)}" height="{_num(height)}" fill="#{bg}"/>' + inner

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{out_w}" height="{out_h}" viewBox="0 0 {_num(width)} {_num(height)}"{root_extra}>'
        f"{inner}</svg>"
    )


PNG_MAX_PX = 2048  # longest side of the raster; up to 4x the SVG size for sharpness


def to_png(doc: str, size: int) -> bytes:
    """Rasterize a document from build() at up to 4x, tagging the PNG's dpi to match.

    Office inserts pictures at pixels / dpi, so the picture keeps the SVG's width and
    height as its physical size and stays sharp when it's enlarged on a slide.
    """
    import resvg_py

    density = max(1, min(4, PNG_MAX_PX // clamp_size(size)))
    png = bytes(resvg_py.svg_to_bytes(svg_string=doc, zoom=density, skip_system_fonts=True))
    ppm = round(96 * density / 0.0254)
    data = struct.pack(">IIB", ppm, ppm, 1)
    phys = struct.pack(">I", len(data)) + b"pHYs" + data + struct.pack(">I", zlib.crc32(b"pHYs" + data))
    ihdr_end = 8 + 8 + 13 + 4  # signature, IHDR length+type, IHDR data, CRC
    return png[:ihdr_end] + phys + png[ihdr_end:]
