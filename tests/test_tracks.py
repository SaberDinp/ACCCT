import hashlib
import json
import math

from fastapi.testclient import TestClient
import pytest

from main import create_app
from telemetry.tracks import (ASSETS, get_track, list_tracks, nearest_progress,
                              point_at_distance, point_at_progress, resample_closed_path,
                              validate_geometry)


EXPECTED_LENGTHS = {"barcelona": 4655, "bathurst": 6213, "brands_hatch": 3916,
                    "cota": 5513, "donington": 4020, "hungaroring": 4381,
                    "imola": 4909, "indianapolis": 4170, "kyalami": 4580,
                    "laguna_seca": 3602, "misano": 4200, "monza": 5793,
                    "nurburgring": 5137, "nurburgring_24h": 25378,
                    "oulton_park": 4333, "paul_ricard": 5770, "red_bull_ring": 4318,
                    "silverstone": 5891, "snetterton": 4779, "spa": 7004,
                    "suzuka": 5807, "valencia": 4005, "watkins_glen": 5552,
                    "zandvoort": 4307, "zolder": 4010}


@pytest.mark.parametrize("track_id,expected_length", EXPECTED_LENGTHS.items())
def test_verified_source_layouts_are_closed_and_aspect_preserving(track_id, expected_length):
    track = get_track(track_id)
    points = track["points"]
    assert track["length_m"] == expected_length
    assert abs(track["geometry_length_m"] - expected_length) < expected_length * 0.04
    assert len(points) > 700
    assert points[0]["x"] == points[-1]["x"]
    assert points[0]["y"] == points[-1]["y"]
    assert points[0]["distance_m"] == 0
    assert points[-1]["distance_m"] == expected_length
    assert all(b["distance_m"] > a["distance_m"] for a, b in zip(points, points[1:]))
    assert all(0.039 <= point[axis] <= 0.961 for point in points for axis in ("x", "y"))
    assert track["normalization"]["aspect_preserved"]
    assert track["source"]["license"] == "AGPL-3.0"
    source_file = ASSETS / track["source"]["local_file"]
    assert hashlib.sha256(source_file.read_bytes()).hexdigest() == track["source"]["sha256"]


def test_silverstone_uses_acc_pit_configuration_and_keeps_recording_alignment_separate():
    track = get_track("silverstone")
    assert track["layout"] == "Grand Prix (Arena)"
    assert track["geometry_length_m"] == pytest.approx(5799.671, abs=0.01)
    assert track["start_finish_verified"] is False
    assert track["requires_origin_confirmation"] is True
    assert track["origin_kind"] == "acc_source_start_finish_estimate"
    assert track["corners"][0]["name"] == "Copse"
    assert track["corners"][0]["progress"] == .042
    assert track["timing_lines"][0]["progress"] == 0
    assert track["racing_line_available"] is False
    assert track["widths_kind"] == "not_exposed"


def test_layouts_keep_acc_era_and_do_not_substitute_different_configs():
    assert get_track("barcelona")["layout"] == "Grand Prix (pre-2021, final chicane)"
    assert get_track("zandvoort")["layout"] == "Grand Prix (pre-2020)"
    assert get_track("brands_hatch")["layout"] == "Grand Prix"
    assert get_track("nurburgring_24h")["geometry_length_m"] > 25000
    assert get_track("nurburgring")["geometry_length_m"] > 5000
    indy = get_track("indianapolis")
    assert indy["layout"] == "ACC GT road course (15 turns)"
    assert len(indy["corners"]) == 15
    assert indy["geometry_length_m"] > 4100


