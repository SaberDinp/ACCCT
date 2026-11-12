import copy
import json

import pytest

from telemetry.comparison import compare_session_ranges, trace_session_range


def trace(speed=72, duration=5, step=0.5, origin=0, segment=1):
    return [{"time": origin + index * step, "active": True, "hud_visible": True,
             "segment_id": segment, "confidence": 0.95, "speed": speed,
             "throttle": 100, "brake": 0, "gear": "3", "rpm": 5000}
            for index in range(round(duration / step) + 1)]


def point_at(result, position):
    return next(point for point in result["points"] if point["position"] == pytest.approx(position))


def test_standalone_trace_has_no_reference_or_delta_and_preserves_source():
    samples = trace(origin=100)
    original = copy.deepcopy(samples)
    result = trace_session_range(samples, {"start_time": 101, "end_time": 104}, max_points=61)
    assert result["extent"] == [0, 60]
    assert result["reference"] is None
    assert result["common_extent"] is None
    assert not result["delta_available"]
    assert result["current"]["selection"]["start_time"] == 101
    assert result["points"][0]["current"]["time"] == 101
    assert result["points"][-1]["current"]["elapsed"] == 3
    assert all(point["reference"] is None and point["delta_time_s"] is None for point in result["points"])
    assert all(not channel["reference"] and not channel["comparable"] for channel in result["channels"].values())
    assert not any("delta" in warning.lower() or "reference" in warning.lower() for warning in result["warnings"])
    assert samples == original
    json.dumps(result, allow_nan=False)


def test_standalone_trace_retains_gaps_and_short_pedal_features():
    samples = trace(duration=10, step=.1)
    samples[30]["brake"] = 100
    samples[50].update(active=False, hud_visible=False, segment_id=None)
    result = trace_session_range(samples, axis="elapsed", max_points=30)
    assert len(result["points"]) <= 30
    assert point_at(result, 3)["current"]["brake"] == 100
    assert point_at(result, 5)["current"] is None
    assert result["current"]["has_gaps"]
    assert any("data gaps" in warning for warning in result["warnings"])


def test_standalone_trace_stationary_run_requires_elapsed_axis():
    with pytest.raises(ValueError, match="no usable distance"):
        trace_session_range(trace(speed=0))
    result = trace_session_range(trace(speed=0), axis="elapsed", max_points=10)
    assert result["extent"] == [0, 5]
    assert result["current"]["distance"] == 0


@pytest.mark.parametrize("arguments", [{"axis": "invalid"}, {"max_points": True}, {"max_points": 1}, {"max_points": 5001}, {"max_points": 4.5}])
def test_standalone_trace_validates_options(arguments):
    with pytest.raises(ValueError):
        trace_session_range(trace(), **arguments)


def test_distance_comparison_preserves_independent_pace_and_unmatched_tail():
    result = compare_session_ranges(trace(72), trace(36, origin=100), align_start=True, step=10)
    assert result["extent"] == [0, 100]
    assert result["common_extent"] == [0, 50]
    assert result["delta_available"]
    matching = point_at(result, 50)
    assert matching["reference"]["elapsed"] == 2.5
    assert matching["current"]["elapsed"] == 5
    assert matching["delta_time_s"] == 2.5
    assert point_at(result, 100)["current"] is None
    assert point_at(result, 100)["reference"]["elapsed"] == 5


def test_unconfirmed_starts_keep_estimated_delta_from_overlapping_distance():
    result = compare_session_ranges(trace(), trace(36), step=10)
    assert result["delta_available"] and result["delta_estimated"]
    assert result["delta_mode"] == "approximate" and result["delta_basis"] == "distance"
    assert point_at(result, 50)["delta_time_s"] == 2.5
    assert point_at(result, 60)["delta_time_s"] is None
    assert any("start alignment is unconfirmed" in warning for warning in result["warnings"])


def test_elapsed_delta_matches_lap_progress_without_changing_source_timestamps():
    result = compare_session_ranges(trace(origin=100), trace(36, duration=10, origin=200), axis="elapsed", step=1)
    assert result["unit"] == "s"
    assert result["extent"] == [0, 10]
    assert result["delta_available"] and result["delta_basis"] == "lap_progress"
    assert point_at(result, 2)["reference"]["distance"] == 40
    assert point_at(result, 2)["current"]["distance"] == 20
    assert point_at(result, 2)["delta_time_s"] == 1
    finish = point_at(result, 10)
    assert finish["reference"] is None
    assert finish["delta_time_s"] == 5
    assert finish["delta_current_time"] == 210
    assert finish["delta_reference_time"] == 105


