"""Synthetic edge cases and labeled frames not used to build the templates."""

from pathlib import Path
import unittest

import cv2
import numpy as np

from telemetry.profiles import default_profile, validated_profile
from telemetry.vision import HUDExtractor, estimate_pedal
from telemetry.steering import extract_steering


VIDEO = Path(__file__).resolve().parent.parent / "acc_video.mp4"


class PedalTests(unittest.TestCase):
    def test_steering_marker_position_and_missing_marker(self):
        for x in (25, 102, 175):
            image = np.full((17, 204, 3), 35, np.uint8)
            cv2.line(image, (3, 8), (200, 8), (85, 85, 85), 1)
            cv2.circle(image, (x, 8), 4, (250, 250, 250), -1)
            value, confidence = extract_steering(image)
            self.assertAlmostEqual(value, (x - 102) / 99 * 100, delta=1)
            self.assertGreater(confidence, 0.65)
        self.assertIsNone(extract_steering(np.zeros((17, 204, 3), np.uint8))[0])

    def test_prefix_values_and_white_text(self):
        for channel, color in (("throttle", (15, 170, 20)), ("brake", (12, 18, 205))):
            for percent in (0, 10, 35, 50, 80, 100):
                with self.subTest(channel=channel, percent=percent):
                    image = np.zeros((15, 100, 3), np.uint8)
                    image[:, :percent] = color
                    image[3:9, 6:92:3] = (245, 245, 245)
                    value, confidence = estimate_pedal(image, channel)
                    self.assertEqual(value, percent)
                    self.assertGreater(confidence, 0.8)

    def test_assist_amber_is_still_input(self):
        image = np.zeros((15, 100, 3), np.uint8)
        image[:, :70] = (10, 185, 240)
        for channel in ("brake", "throttle"):
            self.assertEqual(estimate_pedal(image, channel)[0], 70)

    def test_directions(self):
        image = np.zeros((100, 15, 3), np.uint8)
        image[60:, :] = (0, 200, 0)
        self.assertEqual(estimate_pedal(image, "throttle", "bt")[0], 40)
        self.assertEqual(estimate_pedal(image[::-1], "throttle", "tb")[0], 40)
        self.assertEqual(estimate_pedal(image.transpose(1, 0, 2), "throttle", "rl")[0], 40)

    def test_non_contiguous_color_is_rejected(self):
        image = np.zeros((15, 100, 3), np.uint8)
        image[:, ::2] = (0, 200, 0)
        self.assertIsNone(estimate_pedal(image, "throttle")[0])

    def test_profile_validation_and_independence(self):
        profile = default_profile()
        profile["rois"]["brake"][0] = 0
        self.assertNotEqual(default_profile()["rois"]["brake"][0], 0)
        for roi in ([0, 0, -1, 1], [0.9, 0, 0.2, 1], [float("nan"), 0, 1, 1]):
            with self.assertRaises(ValueError):
                validated_profile({"rois": {"brake": roi}})
        for overrides in ({"reference_width": "1920"}, {"reference_height": True}, {"name": []},
                          {"name": " "}, {"rois": {"brake": [0, 0, 0.0001, 0.0001]}}):
            with self.assertRaises(ValueError):
                validated_profile(overrides)

    def test_missing_hud_does_not_become_zero_inputs(self):
        extractor = HUDExtractor()
        for brightness in (0, 100, 255):
            sample = extractor.extract(np.full((1080, 1920, 3), brightness, np.uint8), 12)
            self.assertFalse(sample["hud_visible"])
            self.assertIsNone(sample["brake"])
            self.assertIsNone(sample["speed"])


