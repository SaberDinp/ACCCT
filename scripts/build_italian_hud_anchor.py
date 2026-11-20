"""Rebuild the Italian current-HUD caption anchor from hl5kv1-Dhv4.

Only static pedal captions from 90 seconds are used. Held-out fixtures cover
other timestamps, including the second lap and setup screens. No numeral or
unit templates are learned from this recording.
"""

import argparse
from pathlib import Path

import cv2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Original 1920x1080 hl5kv1-Dhv4 video")
    args = parser.parse_args()
    capture = cv2.VideoCapture(str(args.video))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, 90000)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok or frame.shape[:2] != (1080, 1920):
        raise ValueError("Expected the source's 1920x1080 frame near 90 seconds")
    # The right-hand portion of Acceleratore and the whole Freno caption have
    # the same offset from the instruments as the English anchor. This HUD is
    # rendered at 80% scale; matching French captions incorrectly found 88%.
    crop = cv2.cvtColor(frame[900:926, 1736:1771], cv2.COLOR_BGR2GRAY)
    anchor = cv2.resize(crop, (44, 33), interpolation=cv2.INTER_LINEAR)
    destination = Path(__file__).resolve().parents[1] / "assets/templates/labels_it.png"
    if not cv2.imwrite(str(destination), anchor):
        raise OSError(f"Could not write {destination}")


if __name__ == "__main__":
    main()