def test_elapsed_delta_uses_observed_speed_progress_instead_of_time_fraction():
    reference, current = trace(duration=10), trace(duration=10)
    for sample in current:
        sample["speed"] = 36 + 7.2 * sample["time"]
    result = compare_session_ranges(reference, current, axis="elapsed", step=.5)
    middle = point_at(result, 5)
    assert middle["current"]["distance"] == pytest.approx(75)
    assert middle["reference"]["distance"] == pytest.approx(100)
    assert middle["delta_reference_time"] == pytest.approx(3.75)
    assert middle["delta_time_s"] == pytest.approx(1.25)
    assert result["points"][-1]["delta_time_s"] == pytest.approx(0)


def test_elapsed_delta_splits_at_the_matched_reference_gap_not_its_simultaneous_time():
    reference, current = trace(duration=10), trace(duration=9)
    reference[8].update(active=False, hud_visible=False, segment_id=None)
    for sample in reference[9:]:
        sample["segment_id"] = 2
    result = compare_session_ranges(reference, current, axis="elapsed", step=.25)
    assert result["delta_available"]
    # Both traces integrate 180 m; the reference cut collapses to 70 m. Its
    # ambiguous passage is at current elapsed 3.5, not reference elapsed 4.
    assert point_at(result, 3.5)["reference"] is not None
    assert point_at(result, 3.5)["delta_time_s"] is None
    before, after = point_at(result, 3.25), point_at(result, 3.75)
    assert before["delta_time_s"] == pytest.approx(0)
    assert after["delta_time_s"] == pytest.approx(-1)
    assert before["delta_segment_id"] != after["delta_segment_id"]
    assert point_at(result, 4)["reference"] is None
    assert point_at(result, 4)["delta_time_s"] == pytest.approx(-1)


def test_registered_elapsed_delta_keeps_physical_landmark_correspondence():
    result = compare_session_ranges(trace(duration=10), trace(duration=12),
        axis="elapsed", align_start=True, align_end=True, alignment_mode="landmarks",
        landmarks=[{"reference_time": 4, "current_time": 5, "label": "Bridge"}], step=.5)
    assert result["delta_mode"] == "registered"
    assert result["delta_basis"] == "registered_distance"
    assert point_at(result, 5)["delta_reference_time"] == pytest.approx(4)
    assert point_at(result, 5)["delta_time_s"] == pytest.approx(1)
    assert point_at(result, 12)["delta_time_s"] == pytest.approx(2)


@pytest.mark.parametrize("speed", [None, 0])
def test_elapsed_delta_without_moving_observations_stays_unavailable(speed):
    result = compare_session_ranges(trace(speed=speed), trace(speed=speed), axis="elapsed")
    assert not result["delta_available"] and result["delta_sample_count"] == 0
    assert all(point["delta_time_s"] is None and point["delta_segment_id"] is None for point in result["points"])
    json.dumps(result, allow_nan=False)


def test_independent_manual_ranges_and_lap_indices_set_origins_without_mutation():
    reference, current = trace(duration=10, origin=100), trace(duration=10, origin=200)
    original = copy.deepcopy((reference, current))
    result = compare_session_ranges(reference, current,
                                    {"start_index": 2, "end_index": 8},
                                    {"start_time": 203, "end_time": 206}, align_start=True, step=10)
    assert result["reference"]["selection"]["start_time"] == 101
    assert result["current"]["selection"]["start_time"] == 203
    assert result["extent"] == [0, 60]
    assert all(point["delta_time_s"] == pytest.approx(0) for point in result["points"])
    assert (reference, current) == original


