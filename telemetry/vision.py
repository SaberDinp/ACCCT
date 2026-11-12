"""Small-ROI classical image extraction. No models or network calls at runtime."""

from pathlib import Path
from time import perf_counter
from copy import deepcopy

import cv2
import numpy as np

from .profiles import layout_profile, validated_profile
from .calibration import INSTRUMENTS, locate_group
from .steering import extract_steering


ASSETS = Path(__file__).resolve().parent.parent / "assets" / "templates"
CANONICAL = {"speed": (78, 40), "gear": (78, 101), "labels": (44, 33), "lap_time": (168, 38), "rpm": (47, 22), "steering": (204, 17), "lap_labels": (42, 79), "speed_unit": (64, 30)}


def white_mask(crop: np.ndarray, threshold: int = 205) -> np.ndarray:
    channels = crop.astype(np.int16)
    return ((channels.min(axis=2) >= threshold) & (np.ptp(channels, axis=2) < 65)).astype(np.uint8) * 255


def timer_mask(crop: np.ndarray, threshold: int = 205) -> np.ndarray:
    channels = crop.astype(np.int16)
    red = (channels[:, :, 2] > 115) & (channels[:, :, 2] - channels[:, :, 1] > 55) & (channels[:, :, 2] - channels[:, :, 0] > 45)
    return np.maximum(white_mask(crop, threshold), red.astype(np.uint8) * 255)


