"""Native speed regressions from the user's Watkins Glen recording.

Source: 423091143698.mp4, 1920x1080. These unscaled 360x230 crops start at
(1560, 730). Speeds were independently read from the visible right-hand HUD
and agree with the dashboard: 216 at 14.848s, 228 at 19.821s, 142 at 49.249s.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from telemetry.vision import HUDExtractor


def hud_frame(stamp):
    crop = cv2.imread(str(Path(__file__).parent / "fixtures/contrast_hud" / f"watkins-{stamp}.png"))
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[730:960, 1560:1920] = crop
    return frame


@pytest.mark.parametrize("stamp,speed,gear", [("14.848", 216, "5"), ("19.821", 228, "5"), ("49.249", 142, "3")])
def test_soft_and_background_merged_hud_digits_remain_observed(stamp, speed, gear):
    frame = hud_frame(stamp)
    original = frame.copy()
    sample = HUDExtractor().extract(frame, float(stamp))
    assert sample["hud_visible"]
    assert sample["hud_speed_unit"] == "km/h"
    assert sample["speed"] == speed
    assert sample["gear"] == gear
    assert np.array_equal(frame, original)


def test_contrast_retry_does_not_shorten_a_damaged_speed():
    extractor = HUDExtractor()
    frame = hud_frame("19.821")
    assert extractor.extract(frame, 19.821)["speed"] == 228
    crop = extractor._crop(frame, "speed")
    # The fallback must retain all three digits; removing either the leading
    # or middle digit cannot make a shorter number appear valid.
    for left, right in ((0, 26), (26, 50)):
        damaged = crop.copy()
        damaged[:, left:right] = 0
        assert extractor._read_speed_contrast(damaged)[0] is None


def test_contrast_retry_rejects_inconsistent_threshold_readings(monkeypatch):
    extractor = HUDExtractor()
    readings = iter([("216", 0.86), ("218", 0.85), (None, 0), ("216", 0.84), (None, 0)])
    monkeypatch.setattr(extractor, "_read_mask", lambda *args: next(readings))
    assert extractor._read_speed_contrast(np.zeros((40, 78, 3), np.uint8)) == (None, 0.0)