def test_hud_gaps_break_delta_locally_without_discarding_observed_intervals():
    current = trace()
    current[4].update(active=False, hud_visible=False, segment_id=None)
    for sample in current[5:]:
        sample["segment_id"] = 2
    elapsed = compare_session_ranges(trace(), current, axis="elapsed", step=0.25, align_start=True)
    assert point_at(elapsed, 1.75)["current"] is None
    assert point_at(elapsed, 2)["current"] is None
    assert point_at(elapsed, 2.25)["current"] is None
    assert point_at(elapsed, 2.5)["current"] is not None
    assert all(point_at(elapsed, position)["delta_time_s"] is None for position in (1.75, 2, 2.25))
    distance = compare_session_ranges(trace(), current, align_start=True, step=5)
    assert distance["delta_available"]
    assert point_at(distance, 25)["delta_segment_id"] != point_at(distance, 35)["delta_segment_id"]
    assert point_at(distance, 30)["delta_time_s"] is None
    assert distance["current"]["has_gaps"]
    assert distance["current"]["distance"] == 80
    assert not distance["current"]["distance_complete"]


def test_missing_speed_leaves_other_channels_visible_and_breaks_distance_trace():
    current = trace()
    current[4]["speed"] = None
    elapsed = compare_session_ranges(trace(), current, axis="elapsed", step=0.25)
    point = point_at(elapsed, 2)["current"]
    assert point["speed"] is None
    assert point["throttle"] == 100
    distance = compare_session_ranges(trace(), current, align_start=True, step=5)
    assert distance["delta_available"]
    assert point_at(elapsed, 2)["delta_time_s"] is None
    assert not distance["current"]["distance_complete"]
    assert distance["current"]["channels"]["speed"]["coverage_pct"] == 80
    assert point_at(distance, 25)["current"]["segment_id"] != point_at(distance, 35)["current"]["segment_id"]


def test_absent_pedal_does_not_remove_known_channel_metrics():
    current = trace()
    for sample in current:
        sample.update(throttle=None, brake=50)
    result = compare_session_ranges(trace(), current, axis="elapsed")
    assert result["current"]["metrics"]["braking_pct"] == 100
    assert result["current"]["metrics"]["avg_brake"] == 50
    assert result["current"]["metrics"]["full_throttle_pct"] is None
    assert result["current"]["metrics"]["overlap_pct"] is None
    assert not result["channels"]["throttle"]["comparable"]


def test_gear_is_a_step_and_missing_gear_is_not_bridged():
    current = trace(duration=2)
    current[0]["gear"] = current[1]["gear"] = "3"
    current[2]["gear"] = "4"
    current[3]["gear"] = None
    current[4]["gear"] = "5"
    result = compare_session_ranges(trace(duration=2), current, axis="elapsed", step=0.25)
    assert point_at(result, 0.75)["current"]["gear"] == "3"
    assert point_at(result, 1)["current"]["gear"] == "4"
    assert point_at(result, 1.25)["current"]["gear"] is None
    assert point_at(result, 1.75)["current"]["gear"] is None
    assert len(result["current"]["shifts"]) == 1
    assert result["current"]["shifts"][0]["from_gear"] == "3"
    assert result["current"]["shifts"][0]["to_gear"] == "4"
    assert result["current"]["metrics"]["upshifts"] == 1


def test_braking_events_include_release_and_mark_partial_range_edges():
    current = trace(duration=4, step=0.5)
    for sample, brake in zip(current, [0, 20, 100, 80, 40, 60, 20, 0, 0]):
        sample["brake"] = brake
        sample["throttle"] = 0
    result = compare_session_ranges(current, current, axis="elapsed")
    event = result["current"]["braking_events"][0]
    assert event["start_time"] == 0.5
    assert event["end_time"] == 3.5
    assert event["peak_brake"] == 100
    assert event["reapplications"] == 1
    assert event["release_seconds"] == 2.5
    assert not event["truncated"]
    partial = compare_session_ranges(current, current, {"start_index": 2, "end_index": 6},
                                     {"start_index": 2, "end_index": 6}, axis="elapsed")
    event = partial["current"]["braking_events"][0]
    assert event["truncated_start"] and event["truncated_end"]
    assert event["release_seconds"] is None


