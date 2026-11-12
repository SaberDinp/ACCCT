import math

import pytest

from telemetry.analysis import SessionAnalyzer, resample_trace


def observation(time, **kwargs):
    return {"time": time, "brake": 0, "throttle": 100, "speed": 72,
            "gear": "3", "hud_visible": True, "confidence": 0.95, **kwargs}


def test_metrics_are_time_weighted_and_distance_is_estimated():
    analyzer = SessionAnalyzer()
    for time, throttle in ((0, 0), (0.1, 100), (0.2, 100), (1.0, 100)):
        analyzer.process(observation(time, throttle=throttle))
    summary = analyzer.summary()
    assert summary["active_seconds"] == pytest.approx(1)
    assert summary["avg_throttle"] == pytest.approx(95)
    assert summary["full_throttle_pct"] == pytest.approx(90.5)
    assert summary["distance_m"] == pytest.approx(20)
    assert summary["speed_coverage_pct"] == 100
    assert "distance_m" in summary["estimated_channels"]


def test_hud_loss_does_not_bridge_distance_or_invent_brake_release():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, brake=90))
    analyzer.process(observation(0.2, brake=60))
    lost = analyzer.process(observation(0.4, hud_visible=False))
    analyzer.process(observation(0.6, hud_visible=False))
    resumed = analyzer.process(observation(0.8))
    analyzer.process(observation(1.0))
    assert not lost["active"]
    assert resumed["segment_id"] == 2
    assert analyzer.summary()["active_seconds"] == pytest.approx(0.4)
    assert analyzer.summary()["distance_m"] == pytest.approx(8)
    assert analyzer.summary()["discontinuities"] == 1
    brake_event = next(event for event in analyzer.events if event["type"] == "brake")
    assert brake_event["truncated"] is True
    assert brake_event["release_seconds"] is None
    assert brake_event["end_time"] == 0.2


@pytest.mark.parametrize("next_time,reason", [(10, "source_gap"), (-1, "seek"), (0, "stale")])
def test_source_discontinuities(next_time, reason):
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0))
    sample = analyzer.process(observation(next_time))
    assert sample["gap_reason"] == reason
    assert analyzer.summary()["distance_m"] == 0
    assert analyzer.summary()["active_seconds"] == 0
    assert analyzer.events[0]["reason"] == reason


def test_stationary_timeout_and_explicit_discontinuities():
    analyzer = SessionAnalyzer(stationary_timeout=2)
    for time in (0, 0.5, 1, 1.5):
        assert analyzer.process(observation(time, speed=0))["active"]
    assert analyzer.process(observation(2, speed=0))["status"] == "stationary"
    assert analyzer.process(observation(2.5, speed=20))["segment_id"] == 2
    analyzer.discontinuity("pause")
    assert analyzer.process(observation(2.6))["segment_id"] == 3
    assert analyzer.summary()["active_seconds"] == 1.5


def test_bad_values_are_not_telemetry():
    analyzer = SessionAnalyzer()
    sample = analyzer.process(observation(0, brake=math.nan, speed=math.inf))
    assert sample["brake"] is None and sample["speed"] is None
    assert sample["status"] == "inputs_missing"
    with pytest.raises(ValueError):
        analyzer.process(observation(math.nan))


def test_speed_jump_is_not_integrated_as_acceleration_or_distance():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, speed=72))
    sample = analyzer.process(observation(0.1, speed=270))
    assert sample["acceleration"] is None
    assert sample["speed_outlier"]
    assert sample["speed"] is None
    assert analyzer.summary()["distance_m"] == 0
    assert analyzer.summary()["max_speed_kph"] == 72


def test_brake_event_release_and_reapplication():
    analyzer = SessionAnalyzer()
    for i, brake in enumerate((0, 20, 100, 95, 80, 40, 60, 30, 0)):
        analyzer.process(observation(i * 0.1, brake=brake, throttle=0))
    event = next(event for event in analyzer.events if event["type"] == "brake")
    assert event["peak_brake"] == 100
    assert event["reapplications"] == 1
    assert event["release_seconds"] == pytest.approx(0.6)
    assert event["duration"] == pytest.approx(0.7)
    assert not event["truncated"]


def test_smooth_brake_release_has_no_reapplication():
    analyzer = SessionAnalyzer()
    for i, brake in enumerate((10, 40, 100, 90, 80, 70, 10, 0)):
        analyzer.process(observation(i * 0.1, brake=brake))
    assert analyzer.events[0]["reapplications"] == 0


def test_finalize_does_not_invent_a_release():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, brake=100))
    analyzer.process(observation(0.1, brake=80))
    analyzer.finalize()
    analyzer.finalize()
    assert len(analyzer.events) == 1
    assert analyzer.events[0]["truncated"]
    assert analyzer.events[0]["end_time"] == 0.1