@pytest.mark.parametrize("track_id", EXPECTED_LENGTHS)
def test_annotations_have_sourced_normalized_positions_and_two_acc_splits(track_id):
    track = get_track(track_id)
    assert track["corners"]
    assert len(track["timing_lines"]) == 3
    assert [line["sector"] for line in track["timing_lines"]] == [3, 1, 2]
    for collection in ("corners", "straights", "timing_lines"):
        for annotation in track[collection]:
            assert math.isfinite(annotation["progress"])
            assert 0 <= annotation["progress"] < 1
            assert annotation["position_kind"]
    source = track["annotation_source"]
    assert source["license"] == "CC-BY-NC-SA-4.0"
    assert hashlib.sha256((ASSETS / source["local_file"]).read_bytes()).hexdigest() == source["sha256"]
    assert track["requires_origin_confirmation"]
    assert track["straights"]
    for source in track["supplementary_annotation_sources"]:
        assert hashlib.sha256((ASSETS / source["local_file"]).read_bytes()).hexdigest() == source["sha256"]
        assert source["license"] == "CC-BY-NC-SA-4.0"


def test_all_catalog_tracks_have_acc_specific_geometry():
    from telemetry.catalog import catalog
    assert {t["id"] for t in catalog()["tracks"]} == {t["id"] for t in list_tracks()}


def test_supplemented_corner_names_do_not_render_missing_values():
    for track in list_tracks():
        for corner in track["corners"]:
            assert isinstance(corner["name"], str) and corner["name"].strip()
            assert corner["name"].casefold() not in {"none", "turn none", "null", "turn null"}


@pytest.mark.parametrize("track_id,expected", [
    ("spa", {"Raidillon": "3–4", "Les Combes": "5–6", "Pouhon / Double Gauche": "10–11",
             "Les Fagnes": "12–13", "Chicane": "18–19"}),
    ("kyalami", {"Jukskei Sweep": "3–4", "The Esses": "8–9", "Leeukop": "10–11",
                 "The Crocodiles": "13–14"}),
    ("misano", {"Variante del Parco": "1–3", "Curva del Rio": "4–5"}),
])
def test_named_complexes_preserve_ranges_without_fabricated_individual_apexes(track_id, expected):
    corners = get_track(track_id)["corners"]
    for name, number in expected.items():
        matches = [corner for corner in corners if corner["name"] == name]
        assert len(matches) == 1
        assert matches[0]["number"] == number


def test_nordschleife_named_sections_do_not_acquire_fabricated_turn_numbers():
    corners = get_track("nurburgring_24h")["corners"]
    gp = [corner for corner in corners if corner["source_index"] < 13]
    nordschleife = [corner for corner in corners if corner["source_index"] >= 13]
    assert [corner["number"] for corner in gp] == list(range(1, 14))
    assert len(nordschleife) > 30
    assert all(corner["number"] is None for corner in nordschleife)
    assert {"Hatzenbach", "Karussell", "Hohenrain Chicane"} <= {corner["name"] for corner in nordschleife}


def test_source_annotation_errors_are_visible_and_do_not_invent_apexes():
    spa = get_track("spa")
    eau_rouge = next(c for c in spa["corners"] if c["name"] == "Eau Rouge")
    assert eau_rouge["progress"] == pytest.approx(.141)
    assert eau_rouge["position_kind"] == "source_interval_midpoint_estimate"
    nords = get_track("nurburgring_24h")
    eiskurve = [c for c in nords["corners"] if c["name"] == "Eiskurve"]
    assert len(eiskurve) == 1
    assert any("duplicate" in warning for warning in nords["annotation_warnings"])
    brabham = [s for s in get_track("brands_hatch")["straights"] if s["name"] == "Brabham Straight"]
    assert brabham and all(s["progress"] > .95 or s["progress"] < .06 for s in brabham)


