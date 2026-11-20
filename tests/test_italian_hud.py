"""Held-out HUD crops from the reported Oulton Park reference, hl5kv1-Dhv4.

Native 240x205 crops at (1670, 740), in a 1920x1080 frame. Speeds/gear were
visually labeled independently; none uses the 90-second caption training frame.
The public video is by Bernagk1: https://www.youtube.com/watch?v=hl5kv1-Dhv4.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from telemetry.vision import HUDExtractor


FIXTURES = Path(__file__).parent / "fixtures/italian_hud"
LABELS = [(15, 120, "2"), (30, 195, "4"), (60, 191, "4"),
          (120, 100, "2"), (160, 202, "4"), (200, 117, "2"), (250, 0, "1")]


def hud_frame(timestamp):
    crop = cv2.imread(str(FIXTURES / f"hl5kv1-Dhv4-{timestamp}.png"))
    assert crop is not None and crop.shape == (205, 240, 3)
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[740:945, 1670:1910] = crop
    return frame


def check_reading(sample, speed, gear):
    assert sample["hud_visible"]
    assert sample["calibration"]["anchor"] == "it"
    assert sample["hud_speed_unit"] == "km/h"
    assert sample["speed"] == speed
    assert sample["gear"] == gear
    assert sample["lap_time_seconds"] is None


@pytest.mark.parametrize("timestamp,speed,gear", LABELS)
def test_italian_hud_cold_start(timestamp, speed, gear):
    check_reading(HUDExtractor().extract(hud_frame(timestamp), timestamp), speed, gear)


def test_italian_hud_continuous_and_setup_screens():
    extractor = HUDExtractor()
    for timestamp, speed, gear in LABELS:
        check_reading(extractor.extract(hud_frame(timestamp), timestamp), speed, gear)
    for timestamp in (300, 400):
        sample = extractor.extract(hud_frame(timestamp), timestamp)
        assert not sample["hud_visible"]
        for channel in ("speed", "gear", "brake", "throttle", "lap_time_seconds"):
            assert sample[channel] is None


def test_italian_hud_reacquires_after_moving():
    frame = hud_frame(30)
    moved = cv2.warpAffine(frame, np.array([[1, 0, -180], [0, 1, -120]], np.float32), (1920, 1080))
    extractor = HUDExtractor()
    check_reading(extractor.extract(frame, 0), 195, "4")
    check_reading(extractor.extract(moved, 2), 195, "4")
    assert extractor.profile["minimum_anchor_score"] == .55


def test_italian_hud_with_unreadable_unit_keeps_speed_missing():
    frame = hud_frame(30)
    # Remove only km/h; readable speed numerals must not imply a speed unit.
    frame[815:838, 1780:1843] = 0
    sample = HUDExtractor().extract(frame, 30)
    assert sample["hud_visible"]
    assert sample["gear"] == "4"
    assert sample["hud_speed_unit"] is None
    assert sample["speed"] is None
