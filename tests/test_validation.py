from concurrent.futures import ThreadPoolExecutor
import pytest

from telemetry.validation import (CHANNELS, choose_videos, coverage_report, extractor_fingerprint,
                                  index_entries, read_json, reindex_title_hints, save_video_result,
                                  title_candidates, video_result, write_json)


@pytest.fixture
def extractor_root(tmp_path):
    root = tmp_path / "extractor"
    (root / "telemetry").mkdir(parents=True)
    for name in ("vision.py", "profiles.py", "calibration.py", "steering.py", "analysis.py"):
        (root / "telemetry" / name).write_text(name)
    (root / "scripts").mkdir()
    (root / "scripts/validate_corpus.py").write_text("frame sampling and discontinuity handling")
    return root


def corpus(tmp_path, fingerprint, channels=None, report_mode="full"):
    folder = tmp_path / "corpus"
    identity = "abcdefghijk"
    write_json(folder / "manifest.json", {"schema": 1, "searches": [], "videos": {
        identity: {"id": identity, "car_id": "mclaren_720s_gt3_evo", "track_id": "silverstone"}}})
    write_json(folder / "results" / (identity + ".json"), {
        "status": "completed", "mode": report_mode, "fingerprint": fingerprint})
    write_json(folder / "reviews" / (identity + ".json"), {
        "result": "pass", "identity_confirmed": True, "lap_boundaries_confirmed": True,
        "fingerprint": fingerprint, "reviewer": "Independent reviewer",
        "evidence": {"labels": "manual-labels.json"},
        "channels": channels if channels is not None else dict.fromkeys(CHANNELS, "pass")})
    return folder


def selected_pair(report):
    return next(pair for pair in report["pairs"] if pair["candidates"])


def test_full_review_requires_current_fingerprint_and_all_channels(tmp_path, extractor_root):
    folder = corpus(tmp_path, extractor_fingerprint(extractor_root))
    report = coverage_report(folder, target=1, root=extractor_root)
    pair = selected_pair(report)
    assert pair["reviewed"] == 1
    assert pair["state"] == "review_target_met"
    assert pair["reviewed_channels"] == dict.fromkeys(CHANNELS, 1)
    assert report["pairs_review_target_met"] == 1
    assert choose_videos(folder, "full", 1, root=extractor_root) == []


def test_missing_steering_only_gets_partial_channel_review(tmp_path, extractor_root):
    channels = {**dict.fromkeys(CHANNELS, "pass"), "steering": "not_visible"}
    folder = corpus(tmp_path, extractor_fingerprint(extractor_root), channels)
    report = coverage_report(folder, target=1, root=extractor_root)
    pair = selected_pair(report)
    assert pair["reviewed"] == 0
    assert pair["partial_reviews"] == 1
    assert pair["reviewed_channels"]["steering"] == 0
    assert pair["reviewed_channels"]["brake"] == 1
    assert pair["state"] == "channel_review_only"
    assert report["pairs_review_target_met"] == 0


@pytest.mark.parametrize("channels", [dict.fromkeys(CHANNELS, "not_visible"), {},
                                      {**dict.fromkeys(CHANNELS, "pass"), "gear": "fail"},
                                      {**dict.fromkeys(CHANNELS, "pass"), "gear": {}}, None])
def test_absent_invalid_or_failed_channel_reviews_do_not_verify(tmp_path, extractor_root, channels):
    folder = corpus(tmp_path, extractor_fingerprint(extractor_root), channels)
    if channels is None:
        review = read_json(folder / "reviews/abcdefghijk.json")
        review.pop("channels")
        write_json(folder / "reviews/abcdefghijk.json", review)
    pair = selected_pair(coverage_report(folder, target=1, root=extractor_root))
    assert pair["reviewed"] == 0
    assert pair["partial_reviews"] == 0
    assert pair["state"] == "analysis_needs_review"


@pytest.mark.parametrize("fingerprint", [None, "", "invalid", "a" * 63])
def test_missing_or_malformed_matching_fingerprints_cannot_verify(tmp_path, extractor_root, fingerprint):
    folder = corpus(tmp_path, fingerprint)
    report = coverage_report(folder, target=1, root=extractor_root)
    pair = selected_pair(report)
    assert pair["reviewed"] == 0
    assert pair["historical_reviewed"] == 0
    assert report["pairs_review_target_met"] == 0
    assert choose_videos(folder, "full", 1, root=extractor_root)


def test_changed_extractor_invalidates_review_and_schedules_reanalysis(tmp_path, extractor_root):
    folder = corpus(tmp_path, extractor_fingerprint(extractor_root))
    (extractor_root / "telemetry/vision.py").write_text("new extractor")
    report = coverage_report(folder, target=1, root=extractor_root)
    pair = selected_pair(report)
    assert pair["analyzed"] == 0 and pair["reviewed"] == 0
    assert pair["state"] == "stale_analysis"
    assert pair["historical_reviewed"] == 1
    assert report["stale_results"] == 1
    assert choose_videos(folder, "full", 1, root=extractor_root)[0]["id"] == "abcdefghijk"
    assert choose_videos(folder, "smoke", 1, root=extractor_root)[0]["id"] == "abcdefghijk"