def test_laps_require_marks_and_gaps_mark_lap_incomplete():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0))
    assert analyzer.laps == []
    assert analyzer.mark_lap() is None
    for time in (0.5, 1, 1.5):
        analyzer.process(observation(time))
    completed = analyzer.mark_lap()
    assert completed["duration"] == 1.5
    assert completed["complete"]
    analyzer.discontinuity("setup_menu")
    analyzer.process(observation(2))
    analyzer.process(observation(2.5))
    interrupted = analyzer.mark_lap()
    assert interrupted["interrupted"]
    assert not interrupted["complete"]
    assert interrupted["active_seconds"] == 0.5


def test_timer_reset_cannot_cross_a_gap():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, lap_time="1:30.0"))
    analyzer.process(observation(0.1, lap_time="0:00.1"))
    assert analyzer.summary()["current_lap"] is None
    analyzer.process(observation(0.2, lap_time="0:00.2"))
    assert analyzer.summary()["current_lap"] == 1
    analyzer.process(observation(0.3, lap_time="1:30.0"))
    analyzer.discontinuity("menu")
    analyzer.process(observation(0.4, lap_time="0:00.1"))
    analyzer.process(observation(0.5, lap_time="0:00.2"))
    assert analyzer.summary()["current_lap"] == 1


def test_single_timer_ocr_error_does_not_start_a_lap():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, lap_time=90))
    analyzer.process(observation(0.1, lap_time=0.1))
    analyzer.process(observation(0.2, lap_time=90.2))
    assert analyzer.summary()["current_lap"] is None


def test_timer_reset_survives_brief_ocr_only_null_frames():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, lap_time_seconds=90))
    analyzer.process(observation(0.1, lap_time_seconds=None))
    analyzer.process(observation(0.2, lap_time_seconds=0.1))
    analyzer.process(observation(0.3, lap_time_seconds=None))
    analyzer.process(observation(0.4, lap_time_seconds=0.3))
    assert analyzer.summary()["current_lap"] == 1
    assert analyzer.summary()["discontinuities"] == 0


def test_advancing_low_timer_reacquires_after_ocr_only_blanks():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, lap_time_seconds=90))
    analyzer.process(observation(0.5, lap_time_seconds=None))
    analyzer.process(observation(1, lap_time_seconds=None))
    analyzer.process(observation(1.2, lap_time_seconds=0.1))
    analyzer.process(observation(1.3, lap_time_seconds=0.2))
    assert analyzer.summary()["current_lap"] == 1
    assert analyzer._lap_start["time"] == pytest.approx(1.1)


def test_timer_boundary_is_backprojected_not_confirmation_time():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, lap_time_seconds=90))
    analyzer.process(observation(0.1, lap_time_seconds=0.05))
    analyzer.process(observation(0.2, lap_time_seconds=0.15))
    for tick in range(3, 403):
        timestamp = tick / 10
        analyzer.process(observation(timestamp, lap_time_seconds=timestamp - 0.05))
    analyzer.process(observation(40.3, lap_time_seconds=0.07))
    analyzer.process(observation(40.4, lap_time_seconds=0.17))
    lap = analyzer.laps[0]
    assert lap["start_time"] == pytest.approx(0.05)
    assert lap["end_time"] == pytest.approx(40.23)
    assert lap["duration"] == pytest.approx(40.18)
    assert lap["end_observed_at"] == 40.4
    assert lap["end_sample"] == 403
    assert lap["complete"]


def test_initial_low_clock_starts_partial_lap_without_inventing_footage():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(10, lap_time_seconds=1))
    analyzer.process(observation(10.1, lap_time_seconds=1.1))
    assert analyzer._lap_start["time"] == 10
    assert analyzer._lap_interrupted
    for tick in range(102, 401):
        timestamp = tick / 10
        analyzer.process(observation(timestamp, lap_time_seconds=timestamp - 9))
    analyzer.process(observation(40.1, lap_time_seconds=0.05))
    analyzer.process(observation(40.2, lap_time_seconds=0.15))
    assert analyzer.laps[0]["complete"] is False
    assert analyzer.laps[0]["interrupted"] is True


def test_integer_speed_rounding_is_not_rejected_at_30_fps():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, speed=200))
    sample = analyzer.process(observation(0.033, speed=197))
    assert sample["speed"] == 197
    assert not sample.get("speed_outlier")
    assert sample["acceleration"] == pytest.approx(-3 / (3.6 * 0.033))
    assert analyzer.summary()["distance_m"] > 0


def test_actual_clock_glitch_is_suppressed_and_recovers_from_last_credible_value():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, lap_time="02:19.212", lap_time_seconds=139.212))
    glitch = analyzer.process(observation(0.6, lap_time="02:10.812", lap_time_seconds=130.812))
    recovered = analyzer.process(observation(0.868, lap_time="02:20.080", lap_time_seconds=140.080))
    assert glitch["lap_time"] is None and glitch["lap_time_seconds"] is None
    assert glitch["raw_lap_time"] == "02:10.812"
    assert glitch["raw_lap_time_seconds"] == 130.812
    assert glitch["timer_outlier"]
    assert recovered["lap_time"] == "02:20.080"
    assert recovered["lap_time_seconds"] == 140.080
    assert not recovered.get("timer_outlier")
    assert analyzer.summary()["current_lap"] is None


