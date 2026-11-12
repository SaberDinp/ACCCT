"""Normalized coordinates for car-independent ACC overlay families."""

from copy import deepcopy


def _roi(x: int, y: int, width: int, height: int) -> list[float]:
    return [x / 1920, y / 1080, width / 1920, height / 1080]


_DEFAULT = {
    "name": "ACC automatic HUD",
    "reference_width": 1920,
    "reference_height": 1080,
    "rois": {
        "throttle": _roi(1758, 1009, 144, 15),
        "brake": _roi(1758, 1027, 144, 15),
        "speed": _roi(1768, 934, 78, 40),
        "gear": _roi(1682, 886, 78, 101),
        "labels": _roi(1711, 1009, 44, 33),
        "lap_time": _roi(70, 56, 168, 38),
        "rpm": _roi(1840, 844, 47, 22),
        "steering": _roi(1704, 991, 204, 17),
        "lap_labels": _roi(43, 104, 42, 79),
        "speed_unit": _roi(1768, 908, 64, 30),
    },
    "throttle_direction": "lr",
    "brake_direction": "lr",
    "speed_unit": "auto",
    "hud_layout": "auto",
    "minimum_anchor_score": 0.55,
    "auto_calibrate": True,
}

_LEGACY_ROIS = {
    "labels": _roi(1700, 1003, 44, 33),
    "throttle": _roi(1743, 1003, 151, 15),
    "brake": _roi(1743, 1020, 151, 15),
    "speed": _roi(1763, 934, 78, 40),
    "gear": _roi(1682, 886, 78, 101),
    "rpm": _roi(1830, 843, 47, 22),
    "speed_unit": _roi(1762, 908, 64, 30),
    "lap_time": _roi(49, 56, 168, 38),
    "lap_labels": _roi(27, 109, 42, 79),
}


def default_profile() -> dict:
    """Return independent normalized [x, y, width, height] regions."""
    return deepcopy(_DEFAULT)


def layout_profile(layout: str) -> dict:
    """Return geometry, not car-specific assumptions or steering inference."""
    profile = default_profile()
    if layout not in ("current", "legacy"):
        raise ValueError("Unknown ACC HUD layout")
    profile["hud_layout"] = layout
    if layout == "legacy":
        profile["rois"].update(deepcopy(_LEGACY_ROIS))
    return profile


def validated_profile(overrides: dict | None = None) -> dict:
    profile = default_profile()
    if overrides is None:
        return profile
    if not isinstance(overrides, dict):
        raise ValueError("HUD profile must be an object")
    if overrides.get("hud_layout") == "legacy":
        profile = layout_profile("legacy")
    for key, value in overrides.items():
        if key == "rois":
            if not isinstance(value, dict):
                raise ValueError("rois must be an object")
            profile["rois"].update(deepcopy(value))
        elif key in profile:
            profile[key] = deepcopy(value)
    if not isinstance(profile["name"], str) or not 1 <= len(profile["name"].strip()) <= 120:
        raise ValueError("Profile name must be a nonempty string up to 120 characters")
    if not isinstance(profile["auto_calibrate"], bool):
        raise ValueError("auto_calibrate must be a boolean")
    for dimension in ("reference_width", "reference_height"):
        value = profile[dimension]
        if isinstance(value, bool) or not isinstance(value, int) or not 240 <= value <= 8192:
            raise ValueError(f"{dimension} must be an integer between 240 and 8192")
    for key, box in profile["rois"].items():
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError(f"{key}: ROI needs [x, y, width, height]")
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not 0 <= v <= 1 for v in box):
            raise ValueError(f"{key}: ROI values must be normalized between 0 and 1")
        x, y, w, h = box
        if w <= 0 or h <= 0 or x + w > 1.00001 or y + h > 1.00001:
            raise ValueError(f"{key}: ROI must fit inside the frame")
        if w * profile["reference_width"] < 3 or h * profile["reference_height"] < 3:
            raise ValueError(f"{key}: ROI must span at least 3 pixels in each reference dimension")
    for channel in ("brake", "throttle"):
        if profile[f"{channel}_direction"] not in ("lr", "rl", "bt", "tb"):
            raise ValueError("Pedal direction must be lr, rl, bt, or tb")
    if profile["speed_unit"] not in ("auto", "km/h", "mph"):
        raise ValueError("speed_unit must be auto, km/h or mph")
    if profile["hud_layout"] not in ("auto", "current", "legacy"):
        raise ValueError("hud_layout must be auto, current or legacy")
    if isinstance(profile["minimum_anchor_score"], bool) or not isinstance(profile["minimum_anchor_score"], (int, float)) or not 0.3 <= profile["minimum_anchor_score"] <= 1:
        raise ValueError("minimum_anchor_score must be between 0.3 and 1")
    return profile
