"""Check the downloadable JSON contract against real comparison calculations."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from telemetry.comparison import compare_session_ranges


@pytest.fixture(scope="module")
def export_page():
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip('Install Chromium with python -m playwright install chromium to check browser exports.')
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / "web/comparison-export.js"))
        yield page
        browser.close()


def samples(duration=6, origin=0, speed=72, step=.1):
    return [{"time": origin + i * step, "speed": speed, "active": True, "hud_visible": True,
             "confidence": .99, "segment_id": 1, "brake": 0, "throttle": 100, "gear": "3",
             "steering": 0, "steering_unit": "hud_percent", "steering_confidence": .95}
            for i in range(round(duration / step) + 1)]


def with_sessions(result):
    result["sessions"] = {
        "current": {"source": {"name": r"C:\recordings\my-lap.mp4", "path": "/private/user/video.mp4", "role": "reference"},
                    "metadata": {"profile": {"private": "DO_NOT_EXPORT"}}},
        "reference": {"source": {"name": "Reference lap", "original_url": "https://www.youtube.com/watch?v=video-id&t=2&token=PRIVATE_TOKEN"}}
    }
    return result


def exported(page, result):
    package = page.evaluate("""result => {
      const before = JSON.stringify(result);
      const exported = window.ACCCComparisonExport.create(result);
      if (before !== JSON.stringify(result)) throw new Error('Export mutated the displayed comparison');
      return exported;
    }""", result)
    assert package["bytes"] == len(package["text"].encode("utf-8")) <= 200 * 1024
    assert package["rowCount"] <= 600
    assert package["filename"].endswith(".json")
    return json.loads(package["text"]), package


def rows_by_name(document):
    table = document["telemetry"]
    assert all(len(row) == len(table["columns"]) for row in table["rows"])
    return [dict(zip(table["columns"], row)) for row in table["rows"]]


def test_export_preserves_exact_ranges_roles_and_sanitizes_session_data(export_page):
    result = with_sessions(compare_session_ranges(samples(origin=100), samples(origin=1000),
        reference_range={"start_time": 100.123456, "end_time": 105.7},
        current_range={"start_time": 1000.604, "end_time": 1005.9}))
    doc, package = exported(export_page, result)
    assert doc["format"] == "accc-telemetry-comparison"
    assert package["filename"].startswith("accc-")
    assert doc["schema_version"] == 1
    assert doc["runs"]["current"]["role"].startswith("Your run")
    assert doc["runs"]["current"]["recording_name"] == "my-lap.mp4"
    assert doc["runs"]["reference"]["selection"]["start_source_s"] == 100.123456
    assert doc["runs"]["current"]["selection"]["start_source_s"] == 1000.604
    assert doc["runs"]["current"]["selection"]["end_source_s"] == 1005.9
    assert doc["runs"]["reference"]["public_video_url"] == "https://www.youtube.com/watch?v=video-id&t=2"
    assert all(private not in package["text"] for private in ["/private/", "C:\\", "DO_NOT_EXPORT", "PRIVATE_TOKEN", "profile"])
    assert doc["runs"]["current"]["context"]["track_name"] is None


def test_recording_labels_keep_driver_and_track_names_separated_by_slashes(export_page):
    result = with_sessions(compare_session_ranges(samples(), samples()))
    result["sessions"]["reference"]["source"]["name"] = "Fri3dolf / Silverstone / McLaren 720S EVO"
    doc, _ = exported(export_page, result)
    assert doc["runs"]["reference"]["recording_name"] == "Fri3dolf / Silverstone / McLaren 720S EVO"


def test_elapsed_chart_keeps_delta_passages_separate_from_plotted_times(export_page):
    result = with_sessions(compare_session_ranges(samples(duration=5, origin=100), samples(duration=6, origin=1000), axis="elapsed"))
    doc, _ = exported(export_page, result)
    assert doc["comparison"]["delta_basis"] == "lap_progress"
    assert doc["comparison"]["selected_duration_delta_s"] == 1
    rows = rows_by_name(doc)
    middle = next(row for row in rows if row["delta_s"] is not None and row["delta_s"] > .1 and row["reference_source_s"] is not None)
    assert middle["current_elapsed_s"] == middle["reference_elapsed_s"]
    assert middle["delta_reference_source_s"] != middle["reference_source_s"]
    assert middle["delta_s"] == pytest.approx(
        (middle["delta_current_source_s"] - 1000) - (middle["delta_reference_source_s"] - 100), abs=.002)
    assert rows[-1]["reference_source_s"] is None


def test_duration_difference_is_not_the_last_distance_delta(export_page):
    result = compare_session_ranges(samples(duration=5), samples(duration=6, speed=72))
    doc, _ = exported(export_page, result)
    assert doc["comparison"]["selected_duration_delta_s"] == 1
    assert next(row for row in reversed(rows_by_name(doc)) if row["delta_s"] is not None)["delta_s"] == 0


def test_registered_coordinates_retain_raw_distances_and_marker_metadata(export_page):
    result = compare_session_ranges(samples(duration=6), samples(duration=6, speed=70),
        align_start=True, align_end=True, alignment_mode="landmarks",
        landmarks=[{"label": "Bridge", "current_time": 3, "reference_time": 3}])
    doc, _ = exported(export_page, result)
    assert doc["comparison"]["alignment"]["applied"]
    assert doc["comparison"]["alignment"]["anchors"]["rows"][1][0] == "Bridge"
    assert doc["runs"]["current"]["raw_distance_m"] == pytest.approx(116.7)
    assert doc["runs"]["current"]["comparison_distance_m"] == 120
    last = rows_by_name(doc)[-1]
    assert last["current_distance_m"] == last["reference_distance_m"] == 120
    assert last["current_raw_distance_m"] == pytest.approx(116.7)


def test_registration_fallback_is_explicit_in_export(export_page):
    current = samples()
    current[20].update(active=False, hud_visible=False, segment_id=None)
    result = compare_session_ranges(samples(), current, align_start=True, align_end=True, alignment_mode="lap_endpoints")
    doc, _ = exported(export_page, result)
    alignment = doc["comparison"]["alignment"]
    assert alignment["mode"] == "raw" and alignment["applied"] is False
    assert alignment["requested_mode"] == "lap_endpoints"
    assert alignment["fallback_reason"]


def test_different_steering_units_are_not_presented_as_comparable_angles(export_page):
    current = samples()
    for point in current:
        point["steering_unit"] = "degrees"
    result = compare_session_ranges(samples(), current)
    doc, _ = exported(export_page, result)
    assert doc["runs"]["current"]["quality"]["channels"]["steering"]["unit"] == "degrees"
    assert doc["runs"]["reference"]["quality"]["channels"]["steering"]["unit"] == "hud_percent"
    assert all(row["current_steering"] is None and row["reference_steering"] is None for row in rows_by_name(doc))


def test_missing_data_zero_inputs_and_quality_flags_survive_export(export_page):
    current = samples()
    current[20]["speed"] = None
    for point in current[40:45]:
        point.update(active=False, hud_visible=False, segment_id=None)
    result = compare_session_ranges(samples(), current, axis="elapsed")
    doc, _ = exported(export_page, result)
    rows = rows_by_name(doc)
    missing = next(row for row in rows if row["position_s"] == 2)
    assert missing["current_speed_kmh"] is None
    assert missing["current_brake_pct"] == 0
    assert missing["current_steering"] == 0
    assert missing["current_distance_interpolated"] is True
    assert any(row["current_source_s"] is None for row in rows)
    assert doc["runs"]["current"]["quality"]["distance_complete"] is False
    assert doc["runs"]["current"]["quality"]["channels"]["steering"]["unit"] == "hud_percent"
    assert doc["warnings"]


def test_braking_events_retain_truncation_and_meaningful_units(export_page):
    current = samples()
    for i, point in enumerate(current):
        point["brake"] = 80 if 10 <= i <= 30 else 0
    result = compare_session_ranges(samples(), current, current_range={"start_time": 1.5, "end_time": 5.5})
    doc, _ = exported(export_page, result)
    table = doc["runs"]["current"]["braking_events"]
    event = dict(zip(table["columns"], table["rows"][0]))
    assert event["truncated_start"] is True
    assert event["minimum_speed_during_braking_kmh"] == 72
    assert event["peak_to_release_s"] is None
    assert "not necessarily apex" in doc["reading_guide"]["events"]


def test_large_export_is_bounded_and_reports_sampling_and_event_omissions(export_page):
    current = samples(duration=12, step=.01)
    current[617]["brake"] = 100
    for point in current[800:810]:
        point.update(active=False, hud_visible=False, segment_id=None)
    result = with_sessions(compare_session_ranges(samples(duration=12, step=.01), current, axis="elapsed", max_points=1200))
    result["current"]["braking_events"] *= 160
    result["warnings"] = [str(i) + "速度未知 " * 100 for i in range(41)]
    doc, _ = exported(export_page, result)
    rows = rows_by_name(doc)
    assert rows[0]["position_s"] == 0
    assert rows[-1]["position_s"] == 12
    assert any(row["current_brake_pct"] == 100 for row in rows)
    assert any(row["current_source_s"] is None for row in rows)
    assert len(rows) == doc["telemetry"]["sampling"]["exported_rows"] <= 600
    assert doc["telemetry"]["sampling"]["input_rows"] == len(result["points"])
    assert doc["runs"]["current"]["braking_events"]["omitted_count"] == 32
    assert doc["warnings_omitted"] == 1


def test_single_recording_cannot_be_exported_as_a_comparison(export_page):
    assert export_page.evaluate("""() => {
      try {ACCCComparisonExport.create({current: {}, reference: null, points: []}); return false;}
      catch (error) {return /two recordings/.test(error.message);}
    }""")
