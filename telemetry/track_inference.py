"""Conservative circuit suggestions from one complete lap's observed telemetry.

This is a heuristic among the bundled ACC layouts, not GPS, a reconstructed
racing line, or a calibrated probability. HUD steering has no universal steering
ratio, so only its signed, scale-independent turn pattern is compared. Inference
never writes recording identity or changes a user's manual circuit selection.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from .comparison import _prepare
from .session_context import session_context
from .tracks import list_tracks, resample_closed_path


SIGNATURE_SIZE = 512
MAX_LENGTH_ERROR = 0.04
MIN_TURN_SIMILARITY = 0.80
MIN_TURN_MARGIN = 0.12


def _smooth(values: np.ndarray, width: int = 2) -> np.ndarray:
    return sum(np.roll(values, shift) for shift in range(-width, width + 1)) / (2 * width + 1)


def _shape(values: np.ndarray) -> np.ndarray | None:
    values = _smooth(values)
    scale = float(np.percentile(np.abs(values), 90))
    if scale <= 1e-8:
        return None
    # Compress tight-corner amplitudes: car setup, steering ratio and racing line
    # change these much more than they change left/right turn order and spacing.
    shaped = np.sign(values) * np.sqrt(np.minimum(np.abs(values) / scale, 4))
    if min(np.mean(shaped > .35), np.mean(shaped < -.35)) < .025:
        return None
    shaped -= shaped.mean()
    norm = float(np.linalg.norm(shaped))
    return shaped / norm if norm > 1e-8 else None


@lru_cache(maxsize=1)
def _templates() -> tuple[dict, ...]:
    templates = []
    for track in list_tracks():
        rows = resample_closed_path(track["id"], SIGNATURE_SIZE)[:-1]
        xy = np.array([[point["x"], point["y"]] for point in rows])
        tangent = np.roll(xy, -2, axis=0) - np.roll(xy, 2, axis=0)
        before, after = np.roll(tangent, 2, axis=0), np.roll(tangent, -2, axis=0)
        # Map y increases downward, so positive cross products are right turns,
        # matching the existing signed HUD-steering channel.
        angles = np.arctan2(before[:, 0] * after[:, 1] - before[:, 1] * after[:, 0],
                            np.sum(before * after, axis=1))
        shape = _shape(angles)
        templates.append({"track_id": track["id"], "track_name": track["name"],
                          "length_m": track["length_m"], "shape": shape,
                          "fft": np.fft.rfft(shape) if shape is not None else None})
    return tuple(templates)


def _lap_basis(session: dict, bounds: dict, range_is_lap: bool) -> str | None:
    if range_is_lap:
        return "selected_complete_lap"
    for lap in (session.get("metadata") or {}).get("reviewed_laps", []):
        if _matching_bounds(lap, bounds):
            return "saved_reviewed_lap"
    for lap in session.get("laps", []):
        if lap.get("complete") is True and not lap.get("interrupted") and _matching_bounds(lap, bounds):
            return "detected_complete_lap"
    return None


def _matching_bounds(lap: dict, bounds: dict) -> bool:
    try:
        return all(abs(float(lap[key]) - bounds[key]) <= .15 for key in ("start_time", "end_time"))
    except (KeyError, TypeError, ValueError):
        return False


def _steering_shape(points: list[dict], distance: float) -> tuple[np.ndarray | None, dict]:
    duration = points[-1]["time"] - points[0]["time"]
    observed = 0.0
    units = set()
    usable = []
    for index, point in enumerate(points):
        if point["steering"] is not None:
            units.add(point["steering_unit"])
            usable.append(point)
            if index and points[index - 1]["steering"] is not None:
                observed += point["time"] - points[index - 1]["time"]
    coverage = observed / duration if duration > 0 else 0
    evidence = {"steering_coverage_pct": round(100 * coverage, 2), "turn_pattern_used": False}
    if coverage < .90 or len(usable) < 50 or len(units) != 1:
        return None, evidence
    distances = np.array([point["distance"] for point in usable])
    values = np.array([point["steering"] for point in usable])
    if (distances[0] > distance * .01 or distances[-1] < distance * .99
            or float(np.max(np.diff(distances))) > max(30, distance * .01)):
        return None, evidence
    if float(np.percentile(np.abs(values), 90)) < 3:
        evidence["turn_pattern_uninformative"] = True
        return None, evidence
    target = np.linspace(0, distance, SIGNATURE_SIZE, endpoint=False)
    shape = _shape(np.interp(target, distances, values))
    if shape is None:
        evidence["turn_pattern_uninformative"] = True
    else:
        evidence["turn_pattern_used"] = True
    return shape, evidence


def infer_track(samples: list[dict], session: dict | None = None,
                selection: dict | None = None, *, range_is_lap: bool = False) -> dict:
    """Suggest a catalog circuit only with enough independent distinguishing data.

    ``range_is_lap`` is the caller's explicit assertion that the selected range
    is one complete lap (a saved/detected lap or a user-confirmed full lap).
    Without that assertion, matching stored lap boundaries are required. Partial
    recordings, gaps, multiple laps, and merely similar track lengths abstain.
    """
    session = session or {}
    context = session_context(session)
    result = {"status": "insufficient", "track_id": None, "track_name": None,
              "source": "none", "confidence": "none", "auto_select": False,
              "reason": "Select one complete lap to estimate its circuit.",
              "evidence": {"method": "lap_length_and_signed_turn_pattern_v1",
                           "confidence_kind": "heuristic_not_calibrated_probability"},
              "candidates": []}
    if context["track_id"]:
        return {**result, "status": "labelled", "track_id": context["track_id"],
                "track_name": context["track_name"], "source": "recording_label",
                "confidence": "high", "auto_select": True,
                "reason": "Using the circuit label saved with this recording."}
    if session.get("status") == "recording":
        result["reason"] = "Finish the recording before estimating its circuit."
        return result
    if len(samples) < 2:
        result["reason"] = "This recording has too little telemetry to estimate a circuit."
        return result
    trace = _prepare(samples, selection)
    bounds, distance, duration = trace["selection"], trace["distance"], trace["duration"]
    evidence = result["evidence"]
    evidence.update(lap_distance_m=round(distance, 1), duration_s=round(duration, 3),
                    sample_count=len(trace["points"]), distance_complete=trace["distance_complete"],
                    distance_estimated=trace["distance_estimated"],
                    speed_coverage_pct=round(trace["channels"]["speed"]["coverage_pct"], 2))
    basis = _lap_basis(session, bounds, range_is_lap)
    evidence["lap_basis"] = basis
    if basis is None:
        return result
    if any(bounds.get("requested_" + key) is not None and abs(bounds["requested_" + key] - bounds[key]) > .05
           for key in ("start_time", "end_time")):
        result["reason"] = "The selected lap extends beyond the available telemetry."
        return result
    if (duration < 30 or duration > 1000 or len(trace["points"]) < 100
            or not trace["distance_complete"] or trace["has_gaps"]
            or trace["channels"]["speed"]["coverage_pct"] < 99):
        result["reason"] = "A complete lap with continuous, reliable speed telemetry is needed to estimate the circuit."
        return result
    if not 3000 <= distance <= 27000 or distance / duration < 10:
        result["reason"] = "This range does not have a plausible complete ACC circuit distance."
        return result
    shape, steering_evidence = _steering_shape(trace["points"], distance)
    evidence.update(steering_evidence)
    shape_fft = np.fft.rfft(shape) if shape is not None else None
    candidates = []
    for template in _templates():
        length_error = abs(distance - template["length_m"]) / template["length_m"]
        if length_error > MAX_LENGTH_ERROR:
            continue
        similarity = None
        if shape_fft is not None and template["fft"] is not None:
            # Unknown footage origin is allowed; driving direction is not
            # reversed and left/right steering is never silently inverted.
            correlation = np.fft.irfft(shape_fft * np.conj(template["fft"]), n=SIGNATURE_SIZE)
            similarity = float(np.max(correlation))
        length_score = max(0, 1 - length_error / MAX_LENGTH_ERROR)
        score = .75 * max(0, similarity) + .25 * length_score if similarity is not None else length_score
        candidates.append({"track_id": template["track_id"], "track_name": template["track_name"],
                           "length_m": template["length_m"], "length_error_pct": round(length_error * 100, 2),
                           "turn_similarity": round(similarity, 4) if similarity is not None else None,
                           "score": round(score, 4)})
    candidates.sort(key=lambda item: (-item["score"], item["length_error_pct"], item["track_id"]))
    result["candidates"] = candidates[:5]
    if not candidates:
        result["reason"] = "The observed lap distance does not closely match a supported ACC layout. Choose the circuit manually."
        return result
    result.update(status="ambiguous", reason="Several circuits or uncertain telemetry could explain this lap. Choose the circuit manually.")
    best = candidates[0]
    if shape is not None:
        alternatives = [item["turn_similarity"] for item in candidates[1:] if item["turn_similarity"] is not None]
        runner = max(alternatives, default=0)
        evidence["turn_similarity_margin"] = round((best["turn_similarity"] or 0) - runner, 4)
        accepted = (best["turn_similarity"] is not None and best["turn_similarity"] >= MIN_TURN_SIMILARITY
                    and best["turn_similarity"] - runner >= MIN_TURN_MARGIN)
        reason = "Lap distance and the signed steering pattern strongly match this circuit. This remains an estimate; you can change it."
    else:
        # Distance alone is useful only for genuinely isolated lengths. Do not
        # choose between the many ACC circuits separated by just a few metres.
        other_errors = sorted(abs(distance - item["length_m"]) / item["length_m"]
                              for item in _templates() if item["track_id"] != best["track_id"])
        accepted = (best["length_error_pct"] <= 1.5 and other_errors[0] >= .04
                    and not evidence.get("turn_pattern_uninformative"))
        reason = "The complete lap distance closely matches one supported circuit and is well separated from the others. Verify this estimate or choose another circuit."
    if accepted:
        result.update(status="matched", track_id=best["track_id"], track_name=best["track_name"],
                      source="telemetry", confidence="high", auto_select=True, reason=reason)
    return result