def test_steering_requires_calibrated_confident_channel_and_matching_units():
    reference, current = trace(), trace()
    for samples in (reference, current):
        for sample in samples:
            sample.update(steering=25, steering_unit="hud_percent", steering_confidence=0.9, steering_status="tracked")
    current[4]["steering_confidence"] = 0.2
    result = compare_session_ranges(reference, current, axis="elapsed", step=0.5)
    assert result["channels"]["steering"]["comparable"]
    assert point_at(result, 2)["current"]["steering"] is None
    assert point_at(result, 2)["reference"]["steering"] == 25
    for sample in current:
        sample["steering_unit"] = "degrees"
    mismatch = compare_session_ranges(reference, current)
    assert not mismatch["channels"]["steering"]["comparable"]
    assert all(point["reference"]["steering"] is None for point in mismatch["points"])
    assert any("units differ" in warning for warning in mismatch["warnings"])


def test_stationary_plateau_is_ambiguous_but_moving_intervals_keep_delta():
    current = trace()
    current[0]["speed"] = current[1]["speed"] = 0
    result = compare_session_ranges(trace(), current, align_start=True)
    assert result["delta_available"]
    assert result["points"][0]["delta_time_s"] is None
    assert any(point["delta_time_s"] is not None for point in result["points"][1:])


