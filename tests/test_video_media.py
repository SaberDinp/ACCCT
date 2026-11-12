"""Native recording delivery and bounded JPEG fallback, using generated media."""

from fractions import Fraction
import os

import av
import cv2
from fastapi.testclient import TestClient
import numpy as np
import pytest

from main import create_app
from telemetry.vision import HUDExtractor
from tests.test_engine import make_video, observed_frame, wait_until
from tests.test_review_audio import make_audio


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(HUDExtractor, "extract", observed_frame)
    with TestClient(create_app(root=tmp_path)) as client:
        yield client


def recording(client, name="original.mp4", source=None):
    engine = client.app.state.engine
    path = engine.root / name
    make_video(path)
    identifier = engine.store.create_session(source or {"kind": "file", "path": str(path)})
    engine.store.save_summary(identifier, {}, [], [], status="completed")
    return identifier, path


def test_native_video_is_unchanged_and_has_source_metadata(client):
    identifier, path = recording(client)
    engine = client.app.state.engine
    before = engine.status()
    result = client.get(f"/api/sessions/{identifier}/video").json()
    assert result == {"state": "ready", "session_id": identifier, "message": None,
                      "url": f"/api/sessions/{identifier}/video/media", "source_start": 0,
                      "duration": 1, "width": 320, "height": 180, "fps": 20,
                      "video_codec": "mpeg4", "media_type": "video/mp4"}
    response = client.get(result["url"])
    assert response.status_code == 200
    assert response.content == path.read_bytes()
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert engine.status() == before
    assert engine.store.get_session(identifier)["status"] == "completed"


def test_ranges_head_and_if_range_preserve_exact_source_bytes(client):
    identifier, path = recording(client)
    endpoint = f"/api/sessions/{identifier}/video/media"
    data = path.read_bytes()
    for header, expected in (("bytes=0-15", data[:16]), ("bytes=16-", data[16:]),
                             ("bytes=-12", data[-12:]), ("bytes=0-99999999", data)):
        response = client.get(endpoint, headers={"Range": header})
        assert response.status_code == 206 and response.content == expected
        assert int(response.headers["content-length"]) == len(expected)
        assert response.headers["content-range"].endswith(f"/{len(data)}")
    head = client.head(endpoint)
    assert head.status_code == 200 and not head.content
    assert int(head.headers["content-length"]) == len(data)
    partial_head = client.head(endpoint, headers={"Range": "bytes=10-20"})
    assert partial_head.status_code == 206 and not partial_head.content
    assert partial_head.headers["content-length"] == "11"
    for validator in (head.headers["etag"], head.headers["last-modified"]):
        partial = client.get(endpoint, headers={"Range": "bytes=1-4", "If-Range": validator})
        assert partial.status_code == 206 and partial.content == data[1:5]
    changed = client.get(endpoint, headers={"Range": "bytes=1-4", "If-Range": '"obsolete"'})
    assert changed.status_code == 200 and changed.content == data


@pytest.mark.parametrize("header", ["bytes=999999999-", "bytes=20-10", "bytes=-0", "bytes=-", "bad",
    "bytes=0-1,4-5", "bytes=" + "9" * 5000 + "-", "bytes=-" + "9" * 5000])
def test_invalid_video_ranges_are_rejected(client, header):
    identifier, path = recording(client)
    response = client.get(f"/api/sessions/{identifier}/video/media", headers={"Range": header})
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{path.stat().st_size}"
    assert not response.content


def test_current_source_metadata_pins_session_and_rejects_stale_requests(client):
    engine = client.app.state.engine
    first, path = recording(client)
    with engine.lock:
        engine.session_id, engine.source, engine.state = first, {"kind": "file", "path": str(path)}, "paused"
    metadata = client.get("/api/source/video", params={"session_id": first}).json()
    assert metadata["url"] == f"/api/sessions/{first}/video/media"
    assert client.get("/api/source/video/media", params={"session_id": first}).content == path.read_bytes()
    second, second_path = recording(client, "second.mp4")
    with engine.lock:
        engine.session_id, engine.source = second, {"kind": "file", "path": str(second_path)}
    for suffix in ("", "/media"):
        assert client.get("/api/source/video" + suffix, params={"session_id": first}).status_code == 409
    assert client.get(metadata["url"]).content == path.read_bytes()
    assert engine.state == "paused" and engine.session_id == second


@pytest.mark.parametrize("source", [{"kind": "browser"}, {"kind": "url", "url": "http://localhost/private"},
    {"kind": "file", "path": "../outside.mp4"}, {"kind": "file", "path": "missing.mp4"},
    {"kind": "file", "path": "secret.txt"}, {"kind": "file", "path": None}, {"kind": "file", "path": 3}])
