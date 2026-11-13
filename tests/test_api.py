"""Exercise the public API and lifespan using an isolated SQLite database."""

import csv
import io

import cv2
from fastapi.testclient import TestClient
import numpy as np
import pytest

from main import create_app
from telemetry.vision import HUDExtractor


@pytest.fixture
def client(tmp_path, monkeypatch):
    def extract(self, frame, timestamp):
        return {"time": timestamp, "hud_visible": True, "confidence": 0.95,
                "throttle": 95, "brake": 0, "speed": 72, "gear": "3",
                "lap_time": "01:30.125", "lap_time_seconds": 90.125}

    monkeypatch.setattr(HUDExtractor, "extract", extract)
    app = create_app(root=tmp_path)
    with TestClient(app) as test_client:
        yield test_client
    assert app.state.engine.store._closed


def frame_bytes():
    return cv2.imencode(".jpg", np.zeros((180, 320, 3), dtype=np.uint8))[1].tobytes()


@pytest.mark.parametrize("resource", [
    "/", "/static/app.js", "/static/styles.css", "/static/credits.js", "/static/brand/accc-mark.svg",
])
def test_app_interface_cannot_remain_cached_across_updates(client, resource):
    response = client.get(resource)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    if resource.startswith("/static/"):
        unchanged = client.get(resource, headers={"If-None-Match": response.headers["etag"]})
        assert unchanged.status_code == 304
        assert unchanged.headers["cache-control"] == "no-store"


def test_seeded_recording_csv_and_json_preserve_measured_fields(client):
    # Seed measured samples directly; live ingestion is not a public endpoint.
    source = client.app.state.engine.start({"kind": "browser"})
    session_id = source["session_id"]
    for timestamp in (0, 0.1):
        response = client.app.state.engine.browser_frame(frame_bytes(), timestamp)
        assert response["accepted"]
    assert client.get("/api/preview.jpg").headers["content-type"] == "image/jpeg"
    assert len(client.get("/api/history").json()) == 2
    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["session"]["sample_count"] == 2
    assert detail["samples"][1]["lap_time"] == "01:30.125"
    export = client.get(f"/api/export/{session_id}.csv")
    assert export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert rows[1]["segment_id"] == "1"
    assert rows[1]["status"] == "driving"
    assert rows[1]["speed_kph"] == "72.0"
    assert float(rows[1]["estimated_distance_m"]) == pytest.approx(2)
    assert float(rows[1]["estimated_longitudinal_accel_m_s2"]) == 0
    assert float(rows[1]["lap_time_s"]) == 90.125
    assert client.post("/api/control", json={"action": "stop"}).status_code == 200
    assert client.get("/api/sessions").json()[0]["status"] == "completed"


def test_profile_validation_is_atomic_and_calibration_breaks_continuity(client):
    client.app.state.engine.start({"kind": "browser"})
    client.app.state.engine.browser_frame(frame_bytes(), 0)
    original = client.get("/api/profile").json()
    bad = client.put("/api/profile", json={"rois": {"throttle": [0.9, 0.9, 0.2, 0.2]}})
    assert bad.status_code == 400
    assert client.get("/api/profile").json() == original
    good = client.put("/api/profile", json={"name": "My HUD"})
    assert good.status_code == 200
    client.app.state.engine.browser_frame(frame_bytes(), 0.1)
    status = client.get("/api/status").json()
    assert status["latest"]["segment_id"] == 2
    assert status["summary"]["active_seconds"] == 0
    assert status["events"][-1]["reason"] == "calibration_changed"


def test_validation_and_failed_upload_leave_no_capture_or_import(client):
    assert client.post("/api/control", json={"action": "pause"}).status_code == 400
    assert client.post("/api/source", json={"kind": "url", "url": "https://u:p@example.com/live"}).status_code == 400
    assert client.post("/api/control", json={"action": "seek", "time": -1}).status_code == 422
    assert client.get("/api/history?seconds=0").status_code == 400
    assert client.get("/api/sessions/absent").status_code == 404
    assert client.get("/api/export/absent.csv").status_code == 404
    assert client.get("/api/preview.jpg").status_code == 404
    assert client.post("/api/frame", content=b"junk", headers={"X-Frame-Time": "nan"}).status_code == 404
    invalid = client.post("/api/upload", files={"file": ("broken.mp4", b"not-video", "video/mp4")})
    assert invalid.status_code == 400
    assert client.get("/api/sources").json() == []


def test_local_analysis_rejects_cross_origin_control_and_remote_access(client):
    denied = client.post("/api/source", json={"kind": "browser"}, headers={"Origin": "https://unrelated.example"})
    assert denied.status_code == 403
    with TestClient(client.app, client=("192.168.1.24", 50000)) as phone:
        assert phone.get("/api/status").status_code == 403
        assert phone.post("/api/source", json={"kind": "browser"}).status_code == 403


