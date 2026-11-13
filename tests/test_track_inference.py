"""Circuit recognition must distinguish real turn order and abstain on weak data."""
from __future__ import annotations

import copy
from functools import lru_cache

from fastapi.testclient import TestClient
import numpy as np
import pytest

from main import create_app
from telemetry.track_inference import infer_track
from telemetry.tracks import get_track, list_tracks


@lru_cache(maxsize=32)
def geometric_lap(track_id, phase=0.0):
    """Generate a bicycle-model steering trace independently of the matcher.

    Dense geometry gradients produce a physical wheel angle. Corner-limited
    speed, understeer, HUD scaling and measurement noise then produce telemetry;
    the recognizer's signature generator is deliberately not used here.
    """
    track = get_track(track_id)
    distance = np.linspace(0, track["length_m"], 3073)
    progress = (distance + phase * track["length_m"]) % track["length_m"]
    points = track["points"]
    x = np.interp(progress, [p["distance_m"] for p in points], [p["x"] for p in points])
    y = np.interp(progress, [p["distance_m"] for p in points], [p["y"] for p in points])
    heading = np.unwrap(np.arctan2(np.gradient(y), np.gradient(x)))
    curvature = np.gradient(heading, distance)
    curvature = np.convolve(np.pad(curvature, (8, 8), mode="wrap"), np.ones(17) / 17, mode="valid")
    speed = np.clip(np.sqrt(13 / np.maximum(abs(curvature), .001)), 20, 80)
    speed = np.convolve(np.pad(speed, (12, 12), mode="wrap"), np.ones(25) / 25, mode="valid")
    times = np.r_[0, np.cumsum(np.diff(distance) / ((speed[1:] + speed[:-1]) / 2))]
    noise = np.random.default_rng(123).normal(0, .5, len(distance))
    steering = np.degrees(np.arctan(2.6 * curvature)) * 14 / 4.5 * (1 + speed * speed / 20000) + noise
    return [{"time": float(stamp), "speed": float(velocity * 3.6),
             "steering": float(np.clip(angle, -99, 99)), "steering_unit": "hud_percent",
             "steering_confidence": .95, "steering_status": "tracked", "active": True,
             "hud_visible": True, "confidence": .95, "segment_id": 1}
            for stamp, velocity, angle in zip(times, speed, steering)]


@pytest.mark.parametrize("track_id", [track["id"] for track in list_tracks()])
def test_geometry_derived_laps_identify_all_supported_layouts(track_id):
    result = infer_track(geometric_lap(track_id), range_is_lap=True)
    assert result["track_id"] == track_id, result
    assert result["status"] == "matched" and result["auto_select"]
    assert result["source"] == "telemetry"
    assert result["evidence"]["turn_pattern_used"]
    assert result["evidence"]["confidence_kind"] == "heuristic_not_calibrated_probability"


@pytest.mark.parametrize("steering_gain,expected", [(.8, "monza"), (.55, None)])
def test_shifted_noisy_steering_disambiguates_only_above_its_noise_floor(steering_gain, expected):
    samples = copy.deepcopy(geometric_lap("monza", phase=.237))
    rng = np.random.default_rng(456)
    for sample in samples:
        # Monza's biased observed length equals Paul Ricard's nominal length.
        sample["speed"] *= 5770 / 5793
        sample["steering"] = sample["steering"] * steering_gain + float(rng.normal(0, 1))
    result = infer_track(samples, range_is_lap=True)
    assert result["track_id"] == expected, result
    if expected is None:
        assert result["status"] == "ambiguous" and not result["auto_select"]
    assert next(c for c in result["candidates"] if c["track_id"] == "paul_ricard")["length_error_pct"] == 0


def test_similar_lengths_without_trusted_steering_remain_ambiguous():
    samples = copy.deepcopy(geometric_lap("monza"))
    for sample in samples:
        sample["steering_confidence"] = .3
    result = infer_track(samples, range_is_lap=True)
    assert result["status"] == "ambiguous" and result["track_id"] is None
    assert not result["auto_select"]
    assert {"monza", "paul_ricard", "suzuka"} <= {c["track_id"] for c in result["candidates"]}


def test_unique_complete_lap_length_can_help_when_steering_is_unavailable():
    samples = copy.deepcopy(geometric_lap("spa"))
    for sample in samples:
        sample["steering"] = None
    result = infer_track(samples, range_is_lap=True)
    assert result["track_id"] == "spa" and result["auto_select"]
    assert not result["evidence"]["turn_pattern_used"]
    assert "distance" in result["reason"]


