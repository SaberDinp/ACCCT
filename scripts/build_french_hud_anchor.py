"""Rebuild the French current-HUD caption anchor from t0Rgya0suMw.

Only a static pedal-caption crop is used; the speed/gear glyph banks are
unchanged. Regression fixtures use other timestamps, on both recorded laps.
"""

import argparse
from pathlib import Path

import cv2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Original 1920x1080 t0Rgya0suMw video")
    args = parser.parse_args()
    capture = cv2.VideoCapture(str(args.video))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, 90000)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok or frame.shape[:2] != (1080, 1920):
        raise ValueError("Expected the source's 1920x1080 frame near 90 seconds")
    # French Accélérateur is wider than the English caption. The existing
    # label ROI covers its fixed right-hand portion and the whole Frein label,
    # preserving the same offset from the pedal bars and other instruments.
    crop = cv2.cvtColor(frame[898:925, 1733:1769], cv2.COLOR_BGR2GRAY)
    anchor = cv2.resize(crop, (44, 33), interpolation=cv2.INTER_LINEAR)
    destination = Path(__file__).resolve().parents[1] / "assets/templates/labels_fr.png"
    if not cv2.imwrite(str(destination), anchor):
        raise OSError(f"Could not write {destination}")


if __name__ == "__main__":
    main()