def test_false_clock_reset_is_not_displayed_and_does_not_poison_recovery():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, lap_time_seconds=145))
    reset_glitch = analyzer.process(observation(0.1, lap_time_seconds=0.1))
    recovered = analyzer.process(observation(0.2, lap_time_seconds=145.2))
    assert reset_glitch["lap_time_seconds"] is None
    assert reset_glitch["timer_pending"]
    assert recovered["lap_time_seconds"] == 145.2
    assert analyzer.summary()["current_lap"] is None


def test_repeated_clock_digits_within_tolerance_remain_visible():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, lap_time_seconds=145))
    assert analyzer.process(observation(0.1, lap_time_seconds=145))["lap_time_seconds"] == 145
    assert analyzer.process(observation(0.2, lap_time_seconds=145.2))["lap_time_seconds"] == 145.2


def test_resampling_leaves_gaps_empty():
    points = [dict(observation(time, throttle=time * 10), elapsed=time, distance=time * 20,
                   segment_id=segment, active=True)
              for time, segment in ((0, 1), (1, 1), (3, 2), (4, 2))]
    resampled = resample_trace(points, [-1, 0.5, 2, 3.5, 5], axis="elapsed")
    assert resampled[0] is None and resampled[2] is None and resampled[4] is None
    assert resampled[1]["throttle"] == 5
    assert resampled[3]["throttle"] == 35


def test_comparison_is_factual_and_missing_laps_error():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0))
    analyzer.mark_lap()
    for time in (0.5, 1):
        analyzer.process(observation(time))
    analyzer.mark_lap()
    for time in (1.5, 2):
        analyzer.process(observation(time))
    comparison = analyzer.compare_laps(1, 2)
    assert comparison["estimated_alignment"]
    assert len(comparison["reference"]) == len(comparison["current"])
    assert comparison["reference"][-1] is not None
    assert analyzer.reference_points(1)[-1]["time"] == 1
    with pytest.raises(ValueError):
        analyzer.compare_laps(1, 20)


def test_resampling_invalid_sample_breaks_even_reused_segment_id():
    points = [{"elapsed": 0, "segment_id": 1, "active": True},
              {"elapsed": 1, "segment_id": 1, "active": False},
              {"elapsed": 2, "segment_id": 1, "active": True}]
    assert resample_trace(points, [1], axis="elapsed") == [None]


def test_invalid_timer_is_not_serialized_as_infinity():
    analyzer = SessionAnalyzer()
    assert analyzer.process(observation(0, lap_time="inf:00"))["lap_time"] is None


def test_vision_timer_schema_preserves_display_string_and_numeric_channel():
    analyzer = SessionAnalyzer()
    sample = analyzer.process(observation(0, lap_time="01:30.125", lap_time_seconds=90.125))
    assert sample["lap_time"] == "01:30.125"
    assert sample["lap_time_seconds"] == 90.125


def test_ocr_speed_outlier_does_not_pollute_braking_event_and_recovers():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0, speed=180, brake=100))
    rejected = analyzer.process(observation(0.1, speed=18, brake=60))
    recovered = analyzer.process(observation(0.2, speed=170, brake=40))
    analyzer.process(observation(0.3, speed=163, brake=0))
    event = next(event for event in analyzer.events if event["type"] == "brake")
    assert rejected["speed"] is None
    assert rejected["raw_speed"] == 18
    assert recovered["speed"] == 170
    assert event["entry_speed"] == 180
    assert event["min_speed"] == 163
    assert event["exit_speed"] == 163


def test_lap_validity_preserves_invalid_and_unknown_laps():
    analyzer = SessionAnalyzer()
    analyzer.process(observation(0))
    analyzer.mark_lap()
    analyzer.process(observation(0.1, lap_valid=True))
    analyzer.process(observation(0.2, lap_valid=False))
    analyzer.process(observation(0.3, lap_valid=True))
    lap = analyzer.mark_lap()
    assert lap["valid"] is False
    assert lap["complete"] is True


def test_import_analysis_can_discard_samples_without_losing_counts_or_lap_indices():
    analyzer = SessionAnalyzer(retain_samples=False)
    analyzer.process(observation(0))
    analyzer.mark_lap()
    analyzer.process(observation(0.1))
    analyzer.process(observation(0.2, hud_visible=False))
    analyzer.process(observation(0.3))
    lap = analyzer.mark_lap()
    assert analyzer.samples == []
    assert analyzer.summary()["samples"] == 4
    assert lap["start_sample"] == 0
    assert lap["end_sample"] == 3
    assert lap["interrupted"]