def test_live_monitor_and_network_sharing_are_not_app_endpoints(client):
    from starlette.websockets import WebSocketDisconnect

    for path in ("/phone", "/api/network", "/api/qr.png?url=http://localhost", "/static/phone.html"):
        assert client.get(path).status_code == 404
    assert "urls" not in client.get("/api/status").json()
    assert "phone_urls" not in client.get("/api/status").json()
    for kind in ("browser", "device", "twitch", "url"):
        result = client.post("/api/source", json={"kind": kind, "url": "https://example.com/live"})
        assert result.status_code == 400
        assert "saved recording" in result.json()["detail"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?view=phone"):
            pass
    assert client.get("/api/sessions").json() == []


def test_single_recording_trace_omits_reference_and_preserves_session(client):
    session_id = client.app.state.engine.start({"kind": "browser"})["session_id"]
    for timestamp in (0, 0.1, 0.2):
        client.app.state.engine.browser_frame(frame_bytes(), timestamp)
    client.post("/api/control", json={"action": "stop"})
    before = client.get(f"/api/sessions/{session_id}").json()
    for axis in ("distance", "elapsed"):
        response = client.post("/api/trace", json={"session_id": session_id, "axis": axis,
                                                  "range": {"start_time": 0.025, "end_time": 0.175}})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["axis"] == axis
        assert result["current"]["duration"] == pytest.approx(0.15)
        assert result["reference"] is None
        assert result["sessions"]["reference"] is None
        assert not result["delta_available"]
        assert result["points"]
        assert all(point["reference"] is None and point["delta_time_s"] is None for point in result["points"])
        assert any(point["current"]["speed"] == 72 for point in result["points"])
    after = client.get(f"/api/sessions/{session_id}").json()
    assert after["samples"] == before["samples"]
    assert after["laps"] == before["laps"]
    assert len(client.get("/api/sessions").json()) == 1
    assert client.get("/api/status").json()["session_id"] == session_id
    assert client.post("/api/trace", json={"session_id": "absent"}).status_code == 404
    assert client.post("/api/trace", json={"session_id": session_id, "axis": "bad"}).status_code == 422
    assert client.post("/api/trace", json={"session_id": session_id, "range": {"start_time": 1, "end_time": 0}}).status_code == 400


def test_track_library_is_read_only_and_origin_is_unverified(client):
    before = client.get("/api/status").json()
    tracks = client.get("/api/tracks").json()
    assert "silverstone" in {track["id"] for track in tracks}
    layout = client.get("/api/tracks/silverstone").json()
    assert layout["name"] and layout["length_m"] > 5800
    assert layout["start_finish_verified"] is False
    assert layout["requires_origin_confirmation"] is True
    assert len(layout["corners"]) == 18
    assert layout["corners"][0]["name"] == "Copse"
    assert [corner["number"] for corner in layout["corners"]] == list(range(1, 19))
    assert all(0 <= corner["progress"] < 1 for corner in layout["corners"])
    assert len(layout["timing_lines"]) == 3
    assert [line["kind"] for line in layout["timing_lines"]] == ["start_finish", "sector", "sector"]
    assert [line["sector"] for line in layout["timing_lines"]] == [3, 1, 2]
    assert layout["source"]["license"]
    assert layout["points"][-1]["distance_m"] == layout["length_m"]
    assert client.get("/api/tracks/missing").status_code == 404
    assert client.get("/api/status").json()["session_id"] == before["session_id"]
    assert client.get("/api/sessions").json() == []


def test_reviewed_laps_persist_without_replacing_observations(client):
    session_id = client.app.state.engine.start({"kind": "browser"})["session_id"]
    for timestamp in (0, 0.1, 0.2, 0.3):
        client.app.state.engine.browser_frame(frame_bytes(), timestamp)
    endpoint = f"/api/sessions/{session_id}/reviewed-laps"
    lap = {"id": "reviewed-1", "start_time": .025, "end_time": .275}
    assert client.patch(endpoint, json={"lap": lap}).status_code == 400
    client.post("/api/control", json={"action": "stop"})
    before = client.get(f"/api/sessions/{session_id}").json()
    saved = client.patch(endpoint, json={"lap": lap})
    assert saved.status_code == 200, saved.text
    assert saved.json()["reviewed_laps"][0]["boundaries_verified"] is False
    defaults = saved.json()["comparison_defaults"]
    assert defaults["range_id"] == "reviewed-1"
    assert defaults["alignment_mode"] == "raw"
    lap.update(boundaries_verified=True, start_marker="silverstone-gp-sf", end_marker="silverstone-gp-sf")
    verified = client.patch(endpoint, json={"lap": lap}).json()
    assert len(verified["reviewed_laps"]) == 1
    assert verified["comparison_defaults"]["revision"] != defaults["revision"]
    assert verified["comparison_defaults"]["alignment_mode"] == "lap_endpoints"
    lap["landmarks"] = [{"marker_id": "abbey", "time": .15}]
    assert client.patch(endpoint, json={"lap": lap}).json()["comparison_defaults"]["alignment_mode"] == "landmarks"
    after = client.get(f"/api/sessions/{session_id}").json()
    for key in ("samples", "laps", "events", "summary"):
        assert after[key] == before[key]
    assert client.patch(endpoint, json={"lap": {"id": "practice", "start_time": .1, "end_time": .2},
                                        "make_default": False}).json()["comparison_defaults"]["range_id"] == "reviewed-1"


@pytest.mark.parametrize("updates", [
    {"start_time": -1}, {"end_time": 2}, {"end_time": .01},
    {"boundaries_verified": True}, {"start_marker": "silverstone-gp-sf"},
    {"id": "invalid/id"},
    {"landmarks": [{"marker_id": "abbey", "time": .1}]},
])
def test_invalid_reviewed_lap_is_atomic(client, updates):
    session_id = client.app.state.engine.start({"kind": "browser"})["session_id"]
    for timestamp in (0, .1, .2, .3):
        client.app.state.engine.browser_frame(frame_bytes(), timestamp)
    client.post("/api/control", json={"action": "stop"})
    endpoint = f"/api/sessions/{session_id}/reviewed-laps"
    before = client.get(f"/api/sessions/{session_id}").json()
    body = {"lap": {"id": "saved-1", "start_time": .025, "end_time": .275, **updates}}
    assert client.patch(endpoint, json=body).status_code in (400, 422)
    after = client.get(f"/api/sessions/{session_id}").json()
    # Reading the engine's most recent session refreshes its summary timestamp.
    before["session"].pop("updated_at")
    after["session"].pop("updated_at")
    assert after == before
    assert client.patch("/api/sessions/absent/reviewed-laps", json={"lap": {
        "id": "saved-1", "start_time": 0, "end_time": 1}}).status_code == 404


def test_verified_lap_rejects_cuts_and_unordered_markers(client):
    store = client.app.state.engine.store
    session_id = store.create_session({"kind": "file", "name": "fixture"})
    for i in range(11):
        store.add_sample(session_id, {"time": i / 10, "active": True, "hud_visible": True,
                                     "segment_id": 1 if i < 5 else 2, "speed": 100})
    store.save_summary(session_id, {}, status="completed")
    endpoint = f"/api/sessions/{session_id}/reviewed-laps"
    lap = {"id": "reviewed-1", "start_time": 0, "end_time": 1, "boundaries_verified": True,
           "start_marker": "silverstone-gp-sf", "end_marker": "silverstone-gp-sf"}
    assert client.patch(endpoint, json={"lap": lap}).status_code == 400
    lap["end_time"] = .4
    for markers in (
        [{"marker_id": "abbey", "time": .2}, {"marker_id": "farm", "time": .1}],
        [{"marker_id": "abbey", "time": .1}, {"marker_id": "abbey", "time": .2}],
        [{"marker_id": "abbey", "time": .4}],
    ):
        assert client.patch(endpoint, json={"lap": {**lap, "landmarks": markers}}).status_code == 400
    assert not store.get_session(session_id)["metadata"].get("reviewed_laps")


def test_saved_video_preview_uses_requested_original_frame(client):
    import av
    from tests.test_engine import make_video, wait_until

    engine = client.app.state.engine
    path = engine.root / "preview.mp4"
    make_video(path)
    session_id = client.post("/api/source", json={"kind": "file", "path": "preview.mp4", "realtime": False}).json()["session_id"]
    wait_until(lambda: engine.state == "ended")
    reply = client.get(f"/api/sessions/{session_id}/frame.jpg?time=0.5")
    assert reply.status_code == 200
    assert reply.headers["content-type"] == "image/jpeg"
    picture = cv2.imdecode(np.frombuffer(reply.content, np.uint8), cv2.IMREAD_COLOR)
    with av.open(str(path)) as video:
        expected = next(frame for frame in video.decode(video=0) if float(frame.time) >= 0.5)
        expected_mean = expected.to_ndarray(format="bgr24").mean()
    assert abs(picture.mean() - expected_mean) < 3
    for requested, expected_time in ((.524, .5), (.526, .55)):
        nearest = client.get(f"/api/sessions/{session_id}/frame.jpg?time={requested}")
        assert nearest.status_code == 200
        assert float(nearest.headers["X-Source-Time"]) == pytest.approx(expected_time)
    assert client.get(f"/api/sessions/{session_id}/frame.jpg?time=-1").status_code == 400
    assert client.get(f"/api/sessions/{session_id}/frame.jpg?time=1e300").status_code == 404
    live_id = client.app.state.engine.start({"kind": "browser"})["session_id"]
    assert client.get(f"/api/sessions/{live_id}/frame.jpg?time=0").status_code == 404