def test_annotation_supplements_keep_source_row_identity_and_support_complex_numbers(tmp_path, monkeypatch):
    import telemetry.tracks as tracks
    (tmp_path / "corner-numbering.json").write_text(json.dumps({"example": {
        "source_urls": ["https://example.test/circuit-map"],
        "turns": [{"number": "1–3"}, None, {"number": 4, "progress": .4}, {"number": None}],
        "extra_corners": [{"number": 5, "name": "Fifth", "progress": .5}],
        "straights": [{"name": "Pit Straight", "progress": .98}]}}))
    monkeypatch.setattr(tracks, "ASSETS", tmp_path)
    result = tracks._annotations("example", {"turn": [
        {"name": "Complex", "marker": .1}, {"name": "duplicate", "marker": .1},
        {"name": "Turn Four"}, {"name": "T6", "marker": .6}], "sector": [{"name": "1", "marker": .3},
                                               {"name": "2", "marker": .7}]})
    assert [c["number"] for c in result["corners"]] == ["1–3", 4, 5, None]
    assert [c["source_index"] for c in result["corners"] if "source_index" in c] == [0, 2, 3]
    assert result["corners"][1]["progress"] == .4
    assert result["corners"][0]["source_urls"] == ["https://example.test/circuit-map"]
    assert result["straights"][0]["progress"] == .98


def test_lookup_wraps_and_respects_manual_origin_and_reverse_direction():
    length = get_track("silverstone")["length_m"]
    first = point_at_progress("silverstone", 0)
    assert point_at_progress("silverstone", 1) == first
    assert point_at_progress("silverstone", -0.75) == point_at_progress("silverstone", 0.25)
    expected = point_at_distance("silverstone", 300)
    aligned = point_at_distance("silverstone", 100, origin_m=200)
    assert aligned["x"] == expected["x"] and aligned["y"] == expected["y"]
    reversed_point = point_at_distance("silverstone", 100, origin_m=400, reverse=True)
    assert reversed_point["x"] == expected["x"] and reversed_point["y"] == expected["y"]
    assert reversed_point["tangent_x"] == -expected["tangent_x"]
    assert reversed_point["tangent_y"] == -expected["tangent_y"]
    assert math.hypot(first["tangent_x"], first["tangent_y"]) == pytest.approx(1)
    assert point_at_distance("silverstone", length + 300)["progress"] == pytest.approx(300 / length)


@pytest.mark.parametrize("track_id,progress,span,direction", [
    ("monza", .162, .004, "right"),
    ("monza", .169, .004, "left"),
    ("silverstone", .042, .01, "right"),
    ("silverstone", .628, .01, "left"),
])
def test_acc_map_corner_handedness_matches_driving_direction(track_id, progress, span, direction):
    # Official Monza describes Rettifilo as right then left; Silverstone describes
    # Copse as right and Farm as left. Check the actual displayed path in source
    # traversal order, independently of the coordinate conversion implementation.
    # Source links and the upstream coordinate convention are in tracks/README.md.
    before, apex, after = [point_at_progress(track_id, progress + offset * span)
                           for offset in (-1, 0, 1)]
    incoming = (apex["x"] - before["x"], apex["y"] - before["y"])
    outgoing = (after["x"] - apex["x"], after["y"] - apex["y"])
    cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
    # Positive is clockwise/right for screen coordinates (y increases downward).
    assert cross > 1e-6 if direction == "right" else cross < -1e-6


@pytest.mark.parametrize("track_id", EXPECTED_LENGTHS)
def test_acc_coordinate_conversion_preserves_every_along_lap_distance(track_id):
    track = get_track(track_id)
    source = json.loads((ASSETS / track["source"]["local_file"]).read_bytes())
    rows = [[(left[axis] + right[axis]) / 2 for axis in ("x", "z")]
            for left, right in zip(source["leftEdge"], source["rightEdge"])]
    old_cartesian_projection = validate_geometry(rows, track["length_m"])
    assert track["normalization"]["source_coordinate_system"] == "acc_xz"
    assert track["geometry_length_m"] == old_cartesian_projection["geometry_length_m"]
    assert [p["distance_m"] for p in track["points"]] == [p["distance_m"] for p in old_cartesian_projection["points"]]
    # The correction is solely a display reflection; it must not reverse traversal
    # or move a saved along-lap origin to a different source point.
    for corrected, old in zip(track["points"], old_cartesian_projection["points"]):
        assert corrected["x"] == old["x"]
        assert corrected["y"] == pytest.approx(1 - old["y"], abs=1e-14)