def normalize_glyph(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    scale = min(24 / width, 44 / height)
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    resized = cv2.resize(mask, (w, h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((48, 28), np.uint8)
    y, x = (48 - h) // 2, (28 - w) // 2
    canvas[y:y + h, x:x + w] = resized
    return canvas


def glyph_components(mask: np.ndarray, kind: str) -> list[tuple[int, np.ndarray]]:
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    limits = {
        "speed": (26, 39, 6, 25, 45),
        "gear": (76, 101, 24, 77, 650),
        "lap_time": (25, 37, 5, 23, 38),
        "rpm": (12, 22, 2, 14, 12),
    }
    min_h, max_h, min_w, max_w, min_area = limits[kind]
    found = []
    for index, (x, y, width, height, area) in enumerate(stats[1:], start=1):
        if min_h <= height <= max_h and min_w <= width <= max_w and area >= min_area:
            glyph = (labels[y:y + height, x:x + width] == index).astype(np.uint8) * 255
            found.append((int(x), glyph))
    return sorted(found, key=lambda entry: entry[0])


def pedal_masks(crop: np.ndarray, channel: str) -> tuple[np.ndarray, np.ndarray]:
    b, g, r = cv2.split(crop.astype(np.int16))
    amber = (r > 120) & (g > 85) & (r - b > 65) & (g - b > 55) & (g > r * 0.48)
    if channel == "throttle":
        colored = (g > 85) & (g - r > 38) & (g - b > 35)
    else:
        colored = (r > 85) & (r - g > 45) & (r - b > 38)
    return colored | amber, amber


def estimate_pedal(crop: np.ndarray, channel: str, direction: str = "lr") -> tuple[float | None, float]:
    """Fit one contiguous colored prefix; white overprinted labels are ignored."""
    if crop.size == 0:
        return None, 0.0
    colored, _ = pedal_masks(crop, channel)
    occupancy = colored.mean(axis=0 if direction in ("lr", "rl") else 1)
    if direction in ("rl", "bt"):
        occupancy = occupancy[::-1]
    filled = np.clip(occupancy / 0.34, 0, 1)
    costs = np.r_[0, np.cumsum(1 - 2 * filled)]
    edge = int(np.argmin(costs))
    shape = np.arange(len(filled)) < edge
    error = float(np.abs(filled - shape).mean())
    if error > 0.23:
        return None, max(0.0, 1 - error * 3)
    return round(edge / len(filled) * 100, 1), round(max(0.0, 1 - error * 3), 3)


class HUDExtractor:
    def __init__(self, profile: dict | None = None):
        self.profile = validated_profile(profile)
        self._banks = {}
        for kind in ("speed", "gear", "lap_time", "rpm"):
            files = sorted((ASSETS / kind).glob("*.png"))
            if files:
                bank = np.stack([cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) for path in files]).astype(np.float32) / 255
                self._banks[kind] = ([path.stem.split("_")[0] for path in files], bank)
        self._anchor = cv2.imread(str(ASSETS / "labels.png"), cv2.IMREAD_GRAYSCALE)
        if self._anchor is None or "speed" not in self._banks or "gear" not in self._banks:
            raise RuntimeError("HUD templates are missing; run scripts/inspect_video.py --build-templates acc_video.mp4")
        self._anchor_variants = {"en": self._anchor}
        # Pedal captions are localized even when the overlay geometry and
        # numerals are unchanged. Keep language anchors behind the same
        # geometric and digit validation used for the English HUD.
        for language in ("de", "fr"):
            anchor = cv2.imread(str(ASSETS / f"labels_{language}.png"), cv2.IMREAD_GRAYSCALE)
            if anchor is not None:
                self._anchor_variants[language] = anchor
        self._layout_anchors = {"current": self._anchor_variants}
        legacy = cv2.imread(str(ASSETS / "labels_legacy_en.png"), cv2.IMREAD_GRAYSCALE)
        if legacy is not None:
            self._layout_anchors["legacy"] = {"en": legacy}
        self._layout = "legacy" if self.profile["hud_layout"] == "legacy" else "current"
        self._unit_templates = {}
        for unit, filename in (("km/h", "unit_kmh.png"), ("mph", "unit_mph.png")):
            template = cv2.imread(str(ASSETS / filename), cv2.IMREAD_GRAYSCALE)
            if template is not None:
                self._unit_templates[unit] = [cv2.resize(template, None, fx=scale, fy=scale)
                                              for scale in (0.9, 0.95, 1.0, 1.05)]
        self._hud_speed_unit = None
        self._unit_confidence = 0.0
        self._unit_detail_templates = {unit: [self._text_detail(template) for template in bank]
                                       for unit, bank in self._unit_templates.items()}
        # Downscaled/compressed uploads soften the tiny unit label much more
        # than the large speed digits. Match equally softened templates before
        # declaring the unit unreadable; keep the same confidence and margin.
        self._soft_unit_templates = {
            unit: [self._text_detail(cv2.GaussianBlur(template, (0, 0), sigma))
                   for template in bank for sigma in (0.8, 1.2)]
            for unit, bank in self._unit_templates.items()
        }
        self._lap_anchor = cv2.imread(str(ASSETS / "lap_labels.png"), cv2.IMREAD_GRAYSCALE)
        self._lap_anchors = {"current": self._lap_anchor,
                             "legacy": cv2.imread(str(ASSETS / "lap_labels_legacy.png"), cv2.IMREAD_GRAYSCALE)}
        self._rois = deepcopy(self.profile["rois"])
        self._last_search = None
        self._last_lap_search = None
        self._last_lap_revision = None
        self._calibration = {"status": "searching" if self.profile["auto_calibrate"] else "manual",
                             "confidence": 0.0, "scale": 1.0, "offset_x": 0.0, "offset_y": 0.0,
                             "revision": 0, "anchor": "en", "layout": self._layout}

    @property
    def calibration_status(self) -> dict:
        return deepcopy(self._calibration)

    def resolved_profile(self) -> dict:
        profile = deepcopy(self.profile)
        profile["rois"] = deepcopy(self._rois)
        return profile

    def _crop(self, frame: np.ndarray, name: str, rois: dict | None = None) -> np.ndarray:
        height, width = frame.shape[:2]
        x, y, w, h = (rois if rois is not None else self._rois)[name]
        left, top = min(width - 1, round(x * width)), min(height - 1, round(y * height))
        right = max(left + 1, min(width, round((x + w) * width)))
        bottom = max(top + 1, min(height, round((y + h) * height)))
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            raise ValueError(f"{name} ROI is smaller than one image pixel")
        if name in CANONICAL and crop.shape[1::-1] != CANONICAL[name]:
            crop = cv2.resize(crop, CANONICAL[name], interpolation=cv2.INTER_LINEAR)
        return crop

    def _anchor_score(self, frame: np.ndarray) -> tuple[float, str]:
        gray = cv2.cvtColor(self._crop(frame, "labels"), cv2.COLOR_BGR2GRAY)
        matches = [(float(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)[0, 0]), name)
                   for name, template in self._layout_anchors[self._layout].items()]
        return max(matches)

    def _candidate_valid(self, frame: np.ndarray, candidate: dict) -> bool:
        rois = dict(self._rois, **candidate)
        gain = self._instrument_brightness_gain(frame, rois)
        gear, gear_score = self._read(cv2.convertScaleAbs(self._crop(frame, "gear", rois), alpha=gain), "gear")
        speed, speed_score = self._read(cv2.convertScaleAbs(self._crop(frame, "speed", rois), alpha=gain), "speed")
        return (gear is not None and gear_score >= 0.88) or (speed is not None and speed_score >= 0.9)

    def _instrument_brightness_gain(self, frame: np.ndarray, rois: dict | None = None) -> float:
        """Recover a dim overlay only after its label anchor has matched.

        Video fades scale every HUD channel together. The fixed white/color
        thresholds otherwise drop visible digits and can report full throttle
        as zero. Require neutral highlights in both numeral regions, retain
        nearly-black frames as missing, and never amplify more than 2.5 times.
        Glyph geometry, shape confidence and unit checks still apply afterward.
        """
        levels = []
        for name in ("speed", "gear"):
            crop = self._crop(frame, name, rois).astype(np.int16)
            neutral = crop.min(axis=2)[np.ptp(crop, axis=2) < 35]
            if not neutral.size:
                return 1.0
            levels.append(float(np.percentile(neutral, 99.5)))
        if min(levels) < 96 or max(levels) >= 215:
            return 1.0
        return min(2.5, 240 / max(levels))

    def _update_calibration(self, frame: np.ndarray, timestamp: float) -> float:
        score, variant = self._anchor_score(frame)
        if not self.profile["auto_calibrate"]:
            self._calibration.update(status="manual", confidence=round(score, 3), anchor=variant)
            return score
        if score >= self.profile["minimum_anchor_score"]:
            self._calibration.update(status="locked", confidence=round(score, 3), anchor=variant,
                                     revision=max(1, self._calibration["revision"]))
            return score
        self._calibration.update(status="searching", confidence=0.0)
        due = self._last_search is None or timestamp < self._last_search or timestamp - self._last_search >= 0.75
        if not due:
            return score
        self._last_search = timestamp
        best = None
        layouts = self._layout_anchors if self.profile["hud_layout"] == "auto" else (self.profile["hud_layout"],)
        for layout in layouts:
            geometry = self.profile if layout == ("legacy" if self.profile["hud_layout"] == "legacy" else "current") else layout_profile(layout)
            for name, anchor in self._layout_anchors[layout].items():
                result = locate_group(frame, geometry, anchor, "labels", INSTRUMENTS,
                                      validate=lambda candidate: self._candidate_valid(frame, candidate))
                if result is not None and (best is None or result[1]["confidence"] > best[1]["confidence"]):
                    best = (result[0], dict(result[1], anchor=name, layout=layout))
        if best is None:
            return score
        changed = any(max(abs(a - b) for a, b in zip(self._rois[name], box)) > 0.001 for name, box in best[0].items())
        if best[1]["layout"] != self._layout or changed:
            self._hud_speed_unit = None
            self._unit_confidence = 0.0
        if best[1]["layout"] != self._layout:
            clock = layout_profile(best[1]["layout"])["rois"]
            self._rois.update({name: clock[name] for name in ("lap_labels", "lap_time")})
        self._layout = best[1]["layout"]
        self._rois.update(best[0])
        revision = self._calibration["revision"] + int(changed or self._calibration["revision"] == 0)
        self._calibration.update(best[1], status="locked", revision=revision)
        for key in ("confidence", "scale", "offset_x", "offset_y"):
            self._calibration[key] = round(float(self._calibration[key]), 5)
        return self._anchor_score(frame)[0]

    def _read_speed_unit(self, frame: np.ndarray) -> tuple[str | None, float]:
        if self.profile["speed_unit"] != "auto":
            return self.profile["speed_unit"], 1.0
        crop = cv2.cvtColor(self._crop(frame, "speed_unit"), cv2.COLOR_BGR2GRAY)
        scores = []
        for unit, templates in self._unit_templates.items():
            score = max(float(cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))[1])
                        for template in templates)
            scores.append((score, unit))
        scores.sort(reverse=True)
        if scores and scores[0][0] >= 0.72 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.1):
            self._unit_confidence, self._hud_speed_unit = scores[0]
        else:
            # The translucent overlay can cross large dashboard digits. Isolate
            # fine text detail without learning the dashboard underneath it.
            detail = self._text_detail(crop)
            detailed = sorted((max(float(cv2.minMaxLoc(cv2.matchTemplate(detail, template, cv2.TM_CCOEFF_NORMED))[1])
                                   for template in bank), unit)
                              for unit, bank in self._unit_detail_templates.items())
            if detailed and detailed[-1][0] >= 0.62 and (len(detailed) == 1 or detailed[-1][0] - detailed[-2][0] >= 0.12):
                self._unit_confidence, self._hud_speed_unit = detailed[-1]
            elif self._hud_speed_unit is None:
                softened = sorted((max(float(cv2.minMaxLoc(cv2.matchTemplate(detail, template, cv2.TM_CCOEFF_NORMED))[1])
                                       for template in bank), unit)
                                  for unit, bank in self._soft_unit_templates.items())
                if softened and softened[-1][0] >= 0.62 and (len(softened) == 1 or softened[-1][0] - softened[-2][0] >= 0.12):
                    self._unit_confidence, self._hud_speed_unit = softened[-1]
        return self._hud_speed_unit, self._unit_confidence

    @staticmethod
    def _text_detail(gray: np.ndarray) -> np.ndarray:
        gray = gray.astype(np.float32)
        return gray - cv2.GaussianBlur(gray, (0, 0), 1.2)

    def _calibrate_clock(self, frame: np.ndarray, timestamp: float) -> bool:
        anchor = self._lap_anchors[self._layout]
        if anchor is None or not self.profile["auto_calibrate"]:
            return True
        gray = cv2.cvtColor(self._crop(frame, "lap_labels"), cv2.COLOR_BGR2GRAY)
        score = float(cv2.matchTemplate(gray, anchor, cv2.TM_CCOEFF_NORMED)[0, 0])
        if score >= 0.52:
            return True
        if self._last_lap_revision == self._calibration["revision"] and self._last_lap_search is not None and 0 <= timestamp - self._last_lap_search < 5:
            return False
        self._last_lap_search = timestamp
        self._last_lap_revision = self._calibration["revision"]
        geometry = layout_profile(self._layout) if self._layout == "legacy" else self.profile
        candidate = locate_group(frame, geometry, anchor, "lap_labels", ("lap_labels", "lap_time"))
        if candidate is None or candidate[1]["confidence"] < 0.7:
            return False
        self._rois.update(candidate[0])
        return True

    def _read_mask(self, mask: np.ndarray, kind: str, minimum: float, margin: float,
                   valid_counts: tuple[int, ...]) -> tuple[str | None, float]:
        names, bank = self._banks[kind]
        pieces = glyph_components(mask, kind)
        if len(pieces) not in valid_counts:
            return None, 0.0
        if kind in ("speed", "rpm"):
            # Reject dropped leading glyphs rather than reading 226 as 6.
            first_x = pieces[0][0]
            if np.count_nonzero(mask[:, :max(0, first_x - 3)]) > (18 if kind == "speed" else 7):
                return None, 0.0
            if kind == "speed" and not 47 <= pieces[-1][0] <= 65:
                return None, 0.0
            if kind == "speed" and any(not 14 <= right[0] - left[0] <= 29 for left, right in zip(pieces, pieces[1:])):
                return None, 0.0
        bank_areas = bank.sum(axis=(1, 2))
        digits, scores = [], []
        for _, glyph in pieces:
            normalized = normalize_glyph(glyph).astype(np.float32) / 255
            intersection = np.minimum(bank, normalized).sum(axis=(1, 2))
            denominator = bank_areas + normalized.sum()
            similarities = 2 * intersection / np.maximum(denominator, 1)
            order = np.argsort(similarities)[::-1]
            index = int(order[0])
            score = float(similarities[index])
            # Different recordings of the same digit must not count as ambiguity.
            runner = next((float(similarities[i]) for i in order[1:] if names[i] != names[index]), 0.0)
            if score < minimum or score - runner < margin:
                return None, 0.0
            digits.append(names[index])
            scores.append(score)
        return "".join(digits), min(scores)

    def _read_speed_contrast(self, crop: np.ndarray) -> tuple[str | None, float]:
        """Separate thin HUD strokes from a broad bright background behind them.

        The translucent speed overlay can cross a white glove or dashboard.
        An opening removes narrow digit strokes and estimates that background;
        subtracting it restores their local contrast without inventing pixels.
        Keep stronger shape/margin checks and require all three digits, so this
        fallback cannot turn a damaged three-digit speed into a shorter value.
        Conflicting otherwise-valid readings remain missing.
        """
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        readings = []
        for size, thresholds in ((9, (75, 105, 135)), (13, (105, 135))):
            contrast = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, np.ones((size, size), np.uint8))
            for threshold in thresholds:
                mask = (contrast >= threshold).astype(np.uint8) * 255
                reading = self._read_mask(mask, "speed", 0.8, 0.05, (3,))
                if reading[0] is not None:
                    readings.append(reading)
        if len({value for value, _ in readings}) != 1:
            return None, 0.0
        return max(readings, key=lambda reading: reading[1])

    def _read(self, crop: np.ndarray, kind: str) -> tuple[str | None, float]:
        if kind not in self._banks:
            return None, 0.0
        valid_counts = {"speed": (1, 2, 3), "gear": (1,), "lap_time": (7,), "rpm": (1, 4, 5)}[kind]
        best = (None, 0.0)
        thresholds = (185, 165, 205) if kind == "rpm" else (205, 225, 185)
        for attempt, threshold in enumerate(thresholds + ((205, 185, 215) if kind == "speed" else ())):
            luminance = attempt >= 3
            if luminance:
                if best[0] is not None:
                    break
                # The translucent HUD can tint white numerals with the car's
                # dashboard color. Chroma rejection then splits real digits.
                # Require stronger shape agreement for a luminance-only retry.
                mask = (cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) >= threshold).astype(np.uint8) * 255
            else:
                mask = timer_mask(crop, threshold) if kind == "lap_time" else white_mask(crop, threshold)
            minimum = {"gear": 0.82, "rpm": 0.82, "lap_time": 0.76, "speed": 0.76}[kind]
            reading = self._read_mask(mask, kind, 0.8 if luminance else minimum,
                                      0.05 if luminance or kind == "rpm" else 0.025, valid_counts)
            if reading[0] is not None and reading[1] > best[1]:
                best = reading
            if best[1] > 0.92:
                break
        if kind == "speed" and best[0] is None:
            return self._read_speed_contrast(crop)
        return best

    def extract(self, frame_bgr: np.ndarray, timestamp: float) -> dict:
        started = perf_counter()
        output = {"time": float(timestamp), "brake": None, "throttle": None, "speed": None,
                  "gear": None, "rpm": None, "lap_time": None, "lap_time_seconds": None, "lap_valid": None,
                  "brake_highlighted": None, "throttle_highlighted": None,
                  "steering": None, "steering_unit": "hud_percent", "steering_status": "unavailable", "steering_confidence": 0.0,
                  "hud_layout": None, "hud_speed_unit": None,
                  "confidence": 0.0, "hud_visible": False, "channel_confidence": {}}
        if not isinstance(frame_bgr, np.ndarray) or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
            raise ValueError("Expected a BGR image with three channels")
        if min(frame_bgr.shape[:2]) < 120:
            raise ValueError("Video frame is too small to read the HUD")
        anchor_score = self._update_calibration(frame_bgr, timestamp)
        output["anchor_score"] = round(anchor_score, 3)
        if anchor_score >= self.profile["minimum_anchor_score"]:
            gain = self._instrument_brightness_gain(frame_bgr)
            if gain > 1:
                frame_bgr = cv2.convertScaleAbs(frame_bgr, alpha=gain)
            gear, gear_score = self._read(self._crop(frame_bgr, "gear"), "gear")
            speed, speed_score = self._read(self._crop(frame_bgr, "speed"), "speed")
            if gear is not None or speed is not None:
                output["hud_visible"] = True
                output["hud_layout"] = self._layout
                output["gear"] = gear
                unit, unit_score = self._read_speed_unit(frame_bgr)
                output["hud_speed_unit"] = unit
                output["channel_confidence"]["speed_unit"] = round(unit_score, 3)
                if speed is not None and unit is not None:
                    converted = int(speed) * (1.609344 if unit == "mph" else 1)
                    if converted <= 400:
                        output["speed"] = round(converted, 1)
                output["channel_confidence"].update(gear=round(gear_score, 3), speed=round(speed_score, 3))
                steering, steering_score = extract_steering(self._crop(frame_bgr, "steering")) if self._layout == "current" else (None, 0.0)
                output.update(steering=steering, steering_confidence=steering_score,
                              steering_status="tracked" if steering is not None else "unavailable")
                output["channel_confidence"]["steering"] = steering_score
                for channel in ("brake", "throttle"):
                    pedal_crop = self._crop(frame_bgr, channel)
                    value, score = estimate_pedal(pedal_crop, channel, self.profile[f"{channel}_direction"])
                    output[channel] = value
                    output["channel_confidence"][channel] = score
                    _, amber = pedal_masks(pedal_crop, channel)
                    output[f"{channel}_highlighted"] = bool(np.count_nonzero(amber) > 12) if value is not None else None
                timer_crop = self._crop(frame_bgr, "lap_time")
                timer, timer_score = self._read(timer_crop, "lap_time")
                if timer is None and self._calibrate_clock(frame_bgr, timestamp):
                    timer_crop = self._crop(frame_bgr, "lap_time")
                    timer, timer_score = self._read(timer_crop, "lap_time")
                if timer is not None and int(timer[2:4]) < 60:
                    output["lap_time"] = f"{timer[:2]}:{timer[2:4]}.{timer[4:]}"
                    output["lap_time_seconds"] = int(timer[:2]) * 60 + int(timer[2:4]) + int(timer[4:]) / 1000
                    timer_channels = timer_crop.astype(np.int16)
                    output["lap_valid"] = bool(np.count_nonzero(timer_channels[:, :, 2] - timer_channels[:, :, 1] > 55) < 30)
                output["channel_confidence"]["lap_time"] = round(timer_score, 3)
                rpm, rpm_score = self._read(self._crop(frame_bgr, "rpm"), "rpm")
                if rpm is not None and (rpm == "0" or 500 <= int(rpm) <= 15000):
                    output["rpm"] = int(rpm)
                output["channel_confidence"]["rpm"] = round(rpm_score, 3)
                output["confidence"] = round(min(anchor_score, max(gear_score, speed_score),
                    output["channel_confidence"]["brake"], output["channel_confidence"]["throttle"]), 3)
        output["calibration"] = self.calibration_status
        output["processing_ms"] = round((perf_counter() - started) * 1000, 3)
        return output
