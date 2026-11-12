"""Review audio uses temporary recordings only; no test downloads actual media."""
from fractions import Fraction
import threading
import time

import av
from fastapi.testclient import TestClient
import numpy as np
import pytest

from main import create_app
from telemetry.review_audio import ReviewAudio
from tests.test_engine import make_video


def make_audio(path, start=0, duration=.5):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("aac", rate=48000)
        stream.layout = "stereo"
        samples = round(duration * 48000)
        data = np.tile((.15 * np.sin(2 * np.pi * 440 * np.arange(samples) / 48000)).astype(np.float32), (2, 1))
        frame = av.AudioFrame.from_ndarray(data, format="fltp", layout="stereo")
        frame.sample_rate = 48000
        frame.time_base = Fraction(1, 48000)
        frame.pts = round(start * 48000)
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(root=tmp_path)) as client:
        yield client


def recording(client, name="clip.mp4", audio=True, start=0, source=None):
    engine = client.app.state.engine
    path = engine.root / name
    if audio:
        make_audio(path, start=start)
    else:
        make_video(path)
    identifier = engine.store.create_session(source or {"kind": "file", "path": str(path), "name": name})
    engine.store.save_summary(identifier, {}, [], [], status="completed")
    return identifier, path


def wait_audio(client, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/sessions/{identifier}/audio")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["state"] != "preparing":
            return result
        time.sleep(.01)
    pytest.fail("Audio worker did not finish.")


def prepare(client, identifier):
    response = client.post(f"/api/sessions/{identifier}/audio")
    assert response.status_code == 200, response.text
    return wait_audio(client, identifier)


def test_audio_is_on_demand_cached_seekable_and_decodes(client, monkeypatch):
    identifier, path = recording(client)
    endpoint = f"/api/sessions/{identifier}/audio"
    assert client.get(endpoint).json()["state"] == "idle"
    assert client.app.state.review_audio.worker is None
    assert client.get(endpoint + "/media").status_code == 404
    result = prepare(client, identifier)
    assert result["state"] == "ready", result
    assert result["source_start"] == pytest.approx(0, abs=.025)
    assert result["duration"] == pytest.approx(.5, abs=.025)
    response = client.get(result["url"])
    assert response.status_code == 200 and response.headers["content-type"] == "audio/mp4"
    assert response.headers["accept-ranges"] == "bytes"
    media = client.app.state.review_audio._directory(identifier) / "audio.m4a"
    with av.open(str(media)) as container:
        assert not container.streams.video and container.streams.audio[0].codec_context.name == "aac"
        decoded = list(container.decode(audio=0))
        assert len(decoded) > 10
        assert max(abs(decoded[5].to_ndarray().flatten())) > .05
    for header, expected in (("bytes=0-15", response.content[:16]), ("bytes=16-", response.content[16:]),
                             ("bytes=-12", response.content[-12:])):
        piece = client.get(result["url"], headers={"Range": header})
        assert piece.status_code == 206 and piece.content == expected
        assert int(piece.headers["content-length"]) == len(expected)
        assert piece.headers["content-range"].endswith(f"/{len(response.content)}")
    for header in ("bytes=999999999-", "bytes=20-10", "bytes=-0", "bytes=-", "not-bytes", "bytes=0-1,4-5",
                   "bytes=" + "9" * 5000 + "-", "bytes=-" + "9" * 5000):
        assert client.get(result["url"], headers={"Range": header}).status_code == 416
    head = client.head(result["url"])
    assert head.status_code == 200 and not head.content
    assert int(head.headers["content-length"]) == len(response.content)
    cache = ReviewAudio(client.app.state.engine)
    assert cache.status(identifier)["state"] == "ready"
    monkeypatch.setattr(cache, "_convert", lambda *args: pytest.fail("Cached audio was reencoded."))
    assert cache.prepare(identifier)["state"] == "ready"
    assert path.exists()


def test_nonzero_audio_source_timestamp_is_preserved(client):
    identifier, _ = recording(client, start=4.0)
    result = prepare(client, identifier)
    assert result["state"] == "ready", result
    assert result["source_start"] == pytest.approx(4.0, abs=.025)
    assert result["duration"] == pytest.approx(.5, abs=.05)


def test_local_video_without_audio_and_unsaved_source_are_unavailable(client, monkeypatch):
    monkeypatch.setattr(ReviewAudio, "_download", lambda *args: pytest.fail("A local video caused a download."))
    identifier, _ = recording(client, audio=False)
    result = prepare(client, identifier)
    assert result["state"] == "unavailable" and "no audio" in result["message"]
    unsaved = client.app.state.engine.store.create_session({"kind": "browser"})
    assert client.get(f"/api/sessions/{unsaved}/audio").json()["state"] == "unavailable"
    assert client.post(f"/api/sessions/{unsaved}/audio").json()["state"] == "unavailable"


def test_video_only_youtube_import_fetches_audio_only_after_request(client, monkeypatch):
    identifier, path = recording(client, audio=False, source={"kind": "file", "path": "clip.mp4",
                                 "original_url": "https://www.youtube.com/watch?v=c0SJgXUahEM"})
    calls = []

    def download(self, url, directory, stop, started):
        calls.append(url)
        audio = directory / "download.m4a"
        make_audio(audio)
        return audio

    monkeypatch.setattr(ReviewAudio, "_download", download)
    assert client.get(f"/api/sessions/{identifier}/audio").json()["state"] == "idle"
    assert calls == []
    result = prepare(client, identifier)
    assert result["state"] == "ready", result
    assert calls == ["https://www.youtube.com/watch?v=c0SJgXUahEM"]
    assert path.exists()


@pytest.mark.parametrize("url", ["https://youtube.com.evil.example/a", "http://localhost/video.mp4",
    "file:///etc/passwd", "https://user:password@youtube.com/watch?v=a", "https://youtube.com:9999/watch?v=a",
    "https://youtube.com:invalid/watch?v=c0SJgXUahEM", "https://youtube.com/redirect?q=http://localhost/a"])
def test_audio_download_does_not_accept_arbitrary_stored_urls(url):
    assert ReviewAudio._youtube_url({"original_url": url}) is None


@pytest.mark.parametrize("url", ["https://youtu.be/c0SJgXUahEM?si=tracking", "https://m.youtube.com/watch?v=c0SJgXUahEM&t=4",
                                 "https://www.youtube.com/shorts/c0SJgXUahEM"])
def test_stored_youtube_links_are_canonicalized_to_the_same_full_recording(url):
    assert ReviewAudio._youtube_url({"original_url": url}) == "https://www.youtube.com/watch?v=c0SJgXUahEM"


def test_existing_embedded_audio_does_not_download_a_second_copy(client, monkeypatch):
    identifier, _ = recording(client, source={"kind": "file", "path": "clip.mp4",
                                              "original_url": "https://www.youtube.com/watch?v=c0SJgXUahEM"})
    monkeypatch.setattr(ReviewAudio, "_download", lambda *args: pytest.fail("Embedded audio should be used directly."))
    assert prepare(client, identifier)["state"] == "ready"


def test_missing_deleted_and_outside_source_cannot_be_served(client, tmp_path):
    identifier, path = recording(client)
    result = prepare(client, identifier)
    assert result["state"] == "ready"
    directory = client.app.state.review_audio._directory(identifier)
    assert client.delete(f"/api/sessions/{identifier}").status_code == 200
    assert path.exists() and not directory.exists()
    for method, suffix in (("get", ""), ("post", ""), ("get", "/media")):
        assert getattr(client, method)(f"/api/sessions/{identifier}/audio{suffix}").status_code == 404
    unsafe, _ = recording(client, name="unsafe.mp4", source={"kind": "file", "path": str(tmp_path.parent / "outside.mp4")})
    assert client.post(f"/api/sessions/{unsafe}/audio").json()["state"] == "unavailable"
    missing, missing_path = recording(client, name="missing.mp4")
    missing_path.unlink()
    assert client.get(f"/api/sessions/{missing}/audio").json()["state"] == "unavailable"


def test_deleting_during_preparation_cancels_without_resurrecting_cache(client, monkeypatch):
    identifier, path = recording(client)
    entered, release = threading.Event(), threading.Event()
    original = ReviewAudio._convert

    def delayed(self, *args):
        entered.set()
        assert release.wait(3)
        return original(self, *args)

    monkeypatch.setattr(ReviewAudio, "_convert", delayed)
    assert client.post(f"/api/sessions/{identifier}/audio").status_code == 200
    try:
        assert entered.wait(2)
        assert client.delete(f"/api/sessions/{identifier}").status_code == 200
    finally:
        release.set()
        client.app.state.review_audio.worker.join(timeout=3)
    assert path.exists()
    assert not client.app.state.review_audio._directory(identifier).exists()
    assert client.app.state.engine.store.get_session(identifier) is None


def test_only_one_audio_worker_and_cross_origin_or_phone_cannot_prepare(client, monkeypatch):
    first, _ = recording(client, name="first.mp4")
    second, _ = recording(client, name="second.mp4")
    entered, release = threading.Event(), threading.Event()
    original = ReviewAudio._convert

    def delayed(self, *args):
        entered.set()
        assert release.wait(3)
        return original(self, *args)

    monkeypatch.setattr(ReviewAudio, "_convert", delayed)
    endpoint = f"/api/sessions/{first}/audio"
    assert client.post(endpoint, headers={"Origin": "https://unrelated.example"}).status_code == 403
    phone = TestClient(client.app, client=("192.168.1.24", 50000))
    try:
        assert phone.post(endpoint).status_code == 403
    finally:
        phone.close()
    assert client.post(endpoint).status_code == 200
    try:
        assert entered.wait(2)
        assert client.post(endpoint).json()["state"] == "preparing"
        assert client.post(f"/api/sessions/{second}/audio").status_code == 409
    finally:
        release.set()
        client.app.state.review_audio.worker.join(timeout=3)
    assert wait_audio(client, first)["state"] == "ready"