def test_unavailable_or_untrusted_sources_cannot_be_streamed(client, source):
    engine = client.app.state.engine
    identifier = engine.store.create_session(source)
    assert client.get(f"/api/sessions/{identifier}/video").json()["state"] == "unavailable"
    assert client.get(f"/api/sessions/{identifier}/video/media").status_code == 404
    with engine.lock:
        engine.source, engine.session_id = source, identifier
    assert client.get("/api/source/video").json()["state"] == "unavailable"
    assert client.get("/api/source/video/media").status_code == 404


def test_symlink_escape_nonvideo_and_deleted_sessions_are_rejected(client, tmp_path):
    engine = client.app.state.engine
    outside = tmp_path.parent / (tmp_path.name + "-outside.mp4")
    make_video(outside)
    try:
        link = tmp_path / "escape.mp4"
        link.symlink_to(outside)
        identifier = engine.store.create_session({"kind": "file", "path": str(link)})
        assert client.get(f"/api/sessions/{identifier}/video/media").status_code == 404
    finally:
        outside.unlink()
    for name, content in (("empty.mp4", b""), ("invalid.mp4", b"not a video")):
        path = tmp_path / name
        path.write_bytes(content)
        identifier = engine.store.create_session({"kind": "file", "path": str(path)})
        assert client.get(f"/api/sessions/{identifier}/video").json()["state"] == "unavailable"
        assert client.get(f"/api/sessions/{identifier}/video/media").status_code == 404
    audio = tmp_path / "audio-only.mp4"
    make_audio(audio)
    identifier = engine.store.create_session({"kind": "file", "path": str(audio)})
    assert client.get(f"/api/sessions/{identifier}/video/media").status_code == 404
    identifier, _ = recording(client)
    assert client.delete(f"/api/sessions/{identifier}").status_code == 200
    for suffix in ("", "/media"):
        assert client.get(f"/api/sessions/{identifier}/video" + suffix).status_code == 404


def test_open_recording_remains_readable_after_its_file_is_deleted(client):
    identifier, path = recording(client)
    expected = path.read_bytes()
    handle, _, opened_id = client.app.state.engine.open_video_media(identifier)
    with handle:
        path.unlink()
        assert opened_id == identifier
        assert handle.read() == expected
    assert client.get(f"/api/sessions/{identifier}/video/media").status_code == 404


def test_path_replaced_with_symlink_after_validation_is_not_followed(client, monkeypatch, tmp_path):
    identifier, path = recording(client)
    outside = tmp_path.parent / (tmp_path.name + "-outside.mp4")
    make_video(outside)
    engine = client.app.state.engine
    original_validate = engine.validate_source

    def replace_after_validation(source):
        result = original_validate(source)
        path.unlink()
        path.symlink_to(outside)
        return result

    monkeypatch.setattr(engine, "validate_source", replace_after_validation)
    try:
        assert client.get(f"/api/sessions/{identifier}/video/media").status_code == 404
    finally:
        outside.unlink()


@pytest.mark.parametrize("feature", ["O_DIRECTORY", "O_NOFOLLOW", "supports_dir_fd"])
def test_portable_file_open_without_posix_directory_apis(client, monkeypatch, feature):
    identifier, path = recording(client)
    original = path.read_bytes()
    monkeypatch.delattr(os, feature)
    response = client.get(f"/api/sessions/{identifier}/video/media", headers={"Range": "bytes=0-127"})
    assert response.status_code == 206 and response.content == original[:128]


def test_portable_open_uses_binary_flag_and_rejects_replaced_path(client, monkeypatch, tmp_path):
    identifier, path = recording(client)
    outside = tmp_path.parent / (tmp_path.name + "-portable.mp4")
    make_video(outside)
    engine = client.app.state.engine
    original_validate, original_open = engine.validate_source, os.open
    monkeypatch.setattr(os, "supports_dir_fd", set())
    # Simulate Windows' binary flag without passing it to this host's syscall.
    binary_flag = 1 << 29
    monkeypatch.setattr(os, "O_BINARY", binary_flag, raising=False)
    observed = []

    def binary_open(filename, flags, *args, **kwargs):
        observed.append(bool(flags & binary_flag))
        return original_open(filename, flags & ~binary_flag, *args, **kwargs)

    monkeypatch.setattr(os, "open", binary_open)
    assert client.get(f"/api/sessions/{identifier}/video/media").content == path.read_bytes()
    assert observed and all(observed)

    def replace_after_validation(source):
        result = original_validate(source)
        path.unlink()
        path.symlink_to(outside)
        return result

    monkeypatch.setattr(engine, "validate_source", replace_after_validation)
    try:
        assert client.get(f"/api/sessions/{identifier}/video/media").status_code == 404
    finally:
        outside.unlink()


