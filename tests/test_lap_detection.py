"""Read-only lap refinement and bounded background detection contracts."""

import copy
import threading

from fastapi.testclient import TestClient
import pytest

from main import create_app
from telemetry.lap_detection import LapDetectionJobs, timer_candidates
from telemetry import visual_laps


def recording(start=0, end=60.25, segment=7):
    samples = []
    for index in range(round((end - start) * 4) + 1):
        stamp = start + index / 4
        samples.append({"time": stamp, "speed": 72, "throttle": 100, "brake": 0,
                        "gear": "3", "active": True, "hud_visible": True,
                        "confidence": 0.95, "segment_id": segment, "lap_valid": True,
                        "lap_time_seconds": stamp % 30})
    return samples


def test_timer_candidates_recover_raw_clock_without_mutating_saved_samples():
    samples = recording()
    for sample in samples:
        sample.update(raw_lap_time_seconds=sample["lap_time_seconds"],
                      lap_time_seconds=None, timer_pending=True, timer_outlier=True)
    before = copy.deepcopy(samples)
    result = timer_candidates(samples)
    assert samples == before
    assert [(lap["start_time"], lap["end_time"]) for lap in result["candidates"]] == [(0, 30), (30, 60)]
    assert all(lap["method"] == "timer_backprojection" and not lap["confirmed"] for lap in result["candidates"])
    assert all(lap["segment_id"] == 7 and lap["end_observed_at"] > lap["end_time"] for lap in result["candidates"])


def test_partial_initial_clock_lap_is_excluded():
    result = timer_candidates(recording(start=1))
    assert [(lap["start_time"], lap["end_time"]) for lap in result["candidates"]] == [(30, 60)]
    assert any("partial" in warning for warning in result["warnings"])


def test_ocr_only_clock_gap_does_not_extend_lap_past_recovered_crossing():
    samples = recording()
    for sample in samples:
        if 27 <= sample["time"] <= 30.5:
            sample["lap_time_seconds"] = None
    result = timer_candidates(samples)
    assert result["candidates"][0]["end_time"] == 30
    assert result["candidates"][0]["end_observed_at"] == 31
    assert result["candidates"][0]["duration"] == 30


def test_hud_gap_excludes_interrupted_lap_but_keeps_next_complete_one():
    samples = recording()
    samples[60].update(active=False, hud_visible=False, segment_id=None, gap_reason="hud_missing")
    result = timer_candidates(samples)
    assert [(lap["start_time"], lap["end_time"]) for lap in result["candidates"]] == [(30, 60)]
    assert any("Interrupted" in warning for warning in result["warnings"])


def test_source_segment_change_excludes_cross_segment_lap():
    samples = recording()
    for sample in samples:
        if sample["time"] >= 35:
            sample["segment_id"] = 8
    result = timer_candidates(samples)
    assert [(lap["start_time"], lap["end_time"], lap["segment_id"]) for lap in result["candidates"]] == [(0, 30, 7)]


def test_source_seek_preserves_distinct_valid_laps_without_crossing_seek():
    samples = recording(end=35) + recording(segment=8)
    result = timer_candidates(samples)
    assert [(lap["start_time"], lap["end_time"], lap["segment_id"]) for lap in result["candidates"]] == [
        (0, 30, 7), (0, 30, 8), (30, 60, 8)]
    assert len({lap["id"] for lap in result["candidates"]}) == 3


def test_absent_timer_has_no_invented_lap_boundaries():
    samples = recording()
    for sample in samples:
        sample["lap_time_seconds"] = None
    result = timer_candidates(samples)
    assert result["candidates"] == []
    assert result["warnings"]
    assert timer_candidates([])["candidates"] == []


@pytest.mark.parametrize("duration", [80, 150, 510, 720, 1200])
def test_timer_candidates_preserve_short_and_long_track_laps(duration):
    samples = recording(end=duration + .5)
    for sample in samples:
        sample["lap_time_seconds"] = sample["time"] % duration
    result = timer_candidates(samples)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["duration"] == duration
    assert not result["candidates"][0]["confirmed"]


