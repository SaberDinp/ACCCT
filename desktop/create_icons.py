#!/usr/bin/env python3
"""Render the shared A + three nested Cs mark without installed-font dependencies."""
import math
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent


def main():
    folder = ROOT / "icons"
    folder.mkdir(exist_ok=True)
    # The SVG and raster icons use the same 256-unit geometry. Render at 8x
    # before downsampling so the small Windows/Linux sizes retain clear edges.
    scale = 8
    image = Image.new("RGBA", (256 * scale, 256 * scale))
    draw = ImageDraw.Draw(image)

    def rect(bounds, radius, fill):
        draw.rounded_rectangle(tuple(int(value * scale) for value in bounds), radius=radius * scale, fill=fill)

    rect((4, 4, 252, 252), 52, "#101212")
    paths = []
    for radius, width in ((100, 14), (75, 12), (51, 10)):
        # Centered strokes and rounded caps match the SVG exactly.
        points = [(128 + radius * math.cos(math.radians(angle)),
                   128 + radius * math.sin(math.radians(angle)))
                  for angle in range(40, 321)]
        outer = radius + width / 2
        draw.arc(tuple(round(value * scale) for value in
                       (128-outer, 128-outer, 128+outer, 128+outer)),
                 start=40, end=320, fill="#d8ee88", width=width * scale)
        for x, y in (points[0], points[-1]):
            draw.ellipse(tuple(round(value * scale) for value in
                               (x-width/2, y-width/2, x+width/2, y+width/2)), fill="#d8ee88")
        sx, sy = points[0]
        ex, ey = points[-1]
        paths.append(f'  <path d="M {sx:.3f} {sy:.3f} A {radius} {radius} 0 1 1 {ex:.3f} {ey:.3f}" stroke-width="{width}"/>')

    # One A sits inside all three Cs; its crossbar is deliberately distinct.
    for points in (((109, 147), (128, 107), (147, 147)), ((116, 133), (140, 133))):
        draw.line([(x*scale, y*scale) for x, y in points], fill="#d8ee88", width=8*scale, joint="curve")
        for x,y in (points[0], points[-1]):
            draw.ellipse(((x-4)*scale,(y-4)*scale,(x+4)*scale,(y+4)*scale),fill="#d8ee88")
    svg = '\n'.join([
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-labelledby="title">',
        '  <title id="title">ACCC Telemetry: A inside three nested Cs</title>',
        '  <rect x="4" y="4" width="248" height="248" rx="52" fill="#101212"/>',
        '  <g fill="none" stroke="#d8ee88" stroke-linecap="round" stroke-linejoin="round">',
        *paths, '  <path d="M 109 147 L 128 107 L 147 147 M 116 133 H 140" stroke-width="8"/>',
        '  </g>', '</svg>', ''
    ])
    brand = ROOT.parent / "web" / "brand"
    brand.mkdir(exist_ok=True)
    (brand / "accc-mark.svg").write_text(svg, encoding="utf-8")
    image = image.resize((1024, 1024), Image.Resampling.LANCZOS)
    image.save(folder / "icon.png")
    image.save(folder / "icon.icns", format="ICNS")
    image.save(folder / "icon.ico", format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Created shared SVG in {brand} and desktop icons in {folder}")


if __name__ == "__main__":
    main()
