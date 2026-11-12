import pytest

from telemetry.catalog import catalog, get_car, get_circuit, lap_search_defaults, list_cars, list_circuits, resolve_car, resolve_circuit
from telemetry.tracks import list_tracks


def test_current_gt3_roster_and_circuit_matrix_are_complete_but_not_validation():
    value = catalog()
    assert value["counts"] == {"gt3_cars": 31, "circuits": 25, "car_track_pairs": 775}
    assert {item["category"] for item in value["cars"]} == {"GT3"}
    for item in value["cars"] + value["tracks"]:
        assert "supported" not in item and "validated" not in item
        assert item["source_ids"]
    assert "not evidence" in value["scope"]


@pytest.mark.parametrize("platform", [None, "pc", "ps5", "xbox_series"])
def test_modern_platforms_have_same_roster_with_dlc_requirements(platform):
    assert len(list_cars(platform)) == 31
    assert len(list_circuits(platform)) == 25
    assert get_car("ferrari_296_gt3")["content_pack"] == "gtwc_2023"
    assert get_circuit("red_bull_ring")["content_pack"] == "gt2"
    assert get_circuit("nurburgring_24h")["content_pack"] == "nurburgring_24h"


@pytest.mark.parametrize("platform", ["ps4", "xbox_one", "PS5", "", 3, []])
def test_no_unverified_legacy_platform_parity(platform):
    with pytest.raises(ValueError):
        list_cars(platform)


def test_every_identity_and_alias_round_trips_and_is_unique():
    for values, getter, resolver in [(list_cars(), get_car, resolve_car),
                                      (list_circuits(), get_circuit, resolve_circuit)]:
        assert len({item["id"] for item in values}) == len(values)
        for item in values:
            assert getter(item["id"]) == item
            for alias in [item["id"], item["name"], *item["aliases"]]:
                assert resolver(alias)["id"] == item["id"]


def test_variants_and_excluded_classes_cannot_silently_collapse():
    assert resolve_car("McLaren 720S GT3")["id"] == "mclaren_720s_gt3"
    assert resolve_car("McLaren 720S GT3 Evo")["id"] == "mclaren_720s_gt3_evo"
    assert resolve_car("Audi R8 LMS Evo2")["id"] == "audi_r8_lms_gt3_evo_ii"
    assert resolve_car("Mercedes AMG GT3 2023")["id"] == "mercedes_amg_gt3_evo"
    assert resolve_car("Lexus RC-F GT3")["id"] == "lexus_rc_f_gt3"
    for ambiguous in ["McLaren", "Bentley Continental GT3", "Nissan GTR", "Porsche 911 GT3 R",
                      "Porsche 992 GT3 Cup", "Ferrari 488 Challenge Evo", "Lamborghini Huracan ST EVO2",
                      "BMW M4 GT4", "McLaren 720S GT3 Evo onboard Silverstone"]:
        assert resolve_car(ambiguous) is None


@pytest.mark.parametrize("alias,identity", [
    ("Audi R8 Evo 2", "audi_r8_lms_gt3_evo_ii"),
    ("Audi R8 LMS GT3 Evo 2", "audi_r8_lms_gt3_evo_ii"),
    ("Audi R8 LMS Evo II", "audi_r8_lms_gt3_evo_ii"),
    ("Lamborghini Huracan Evo 2", "lamborghini_huracan_gt3_evo2"),
    ("Lamborghini Huracán GT3 EVO 2", "lamborghini_huracan_gt3_evo2"),
    ("Lamborghini Huracan Evo II", "lamborghini_huracan_gt3_evo2"),
])
def test_spaced_second_generation_aliases_never_select_original_evo(alias, identity):
    assert resolve_car(alias)["id"] == identity


def test_circuit_aliases_preserve_distinct_layouts():
    assert resolve_circuit("N\u00fcrburgring GP")["id"] == "nurburgring"
    assert resolve_circuit("N\u00fcrburgring 24h")["id"] == "nurburgring_24h"
    assert resolve_circuit("Nordschleife 24h")["id"] == "nurburgring_24h"
    assert resolve_circuit("Nordschleife") is None
    assert resolve_circuit("Nurburgring Tourist") is None
    assert resolve_circuit("Ricardo Tormo")["id"] == "valencia"
    assert resolve_circuit("COTA")["id"] == "cota"


def test_geometry_subset_keeps_existing_ids_without_claiming_all_maps():
    assert {item["id"] for item in list_tracks()} <= {item["id"] for item in list_circuits()}
    assert "points" not in get_circuit("silverstone")


@pytest.mark.parametrize("track", list_circuits(), ids=lambda track: track["id"])
def test_each_circuit_has_bounded_lap_search_without_truncating_nordschleife(track):
    defaults = lap_search_defaults(track["id"])
    assert defaults["min_lap_seconds"] == 30
    assert defaults["max_lap_seconds"] == (900 if track["id"] == "nurburgring_24h" else 300)
    assert lap_search_defaults()["max_lap_seconds"] >= defaults["max_lap_seconds"]


def test_unknown_circuit_is_not_silently_treated_as_a_short_track():
    with pytest.raises(ValueError, match="Unknown ACC circuit"):
        lap_search_defaults("unrecognized-layout")


def test_catalog_results_are_detached_from_cache():
    value = catalog()
    value["cars"][0]["name"] = "Changed"
    value["platforms"].clear()
    entry = get_car("mclaren_720s_gt3_evo")
    entry["aliases"].append("nonsense")
    assert catalog()["cars"][0]["name"] != "Changed"
    assert list_cars("ps5")
    assert resolve_car("nonsense") is None


@pytest.mark.parametrize("getter", [get_car, get_circuit])
@pytest.mark.parametrize("identity", [None, [], 1, "not_in_acc"])
def test_unknown_or_invalid_identity_is_rejected(getter, identity):
    with pytest.raises(ValueError):
        getter(identity)


@pytest.mark.parametrize("resolver", [resolve_car, resolve_circuit])
def test_empty_alias_is_unresolved_not_a_default(resolver):
    assert resolver(None) is None
    assert resolver("") is None
    with pytest.raises(ValueError):
        resolver([])
