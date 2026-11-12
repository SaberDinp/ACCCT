"""Car/circuit metadata and cross-car safety through the public API."""

import copy

import cv2
from fastapi.testclient import TestClient
import numpy as np
import pytest

from main import create_app
from telemetry.vision import HUDExtractor
from tests.test_engine import make_video, wait_until


@pytest.fixture
def client(tmp_path, monkeypatch):
    def extract(self, frame, timestamp):
        return {"time": timestamp, "active": True, "hud_visible": True, "confidence": .95,
                "speed": 100, "gear": "3", "brake": 0, "throttle": 100}

    monkeypatch.setattr(HUDExtractor, "extract", extract)
    with TestClient(create_app(root=tmp_path)) as client:
        yield client


def record(client, context=None, stop=True):
    body = {"kind": "browser"}
    if context is not None:
        body["context"] = context
    # Seed a legacy-compatible session; public APIs below still exercise all label/trace operations.
    session_id = client.app.state.engine.start(body)["session_id"]
    frame = cv2.imencode(".jpg", np.zeros((180, 320, 3), np.uint8))[1].tobytes()
    for stamp in (0, .1, .2, .3):
        client.app.state.engine.browser_frame(frame, stamp)
    if stop:
        client.post("/api/control", json={"action": "stop"})
    return session_id


def test_context_patch_changes_only_user_labels_and_round_trips(client):
    session_id = record(client)
    detail = f"/api/sessions/{session_id}"
    before = client.get(detail).json()
    context = {"car_id": "ferrari_296_gt3", "track_id": "silverstone", "platform": "ps5",
               "game_version": "1.10", "conditions": "Dry / 25 C / 30 L", "bop": "Default"}
    response = client.patch(detail + "/context", json=context)
    assert response.status_code == 200, response.text
    value = response.json()["context"]
    assert value["car_name"] == "Ferrari 296 GT3"
    assert value["track_name"] == "Silverstone"
    assert value["identity_source"] == "user"
    after = client.get(detail).json()
    assert after["session"]["metadata"]["telemetry_context"] == context
    assert after["session"]["context"] == value
    assert client.get("/api/sessions").json()[0]["context"] == value
    for field in ("samples", "events", "laps", "summary"):
        assert after[field] == before[field]
    metadata = copy.deepcopy(after["session"]["metadata"])
    metadata.pop("telemetry_context")
    previous = copy.deepcopy(before["session"]["metadata"])
    previous.pop("telemetry_context", None)
    assert metadata == previous
    assert client.patch(detail + "/context", json={}).status_code == 200
    assert client.get(detail).json()["session"]["context"]["identity_source"] == "unknown"


def test_context_validation_is_atomic_and_rejects_recording_edits(client):
    session_id = record(client, {"car_id": "mclaren_720s_gt3", "track_id": "monza"}, stop=False)
    endpoint = f"/api/sessions/{session_id}/context"
    assert client.patch(endpoint, json={"track_id": "silverstone"}).status_code == 400
    client.post("/api/control", json={"action": "stop"})
    before = client.get(f"/api/sessions/{session_id}").json()["session"]["context"]
    for body in ({"car_id": "mclaren"}, {"track_id": "unknown-track"}, {"platform": "ps4"}, {"conditions": []}, {"verified": True}):
        assert client.patch(endpoint, json=body).status_code == 400
        assert client.get(f"/api/sessions/{session_id}").json()["session"]["context"] == before
    assert client.patch("/api/sessions/missing/context", json={}).status_code == 404


def test_context_mutation_retains_localhost_origin_protection(client):
    session_id = record(client)
    endpoint = f"/api/sessions/{session_id}/context"
    assert client.patch(endpoint, json={}, headers={"Origin": "https://untrusted.example"}).status_code == 403
    phone = TestClient(client.app, client=("192.168.1.10", 50000))
    try:
        assert phone.get("/api/catalog").status_code == 403
        assert phone.patch(endpoint, json={}).status_code == 403
    finally:
        phone.close()


def test_cross_car_same_circuit_compares_and_different_circuit_always_rejects(client):
    current = record(client, {"car_id": "mclaren_720s_gt3", "track_id": "silverstone"})
    reference = record(client, {"car_id": "ferrari_296_gt3", "track_id": "silverstone"})
    body = {"current_session_id": current, "reference_session_id": reference, "align_start": True}
    result = client.post("/api/compare", json=body)
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["context"]["different_cars"]
    assert result["delta_available"]
    assert "gearing" in " ".join(result["warnings"])
    client.patch(f"/api/sessions/{reference}/context", json={"track_id": "monza"})
    for confirmed in (False, True):
        response = client.post("/api/compare", json={**body, "confirm_same_track": confirmed})
        assert response.status_code == 400
        assert "different track labels" in response.json()["detail"]


def test_unknown_circuits_keep_raw_overlay_without_claiming_alignment(client):
    current, reference = record(client), record(client)
    body = {"current_session_id": current, "reference_session_id": reference}
    result = client.post("/api/compare", json=body).json()
    assert result["points"] and result["delta_available"]
    assert result["delta_mode"] == "approximate"
    assert result["context"]["track_basis"] == "unknown"
    assert "Circuit identity is unknown" in " ".join(result["warnings"])
    confirmed = client.post("/api/compare", json={**body, "confirm_same_track": True}).json()
    assert confirmed["context"]["same_track"]
    assert confirmed["delta_available"] and confirmed["delta_mode"] == "approximate"
    assert not confirmed["align_start"], "An estimated delta does not confirm the physical start."
    aligned = client.post("/api/compare", json={**body, "align_start": True}).json()
    assert aligned["context"]["track_basis"] == "user_confirmation"
    assert aligned["delta_available"]


def test_import_carries_context_without_guessing_identity_from_its_name(client):
    root = client.app.state.engine.root
    make_video(root / "reference.mp4")
    context = {"car_id": "bmw_m4_gt3", "track_id": "spa", "platform": "pc"}
    response = client.post("/api/imports", json={"kind": "file", "path": "reference.mp4",
                            "name": "Ferrari 296 at Silverstone", "context": context})
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    importer = client.app.state.imports
    wait_until(lambda: importer.get(job_id)["state"] in {"completed", "error"})
    job = importer.get(job_id)
    assert job["state"] == "completed", job
    detail = client.get(f"/api/sessions/{job['session_id']}").json()
    assert detail["session"]["context"]["car_id"] == "bmw_m4_gt3"
    assert detail["session"]["context"]["track_id"] == "spa"
    assert detail["session"]["metadata"]["telemetry_context"]["platform"] == "pc"
    assert detail["samples"]


def test_invalid_import_or_source_context_does_not_start_work(client):
    make_video(client.app.state.engine.root / "context.mp4")
    source = client.post("/api/source", json={"kind": "file", "path": "context.mp4", "context": {"car_id": "mclaren"}})
    assert source.status_code == 400
    imported = client.post("/api/imports", json={"kind": "youtube", "url": "https://youtu.be/c0SJgXUahEM",
                                                "context": {"track_id": "bad"}})
    assert imported.status_code == 400
    assert client.get("/api/sessions").json() == []
    assert client.get("/api/imports").json() == []
