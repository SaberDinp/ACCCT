"""Render bundled source centerlines for human layout auditing, not racing lines."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry.tracks import get_track, list_tracks, point_at_progress


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/track-source-audit.png")
    args = parser.parse_args()
    catalog = list_tracks()
    columns, cell_width, cell_height = 5, 600, 510
    canvas = np.full((((len(catalog) + columns - 1) // columns) * cell_height,
                      columns * cell_width, 3), 247, dtype=np.uint8)
    for index, item in enumerate(catalog):
        track = get_track(item['id'])
        x, y = (index % columns) * cell_width, (index // columns) * cell_height
        def pixel(point):
            return (round(x + 70 + point['x'] * 450), round(y + 45 + point['y'] * 450))
        points = np.array([pixel(p)
                           for p in track["points"]], dtype=np.int32)
        cv2.polylines(canvas, [points], True, (90, 75, 35), 2, cv2.LINE_AA)
        for marker in track['corners']:
            position = pixel(point_at_progress(item['id'], marker['progress']))
            cv2.circle(canvas, position, 2, (190, 70, 50), -1)
            if marker['number'] is not None:
                # OpenCV's built-in font is ASCII only; full names are in the UI.
                label = str(marker['number']).replace('–', '-')
                cv2.putText(canvas, label, position, cv2.FONT_HERSHEY_SIMPLEX,
                            .3, (190, 70, 50), 1, cv2.LINE_AA)
        for marker in track['timing_lines']:
            position = pixel(point_at_progress(item['id'], marker['progress']))
            cv2.circle(canvas, position, 4, (35, 35, 220), -1)
            label = 'SF' if marker['kind'] == 'start_finish' else f"S{marker['sector']}"
            cv2.putText(canvas, label, position, cv2.FONT_HERSHEY_SIMPLEX,
                        .35, (35, 35, 220), 1, cv2.LINE_AA)
        title = f"{item['id']}: {track['geometry_length_m']:.0f} m"
        cv2.putText(canvas, title, (x + 20, y + 27), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (25, 25, 25), 1, cv2.LINE_AA)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise RuntimeError(f"Could not write {output}")
    print(output)


if __name__ == "__main__":
    main()