def test_smoke_cannot_be_reviewed_as_a_full_lap(tmp_path, extractor_root):
    folder = corpus(tmp_path, extractor_fingerprint(extractor_root), report_mode="smoke")
    pair = selected_pair(coverage_report(folder, target=1, root=extractor_root))
    assert pair["smoke_checked"] == 1 and pair["reviewed"] == 0
    assert choose_videos(folder, "smoke", 1, root=extractor_root) == []
    assert choose_videos(folder, "full", 1, root=extractor_root)


@pytest.mark.parametrize("target", [0, -1, True, False, "3", 1.5, None])
def test_review_target_must_be_a_positive_integer(tmp_path, extractor_root, target):
    with pytest.raises(ValueError):
        coverage_report(tmp_path, target=target, root=extractor_root)


@pytest.mark.parametrize("title,car_id,track_id", [
    ("ACC McLaren 720S GT3 EVO at Silverstone", "mclaren_720s_gt3_evo", "silverstone"),
    ("ACC McLaren 720S GT3 at Silverstone", "mclaren_720s_gt3", "silverstone"),
    ("ACC Audi R8 LMS GT3 Evo II - Nurburgring 24h", "audi_r8_lms_gt3_evo_ii", "nurburgring_24h"),
    ("ACC LFM S13 licence - Audi R8 EVO 2 GT3 - Misano", "audi_r8_lms_gt3_evo_ii", "misano"),
    ("ACC Lamborghini Huracan EVO 2 at Monza", "lamborghini_huracan_gt3_evo2", "monza"),
    ("ACC Lamborghini Huracan GT3 EVO 2 at Spa", "lamborghini_huracan_gt3_evo2", "spa"),
    ("ACC Ferrari 296 GT3 vs McLaren 720S GT3 Evo at Monza", None, "monza"),
    ("ACC Ferrari 296 GT3 Monza versus Silverstone", "ferrari_296_gt3", None),
    ("ACC McLaren 720S GT3 vs McLaren 720S GT3 Evo at Monza", None, "monza"),
    ("ACC BMW M4 GT3 Evo at Spa", None, "spa"),
    ("ACC Ferrari 296 GT3 Nurburgring Nordschleife", "ferrari_296_gt3", None),
    ("ACC Porsche 992 GT3 Cup at Spa", None, None),
    ("iRacing Ferrari 296 GT3 Spa", None, None),
])
def test_title_hints_reject_different_cars_and_tracks_but_accept_nested_variants(title, car_id, track_id):
    assert title_candidates(title) == {"car_id": car_id, "track_id": track_id}


def test_indexed_candidates_remain_unverified_and_ambiguous_titles_are_not_added(tmp_path):
    added = index_entries(tmp_path, {"entries": [
        {"id": "abcdefghijk", "title": "ACC McLaren 720S GT3 Evo Silverstone"},
        {"id": "abcdefghijl", "title": "ACC Ferrari 296 GT3 vs McLaren 720S GT3 Evo Monza"},
        {"id": "../unsafe/xx", "title": "ACC McLaren 720S GT3 Evo Silverstone"},
    ]}, "manual test")
    assert added == 1
    assert read_json(tmp_path / "manifest.json")["videos"]["abcdefghijk"]["identity_basis"] == "unverified_title"


def test_json_writes_are_unique_atomic_and_leave_no_temporary_files(tmp_path):
    destination = tmp_path / "coverage.json"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda number: write_json(destination, {"n": number, "values": [number] * 100}), range(60)))
    value = read_json(destination)
    assert value["values"] == [value["n"]] * 100
    assert list(tmp_path.iterdir()) == [destination]
    before = destination.read_bytes()
    with pytest.raises(ValueError):
        write_json(destination, {"nan": float("nan")})
    assert destination.read_bytes() == before


def test_failed_current_run_requires_retry_but_changed_extractor_retries(tmp_path, extractor_root):
    folder = corpus(tmp_path, extractor_fingerprint(extractor_root))
    report_path = folder / "results/abcdefghijk.json"
    report = read_json(report_path)
    report["status"] = "error"
    write_json(report_path, report)
    assert choose_videos(folder, "full", 1, root=extractor_root) == []
    assert choose_videos(folder, "full", 1, retry=True, root=extractor_root)
    (extractor_root / "telemetry/vision.py").write_text("changed")
    assert choose_videos(folder, "full", 1, root=extractor_root)