def test_jobs_allow_only_one_worker_and_cancel_cooperatively(monkeypatch):
    entered = threading.Event()

    def detect(path, *, cancel_event, progress, **settings):
        progress({"progress": 0.25, "stage": "scanning"})
        entered.set()
        assert cancel_event.wait(2)
        return {"candidates": []}

    monkeypatch.setattr(visual_laps, "find_visual_laps", detect)
    jobs = LapDetectionJobs()
    try:
        first = jobs.start("fixture.mp4", "recording", {"anchor_time": 1})
        assert entered.wait(2)
        assert jobs.get(first["id"])["progress"] == 0.25
        with pytest.raises(ValueError, match="already running"):
            jobs.start("other.mp4", "other", {"anchor_time": 2})
        assert jobs.cancel(first["id"])["state"] == "running"
        jobs._thread.join(2)
        assert not jobs._thread.is_alive()
        assert jobs.get(first["id"])["state"] == "cancelled"
        assert jobs.get(first["id"])["result"] == {"candidates": []}
    finally:
        jobs.close()


def test_jobs_return_independent_copies_and_isolate_nested_settings(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    observed = {}

    def detect(path, *, cancel_event, progress, **settings):
        entered.set()
        assert release.wait(2)
        observed.update(settings)
        return {"candidates": [{"start_time": 1, "end_time": 31}]}

    monkeypatch.setattr(visual_laps, "find_visual_laps", detect)
    jobs = LapDetectionJobs()
    settings = {"anchor_time": 1, "roi": [0.2, 0.2, 0.6, 0.5]}
    try:
        returned = jobs.start("fixture.mp4", "session", settings)
        assert entered.wait(2)
        returned["session_id"] = "changed"
        settings["roi"][0] = 0.9
        release.set()
        jobs._thread.join(2)
        assert observed["roi"][0] == 0.2
        result = jobs.get(returned["id"])
        result["result"]["candidates"][0]["start_time"] = 99
        unchanged = jobs.get(returned["id"])
        assert unchanged["session_id"] == "session"
        assert unchanged["result"]["candidates"][0]["start_time"] == 1
        assert jobs.cancel(returned["id"])["state"] == "completed"
    finally:
        release.set()
        jobs.close()


def test_worker_failure_is_reported_and_future_jobs_can_run(monkeypatch):
    def detect(*args, **kwargs):
        raise ValueError("Unreadable video")

    monkeypatch.setattr(visual_laps, "find_visual_laps", detect)
    jobs = LapDetectionJobs()
    try:
        first = jobs.start("fixture.mp4", "session", {})
        jobs._thread.join(2)
        result = jobs.get(first["id"])
        assert result["state"] == "failed" and result["error"] == "Unreadable video"
        second = jobs.start("fixture.mp4", "session", {})
        jobs._thread.join(2)
        assert jobs.get(second["id"])["state"] == "failed"
        with pytest.raises(KeyError):
            jobs.get("absent")
        with pytest.raises(KeyError):
            jobs.cancel("absent")
    finally:
        jobs.close()


def test_close_cancels_worker_and_prevents_restarting(monkeypatch):
    entered = threading.Event()

    def detect(path, *, cancel_event, progress, **settings):
        entered.set()
        assert cancel_event.wait(2)
        raise ValueError("Cancelled while decoding")

    monkeypatch.setattr(visual_laps, "find_visual_laps", detect)
    jobs = LapDetectionJobs()
    first = jobs.start("fixture.mp4", "session", {})
    assert entered.wait(2)
    jobs.close()
    assert not jobs._thread.is_alive()
    assert jobs.get(first["id"])["state"] == "cancelled"
    with pytest.raises(ValueError, match="shutting down"):
        jobs.start("fixture.mp4", "session", {})
    jobs.close()


def test_completed_job_history_is_bounded(monkeypatch):
    monkeypatch.setattr(visual_laps, "find_visual_laps", lambda *args, **kwargs: {"candidates": []})
    jobs = LapDetectionJobs()
    try:
        ids = []
        for _ in range(22):
            ids.append(jobs.start("fixture.mp4", "session", {})["id"])
            jobs._thread.join(2)
        assert len(jobs._jobs) == 20
        with pytest.raises(KeyError):
            jobs.get(ids[0])
        assert jobs.get(ids[-1])["state"] == "completed"
    finally:
        jobs.close()


@pytest.fixture
def lap_client(tmp_path):
    app = create_app(root=tmp_path)
    with TestClient(app) as client:
        yield client
    assert app.state.engine.store._closed


def save_recording(client, samples, source=None):
    store = client.app.state.engine.store
    session_id = store.create_session(source or {"kind": "browser"})
    for sample in samples:
        store.add_sample(session_id, sample)
    store.save_summary(session_id, {"fixture": True}, laps=[{"id": 99, "complete": False}], status="completed")
    return session_id


def test_lap_candidate_api_is_read_only_and_cached_results_do_not_modify_storage(lap_client):
    session_id = save_recording(lap_client, recording())
    store = lap_client.app.state.engine.store
    before = list(store._db.iterdump())
    original_status = lap_client.get("/api/status").json()
    path = f"/api/sessions/{session_id}/lap-candidates"
    first = lap_client.get(path)
    assert first.status_code == 200
    assert len(first.json()["candidates"]) == 2
    assert lap_client.get(path).json() == first.json()
    assert list(store._db.iterdump()) == before
    assert lap_client.get("/api/status").json()["session_id"] == original_status["session_id"]
    assert lap_client.get("/api/sessions/absent/lap-candidates").status_code == 404


def test_comparison_api_passes_registration_and_exact_boundaries_read_only(lap_client):
    reference = save_recording(lap_client, recording(end=10))
    current_samples = recording(end=12)
    current_samples[10]["speed"] = None
    current = save_recording(lap_client, current_samples)
    store = lap_client.app.state.engine.store
    before = list(store._db.iterdump())
    payload = {"reference_session_id": reference, "current_session_id": current,
               "reference_range": {"start_time": 0.11, "end_time": 9.91},
               "current_range": {"start_time": 0.22, "end_time": 11.52},
               "align_start": True, "align_end": True, "alignment_mode": "landmarks",
               "landmarks": [{"reference_time": 4.13, "current_time": 5.17, "label": "Bridge"}]}
    reply = lap_client.post("/api/compare", json=payload)
    assert reply.status_code == 200, reply.text
    result = reply.json()
    assert result["delta_available"]
    assert result["alignment"]["mode"] == "landmarks"
    assert result["current"]["selection"]["end_time"] == 11.52
    assert result["points"][-1]["delta_time_s"] == pytest.approx(1.5)
    assert list(store._db.iterdump()) == before
    assert lap_client.post("/api/compare", json={**payload, "align_end": False}).status_code == 400
    assert lap_client.post("/api/compare", json={**payload, "alignment_mode": "invented"}).status_code == 422


def test_visual_job_api_validates_source_and_remains_read_only(lap_client, monkeypatch):
    engine = lap_client.app.state.engine
    path = engine.root / "fixture.mp4"
    path.write_bytes(b"Mocked decoder fixture")
    session_id = save_recording(lap_client, recording(), {"kind": "file", "path": str(path)})
    browser_id = save_recording(lap_client, recording())
    entered = threading.Event()

    def detect(source, *, anchor_time, cancel_event, progress, **kwargs):
        assert source == str(path) and anchor_time == 1
        entered.set()
        assert cancel_event.wait(2)
        return {"candidates": []}

    monkeypatch.setattr(visual_laps, "find_visual_laps", detect)
    before = list(engine.store._db.iterdump())
    status = lap_client.get("/api/status").json()
    assert lap_client.post("/api/lap-detection", json={"session_id": browser_id, "anchor_time": 1}).status_code == 400
    assert lap_client.post("/api/lap-detection", json={"session_id": "absent", "anchor_time": 1}).status_code == 404
    assert lap_client.post("/api/lap-detection", json={"session_id": session_id, "anchor_time": -1}).status_code == 422
    reply = lap_client.post("/api/lap-detection", json={"session_id": session_id, "anchor_time": 1})
    assert reply.status_code == 200, reply.text
    job_id = reply.json()["id"]
    assert entered.wait(2)
    assert lap_client.post("/api/lap-detection", json={"session_id": session_id, "anchor_time": 1}).status_code == 400
    assert lap_client.post(f"/api/lap-detection/{job_id}/cancel").status_code == 200
    lap_client.app.state.lap_detection._thread.join(2)
    assert lap_client.get(f"/api/lap-detection/{job_id}").json()["state"] == "cancelled"
    assert lap_client.get("/api/lap-detection/absent").status_code == 404
    assert lap_client.post("/api/lap-detection/absent/cancel").status_code == 404
    assert list(engine.store._db.iterdump()) == before
    assert lap_client.get("/api/status").json()["session_id"] == status["session_id"]


@pytest.mark.parametrize("track_id", [None, "silverstone", "nurburgring_24h"])
@pytest.mark.parametrize("bounds", [{}, {"min_lap_seconds": 500, "max_lap_seconds": 1800}])
def test_visual_job_api_uses_saved_circuit_and_optional_long_lap_bounds(lap_client, monkeypatch, track_id, bounds):
    engine = lap_client.app.state.engine
    path = engine.root / "long-lap.mp4"
    path.write_bytes(b"Mocked decoder fixture")
    session_id = save_recording(lap_client, [], {"kind": "file", "path": str(path)})
    engine.store.update_metadata(session_id, {"telemetry_context": {"track_id": track_id}})
    captured = {}

    def start(source, selected_id, settings):
        captured.update(settings)
        assert source == str(path) and selected_id == session_id
        return {"id": "fixture-job"}

    monkeypatch.setattr(lap_client.app.state.lap_detection, "start", start)
    body = {"session_id": session_id, "anchor_time": 10, **bounds}
    reply = lap_client.post("/api/lap-detection", json=body)
    assert reply.status_code == 200, reply.text
    assert captured["track_id"] == track_id
    assert captured["anchor_time"] == 10
    if bounds:
        assert captured["min_lap_seconds"] == 500
        assert captured["max_lap_seconds"] == 1800
    else:
        # Omitting bounds must reach the detector's circuit-aware defaults.
        assert "min_lap_seconds" not in captured
        assert "max_lap_seconds" not in captured
    assert lap_client.post("/api/lap-detection", json={**body, "max_lap_seconds": 1801}).status_code == 422


def test_cross_recording_landmark_api_uses_shared_worker_without_mutating_sessions(lap_client, monkeypatch):
    engine = lap_client.app.state.engine
    source, target = engine.root / "anchor.mp4", engine.root / "target.mp4"
    source.write_bytes(b"Mocked anchor")
    target.write_bytes(b"Mocked target")
    anchor_id = save_recording(lap_client, recording(), {"kind": "file", "path": str(source)})
    target_id = save_recording(lap_client, recording(), {"kind": "file", "path": str(target)})
    browser_id = save_recording(lap_client, recording())
    entered, release = threading.Event(), threading.Event()
    calls = []

    def match(anchor_path, *, target_path, anchor_time, search_start, search_end, progress, cancel_event, **kwargs):
        calls.append((anchor_path, target_path, anchor_time, search_start, search_end))
        progress({"progress": 0.5, "stage": "matching"})
        entered.set()
        assert release.wait(2)
        return {"candidates": [{"time": 15.25, "confirmed": False}]}

    monkeypatch.setattr(visual_laps, "match_visual_landmark", match)
    body = {"anchor_session_id": anchor_id, "target_session_id": target_id,
            "anchor_time": 12.5, "search_start": 10, "search_end": 20}
    before = list(engine.store._db.iterdump())
    try:
        assert lap_client.post("/api/landmark-detection", json={**body, "target_session_id": "absent"}).status_code == 404
        assert lap_client.post("/api/landmark-detection", json={**body, "target_session_id": browser_id}).status_code == 400
        reply = lap_client.post("/api/landmark-detection", json=body)
        assert reply.status_code == 200, reply.text
        job = reply.json()
        assert job["kind"] == "landmark"
        assert job["session_id"] == anchor_id and job["target_session_id"] == target_id
        assert entered.wait(2)
        assert lap_client.get(f"/api/lap-detection/{job['id']}").json()["progress"] == 0.5
        assert lap_client.post("/api/lap-detection", json={"session_id": anchor_id, "anchor_time": 1}).status_code == 400
        release.set()
        lap_client.app.state.lap_detection._thread.join(2)
        result = lap_client.get(f"/api/lap-detection/{job['id']}").json()
        assert result["state"] == "completed"
        assert result["result"]["candidates"][0]["time"] == 15.25
        assert calls == [(str(source), str(target), 12.5, 10, 20)]
        assert list(engine.store._db.iterdump()) == before
        assert lap_client.get("/api/status").json()["session_id"] is None
    finally:
        release.set()