def test_comparison_point_budget_and_common_endpoint():
    result = compare_session_ranges(trace(72), trace(47), align_start=True, max_points=8)
    assert len(result["points"]) <= 8
    assert result["points"][0]["position"] == 0
    assert result["points"][-1]["position"] == 100
    assert any(point["position"] == result["common_extent"][1] for point in result["points"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("kwargs", [{"axis": "normalized"}, {"step": 0}, {"step": float("nan")},
                                    {"max_points": 1}, {"max_points": True}, {"align_start": "yes"},
                                    {"reference_range": {"start_index": -1, "end_index": 3}},
                                    {"reference_range": {"start_time": 2, "end_time": 1}},
                                    {"reference_range": {"start_index": 1, "end_time": 2}}])
def test_invalid_comparison_requests_fail_clearly(kwargs):
    with pytest.raises(ValueError):
        compare_session_ranges(trace(), trace(), **kwargs)


def test_seek_selection_requires_unambiguous_range():
    samples = trace(duration=2) + trace(duration=2, segment=2)
    with pytest.raises(ValueError, match="seek"):
        compare_session_ranges(samples, trace())
    result = compare_session_ranges(samples, trace(duration=2),
                                    reference_range={"start_time": 0, "end_time": 2, "segment_id": 2})
    assert result["reference"]["selection"]["start_index"] == 5


def test_all_missing_speed_uses_elapsed_axis_without_invented_distance():
    current = trace(speed=None)
    with pytest.raises(ValueError, match="distance"):
        compare_session_ranges(current, current)
    result = compare_session_ranges(current, current, axis="elapsed")
    assert result["current"]["distance"] == 0
    assert not result["channels"]["speed"]["comparable"]
    assert not result["current"]["channels"]["speed"]["available"]
    assert not result["delta_available"]
    assert all(point["delta_time_s"] is None for point in result["points"])


def test_missing_channel_is_not_interpolated_across_its_gap():
    current = trace()
    current[4]["brake"] = None
    result = compare_session_ranges(trace(), current, axis="elapsed", step=0.25)
    assert point_at(result, 1.75)["current"]["brake"] is None
    assert point_at(result, 2.25)["current"]["brake"] is None


def test_bounded_comparison_preserves_a_short_brake_spike_and_its_width():
    current = trace(duration=10, step=0.01)
    current[431]["brake"] = 100
    result = compare_session_ranges(trace(duration=10, step=0.01), current,
                                    axis="elapsed", max_points=20)
    assert len(result["points"]) <= 20
    assert point_at(result, 4.31)["current"]["brake"] == 100
    assert point_at(result, 4.30)["current"]["brake"] == 0
    assert point_at(result, 4.32)["current"]["brake"] == 0
    assert result["current"]["braking_events"][0]["peak_brake"] == 100


def test_bounded_comparison_preserves_a_short_throttle_drop():
    current = trace(duration=10, step=0.01)
    current[431]["throttle"] = 0
    result = compare_session_ranges(trace(duration=10, step=0.01), current,
                                    axis="elapsed", max_points=20)
    assert len(result["points"]) <= 20
    assert point_at(result, 4.31)["current"]["throttle"] == 0
    assert point_at(result, 4.30)["current"]["throttle"] == 100
    assert point_at(result, 4.32)["current"]["throttle"] == 100


def test_bounded_comparison_keeps_brief_missing_channel_sample_null():
    current = trace(duration=10, step=0.01)
    current[431]["brake"] = None
    result = compare_session_ranges(trace(duration=10, step=0.01), current,
                                    axis="elapsed", max_points=20)
    assert point_at(result, 4.31)["current"]["brake"] is None


def test_short_bounded_speed_dropout_restores_distance_delta_without_inventing_observed_speed():
    current = trace(duration=2, step=0.1)
    current[10]["speed"] = None
    original = copy.deepcopy(current)
    result = compare_session_ranges(trace(duration=2, step=0.1), current, align_start=True, step=2)
    assert result["delta_available"] and result["delta_estimated"]
    assert result["current"]["distance_complete"]
    assert result["current"]["distance"] == pytest.approx(40)
    assert result["current"]["distance_estimated"]
    assert result["current"]["channels"]["speed"]["coverage_pct"] == pytest.approx(90)
    point = point_at(result, 20)
    assert point["current"]["speed"] is None
    assert point["current"]["distance_interpolated"]
    assert point["delta_time_s"] == pytest.approx(0)
    assert any("missing speed observations" in warning for warning in result["warnings"])
    assert current == original


@pytest.mark.parametrize("change", ["cut", "hidden", "edge", "acceleration", "long"])
def test_speed_gap_recovery_is_bounded_and_never_crosses_cuts(change):
    current = trace(duration=2, step=0.1)
    current[10]["speed"] = None
    if change == "cut":
        for sample in current[10:]:
            sample["segment_id"] = 2
    elif change == "hidden":
        current[10]["hud_visible"] = False
    elif change == "edge":
        current[0]["speed"] = None
    elif change == "acceleration":
        current[11]["speed"] = 200
    else:
        for sample in current[8:14]:
            sample["speed"] = None
    result = compare_session_ranges(trace(duration=2, step=0.1), current, align_start=True)
    assert result["delta_available"], "Observed intervals remain comparable even when speed-gap recovery refuses a gap."
    assert not result["current"]["distance_complete"]


def test_manual_time_boundaries_are_exact_and_interpolated_without_mutation():
    reference, current = trace(duration=4, origin=10), trace(duration=4, origin=100)
    original = copy.deepcopy((reference, current))
    result = compare_session_ranges(reference, current,
                                    reference_range={"start_time": 10.11, "end_time": 12.31},
                                    current_range={"start_time": 100.22, "end_time": 102.52},
                                    align_start=True, align_end=True, alignment_mode="lap_endpoints")
    assert result["reference"]["selection"]["start_time"] == 10.11
    assert result["reference"]["selection"]["end_time"] == 12.31
    assert result["current"]["selection"]["start_time"] == 100.22
    assert result["current"]["selection"]["end_time"] == 102.52
    assert result["points"][0]["delta_time_s"] == 0
    assert result["points"][-1]["delta_time_s"] == pytest.approx(0.1)
    assert result["points"][-1]["current"]["time"] == 102.52
    assert (reference, current) == original


@pytest.mark.parametrize("edge", ["start", "end"])
@pytest.mark.parametrize("missing", ["hud", "speed", "stationary"])
def test_distance_review_reports_when_it_cannot_represent_selected_boundary(edge, missing):
    samples = trace(duration=4, step=.1)
    affected = samples[:5] if edge == "start" else samples[-5:]
    for sample in affected:
        if missing == "hud":
            sample.update(active=False, hud_visible=False, segment_id=None)
        else:
            sample["speed"] = None if missing == "speed" else 0
    original = copy.deepcopy(samples)
    standalone = trace_session_range(samples)
    paired = compare_session_ranges(trace(duration=4, step=.1), samples)
    assert not standalone["current"]["distance_boundaries_complete"]
    assert not paired["current"]["distance_boundaries_complete"]
    assert paired["reference"]["distance_boundaries_complete"]
    elapsed = trace_session_range(samples, axis="elapsed")
    boundary = elapsed["points"][0 if edge == "start" else -1]["current"]
    assert elapsed["extent"] == [0, 4]
    if missing == "hud":
        assert boundary is None
    else:
        assert boundary["time"] == (0 if edge == "start" else 4)
        assert boundary["speed"] == (None if missing == "speed" else 0)
    assert samples == original


@pytest.mark.parametrize("axis", ["elapsed", "distance"])
def test_segment_filter_keeps_manual_boundaries_outside_its_observations(axis):
    samples = trace(duration=4, step=.1)
    for sample in samples:
        if sample["time"] < 1.6:
            sample.update(active=False, hud_visible=False, segment_id=None, speed=None)
        elif sample["time"] > 3:
            # Even usable observations from another segment must stay excluded.
            sample["segment_id"] = 2
    original = copy.deepcopy(samples)
    result = trace_session_range(samples, {"start_time": 1.15, "end_time": 3.85, "segment_id": 1}, axis=axis)
    selected = result["current"]
    assert selected["selection"]["start_time"] == 1.15
    assert selected["selection"]["end_time"] == 3.85
    assert selected["duration"] == pytest.approx(2.7)
    assert selected["has_gaps"]
    assert not selected["distance_boundaries_complete"]
    if axis == "elapsed":
        assert result["points"][0]["current"] is None
        assert result["points"][-1]["current"] is None
    assert samples == original


def test_missing_interior_speed_does_not_hide_usable_distance_boundaries():
    samples = trace(duration=4, step=.1)
    samples[20]["speed"] = None
    result = trace_session_range(samples)
    assert result["current"]["distance_boundaries_complete"]


def test_boundary_inside_cut_stays_missing_and_registration_falls_back_to_raw():
    current = trace(duration=4)
    for sample in current[4:]:
        sample["segment_id"] = 2
    result = compare_session_ranges(trace(duration=4), current,
                                    current_range={"start_time": 1.75, "end_time": 3.75}, axis="elapsed")
    assert result["current"]["selection"]["start_time"] == 1.75
    assert result["points"][0]["current"] is None
    assert result["current"]["has_gaps"]
    fallback = compare_session_ranges(trace(duration=4), current,
                                      current_range={"start_time": 1.75, "end_time": 3.75},
                                      align_start=True, align_end=True, alignment_mode="lap_endpoints")
    assert fallback["alignment"]["mode"] == "raw"
    assert fallback["alignment"]["requested_mode"] == "lap_endpoints"
    assert not fallback["alignment"]["applied"]
    assert fallback["delta_available"]


@pytest.mark.parametrize("mode", ["lap_endpoints", "landmarks"])
@pytest.mark.parametrize("quality", ["speed_gap", "cut", "hidden", "stop"])
def test_registration_quality_fallback_keeps_observed_plot_and_estimated_delta(mode, quality):
    reference, current = trace(), trace()
    if quality == "speed_gap":
        current[4]["speed"] = None
    elif quality == "cut":
        for sample in current[4:]:
            sample["segment_id"] = 2
    elif quality == "hidden":
        current[4].update(active=False, hud_visible=False)
    else:
        current[3]["speed"] = current[4]["speed"] = 0
    original = copy.deepcopy((reference, current))
    landmarks = [{"reference_time": 1, "current_time": 1, "label": "Bridge"}] if mode == "landmarks" else []
    result = compare_session_ranges(reference, current, align_start=True, align_end=True,
                                    alignment_mode=mode, landmarks=landmarks, step=5)
    raw = compare_session_ranges(reference, current, align_start=True, align_end=True, step=5)
    assert result["alignment"]["requested_mode"] == mode
    assert result["alignment"]["mode"] == "raw" and not result["alignment"]["applied"]
    assert result["alignment"]["fallback_reason"]
    assert result["alignment"]["anchors"] == result["alignment"]["scales"] == []
    assert result["delta_available"] and result["delta_mode"] == "approximate"
    assert result["delta_basis"] == "distance"
    assert result["points"] == raw["points"]
    assert result["current"]["distance"] == result["current"]["raw_distance"]
    assert any("Showing raw distance" in warning for warning in result["warnings"])
    assert (reference, current) == original


def test_valid_landmark_inside_speed_gap_falls_back_without_registering_missing_data():
    current = trace()
    current[4]["speed"] = None
    result = compare_session_ranges(trace(), current, align_start=True, align_end=True,
        alignment_mode="landmarks", landmarks=[{"reference_time": 2, "current_time": 2}])
    assert result["alignment"]["mode"] == "raw"
    assert result["alignment"]["requested_mode"] == "landmarks"
    assert result["delta_available"]


def test_lap_endpoints_correct_distance_drift_but_keep_true_lap_time_difference():
    reference, current = trace(speed=72, duration=5), trace(speed=54, duration=6, origin=100)
    result = compare_session_ranges(reference, current, align_start=True, align_end=True,
                                    alignment_mode="lap_endpoints", step=10)
    assert result["extent"] == result["common_extent"] == [0, 100]
    assert result["current"]["raw_distance"] == 90
    assert result["current"]["distance"] == 100
    assert result["current"]["metrics"]["distance_m"] == 90
    assert result["current"]["metrics"]["registered_distance_m"] == 100
    assert result["alignment"]["scales"] == [pytest.approx(100 / 90)]
    assert result["points"][-1]["delta_time_s"] == pytest.approx(1)
    assert result["points"][-1]["current"]["time"] == 106
    assert point_at(result, 50)["current"]["raw_distance"] == pytest.approx(45)
    assert point_at(result, 50)["delta_time_s"] == pytest.approx(0.5)


def test_piecewise_landmarks_register_exact_source_times_and_preserve_between_marker_pace():
    reference = trace(duration=10, step=0.5, origin=10)
    current = trace(duration=12, step=0.5, origin=100)
    result = compare_session_ranges(reference, current, align_start=True, align_end=True,
                                    alignment_mode="landmarks", max_points=32,
                                    landmarks=[{"reference_time": 12.13, "current_time": 102.27, "label": "Bridge"},
                                               {"reference_time": 17.11, "current_time": 108.13, "label": "Sign"}])
    for anchor in result["alignment"]["anchors"]:
        point = point_at(result, anchor["reference_distance_m"])
        assert point["reference"]["time"] == pytest.approx(anchor["reference_time"])
        assert point["current"]["time"] == pytest.approx(anchor["current_time"])
        expected = anchor["current_time"] - 100 - (anchor["reference_time"] - 10)
        assert point["delta_time_s"] == pytest.approx(expected)
    assert result["points"][-1]["delta_time_s"] == 2
    assert result["alignment"]["max_correction_pct"] > 0
    json.dumps(result, allow_nan=False)


def test_registration_does_not_align_driver_braking_points():
    reference, current = trace(duration=10, step=0.1), trace(duration=12, step=0.1)
    reference[20]["brake"] = current[30]["brake"] = 100
    result = compare_session_ranges(reference, current, align_start=True, align_end=True,
                                    alignment_mode="lap_endpoints")
    reference_event = result["reference"]["braking_events"][0]
    current_event = result["current"]["braking_events"][0]
    assert reference_event["peak_distance"] == pytest.approx(40)
    assert current_event["peak_distance"] == pytest.approx(50)
    assert current_event["raw_peak_distance"] == pytest.approx(60)


@pytest.mark.parametrize("kwargs", [
    {"alignment_mode": "stretch"},
    {"alignment_mode": "lap_endpoints", "align_end": False},
    {"alignment_mode": "landmarks", "landmarks": []},
    {"alignment_mode": "landmarks", "landmarks": [{"current_time": 0, "reference_time": 1}]},
    {"alignment_mode": "landmarks", "landmarks": [{"current_time": 1, "reference_time": float("nan")}]},
    {"alignment_mode": "landmarks", "landmarks": [{"current_time": 1, "reference_time": 2},
                                                     {"current_time": 2, "reference_time": 1}]},
    {"alignment_mode": "landmarks", "landmarks": [{"current_time": 1, "reference_time": 2},
                                                     {"current_time": 1, "reference_time": 3}]},
    {"alignment_mode": "landmarks", "landmarks": [{"current_time": 1, "reference_time": 2}], "max_points": 2},
    {"alignment_mode": "landmarks", "landmarks": [{"current_time": 1, "reference_time": 2, "label": 42}]},
    {"alignment_mode": "lap_endpoints", "landmarks": [{"current_time": 1, "reference_time": 2}]},
    {"align_end": "true"},
])
@pytest.mark.parametrize("has_gap", [False, True])
def test_invalid_registration_rejected_even_when_quality_would_require_fallback(kwargs, has_gap):
    options = {"align_start": True, "align_end": True, **kwargs}
    current = trace()
    if has_gap:
        current[4]["speed"] = None
    with pytest.raises(ValueError):
        compare_session_ranges(trace(), current, **options)