@pytest.mark.parametrize("change", ["reverse_sign", "shuffle", "flat"])
def test_incompatible_or_uninformative_turn_patterns_do_not_auto_select(change):
    samples = copy.deepcopy(geometric_lap("spa"))
    values = np.array([s["steering"] for s in samples])
    if change == "reverse_sign":
        values *= -1
    elif change == "shuffle":
        np.random.default_rng(789).shuffle(values)
    else:
        values[:] = 8
    for sample, value in zip(samples, values):
        sample["steering"] = float(value)
    result = infer_track(samples, range_is_lap=True)
    assert not result["auto_select"] and result["track_id"] is None, result


@pytest.mark.parametrize("fault", ["hud_gap", "speed_gap", "segment_cut", "short_range", "outside_range"])
def test_incomplete_or_discontinuous_laps_do_not_claim_a_circuit(fault):
    samples = copy.deepcopy(geometric_lap("spa"))
    selection = None
    if fault == "hud_gap":
        samples[1000]["active"] = False
    elif fault == "speed_gap":
        for sample in samples[1000:1050]:
            sample["speed"] = None
    elif fault == "segment_cut":
        for sample in samples[1000:]:
            sample["segment_id"] = 2
    elif fault == "short_range":
        selection = {"start_time": 0, "end_time": 10}
    else:
        selection = {"start_time": -10, "end_time": samples[-1]["time"]}
    result = infer_track(samples, selection=selection, range_is_lap=True)
    assert result["status"] == "insufficient" and not result["auto_select"]
    assert result["track_id"] is None


def test_whole_lap_assertion_or_matching_saved_lap_is_required():
    samples = geometric_lap("spa")
    assert infer_track(samples)["status"] == "insufficient"
    lap = {"start_time": samples[0]["time"], "end_time": samples[-1]["time"], "complete": True}
    session = {"laps": [lap]}
    result = infer_track(samples, session)
    assert result["track_id"] == "spa"
    assert result["evidence"]["lap_basis"] == "detected_complete_lap"
    # A custom fragment of a detected lap is not silently treated as a full lap.
    partial = infer_track(samples, session, {"start_time": 10, "end_time": samples[-1]["time"]})
    assert partial["status"] == "insufficient" and partial["track_id"] is None
    lap["complete"] = False
    assert infer_track(samples, session)["status"] == "insufficient"


def test_explicit_recording_context_wins_and_inference_is_read_only():
    samples = geometric_lap("spa")
    session = {"metadata": {"telemetry_context": {"track_id": "monza"}}, "source": {"name": "Spa"}}
    previous = copy.deepcopy((samples, session))
    result = infer_track(samples, session, range_is_lap=True)
    assert result["status"] == "labelled" and result["track_id"] == "monza"
    assert result["source"] == "recording_label" and result["track_name"] == "Monza"
    assert (samples, session) == previous
    # Names and URLs are never used as a substitute for measured evidence.
    assert infer_track([], {"source": {"name": "Monza", "original_url": "https://example.test/monza"}})["track_id"] is None


def test_inference_endpoint_returns_estimate_without_editing_saved_identity(tmp_path):
    with TestClient(create_app(root=tmp_path)) as client:
        store = client.app.state.engine.store
        samples = geometric_lap("kyalami")
        identity = store.create_session({"kind": "file", "path": "fixture.mp4"})
        for sample in samples:
            store.add_sample(identity, sample)
        store.save_summary(identity, {}, status="completed")
        before = store.get_session(identity)
        body = {"session_id": identity, "range": {"start_time": 0, "end_time": samples[-1]["time"]},
                "range_is_lap": True}
        response = client.post("/api/track-inference", json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["track_id"] == "kyalami" and result["track_name"] == "Kyalami"
        assert store.get_session(identity) == before
        assert store.get_samples(identity) == samples
        assert client.post("/api/track-inference", json={**body, "session_id": "missing"}).status_code == 404
        assert client.post("/api/track-inference", json={**body, "range_is_lap": "true"}).status_code == 422
        invalid = {**body, "range": {"start_time": 0, "end_time": 20, "start_index": 0}}
        assert client.post("/api/track-inference", json=invalid).status_code == 400
        assert client.post("/api/track-inference", json=body, headers={"Origin": "https://untrusted.example"}).status_code == 403