def test_media_descriptors_close_for_head_bad_range_and_full_responses(client, monkeypatch):
    identifier, _ = recording(client)
    engine = client.app.state.engine
    original_open = engine.open_video_media
    handles = []

    def track_open(*args, **kwargs):
        result = original_open(*args, **kwargs)
        handles.append(result[0])
        return result

    monkeypatch.setattr(engine, "open_video_media", track_open)
    endpoint = f"/api/sessions/{identifier}/video/media"
    assert client.head(endpoint).status_code == 200
    assert client.get(endpoint, headers={"Range": "invalid"}).status_code == 416
    assert client.get(endpoint, headers={"Range": "bytes=0-15"}).status_code == 206
    assert client.get(endpoint).status_code == 200
    assert all(handle.closed for handle in handles)


def test_preview_is_sharper_throttled_and_does_not_upscale(client, monkeypatch):
    engine = client.app.state.engine
    engine.source = {"kind": "browser"}
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:, ::4] = 255
    original_encode = cv2.imencode
    calls = []

    def encode(*args, **kwargs):
        calls.append(args[1].shape)
        return original_encode(*args, **kwargs)

    monkeypatch.setattr(cv2, "imencode", encode)
    engine._update_preview(frame, 5, now=10)
    first = engine.preview
    engine._update_preview(frame, 5.05, now=10.05)
    assert engine.preview is first and engine.preview_time == 5 and len(calls) == 1
    engine._update_preview(frame, 5.11, now=10.11)
    assert engine.preview_time == 5.11 and len(calls) == 2
    assert calls[0] == (720, 1280, 3)
    response = client.get("/api/preview.jpg")
    assert response.headers["x-source-time"] == "5.110000"
    engine._update_preview(frame[:180, :320], 7, force=True)
    assert cv2.imdecode(np.frombuffer(engine.preview, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (180, 320)


def test_paused_seek_regenerates_still_without_resuming_telemetry(client):
    engine = client.app.state.engine
    path = engine.root / "paused.mp4"
    make_video(path, seconds=3)
    engine.start({"kind": "file", "path": str(path), "realtime": True})
    wait_until(lambda: engine.latest is not None)
    engine.control("pause")
    previous_samples = engine.analyzer.summary()["samples"]
    engine.control("seek", 1.5)
    wait_until(lambda: engine.preview_time is not None and engine.preview_time >= 1.5)
    response = client.get("/api/preview.jpg")
    assert response.status_code == 200 and float(response.headers["x-source-time"]) >= 1.5
    assert engine.state == "paused" and engine.analyzer.summary()["samples"] == previous_samples
    assert cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (180, 320)


def test_saved_frame_size_is_bounded_and_width_is_validated(client):
    identifier, _ = recording(client)
    endpoint = f"/api/sessions/{identifier}/frame.jpg"
    for width in (320, 1280, 3840):
        response = client.get(endpoint, params={"time": .5, "width": width})
        assert response.status_code == 200
        decoded = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
        assert decoded.shape[:2] == (180, 320)
    for width in (0, 319, 3841):
        assert client.get(endpoint, params={"time": .5, "width": width}).status_code == 400


def test_hd_recording_retains_native_pixels_and_nonzero_source_time(client):
    engine = client.app.state.engine
    path = engine.root / "hd.mp4"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=20)
        stream.width, stream.height, stream.pix_fmt = 1920, 1080, "yuv420p"
        for index in range(3):
            image = np.zeros((1080, 1920, 3), np.uint8)
            image[:, (index * 10):, :] = 150
            frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            frame.pts, frame.time_base = 80 + index, Fraction(1, 20)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    identifier = engine.store.create_session({"kind": "file", "path": str(path)})
    info = client.get(f"/api/sessions/{identifier}/video").json()
    assert info["width"] == 1920 and info["height"] == 1080
    assert info["source_start"] == pytest.approx(4) and info["fps"] == 20
    assert client.get(info["url"]).content == path.read_bytes()
    for width, expected in ((1280, (720, 1280)), (1920, (1080, 1920)), (3840, (1080, 1920))):
        response = client.get(f"/api/sessions/{identifier}/frame.jpg", params={"time": 4.05, "width": width})
        assert response.status_code == 200 and float(response.headers["x-source-time"]) == pytest.approx(4.05)
        image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
        assert image.shape[:2] == expected


def test_native_recording_media_is_local_only(client):
    identifier, path = recording(client)
    endpoint = f"/api/sessions/{identifier}/video/media"
    local = client.get(endpoint, headers={"Range": "bytes=0-15"})
    assert local.status_code == 206 and local.content == path.read_bytes()[:16]
    remote = TestClient(client.app, client=("192.168.1.24", 50000))
    try:
        response = remote.get(endpoint, headers={"Range": "bytes=0-15"})
        assert response.status_code == 403
        assert "locally" in response.json()["detail"]
    finally:
        remote.close()