@unittest.skipUnless(VIDEO.exists(), "Supplied sample video is not available")
class FootageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture = cv2.VideoCapture(str(VIDEO))
        cls.extractor = HUDExtractor()

    @classmethod
    def tearDownClass(cls):
        cls.capture.release()

    def frame(self, timestamp):
        self.capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ok, frame = self.capture.read()
        self.assertTrue(ok)
        return frame

    def test_held_out_speed_and_gear(self):
        labels = {41: (197, "5"), 43: (192, "5"), 45: (157, "4"), 47: (147, "3"),
                  49: (132, "3"), 63: (240, "6"), 65: (165, "3"), 67: (143, "3"),
                  75: (202, "5"), 77: (92, "1"), 79: (81, "1"), 101: (217, "5"),
                  103: (131, "2"), 105: (87, "1"), 125: (237, "5"), 127: (192, "4"),
                  129: (128, "2"), 131: (115, "2"), 201: (81, "1"), 301: (212, "5"),
                  401: (216, "5"), 501: (128, "2"), 531: (168, "3")}
        for timestamp, (speed, gear) in labels.items():
            with self.subTest(timestamp=timestamp):
                sample = self.extractor.extract(self.frame(timestamp), timestamp)
                self.assertTrue(sample["hud_visible"])
                self.assertEqual(sample["speed"], speed)
                self.assertEqual(sample["gear"], gear)

    def test_menu_discontinuities(self):
        for timestamp in (2, 12, 20, 360, 541):
            with self.subTest(timestamp=timestamp):
                sample = self.extractor.extract(self.frame(timestamp), timestamp)
                self.assertFalse(sample["hud_visible"])
                self.assertIsNone(sample["brake"])
                self.assertIsNone(sample["throttle"])
                self.assertIsNone(sample["lap_time"])

    def test_braking_and_assist_frames(self):
        sample = self.extractor.extract(self.frame(75), 75)
        self.assertGreaterEqual(sample["brake"], 98)
        self.assertEqual(sample["throttle"], 0)
        self.assertTrue(sample["brake_highlighted"])
        sample = self.extractor.extract(self.frame(131), 131)
        self.assertGreaterEqual(sample["throttle"], 98)
        self.assertEqual(sample["brake"], 0)
        self.assertTrue(sample["throttle_highlighted"])

    def test_red_timer_is_invalid_lap(self):
        sample = self.extractor.extract(self.frame(300), 300)
        self.assertEqual(sample["lap_time_seconds"], 30)
        self.assertFalse(sample["lap_valid"])
        sample = self.extractor.extract(self.frame(420), 420)
        self.assertEqual(sample["lap_time"], "02:28.500")

    def test_rpm_fails_closed_for_ambiguous_digit(self):
        sample = self.extractor.extract(self.frame(103), 103)
        self.assertIn(sample["rpm"], (None, 6343))

    def test_missing_middle_speed_digit_is_not_shortened_number(self):
        frame = self.frame(40)
        frame[934:974, 1795:1818] = 0
        sample = self.extractor.extract(frame, 40)
        self.assertTrue(sample["hud_visible"])
        self.assertIsNone(sample["speed"])

    def test_scaled_720p_frame(self):
        frame = cv2.resize(self.frame(40), (1280, 720), interpolation=cv2.INTER_AREA)
        sample = self.extractor.extract(frame, 40)
        self.assertTrue(sample["hud_visible"])
        self.assertEqual(sample["gear"], "5")
        self.assertEqual(sample["speed"], 226)
        self.assertGreaterEqual(sample["throttle"], 98)

    def test_mph_profile_converts_output_to_kph(self):
        extractor = HUDExtractor({"speed_unit": "mph"})
        sample = extractor.extract(self.frame(40), 40)
        self.assertEqual(sample["speed"], 363.7)

    def test_auto_calibration_translation_and_scale(self):
        frame = self.frame(40)
        for scale, x, y in ((1, -360, -300), (0.8, 270, 180)):
            with self.subTest(scale=scale):
                moved = cv2.warpAffine(frame, np.array([[scale, 0, x], [0, scale, y]], np.float32), (1920, 1080))
                extractor = HUDExtractor()
                sample = extractor.extract(moved, 0)
                self.assertTrue(sample["hud_visible"])
                self.assertEqual(sample["gear"], "5")
                self.assertEqual(sample["speed"], 226)
                self.assertGreater(sample["throttle"], 97)
                self.assertEqual(sample["calibration"]["status"], "locked")
                self.assertAlmostEqual(sample["calibration"]["scale"], scale, delta=0.04)

    def test_auto_reacquires_after_layout_change_and_menu(self):
        frame = self.frame(40)
        extractor = HUDExtractor()
        first = extractor.extract(frame, 0)
        moved = cv2.warpAffine(frame, np.array([[1, 0, -360], [0, 1, -300]], np.float32), (1920, 1080))
        second = extractor.extract(moved, 1)
        self.assertGreater(second["calibration"]["revision"], first["calibration"]["revision"])
        self.assertEqual(second["speed"], 226)
        hidden = extractor.extract(np.zeros_like(frame), 2)
        self.assertIsNone(hidden["steering"])
        self.assertFalse(hidden["hud_visible"])
        restored = extractor.extract(frame, 3)
        self.assertEqual(restored["speed"], 226)
        self.assertGreater(restored["calibration"]["revision"], second["calibration"]["revision"])

    def test_actual_steering_direction(self):
        left = self.extractor.extract(self.frame(110), 110)
        right = self.extractor.extract(self.frame(106), 111)
        self.assertLess(left["steering"], -25)
        self.assertGreater(right["steering"], 15)
        self.assertEqual(left["steering_unit"], "hud_percent")

    def test_manual_mode_does_not_change_regions(self):
        frame = self.frame(40)
        moved = cv2.warpAffine(frame, np.array([[1, 0, -360], [0, 1, -300]], np.float32), (1920, 1080))
        extractor = HUDExtractor({"auto_calibrate": False})
        sample = extractor.extract(moved, 0)
        self.assertFalse(sample["hud_visible"])
        self.assertEqual(sample["calibration"]["status"], "manual")
        self.assertEqual(extractor.profile["rois"], extractor.resolved_profile()["rois"])


REFERENCE_VIDEO = VIDEO.parent / "data" / "references" / "c0SJgXUahEM" / "media.mp4"


@unittest.skipUnless(REFERENCE_VIDEO.exists(), "Downloaded reference video is not available")
class ReferenceFootageTests(unittest.TestCase):
    def test_german_reference_auto_layout(self):
        import av
        extractor = HUDExtractor()
        with av.open(str(REFERENCE_VIDEO)) as container:
            stream = container.streams.video[0]
            for timestamp, speed, gear in ((10, 198, "4"), (20, 193, "4"), (45, 169, "3"), (180, 252, "6")):
                container.seek(int(timestamp / float(stream.time_base)), stream=stream)
                for frame in container.decode(stream):
                    if float(frame.time) >= timestamp:
                        break
                sample = extractor.extract(frame.to_ndarray(format="bgr24"), timestamp)
                with self.subTest(timestamp=timestamp):
                    self.assertTrue(sample["hud_visible"])
                    self.assertEqual(sample["speed"], speed)
                    self.assertEqual(sample["gear"], gear)
                    self.assertEqual(sample["calibration"]["anchor"], "de")
                    self.assertIsNotNone(sample["steering"])


if __name__ == "__main__":
    unittest.main()
