#!/usr/bin/env python3
"""Build a short test clip from the attributed HUD fixture, never private video.

The HUD crop and its source are documented in tests/test_faded_hud.py.
This generated clip is only a packaging test input, not a demonstration lap.
"""
from fractions import Fraction
from pathlib import Path
import argparse

import av
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    hud = Image.open(root / "tests/fixtures/faded_hud/imola-1.150.png").convert("RGB")
    with av.open(str(args.output), "w") as output:
        stream = output.add_stream("libx264", rate=30)
        stream.width, stream.height, stream.pix_fmt = 1920, 1080, "yuv420p"
        stream.options = {"crf": "8", "preset": "ultrafast"}
        for index in range(90):
            image = Image.new("RGB", (1920, 1080), "#101818")
            draw = ImageDraw.Draw(image)
            draw.text((80, 80), "ACCC Telemetry — packaging test fixture", fill="white")
            draw.rectangle((80, 150, 80 + index * 10, 180), fill="#d3ed64")
            image.paste(hud, (1560, 730))
            frame = av.VideoFrame.from_image(image)
            frame.pts, frame.time_base = index, Fraction(1, 30)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    print(args.output)


if __name__ == "__main__":
    main()
