"""Native fade-in pixels from Fri3d0lf's Imola lap, independently transcribed.

Source: https://www.youtube.com/watch?v=ZHPu7y_mLP8 at 1.150 seconds.
The retained 360x230 crop starts at (1560, 730) in the 1920x1080 frame.
Visible HUD labels: 240 km/h, gear 5, full throttle and no brake.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from telemetry.vision import HUDExtractor


def imola_frame():
    crop = cv2.imread(str(Path(__file__).parent / "fixtures/faded_hud/imola-1.150.png"))
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[730:960, 1560:1920] = crop
    return frame


def test_visible_faded_imola_hud_is_read_at_requested_start():
    frame = imola_frame()
    original = frame.copy()
    sample = HUDExtractor().extract(frame, 1.15)
    assert sample["time"] == 1.15
    assert sample["hud_visible"]
    assert sample["speed"] == 240
    assert sample["gear"] == "5"
    assert sample["hud_speed_unit"] == "km/h"
    assert sample["throttle"] >= 98
    assert sample["brake"] == 0
    assert np.array_equal(frame, original)


@pytest.mark.parametrize("brightness", [0.0, 0.1, 0.25])
def test_nearly_black_fade_does_not_invent_hud_values(brightness):
    sample = HUDExtractor().extract(cv2.convertScaleAbs(imola_frame(), alpha=brightness), 1.15)
    assert not sample["hud_visible"]
    for channel in ("speed", "gear", "brake", "throttle", "steering"):
        assert sample[channel] is None


def test_dim_numerals_without_hud_label_anchor_remain_unavailable():
    frame = imola_frame()
    frame[895:932, 1720:1771] = 0
    sample = HUDExtractor().extract(frame, 1.15)
    assert not sample["hud_visible"]
    assert sample["speed"] is None
    assert sample["gear"] is None
