"""Held-out French HUD pixels, independent of the caption template frame."""
import json
from pathlib import Path
import unittest

import cv2
import numpy as np

from telemetry.vision import HUDExtractor


ROOT = Path(__file__).resolve().parents[1]
LABELS = json.loads((ROOT / "tests/fixtures/french_hud_labels.json").read_text())
FIXTURES = ROOT / "tests/fixtures/french_hud"


def driving_frame(label):
    width, height = LABELS["frame_size"]
    left, top, crop_width, crop_height = LABELS["placement"]
    frame = np.zeros((height, width, 3), np.uint8)
    crop = cv2.imread(str(FIXTURES / label["crop"]))
    assert crop is not None and crop.shape[:2] == (crop_height, crop_width)
    frame[top:top + crop_height, left:left + crop_width] = crop
    return frame


class FrenchHUDTests(unittest.TestCase):
    def check_reading(self, sample, label):
        self.assertTrue(sample["hud_visible"])
        self.assertEqual(sample["calibration"]["anchor"], "fr")
        self.assertEqual(sample["hud_speed_unit"], "km/h")
        self.assertEqual(sample["speed"], label["speed"])
        self.assertEqual(sample["gear"], label["gear"])
        for channel in ("brake", "throttle"):
            low, high = label[channel + "_range"]
            self.assertIsNotNone(sample[channel])
            self.assertLessEqual(low, sample[channel])
            self.assertLessEqual(sample[channel], high)
        self.assertIsNone(sample["lap_time_seconds"])

    def test_held_out_driving_frames_cold_and_continuous(self):
        continuous = HUDExtractor()
        for label in LABELS["frames"]:
            with self.subTest(time=label["time"]):
                self.assertNotIn(label["decoded_time"], LABELS["training_timestamps_excluded"])
                frame = driving_frame(label)
                self.check_reading(HUDExtractor().extract(frame, label["time"]), label)
                self.check_reading(continuous.extract(frame, label["time"]), label)

    def test_setup_screens_still_have_no_hud_after_driving(self):
        extractor = HUDExtractor()
        label = LABELS["frames"][0]
        self.check_reading(extractor.extract(driving_frame(label), 0), label)
        for index, negative in enumerate(LABELS["no_hud_frames"]):
            with self.subTest(time=negative["time"]):
                frame = cv2.imread(str(FIXTURES / negative["image"]))
                self.assertIsNotNone(frame)
                sample = extractor.extract(frame, 10 + index * 10)
                self.assertFalse(sample["hud_visible"])
                for channel in ("speed", "gear", "brake", "throttle", "steering", "lap_time_seconds"):
                    self.assertIsNone(sample[channel])

    def test_moved_french_hud_reacquires_without_lowering_threshold(self):
        label = LABELS["frames"][2]
        frame = driving_frame(label)
        moved = cv2.warpAffine(frame, np.array([[1, 0, -180], [0, 1, -120]], np.float32), (1920, 1080))
        extractor = HUDExtractor()
        self.check_reading(extractor.extract(frame, 0), label)
        for timestamp in (2, 4, 6):
            sample = extractor.extract(moved, timestamp)
        self.check_reading(sample, label)
        self.assertEqual(extractor.profile["minimum_anchor_score"], .55)
