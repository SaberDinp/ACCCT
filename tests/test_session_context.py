"""Recording identity is explicit and never a claim of comparative car physics."""

import copy

import pytest

from telemetry.comparison import compare_session_ranges
from telemetry.catalog import list_cars, list_circuits
from telemetry.session_context import comparison_context, enrich_comparison, session_context, validate_context


def session(car=None, track=None, **extra):
    return {"metadata": {"telemetry_context": {"car_id": car, "track_id": track, **extra}}}


def samples(duration):
    return [{"time": index / 10, "active": True, "hud_visible": True, "confidence": 1,
             "segment_id": 1, "speed": 144, "brake": 0, "throttle": 100, "gear": "4"}
            for index in range(duration * 10 + 1)]


def registered_result():
    return compare_session_ranges(samples(100), samples(102), align_start=True, align_end=True,
                                  alignment_mode="landmarks",
                                  landmarks=[{"label": "Bridge", "current_time": 52, "reference_time": 50}])


def test_context_uses_explicit_catalog_variants_and_never_guesses_video_titles():
    old = validate_context({"car_id": "mclaren_720s_gt3", "track_id": "silverstone"})
    evo = validate_context({"car_id": "mclaren_720s_gt3_evo", "track_id": "silverstone"})
    assert old["car_id"] != evo["car_id"]
    assert session_context({"name": "McLaren 720S GT3 EVO Silverstone fast lap"})["car_id"] is None
    assert session_context(session(old["car_id"], old["track_id"]))["identity_source"] == "user"


@pytest.mark.parametrize("value", [[], {"car_id": "mclaren"}, {"track_id": "not-a-track"},
    {"car_id": True}, {"platform": "ps4"}, {"conditions": "x" * 161}, {"conditions": "wet\ntrack"},
    {"car_id": None, "automatic_verification": True}])
def test_context_rejects_ambiguous_or_malformed_metadata(value):
    with pytest.raises(ValueError):
        validate_context(value)


def test_cross_car_same_track_allowed_with_gearing_steering_and_conditions_caveats():
    a = session("mclaren_720s_gt3", "silverstone", conditions="wet", game_version="1.9")
    b = session("mclaren_720s_gt3_evo", "silverstone", conditions="dry", game_version="1.10")
    result = comparison_context(a, b)
    assert result["different_cars"] and result["same_track"]
    assert result["track_basis"] == "user_labels"
    warnings = " ".join(result["warnings"])
    assert "gearing" in warnings and "road-wheel" in warnings and "intrinsic car ranking" in warnings
    assert "Conditions differ" in warnings and "Game versions differ" in warnings


@pytest.mark.parametrize("confirmed", [False, True])
def test_different_circuits_cannot_be_overridden_by_confirmation(confirmed):
    with pytest.raises(ValueError, match="same circuit"):
        comparison_context(session(track="silverstone"), session(track="monza"), confirmed)


def test_unknown_tracks_keep_estimated_delta_without_claiming_same_track_identity():
    result = comparison_context(session(), session())
    assert not result["same_track"] and result["track_basis"] == "unknown"
    assert "Circuit identity is unknown" in result["warnings"][0]
    confirmed = comparison_context(session(), session(), True)
    assert confirmed["same_track"] and confirmed["track_basis"] == "user_confirmation"
    enriched = enrich_comparison(registered_result(), session(), session())
    assert enriched["delta_available"] and enriched["delta_estimated"]
    assert enriched["points"][-1]["delta_time_s"] == pytest.approx(2)
    assert not enriched["observed_segments"]["available"]


def test_marker_segment_differences_preserve_time_and_do_not_claim_a_car_ranking():
    result = registered_result()
    original_points = copy.deepcopy(result["points"])
    enriched = enrich_comparison(result, session("mclaren_720s_gt3", "silverstone"),
                                session("mclaren_720s_gt3_evo", "silverstone"))
    observed = enriched["observed_segments"]
    assert observed["available"]
    assert [part["delta_seconds"] for part in observed["segments"]] == [2, 0]
    assert observed["basis"] == "user_confirmed_physical_markers"
    assert "not a car ranking" in observed["reason"]
    assert enriched["points"] == original_points
    assert enriched["points"][-1]["delta_time_s"] == pytest.approx(2)


@pytest.mark.parametrize("case", ["unknown_track", "no_delta", "endpoints", "excessive_correction", "speed_gap", "low_speed_coverage"])
def test_segment_interpretation_is_gated_by_identity_markers_and_observation_quality(case):
    result = registered_result()
    a, b = session(track="silverstone"), session(track="silverstone")
    if case == "unknown_track":
        a = b = session()
    elif case == "no_delta":
        result["delta_available"] = False
    elif case == "endpoints":
        result["alignment"]["mode"] = "lap_endpoints"
    elif case == "excessive_correction":
        result["alignment"]["max_correction_pct"] = 10.001
    elif case == "speed_gap":
        result["current"]["has_gaps"] = True
    elif case == "low_speed_coverage":
        result["reference"]["channels"]["speed"]["coverage_pct"] = 89.9
    observed = enrich_comparison(result, a, b)["observed_segments"]
    assert not observed["available"] and not observed["segments"]


def test_swap_preserves_segment_extent_and_reverses_observed_time_difference():
    result = compare_session_ranges(samples(102), samples(100), align_start=True, align_end=True,
                                    alignment_mode="landmarks",
                                    landmarks=[{"label": "Bridge", "current_time": 50, "reference_time": 52}])
    observed = enrich_comparison(result, session(track="silverstone"), session(track="silverstone"))["observed_segments"]
    assert observed["available"]
    assert [segment["delta_seconds"] for segment in observed["segments"]] == [-2, 0]


@pytest.fixture(scope="module")
def catalog_comparison_fixture():
    # Shared observed trace checks metadata integration, not video accuracy or
    # each circuit's pace. Car/track labels must never rescale observed timing.
    return compare_session_ranges(samples(100), samples(102), align_start=True, align_end=True,
                                  alignment_mode="landmarks", max_points=20,
                                  landmarks=[{"label": "Bridge", "current_time": 52, "reference_time": 50}])


@pytest.mark.parametrize("car_id", [item["id"] for item in list_cars()])
@pytest.mark.parametrize("track_id", [item["id"] for item in list_circuits()])
def test_all_catalog_pairs_preserve_observed_cross_car_comparison(car_id, track_id, catalog_comparison_fixture):
    context = validate_context({"car_id": car_id, "track_id": track_id, "platform": "pc"})
    other_car = "ferrari_296_gt3" if car_id != "ferrari_296_gt3" else "bmw_m4_gt3"
    current = {"metadata": {"telemetry_context": context}}
    reference = session(other_car, track_id)
    result = enrich_comparison(copy.deepcopy(catalog_comparison_fixture), current, reference)
    assert result["context"]["same_track"]
    assert result["context"]["different_cars"]
    assert result["context"]["current"]["car_id"] == car_id
    assert result["context"]["current"]["track_id"] == track_id
    assert result["delta_available"]
    assert result["points"][-1]["delta_time_s"] == pytest.approx(2)
    assert [part["delta_seconds"] for part in result["observed_segments"]["segments"]] == [2, 0]
