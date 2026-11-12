"""Recording deletion API checks; every file and database is a temporary fixture."""
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import pytest

from main import create_app
from telemetry.vision import HUDExtractor
from tests.test_engine import make_video, observed_frame


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(HUDExtractor, "extract", observed_frame)
    with TestClient(create_app(root=tmp_path)) as client:
        yield client


def saved_recording(client, name="video.mp4", managed=True, path=None):
    engine = client.app.state.engine
    if path is None:
        path = engine.root / ("data/uploads" if managed else "") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        make_video(path)
    identifier = engine.store.create_session({"kind": "file", "path": str(path), "name": name})
    for timestamp in (0, .1, .2):
        engine.store.add_sample(identifier, {"time": timestamp, "active": True, "hud_visible": True,
            "segment_id": 1, "speed": 72, "gear": "3", "throttle": 100, "brake": 0})
    engine.store.save_summary(identifier, {"samples": 3}, [], [], status="completed")
    return identifier, Path(path)


def test_default_deletion_keeps_media_and_invalidates_details_exports_and_frame_cache(client):
    identifier, path = saved_recording(client)
    preview = client.get(f"/api/sessions/{identifier}/deletion")
    assert preview.status_code == 200
    assert preview.json()["can_delete"] and preview.json()["video"]["can_delete"]
    assert client.get(f"/api/sessions/{identifier}/frame.jpg?time=0.1").status_code == 200
    assert client.get(f"/api/sessions/{identifier}/lap-candidates").status_code == 200
    response = client.delete(f"/api/sessions/{identifier}")
    assert response.status_code == 200, response.text
    assert response.json() == {"session_id": identifier, "deleted": True, "video_deleted": False,
                               "video_retained": True, "warnings": []}
    assert path.exists()
    for endpoint in (f"/api/sessions/{identifier}", f"/api/sessions/{identifier}/deletion",
                     f"/api/sessions/{identifier}/frame.jpg?time=0.1",
                     f"/api/sessions/{identifier}/lap-candidates", f"/api/export/{identifier}.csv"):
        assert client.get(endpoint).status_code == 404, endpoint
    assert client.delete(f"/api/sessions/{identifier}").status_code == 404
    assert client.get("/api/sessions").json() == []


def test_explicit_deletion_removes_only_unshared_managed_media(client):
    identifier, path = saved_recording(client)
    other_id, other_path = saved_recording(client, "other.mp4")
    before = client.get(f"/api/sessions/{other_id}").json()
    response = client.delete(f"/api/sessions/{identifier}?delete_video=true")
    assert response.status_code == 200 and response.json()["video_deleted"]
    assert not path.exists() and other_path.exists()
    assert client.get(f"/api/sessions/{other_id}").json() == before


def test_shared_and_original_media_requests_conflict_before_deleting_data(client):
    original, original_path = saved_recording(client, "acc_video.mp4", managed=False)
    shared, shared_path = saved_recording(client)
    alias, _ = saved_recording(client, path=Path("data/uploads") / shared_path.name)
    for identifier, path in ((original, original_path), (shared, shared_path)):
        before = client.get(f"/api/sessions/{identifier}").json()
        response = client.delete(f"/api/sessions/{identifier}?delete_video=true")
        assert response.status_code == 409
        assert path.exists() and client.get(f"/api/sessions/{identifier}").json() == before
    assert client.get(f"/api/sessions/{shared}/deletion").json()["video"]["shared_session_ids"] == [alias]


def test_deletion_requires_localhost_and_same_origin(client):
    identifier, path = saved_recording(client)
    route = f"/api/sessions/{identifier}"
    assert client.delete(route, headers={"Origin": "https://unrelated.example"}).status_code == 403
    # Reuse the running app without nesting another lifespan that would close its store.
    phone = TestClient(client.app, client=("192.168.1.24", 50000))
    try:
        assert phone.get(route + "/deletion").status_code == 403
        assert phone.delete(route).status_code == 403
    finally:
        phone.close()
    assert client.get(route).status_code == 200 and path.exists()
    assert client.delete(route, headers={"Origin": "http://testserver"}).status_code == 200


def test_active_capture_is_blocked_then_ended_session_cannot_resurrect(client):
    identifier = client.app.state.engine.start({"kind": "browser"})["session_id"]
    assert client.delete(f"/api/sessions/{identifier}").status_code == 409
    assert not client.get(f"/api/sessions/{identifier}/deletion").json()["can_delete"]
    assert client.post("/api/control", json={"action": "stop"}).status_code == 200
    assert client.delete(f"/api/sessions/{identifier}").status_code == 200
    assert client.get("/api/status").json()["session_id"] is None
    assert client.post("/api/control", json={"action": "stop"}).status_code == 200
    assert client.get(f"/api/sessions/{identifier}").status_code == 404
    assert client.get("/api/sessions").json() == []


