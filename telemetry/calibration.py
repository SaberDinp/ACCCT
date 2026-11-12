"""Occasional multiscale template search for an ACC HUD instrument group."""

from copy import deepcopy

import cv2
import numpy as np


INSTRUMENTS = ("labels", "brake", "throttle", "speed", "gear", "rpm", "steering", "speed_unit")


def transformed_rois(profile: dict, anchor_name: str, found: tuple[float, float, float], names) -> dict | None:
    left, top, physical_scale = found
    frame_width, frame_height = profile["_frame_size"]
    reference_width, reference_height = profile["reference_width"], profile["reference_height"]
    anchor = profile["rois"][anchor_name]
    rois = {}
    for name in names:
        x, y, width, height = profile["rois"][name]
        box = [(left + (x - anchor[0]) * reference_width * physical_scale) / frame_width,
               (top + (y - anchor[1]) * reference_height * physical_scale) / frame_height,
               width * reference_width * physical_scale / frame_width,
               height * reference_height * physical_scale / frame_height]
        if box[0] < 0 or box[1] < 0 or box[0] + box[2] > 1.0001 or box[1] + box[3] > 1.0001:
            return None
        rois[name] = box
    return rois


def locate_group(frame: np.ndarray, profile: dict, anchor: np.ndarray, anchor_name: str,
                 names, validate=None) -> tuple[dict, dict] | None:
    """Find coarse candidates at reduced resolution, verify with full-size pixels."""
    frame_height, frame_width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 704 keeps the small pedal-label anchor legible while bounding the
    # expensive full-frame search. Final matching still uses source pixels.
    reduction = min(1.0, 704 / frame_width)
    reduced = cv2.resize(gray, None, fx=reduction, fy=reduction, interpolation=cv2.INTER_AREA)
    base_scale = frame_width / profile["reference_width"]
    candidates = []
    for relative_scale in (0.6, 0.75, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6, 1.85):
        physical_scale = base_scale * relative_scale
        width, height = max(8, round(anchor.shape[1] * physical_scale * reduction)), max(8, round(anchor.shape[0] * physical_scale * reduction))
        if width >= reduced.shape[1] or height >= reduced.shape[0]:
            continue
        template = cv2.resize(anchor, (width, height), interpolation=cv2.INTER_AREA)
        matches = cv2.matchTemplate(reduced, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(matches)
        if score > 0.42:
            candidates.append((score, location[0] / reduction, location[1] / reduction, physical_scale))
    candidates.sort(reverse=True)
    result = None
    contextual = dict(profile, _frame_size=(frame_width, frame_height))
    for coarse_score, coarse_x, coarse_y, coarse_scale in candidates[:4]:
        for physical_scale in np.linspace(coarse_scale * 0.92, coarse_scale * 1.08, 9):
            width, height = round(anchor.shape[1] * physical_scale), round(anchor.shape[0] * physical_scale)
            if min(width, height) < 10:
                continue
            margin = max(5, round(10 * base_scale))
            left, top = max(0, round(coarse_x) - margin), max(0, round(coarse_y) - margin)
            right, bottom = min(frame_width, round(coarse_x) + width + margin), min(frame_height, round(coarse_y) + height + margin)
            if right - left < width or bottom - top < height:
                continue
            template = cv2.resize(anchor, (width, height), interpolation=cv2.INTER_LINEAR)
            matches = cv2.matchTemplate(gray[top:bottom, left:right], template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(matches)
            if score < 0.64 or (result is not None and score <= result[1]["confidence"]):
                continue
            found = (left + location[0], top + location[1], float(physical_scale))
            rois = transformed_rois(contextual, anchor_name, found, names)
            if rois is None or (validate is not None and not validate(rois)):
                continue
            original = profile["rois"][anchor_name]
            relative_scale = physical_scale / base_scale
            metrics = {"confidence": float(score), "scale": float(relative_scale),
                       "offset_x": found[0] / frame_width - original[0] * relative_scale,
                       "offset_y": found[1] / frame_height - original[1] * physical_scale * profile["reference_height"] / frame_height}
            result = (deepcopy(rois), metrics)
    return result
