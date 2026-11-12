"""User-owned recording identity and conservative cross-car comparison context."""

from __future__ import annotations

import math


FIELDS = {"car_id", "track_id", "platform", "game_version", "conditions", "bop"}
PLATFORMS = {"pc", "ps5", "xbox_series"}


def validate_context(value: dict) -> dict:
    """Validate a complete replacement; never infer car or circuit from a title."""
    from .catalog import get_car, get_circuit

    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError("Recording context must contain car_id, track_id, platform, game_version, conditions or bop.")
    result = {}
    for key in FIELDS:
        item = value.get(key)
        if item is not None and not isinstance(item, str):
            raise ValueError(f"{key} must be text or null.")
        item = item.strip() if item is not None else None
        item = item or None
        if item and (len(item) > 160 or any(ord(character) < 32 for character in item)):
            raise ValueError(f"{key} must contain at most 160 printable characters.")
        result[key] = item
    if result["car_id"]:
        get_car(result["car_id"])
    if result["track_id"]:
        get_circuit(result["track_id"])
    if result["platform"] and result["platform"] not in PLATFORMS:
        raise ValueError("Platform must be pc, ps5 or xbox_series.")
    return result


def session_context(session: dict) -> dict:
    from .catalog import get_car, get_circuit

    metadata = session.get("metadata") or {}
    stored = metadata.get("telemetry_context") or {}
    result = {key: stored.get(key) for key in FIELDS}
    for key, name, lookup in (("car_id", "car_name", get_car), ("track_id", "track_name", get_circuit)):
        try:
            result[name] = lookup(result[key])["name"] if result[key] else None
        except (ValueError, TypeError, KeyError):
            result[key] = result[name] = None
    result["identity_source"] = "user" if any(result[key] for key in FIELDS) else "unknown"
    return result


def comparison_context(current: dict, reference: dict, confirm_same_track: bool = False) -> dict:
    a, b = session_context(current), session_context(reference)
    known = bool(a["track_id"] and b["track_id"])
    if known and a["track_id"] != b["track_id"]:
        raise ValueError("Comparison requires the same circuit layout. These recordings have different track labels.")
    same_track = known or confirm_same_track is True
    different_cars = bool(a["car_id"] and b["car_id"] and a["car_id"] != b["car_id"])
    warnings = []
    if not same_track:
        warnings.append("Circuit identity is unknown. Delta is an approximate comparison of the selected ranges; verify that they show the same circuit layout.")
    elif not known:
        warnings.append("Same circuit layout is user-confirmed; one or both recordings still have no circuit label.")
    if not a["car_id"] or not b["car_id"]:
        warnings.append("One or both car variants are unknown. Car-specific conclusions are unavailable.")
    warnings.append("Observed differences include driver, setup, tyres, fuel, weather, game version and Balance of Performance; they do not establish an intrinsic car ranking.")
    if different_cars:
        warnings.append("Different cars: gear numbers reflect different gearing, not equivalent wheel torque or speed.")
        warnings.append("Steering HUD percentages describe each video's input display, not matched road-wheel angles or steering ratios.")
    for field, label in (("platform", "Platforms"), ("game_version", "Game versions"), ("conditions", "Conditions"), ("bop", "Balance of Performance labels")):
        if a[field] and b[field] and a[field] != b[field]:
            warnings.append(f"{label} differ between the recordings.")
    return {"current": a, "reference": b, "same_track": same_track,
            "track_basis": "user_labels" if known else "user_confirmation" if same_track else "unknown",
            "different_cars": different_cars, "warnings": warnings}


def _finite(value) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def observed_segments(result: dict, context: dict) -> dict:
    """Use paired physical-marker timestamps, never speed/pedal-pattern warping."""
    unavailable = {"available": False, "segments": []}
    if not context["same_track"]:
        return {**unavailable, "reason": "Same circuit layout is unconfirmed."}
    alignment = result.get("alignment") or {}
    anchors = alignment.get("anchors") or []
    if not (result.get("delta_available") and alignment.get("applied") and alignment.get("mode") == "landmarks" and len(anchors) >= 3):
        return {**unavailable, "reason": "Segment differences need continuous observations and confirmed paired track landmarks."}
    if alignment.get("max_correction_pct", 0) > 10:
        return {**unavailable, "reason": "Distance correction exceeds 10%; verify speed readings and physical markers first."}
    for side in ("current", "reference"):
        trace = result.get(side) or {}
        if trace.get("has_gaps") or not trace.get("distance_complete") or trace.get("channels", {}).get("speed", {}).get("coverage_pct", 0) < 90:
            return {**unavailable, "reason": "Segment differences need at least 90% observed speed coverage without discontinuities."}
    segments = []
    for before, after in zip(anchors, anchors[1:]):
        if not all(_finite(anchor.get(side + "_time")) for anchor in (before, after) for side in ("current", "reference")):
            return {**unavailable, "reason": "A physical marker has no valid timestamp."}
        current = after["current_time"] - before["current_time"]
        reference = after["reference_time"] - before["reference_time"]
        if min(current, reference) <= 0:
            return {**unavailable, "reason": "Physical markers must increase in both recordings."}
        segments.append({"start_label": before.get("label", "Marker"), "end_label": after.get("label", "Marker"),
                         "start_position": before.get("reference_distance_m"),
                         "end_position": after.get("reference_distance_m"),
                         "current_seconds": current, "reference_seconds": reference,
                         "delta_seconds": current - reference})
    return {"available": True, "segments": segments, "basis": "user_confirmed_physical_markers",
            "reason": "Observed marker-to-marker times, not a car ranking. Marker timing uncertainty is not measured."}


def enrich_comparison(result: dict, current: dict, reference: dict, confirm_same_track: bool = False) -> dict:
    context = comparison_context(current, reference, confirm_same_track)
    result["context"] = context
    result.setdefault("warnings", []).extend(context["warnings"])
    result["observed_segments"] = observed_segments(result, context)
    return result