def test_smoke_rerun_preserves_full_result_and_review(tmp_path, extractor_root):
    fingerprint = extractor_fingerprint(extractor_root)
    folder = corpus(tmp_path, fingerprint)
    save_video_result(folder, "abcdefghijk", {
        "status": "completed", "mode": "smoke", "fingerprint": fingerprint})
    assert video_result(folder, "abcdefghijk", fingerprint)["mode"] == "full"
    assert selected_pair(coverage_report(folder, root=extractor_root))["reviewed"] == 1
    assert choose_videos(folder, "full", 1, root=extractor_root) == []
    assert read_json(folder / "results/abcdefghijk/smoke.json")["mode"] == "smoke"


def test_new_smoke_fingerprint_does_not_masquerade_as_current_full_run(tmp_path, extractor_root):
    folder = corpus(tmp_path, extractor_fingerprint(extractor_root))
    (extractor_root / "telemetry/vision.py").write_text("changed")
    fingerprint = extractor_fingerprint(extractor_root)
    save_video_result(folder, "abcdefghijk", {
        "status": "completed", "mode": "smoke", "fingerprint": fingerprint})
    assert video_result(folder, "abcdefghijk", fingerprint)["mode"] == "smoke"
    assert choose_videos(folder, "smoke", 1, root=extractor_root) == []
    assert choose_videos(folder, "full", 1, root=extractor_root)[0]["id"] == "abcdefghijk"
    pair = selected_pair(coverage_report(folder, root=extractor_root))
    assert pair["smoke_checked"] == 1 and pair["reviewed"] == 0


def test_no_hud_run_counts_as_tested_but_not_as_observed_channels(tmp_path, extractor_root):
    fingerprint = extractor_fingerprint(extractor_root)
    folder = corpus(tmp_path, fingerprint, report_mode="smoke")
    save_video_result(folder, "abcdefghijk", {
        "status": "completed", "mode": "smoke", "fingerprint": fingerprint,
        "coverage": dict.fromkeys(CHANNELS, 0)})
    report = coverage_report(folder, root=extractor_root)
    assert report["tested_cars"] == report["tested_circuits"] == 1
    assert report["pairs_with_observed_core_channels"] == 0


def test_failed_full_retry_cannot_erase_success_after_later_smoke(tmp_path, extractor_root):
    fingerprint = extractor_fingerprint(extractor_root)
    folder = corpus(tmp_path, fingerprint)
    save_video_result(folder, "abcdefghijk", {
        "status": "error", "mode": "full", "fingerprint": fingerprint, "error": "Interrupted"})
    save_video_result(folder, "abcdefghijk", {
        "status": "completed", "mode": "smoke", "fingerprint": fingerprint})
    full = video_result(folder, "abcdefghijk", fingerprint, "full")
    assert full["status"] == "completed"
    assert full["last_attempt_error"] == "Interrupted"
    assert video_result(folder, "abcdefghijk", fingerprint, "smoke", exact_mode=True)["mode"] == "smoke"
    assert video_result(folder, "abcdefghijk", fingerprint, "smoke")["mode"] == "full"


def test_reindex_keeps_ambiguous_clips_and_manual_labels_but_unassigns_bad_hints(tmp_path, extractor_root):
    original = {"schema": 1, "searches": [{"source": "preserved"}], "videos": {
        "abcdefghijk": {"id": "abcdefghijk", "title": "ACC Ferrari 296 GT3 vs McLaren 720S GT3 Evo at Monza",
                        "identity_basis": "unverified_title", "car_id": "mclaren_720s_gt3_evo", "track_id": "monza",
                        "url": "https://www.youtube.com/watch?v=abcdefghijk", "custom": {"retained": True}},
        "abcdefghijl": {"id": "abcdefghijl", "title": "ACC Ferrari 296 GT3 at Monza",
                        "identity_basis": "user_confirmed", "car_id": "bmw_m4_gt3", "track_id": "silverstone"}}}
    write_json(tmp_path / "manifest.json", original)
    write_json(tmp_path / "results/abcdefghijk.json", {"status": "completed"})
    before_result = (tmp_path / "results/abcdefghijk.json").read_bytes()
    counts = reindex_title_hints(tmp_path)
    updated = read_json(tmp_path / "manifest.json")
    assert counts == {"changed": 1, "unassigned": 1, "videos": 2}
    assert set(updated["videos"]) == set(original["videos"])
    assert updated["videos"]["abcdefghijk"]["car_id"] is None
    assert updated["videos"]["abcdefghijk"]["custom"] == {"retained": True}
    assert updated["videos"]["abcdefghijl"] == original["videos"]["abcdefghijl"]
    assert updated["searches"] == original["searches"]
    assert (tmp_path / "results/abcdefghijk.json").read_bytes() == before_result
    report = coverage_report(tmp_path, root=extractor_root)
    assert report["unassigned_candidates"] == 1
    assert report["total_indexed_videos"] == 2
    assert report["total_candidates"] == 1
    assert [item["id"] for item in choose_videos(tmp_path, "full", 5, root=extractor_root)] == ["abcdefghijl"]
    assert reindex_title_hints(tmp_path)["changed"] == 0