def test_visual_search_blocks_both_recordings_and_shared_file_aliases(client, monkeypatch):
    first, first_path = saved_recording(client, "first.mp4")
    first_alias, _ = saved_recording(client, path=Path("data/uploads") / first_path.name)
    second, second_path = saved_recording(client, "second.mp4")
    second_alias, _ = saved_recording(client, path=Path("data/uploads") / second_path.name)
    unrelated, _ = saved_recording(client, "unrelated.mp4")
    entered, release = threading.Event(), threading.Event()

    def matching(*args, cancel_event, **kwargs):
        entered.set()
        while not release.wait(.01) and not cancel_event.is_set():
            pass
        return {"candidates": []}

    monkeypatch.setattr("telemetry.visual_laps.match_visual_landmark", matching)
    response = client.post("/api/landmark-detection", json={"anchor_session_id": first,
        "target_session_id": second, "anchor_time": 0, "search_start": .1, "search_end": .8})
    assert response.status_code == 200, response.text
    try:
        assert entered.wait(2)
        for identifier in (first, second, first_alias, second_alias):
            preview = client.get(f"/api/sessions/{identifier}/deletion").json()
            assert not preview["can_delete"] and "visual search" in preview["blocked_reason"]
            assert client.delete(f"/api/sessions/{identifier}").status_code == 409
        assert client.delete(f"/api/sessions/{unrelated}").status_code == 200
    finally:
        release.set()
        client.app.state.lap_detection._thread.join(timeout=3)
    assert client.get(f"/api/sessions/{first}/deletion").json()["can_delete"]


def test_active_import_blocks_recording_and_alias_then_terminal_link_is_removed(client, monkeypatch):
    engine, importer = client.app.state.engine, client.app.state.imports
    path = engine.root / "data/uploads/analysis.mp4"
    path.parent.mkdir(parents=True)
    make_video(path)
    entered, release = threading.Event(), threading.Event()
    original_extract = HUDExtractor.extract

    def extract(self, frame, timestamp):
        entered.set()
        assert release.wait(3)
        return original_extract(self, frame, timestamp)

    monkeypatch.setattr(HUDExtractor, "extract", extract)
    response = client.post("/api/imports", json={"kind": "file", "path": str(path)})
    assert response.status_code == 200
    job_id = response.json()["id"]
    try:
        assert entered.wait(2)
        identifier = importer.get(job_id)["session_id"]
        alias, _ = saved_recording(client, path=Path("data/uploads") / path.name)
        assert client.delete(f"/api/sessions/{identifier}").status_code == 409
        preview = client.get(f"/api/sessions/{alias}/deletion").json()
        assert not preview["can_delete"] and "import" in preview["blocked_reason"]
        assert client.delete(f"/api/sessions/{alias}").status_code == 409
    finally:
        release.set()
        importer.worker.join(timeout=3)
    assert importer.get(job_id)["state"] == "completed"
    assert client.delete(f"/api/sessions/{identifier}").status_code == 200
    job = client.get(f"/api/imports/{job_id}").json()
    assert job["session_id"] is None and job["recording_deleted"]
    assert path.exists() and client.get(f"/api/sessions/{alias}").status_code == 200


def test_frame_decoding_finishes_before_concurrent_video_deletion(client, monkeypatch):
    identifier, path = saved_recording(client)
    opened, release, delete_started, delete_finished = [threading.Event() for _ in range(4)]
    responses, errors = {}, []
    import main
    original_open = main.av.open

    def open_video(filename, *args, **kwargs):
        if Path(filename) == path:
            opened.set()
            assert release.wait(3)
        return original_open(filename, *args, **kwargs)

    def frame_request():
        try:
            responses["frame"] = client.get(f"/api/sessions/{identifier}/frame.jpg?time=0.1")
        except Exception as error:
            errors.append(error)

    def delete_request():
        delete_started.set()
        try:
            responses["delete"] = client.delete(f"/api/sessions/{identifier}?delete_video=true")
        except Exception as error:
            errors.append(error)
        finally:
            delete_finished.set()

    monkeypatch.setattr(main.av, "open", open_video)
    frame = threading.Thread(target=frame_request)
    deletion = threading.Thread(target=delete_request)
    frame.start()
    try:
        assert opened.wait(2)
        deletion.start()
        assert delete_started.wait(1)
        assert not delete_finished.wait(.1)
        assert path.exists()
    finally:
        release.set()
        frame.join(timeout=3)
        if deletion.ident is not None:
            deletion.join(timeout=3)
    assert not frame.is_alive() and not deletion.is_alive() and not errors
    assert responses["frame"].status_code == responses["delete"].status_code == 200
    assert not path.exists()
