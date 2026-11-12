"""Reproduce static legacy-HUD and unit-label templates from inspected frames.

Training inputs: acc_video.mp4 at 40 seconds and RMMk8BH0e3E at 90 seconds.
Held-out validation uses different timestamps, never these template sources.
"""

import argparse
from pathlib import Path
import sys

import av
import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from telemetry.vision import ASSETS


def frame_at(path, timestamp):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        container.seek(int(timestamp / float(stream.time_base)), stream=stream)
        for frame in container.decode(stream):
            if frame.time >= timestamp:
                return frame.to_ndarray(format="bgr24")
    raise ValueError(f"No frame at {timestamp}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_video", type=Path)
    parser.add_argument("--current-video", type=Path, default=Path("acc_video.mp4"))
    args = parser.parse_args()
    legacy = cv2.cvtColor(frame_at(args.legacy_video, 90), cv2.COLOR_BGR2GRAY)
    current = cv2.cvtColor(frame_at(args.current_video, 40), cv2.COLOR_BGR2GRAY)
    cv2.imwrite(str(ASSETS / "labels_legacy_en.png"), legacy[1003:1036, 1700:1744])
    cv2.imwrite(str(ASSETS / "lap_labels_legacy.png"), legacy[109:188, 27:69])
    cv2.imwrite(str(ASSETS / "unit_mph.png"), legacy[914:934, 1772:1811])
    cv2.imwrite(str(ASSETS / "unit_kmh.png"), current[914:934, 1771:1820])


if __name__ == "__main__":
    main()
