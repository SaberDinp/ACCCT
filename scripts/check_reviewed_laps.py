"""Audit the two requested recordings and reviewed lap alignment without writes.

Comparison and standalone trace POSTs are read-only. Optional inspected landmarks
are evaluated as holdouts; they are never added to the registration by this check.
"""
import argparse
from bisect import bisect_left
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx


EXPECTED_VIDEOS = {"c0SJgXUahEM", "x-RaRVP00r4"}


def video_id(url):
    parsed = urlparse(url or "")
    return parsed.path.strip("/") if parsed.hostname == "youtu.be" else parse_qs(parsed.query).get("v", [None])[0]


def request(client, method, path, body=None):
    response = client.request(method, path, json=body) if body is not None else client.request(method, path)
    response.raise_for_status()
    return response.json()


def selected_lap(recording):
    metadata = recording["session"]["metadata"]
    defaults = metadata["comparison_defaults"]
    lap = next(lap for lap in metadata["reviewed_laps"] if lap["id"] == defaults["range_id"])
    assert lap["boundaries_verified"] is True, lap
    selection = {"start_time": lap["start_time"], "end_time": lap["end_time"]}
    if lap.get("segment_id") is not None:
        selection["segment_id"] = lap["segment_id"]
    return lap, selection


def source_time_at(points, side, distance):
    right = bisect_left([point["position"] for point in points], distance)
    if right == 0 or points[right]["position"] == distance:
        return points[right][side]["time"]
    before, after = points[right - 1], points[right]
    fraction = (distance - before["position"]) / (after["position"] - before["position"])
    return before[side]["time"] + fraction * (after[side]["time"] - before[side]["time"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--landmarks", type=Path, help="JSON array of independently inspected paired source timestamps")
    parser.add_argument("--max-residual-m", type=float, help="Optional maximum endpoint-only holdout error")
    parser.add_argument("--output", type=Path, default=Path("artifacts/reviewed-lap-audit.json"))
    args = parser.parse_args()
    holdouts = json.loads(args.landmarks.read_text()) if args.landmarks else []
    if isinstance(holdouts, dict):
        holdouts = holdouts.get("holdouts", [])
    with httpx.Client(base_url=args.url, timeout=60) as client:
        before_status = request(client, "GET", "/api/status")
        library = request(client, "GET", "/api/sessions")
        assert len(library) == 2, f"Expected only the two requested recordings, found {len(library)}"
        assert {video_id(session["source"].get("original_url")) for session in library} == EXPECTED_VIDEOS
        recordings, digests, laps, selections, identifiers = {}, {}, {}, {}, {}
        for summary in library:
            identifier = summary["id"]
            response = client.get(f"/api/sessions/{identifier}")
            response.raise_for_status()
            recording = response.json()
            assert recording["session"]["status"] == "completed"
            role = recording["session"]["metadata"].get("role", recording["session"]["source"].get("role"))
            assert role in {"current", "reference"} and role not in recordings, role
            recordings[role], identifiers[role] = recording, identifier
            digests[role] = hashlib.sha256(response.content).hexdigest()
            laps[role], selections[role] = selected_lap(recording)
        assert laps["current"]["start_marker"] == laps["reference"]["start_marker"]
        assert laps["current"]["end_marker"] == laps["reference"]["end_marker"]
        body = {f"{side}_session_id": identifier for side, identifier in identifiers.items()}
        body.update({f"{side}_range": selection for side, selection in selections.items()})
        body.update(axis="distance", alignment_mode="lap_endpoints", align_start=True, align_end=True)
        result = request(client, "POST", "/api/compare", body)
        endpoints = result
        reference_markers = {marker["marker_id"]: marker for marker in laps["reference"].get("landmarks", [])}
        paired_markers = [{"label": marker.get("label", marker["marker_id"]), "current_time": marker["time"],
                           "reference_time": reference_markers[marker["marker_id"]]["time"]}
                          for marker in laps["current"].get("landmarks", []) if marker["marker_id"] in reference_markers]
        if paired_markers:
            result = request(client, "POST", "/api/compare", {**body, "alignment_mode": "landmarks", "landmarks": paired_markers})
        expected_delta = (laps["current"]["end_time"] - laps["current"]["start_time"] -
                          laps["reference"]["end_time"] + laps["reference"]["start_time"])
        assert result["delta_available"]
        assert math.isclose(result["points"][0]["delta_time_s"], 0, abs_tol=1e-8)
        assert math.isclose(result["points"][-1]["delta_time_s"], expected_delta, abs_tol=1e-8)
        channels = ("speed", "brake", "throttle", "gear", "steering")
        observed = {}
        for side in ("current", "reference"):
            trace = result[side]
            assert not trace["has_gaps"] and trace["distance_complete"]
            samples = [point[side] for point in result["points"] if point[side] is not None]
            assert len(samples) == len(result["points"])
            assert all(selections[side]["start_time"] - 1e-8 <= point["time"] <= selections[side]["end_time"] + 1e-8
                       for point in samples)
            observed[side] = {channel: 100 * sum(point[channel] is not None for point in samples) / len(samples)
                              for channel in channels}
            assert all(coverage > 95 for coverage in observed[side].values()), observed[side]
        scale = endpoints["alignment"]["scales"][0]
        residuals = []
        for marker in holdouts:
            distances = {}
            for side in ("current", "reference"):
                stamp = marker[f"{side}_time"]
                assert selections[side]["start_time"] < stamp < selections[side]["end_time"]
                prefix = request(client, "POST", "/api/trace", {
                    "session_id": identifiers[side], "range": {**selections[side], "end_time": stamp},
                    "axis": "distance", "max_points": 10})
                distances[side] = prefix["current"]["raw_distance"]
            residual = distances["current"] * scale - distances["reference"]
            predicted = source_time_at(endpoints["points"], "current", distances["reference"])
            residuals.append({**marker, "reference_distance_m": distances["reference"],
                              "current_raw_distance_m": distances["current"],
                              "endpoint_registered_residual_m": residual,
                              "current_predicted_crossing_s": predicted,
                              "endpoint_time_residual_s": predicted - marker["current_time"],
                              "used_as_endpoint_registration_anchor": False})
            if args.max_residual_m is not None:
                assert abs(residual) <= args.max_residual_m, residuals[-1]
        after_status = request(client, "GET", "/api/status")
        assert (before_status["state"], before_status["session_id"]) == (after_status["state"], after_status["session_id"])
        for side, identifier in identifiers.items():
            response = client.get(f"/api/sessions/{identifier}")
            response.raise_for_status()
            assert hashlib.sha256(response.content).hexdigest() == digests[side], f"{side} recording changed during audit"
    report = {"session_ids": identifiers, "reviewed_laps": laps, "selected_ranges": selections,
              "delta_available": result["delta_available"], "finish_delta_s": result["points"][-1]["delta_time_s"],
              "default_alignment_mode": result["alignment"]["mode"],
              "endpoint_current_distance_scale": scale,
              "raw_distance_m": {side: result[side]["raw_distance"] for side in recordings},
              "channel_observed_pct": observed, "independent_holdouts": residuals,
              "saved_recordings_unchanged": True, "source_controls_mutated": False,
              "caveat": "Observed coverage is not accuracy. Delta and distance remain video-derived estimates; marker times retain review uncertainty."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