def test_map_click_roundtrip_returns_selection_not_vehicle_position():
    point = point_at_progress("silverstone", 0.371623)
    selected = nearest_progress("silverstone", point["x"], point["y"])
    assert selected["progress"] == pytest.approx(0.371623, abs=1e-8)
    assert selected["distance_to_centerline_m"] == pytest.approx(0, abs=1e-6)
    assert selected["kind"] == "map_selection"
    assert not selected["ambiguous"]


def test_resampled_path_has_uniform_nominal_distances_and_closure():
    points = resample_closed_path("spa", 128)
    assert len(points) == 129
    assert points[0]["x"] == points[-1]["x"]
    assert points[0]["y"] == points[-1]["y"]
    assert all(b["distance_m"] - a["distance_m"] == pytest.approx(7004 / 128) for a, b in zip(points, points[1:]))


def test_public_records_cannot_mutate_cached_track_and_list_omits_geometry():
    track = get_track("monza")
    track["points"][0]["x"] = -100
    track["source"]["license"] = "changed"
    fresh = get_track("monza")
    assert fresh["points"][0]["x"] >= 0
    assert fresh["source"]["license"] == "AGPL-3.0"
    listing = list_tracks()
    assert {track["id"] for track in listing} == set(EXPECTED_LENGTHS)
    assert all("points" not in track for track in listing)
    json.dumps(listing, allow_nan=False)
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (ASSETS / "LICENSE-RaceIQ-AGPL-3.0.txt").read_text()
    assert "CC BY-NC-SA 4.0" in (ASSETS / "Lovely-README.md").read_text()


@pytest.mark.parametrize("rows,length", [([], None),
                                         ([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]], None),
                                         ([[0, 0], [100, 0], [100, 100], [0, 100]], None),
                                         ([[0, 0], [100, 0], [100, float("nan")], [0, 0]], None),
                                         ([[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]], 2000)])
def test_malformed_or_open_geometry_is_rejected(rows, length):
    with pytest.raises(ValueError):
        validate_geometry(rows, length)


def test_geometry_validation_accepts_closed_metric_path_without_width_claims():
    track = validate_geometry([[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]])
    assert track["length_m"] == 400
    assert "source_width_left_m" not in track["points"][0]


def test_geometry_coordinate_system_is_explicit_and_cartesian_default_is_preserved():
    rows = [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]
    cartesian = validate_geometry(rows)
    acc = validate_geometry(rows, coordinate_system="acc_xz")
    assert cartesian["normalization"]["source_coordinate_system"] == "cartesian_xy"
    assert cartesian["points"][0]["y"] > cartesian["points"][2]["y"]
    assert acc["points"][0]["y"] < acc["points"][2]["y"]
    with pytest.raises(ValueError, match="coordinate system"):
        validate_geometry(rows, coordinate_system="unknown")


def test_invalid_queries_and_unknown_layouts_are_rejected():
    with pytest.raises(KeyError):
        get_track("old_silverstone")
    with pytest.raises(ValueError):
        get_track(None)
    for bad in (float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            point_at_progress("silverstone", bad)
    with pytest.raises(ValueError):
        nearest_progress("silverstone", -1, 0.5)
    with pytest.raises(ValueError):
        resample_closed_path("silverstone", 2)


def test_track_library_api_has_geometry_metadata_and_404(tmp_path):
    with TestClient(create_app(root=tmp_path)) as client:
        listing = client.get("/api/tracks")
        assert listing.status_code == 200
        assert len(listing.json()) == len(EXPECTED_LENGTHS)
        track = client.get("/api/tracks/silverstone")
        assert track.status_code == 200
        assert track.json()["points"]
        assert track.json()["start_finish_verified"] is False
        assert client.get("/api/tracks/unknown").status_code == 404
