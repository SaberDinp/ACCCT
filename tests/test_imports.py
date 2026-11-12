"""Reference import isolation, cancellation, persistence, and API contracts."""
import threading

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from main import create_app
from telemetry.vision import HUDExtractor
from tests.test_engine import make_video, wait_until


@pytest.fixture
def client(tmp_path, monkeypatch):
    def extract(self, frame, timestamp):
        return {"time": timestamp, "hud_visible": True, "confidence": .95,
                "speed": 72, "gear": "3", "brake": 0, "throttle": 100,
                "steering": -22, "steering_unit": "hud_percent", "steering_confidence": .95,
                "steering_status": "tracked"}

    monkeypatch.setattr(HUDExtractor, "extract", extract)
    with TestClient(create_app(root=tmp_path)) as client:
        yield client


def test_reference_import_is_independent_and_comparable(client):
    app = client.app
    source_id = client.app.state.engine.start({"kind": "browser"})["session_id"]
    frame = cv2.imencode(".jpg", np.zeros((180, 320, 3), np.uint8))[1].tobytes()
    for timestamp in (0, .1, .2):
        client.app.state.engine.browser_frame(frame, timestamp)
    profile = client.get("/api/profile").json()
    make_video(app.state.engine.root / "reference.mp4")
    reply = client.post("/api/imports", json={"kind": "file", "path": "reference.mp4", "name": "Fast lap"})
    assert reply.status_code == 200
    job_id = reply.json()["id"]
    wait_until(lambda: app.state.imports.get(job_id)["state"] in {"completed", "error"})
    job = client.get(f"/api/imports/{job_id}").json()
    assert job["state"] == "completed", job
    assert job["processed_frames"] == 20
    assert job["session_id"] != source_id
    assert client.get("/api/status").json()["session_id"] == source_id
    assert client.get("/api/status").json()["state"] == "running"
    assert client.get("/api/profile").json() == profile
    saved = client.get("/api/sessions/" + job["session_id"]).json()
    assert saved["session"]["metadata"]["role"] == "reference"
    assert saved["session"]["status"] == "completed"
    assert saved["samples"][0]["steering"] == -22
    assert client.get("/api/sessions/" + job["session_id"] + "/frame.jpg?time=0.5").status_code == 200
    exported = client.get("/api/export/" + job["session_id"] + ".csv").text
    assert "steering_hud_pct" in exported
    assert "hud_percent" in exported
    comparison = client.post("/api/compare", json={"reference_session_id": job["session_id"],
                            "current_session_id": source_id, "axis": "elapsed"})
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["points"]
    assert comparison.json()["delta_available"]
    assert comparison.json()["delta_basis"] == "lap_progress"
    assert len(client.get("/api/imports").json()) == 1


def test_cancel_download_is_joined_and_does_not_create_empty_session(client, monkeypatch):
    importer = client.app.state.imports
    entered = threading.Event()

    def download(job_id, source, stop):
        entered.set()
        stop.wait(3)
        importer._check(stop)

    monkeypatch.setattr(importer, "_download_youtube", download)
    reply = client.post("/api/imports", json={"kind": "youtube", "url": "https://youtu.be/c0SJgXUahEM"})
    job_id = reply.json()["id"]
    assert entered.wait(2)
    duplicate = client.post("/api/imports", json={"kind": "youtube", "url": "https://youtu.be/c0SJgXUahEM"})
    assert duplicate.status_code == 400
    assert client.post(f"/api/imports/{job_id}/cancel").status_code == 200
    importer.worker.join(timeout=3)
    assert not importer.worker.is_alive()
    assert importer.get(job_id)["state"] == "cancelled"
    assert client.get("/api/sessions").json() == []


def test_import_validation_and_errors_are_actionable(client, monkeypatch):
    for body in ({"kind": "file", "path": "../private.mp4"},
                 {"kind": "youtube", "url": "https://not-youtube.example/video"},
                 {"kind": "youtube", "url": "https://user:secret@youtube.com/watch?v=x"},
                 {"kind": "url", "url": "file:///private.mp4"},
                 {"kind": "browser"}):
        assert client.post("/api/imports", json=body).status_code == 400
    assert client.get("/api/imports/unknown").status_code == 404
    assert client.post("/api/imports/unknown/cancel").status_code == 404
    assert client.post("/api/compare", json={"reference_session_id": "missing", "current_session_id": "missing"}).status_code == 404
    importer = client.app.state.imports

    def fail(*_):
        raise ValueError("Public reference unavailable; import a local copy.")

    monkeypatch.setattr(importer, "_download_youtube", fail)
    job_id = client.post("/api/imports", json={"kind": "youtube", "url": "https://youtu.be/c0SJgXUahEM"}).json()["id"]
    wait_until(lambda: importer.get(job_id)["state"] == "error")
    assert "local copy" in importer.get(job_id)["error"]


def test_profile_overrides_do_not_share_live_mutable_state(client):
    root = client.app.state.engine.root
    make_video(root / "manual.mp4")
    before = client.get("/api/profile").json()
    reply = client.post("/api/imports", json={"kind": "file", "path": "manual.mp4", "auto_calibrate": False,
                                             "profile": {"name": "Independent reference"}}).json()
    importer = client.app.state.imports
    wait_until(lambda: importer.get(reply["id"])["state"] in {"completed", "error"})
    session = client.get("/api/sessions/" + importer.get(reply["id"])["session_id"]).json()["session"]
    assert session["metadata"]["profile"]["name"] == "Independent reference"
    assert client.get("/api/profile").json() == before


def test_current_role_and_cancelled_analysis_preserve_partial_data_and_profile(client, monkeypatch):
    importer = client.app.state.imports
    make_video(importer.root / "own.mp4")
    entered, release = threading.Event(), threading.Event()
    extract = HUDExtractor.extract
    calls = 0

    def delayed(self, frame, timestamp):
        nonlocal calls
        calls += 1
        if calls == 6:
            entered.set()
            assert release.wait(3)
        return extract(self, frame, timestamp)

    monkeypatch.setattr(HUDExtractor, "extract", delayed)
    job = importer.start({"kind": "file", "path": "own.mp4", "role": "current",
                          "profile": {"name": "Own recording HUD"}, "auto_calibrate": False})
    try:
        assert entered.wait(3)
        importer.cancel(job["id"])
    finally:
        release.set()
        importer.worker.join(timeout=3)
    final = importer.get(job["id"])
    assert final["state"] == "cancelled"
    assert final["processed_frames"] == 5
    session = importer.store.get_session(final["session_id"])
    assert session["status"] == "interrupted"
    assert session["sample_count"] == 5
    assert session["metadata"]["role"] == session["source"]["role"] == "current"
    assert session["metadata"]["profile"]["name"] == "Own recording HUD"
    assert "analysis_seconds" in session["metadata"]


def test_decoder_failure_keeps_partial_data_and_final_metadata(client, monkeypatch):
    importer = client.app.state.imports
    make_video(importer.root / "failure.mp4")
    extract = HUDExtractor.extract
    calls = 0

    def fail(self, frame, timestamp):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError("decoder test failure")
        return extract(self, frame, timestamp)

    monkeypatch.setattr(HUDExtractor, "extract", fail)
    job = importer.start({"kind": "file", "path": "failure.mp4"})
    importer.worker.join(timeout=3)
    final = importer.get(job["id"])
    assert final["state"] == "error"
    assert "decoder test failure" in final["error"]
    session = importer.store.get_session(final["session_id"])
    assert session["status"] == "error"
    assert session["sample_count"] == 3
    assert session["metadata"]["sample_rate_limit_hz"] == 30


def test_worker_initialization_failure_reports_error_instead_of_stuck_queue(client, monkeypatch):
    from telemetry import imports as module

    importer = client.app.state.imports
    make_video(importer.root / "init.mp4")
    construct = module.HUDExtractor
    calls = 0

    def fail_second(profile):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("template initialization failed")
        return construct(profile)

    monkeypatch.setattr(module, "HUDExtractor", fail_second)
    job = importer.start({"kind": "file", "path": "init.mp4"})
    importer.worker.join(timeout=3)
    assert importer.get(job["id"])["state"] == "error"
    assert "initialization" in importer.get(job["id"])["error"]
    assert importer.get(job["id"])["session_id"] is None


def test_cancelled_youtube_download_removes_only_generated_media(client, monkeypatch):
    importer = client.app.state.imports
    entered = threading.Event()

    def download(job_id, source, stop):
        directory = importer.folder / job_id
        directory.mkdir()
        (directory / "media.mp4.part").write_bytes(b"partial download")
        (directory / "download.log").write_text("download diagnostics")
        entered.set()
        stop.wait(3)
        importer._check(stop)

    monkeypatch.setattr(importer, "_download_youtube", download)
    job = importer.start({"kind": "youtube", "url": "https://youtu.be/example"})
    assert entered.wait(3)
    importer.cancel(job["id"])
    importer.worker.join(timeout=3)
    directory = importer.folder / job["id"]
    assert importer.get(job["id"])["state"] == "cancelled"
    assert not (directory / "media.mp4.part").exists()
    assert (directory / "download.log").read_text() == "download diagnostics"


def test_oversized_duration_and_invalid_role_or_profile_fail_cleanly(client, monkeypatch):
    from telemetry import imports as module

    importer = client.app.state.imports
    make_video(importer.root / "long.mp4")
    before = client.get("/api/profile").json()
    for kwargs in ({"role": "unsupported"}, {"profile": {"reference_width": -1}}):
        with pytest.raises(ValueError):
            importer.start({"kind": "file", "path": "long.mp4", **kwargs})
    assert importer.list() == []
    assert client.get("/api/profile").json() == before
    monkeypatch.setattr(module, "MAX_DURATION", 0.1)
    job = importer.start({"kind": "file", "path": "long.mp4"})
    importer.worker.join(timeout=3)
    final = importer.get(job["id"])
    assert final["state"] == "error"
    assert "two hours" in final["error"]
    assert importer.store.get_session(final["session_id"])["sample_count"] == 0


def test_persistence_failure_is_visible_as_terminal_job_error(client, monkeypatch):
    importer = client.app.state.imports
    make_video(importer.root / "storage.mp4")

    def fail(*args, **kwargs):
        raise OSError("test disk full")

    monkeypatch.setattr(importer.store, "save_summary", fail)
    job = importer.start({"kind": "file", "path": "storage.mp4"})
    importer.worker.join(timeout=3)
    final = importer.get(job["id"])
    assert final["state"] == "error"
    assert "disk full" in final["error"]


@pytest.mark.parametrize("reset", [False, True])
def test_nonzero_and_reset_video_timestamps_preserve_samples_and_fair_progress(client, monkeypatch, reset):
    from telemetry import imports as module

    importer = client.app.state.imports
    make_video(importer.root / "timestamps.mp4")
    original_open = module.av.open
    updates = []
    update = importer._update

    def record_update(job_id, **values):
        updates.append(values)
        update(job_id, **values)

    class ShiftedVideo:
        def __init__(self, *args, **kwargs):
            self.container = original_open(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.container, name)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.container.close()

        def decode(self, stream):
            for index, frame in enumerate(self.container.decode(stream)):
                if index == 0:
                    threading.Event().wait(0.02)
                if not reset or index < 10:
                    frame.pts += int(100 / float(frame.time_base))
                yield frame

    monkeypatch.setattr(module.av, "open", ShiftedVideo)
    monkeypatch.setattr(importer, "_update", record_update)
    job = importer.start({"kind": "file", "path": "timestamps.mp4"})
    importer.worker.join(timeout=3)
    final = importer.get(job["id"])
    assert final["state"] == "completed", final
    assert final["processed_frames"] == 20
    assert next(value["progress"] for value in updates if value.get("processed_frames") == 1) == 0
    assert final["realtime_factor"] * final["elapsed_seconds"] < 2
    session = importer.store.get_session(final["session_id"])
    assert session["sample_count"] == 20
    if reset:
        assert any(event.get("reason") == "source_timestamp_reset" for event in session["events"])
        assert session["metadata"]["source_span_seconds"] == pytest.approx(0.9)
    else:
        assert final["source_time"] == pytest.approx(100.95)
        assert session["metadata"]["source_span_seconds"] == pytest.approx(0.95)


@pytest.mark.parametrize("url,canonical", [
    ("https://www.twitch.tv/videos/123456?t=5m", "https://www.twitch.tv/videos/123456"),
    ("https://m.twitch.tv/racer/v/123456", "https://www.twitch.tv/videos/123456"),
    ("https://player.twitch.tv/?video=v123456&parent=example.test", "https://www.twitch.tv/videos/123456"),
    ("https://www.twitch.tv/racer/schedule?vodID=123456", "https://www.twitch.tv/videos/123456"),
    ("https://clips.twitch.tv/FaintLightGullWholeWheat", "https://clips.twitch.tv/FaintLightGullWholeWheat"),
    ("https://www.twitch.tv/racer/clip/Clip-Slug_123?tt_content=url", "https://clips.twitch.tv/Clip-Slug_123"),
    ("https://twitch.tv/clip/ClipSlug", "https://clips.twitch.tv/ClipSlug"),
    ("https://clips.twitch.tv/embed?clip=ClipSlug&parent=example.test", "https://clips.twitch.tv/ClipSlug"),
])
def test_twitch_recording_urls_are_canonicalized_without_live_channel_routing(url, canonical):
    from urllib.parse import urlparse
    from telemetry.imports import _twitch_recording_url

    assert _twitch_recording_url(urlparse(url)) == canonical


@pytest.mark.parametrize("kind,url", [
    ("twitch", "https://www.twitch.tv/racer"),
    ("url", "https://www.twitch.tv/racer"),
    ("twitch", "https://player.twitch.tv/?channel=racer"),
    ("twitch", "https://www.twitch.tv/racer/videos"),
    ("twitch", "https://www.twitch.tv/directory"),
    ("twitch", "https://www.twitch.tv/videos/not-an-id"),
    ("twitch", "https://www.twitch.tv/collections/collection-id"),
    ("twitch", "https://twitch.tv.evil.example/videos/123"),
    ("twitch", "https://www.twitch.tv:8443/videos/123"),
    ("twitch", "https://user:secret@www.twitch.tv/videos/123"),
    ("twitch", "https://www.twitch.tv:invalid/videos/123"),
    ("twitch", "file:///videos/123"),
])
def test_twitch_rejects_live_channels_collections_and_invalid_hosts_before_work(client, kind, url):
    response = client.post("/api/imports", json={"kind": kind, "url": url})
    assert response.status_code == 400
    if url.endswith("/racer") or "?channel=" in url:
        assert "Connect Source" in response.text
    assert client.app.state.imports.list() == []
    assert client.get("/api/sessions").json() == []


@pytest.mark.parametrize("kind,url,canonical", [
    ("twitch", "https://twitch.tv/videos/123456?t=1m", "https://www.twitch.tv/videos/123456"),
    ("url", "https://www.twitch.tv/racer/clip/ExampleClip", "https://clips.twitch.tv/ExampleClip"),
])
def test_twitch_download_routing_persists_completed_recording_and_original_link(client, monkeypatch, kind, url, canonical):
    importer = client.app.state.imports
    calls = []

    def download(job_id, source, stop):
        calls.append(source.copy())
        directory = importer.folder / job_id
        directory.mkdir()
        path = directory / "media.mp4"
        make_video(path)
        return path, "Public Twitch racing clip"

    def wrong_downloader(*args):
        pytest.fail("Twitch URLs must use the archived Twitch downloader")

    monkeypatch.setattr(importer, "_download_twitch", download)
    monkeypatch.setattr(importer, "_download_url", wrong_downloader)
    monkeypatch.setattr(importer, "_download_youtube", wrong_downloader)
    live_id = client.app.state.engine.start({"kind": "browser"})["session_id"]
    response = client.post("/api/imports", json={"kind": kind, "url": url, "role": "reference",
                                              "context": {"track_id": "monza"}})
    assert response.status_code == 200, response.text
    importer.worker.join(timeout=3)
    job = importer.get(response.json()["id"])
    assert job["state"] == "completed", job
    assert len(calls) == 1 and calls[0]["kind"] == "twitch"
    assert calls[0]["url"] == canonical
    session = importer.store.get_session(job["session_id"])
    assert session["source"]["kind"] == "file"
    assert session["source"]["original_url"] == canonical
    assert session["metadata"]["telemetry_context"]["track_id"] == "monza"
    assert session["sample_count"] == 20
    assert client.get("/api/status").json()["session_id"] == live_id


def test_twitch_downloader_preserves_combined_audio_and_uses_bounded_native_hls(client, monkeypatch):
    import json
    from pathlib import Path
    import yt_dlp
    from telemetry import imports as module

    importer = client.app.state.imports
    captured = []

    class CompletedDownload:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured.append(command)
            directory = Path(command[command.index("-o") + 1]).parent
            make_video(directory / "media.mp4")
            (directory / "media.info.json").write_text(json.dumps({"title": "Twitch clip", "duration": 1}))

        def poll(self):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", CompletedDownload)
    path, title = importer._download_twitch("test-twitch", {"url": "https://clips.twitch.tv/ClipSlug"}, threading.Event())
    command = captured[0]
    assert path.is_file() and title == "Twitch clip"
    assert command[-2:] == ["--", "https://clips.twitch.tv/ClipSlug"]
    assert "--no-playlist" in command and "--ignore-config" in command
    assert command[command.index("--socket-timeout") + 1] == "15"
    assert command[command.index("--max-filesize") + 1] == str(module.MAX_BYTES)
    assert command[command.index("--match-filter") + 1] == "!is_live & duration > 0 & duration <= 7200"
    assert command[command.index("--downloader") + 1] == "m3u8:native"
    assert "--hls-use-mpegts" in command
    assert command[command.index("--fixup") + 1] == "never"
    assert "--js-runtimes" not in command
    # Exercise yt-dlp's selector semantics: keep the best combined source, capped
    # at1080p, rather than accidentally choosing a silent higher-resolution feed.
    formats = [{"format_id": identifier, "height": height, "vcodec": "h264", "acodec": audio,
                "ext": "mp4", "url": f"https://example.test/{identifier}.mp4"}
               for identifier, height, audio in [("720-with-audio", 720, "aac"),
                                                  ("1080-with-audio", 1080, "aac"),
                                                  ("1080-silent", 1080, "none"),
                                                  ("2160-with-audio", 2160, "aac")]]
    with yt_dlp.YoutubeDL({"quiet": True}) as downloader:
        select = downloader.build_format_selector(command[command.index("-f") + 1])
        picked = list(select({"formats": formats, "incomplete_formats": False, "has_merged_format": True}))
        assert [item["format_id"] for item in picked] == ["1080-with-audio"]
        silent_only = list(select({"formats": [formats[2]], "incomplete_formats": True, "has_merged_format": False}))
        assert [item["format_id"] for item in silent_only] == ["1080-silent"]


@pytest.mark.parametrize("outcome", ["cancel", "timeout", "oversize", "unavailable"])
def test_twitch_download_lifecycle_stops_process_and_cleans_only_generated_media(client, monkeypatch, outcome):
    from pathlib import Path
    from telemetry import imports as module

    importer = client.app.state.imports
    entered = threading.Event()
    processes = []
    if outcome == "timeout":
        monkeypatch.setattr(module, "MAX_DOWNLOAD_SECONDS", -1)
    if outcome == "oversize":
        monkeypatch.setattr(module, "MAX_BYTES", 8)

    class Download:
        returncode = None
        terminated = False

        def __init__(self, command, **kwargs):
            processes.append(self)
            directory = Path(command[command.index("-o") + 1]).parent
            (directory / "media.mp4.part").write_bytes(b"partial download")
            (directory / "media.info.json").write_text('{"title":"diagnostic metadata"}')
            (directory / "keep.txt").write_text("unrelated file")
            kwargs["stdout"].write(b"Twitch download diagnostics")
            if outcome == "unavailable":
                self.returncode = 1
            entered.set()

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    monkeypatch.setattr(module.subprocess, "Popen", Download)
    job = importer.start({"kind": "twitch", "url": "https://clips.twitch.tv/ExampleClip"})
    assert entered.wait(2)
    if outcome == "cancel":
        importer.cancel(job["id"])
    importer.worker.join(timeout=3)
    assert not importer.worker.is_alive()
    final = importer.get(job["id"])
    assert final["state"] == ("cancelled" if outcome == "cancel" else "error")
    assert final["session_id"] is None
    assert importer.store.list_sessions() == []
    assert processes[0].terminated is (outcome != "unavailable")
    directory = importer.folder / job["id"]
    assert not (directory / "media.mp4.part").exists()
    assert (directory / "media.info.json").is_file()
    assert (directory / "keep.txt").read_text() == "unrelated file"
    assert "diagnostics" in (directory / "download.log").read_text()
    if outcome == "unavailable":
        assert "Twitch" in final["error"] and "expired" in final["error"] and "local copy" in final["error"]
    elif outcome == "timeout":
        assert "20 minutes" in final["error"]
    elif outcome == "oversize":
        assert "4 GB" in final["error"]


def _make_twitch_transport_stream(path):
    """Short H.264/AAC VOD-like file with a100s timestamp origin and B-frames."""
    from fractions import Fraction
    import av

    with av.open(str(path), "w", format="mpegts") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        video.options = {"preset": "fast", "bf": "2"}
        audio = output.add_stream("aac", rate=48000)
        audio.layout = "stereo"
        packets = []
        for index in range(20):
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), index * 10, np.uint8), format="rgb24")
            frame.pts, frame.time_base = 1000 + index, Fraction(1, 10)
            packets.extend(video.encode(frame))
        packets.extend(video.encode(None))
        samples = 96000
        data = np.tile((.15 * np.sin(2 * np.pi * 440 * np.arange(samples) / 48000)).astype(np.float32), (2, 1))
        frame = av.AudioFrame.from_ndarray(data, format="fltp", layout="stereo")
        frame.sample_rate, frame.time_base, frame.pts = 48000, Fraction(1, 48000), 100 * 48000
        packets.extend(audio.encode(frame))
        packets.extend(audio.encode(None))
        for packet in sorted(packets, key=lambda item: item.dts * item.time_base):
            output.mux(packet)


def test_twitch_hls_remux_is_real_mp4_preserves_video_audio_and_common_time_origin(client):
    import hashlib
    import time
    import av

    importer = client.app.state.imports
    directory = importer.folder / "vod-remux"
    directory.mkdir()
    # Native HLS may have MPEG-TS bytes despite yt-dlp's mp4 filename.
    path = directory / "media.mp4"
    _make_twitch_transport_stream(path)
    with av.open(str(path)) as original:
        assert original.format.name == "mpegts"
        old_frames = list(original.decode(video=0))
        old_video_start = old_frames[0].time
        hashes = [hashlib.sha256(frame.to_ndarray().tobytes()).hexdigest() for frame in old_frames]
    with av.open(str(path)) as original:
        old_audio_start = next(original.decode(audio=0)).time
    result = importer._remux_twitch_hls(path, threading.Event(), time.monotonic())
    assert result == path and result.is_file()
    data = result.read_bytes()
    assert data[4:8] == b"ftyp"
    assert data.index(b"moov") < data.index(b"mdat")  # Fast-start browser playback.
    with av.open(str(result)) as prepared:
        assert "mp4" in prepared.format.name.split(",")
        assert prepared.streams.video[0].codec_context.name == "h264"
        assert prepared.streams.audio[0].codec_context.name == "aac"
        frames = list(prepared.decode(video=0))
        assert [hashlib.sha256(frame.to_ndarray().tobytes()).hexdigest() for frame in frames] == hashes
        new_video_start = frames[0].time
        assert 0 <= new_video_start < .1
        assert frames[-1].time < 2.1
    with av.open(str(result)) as prepared:
        audio = list(prepared.decode(audio=0))
        assert len(audio) > 80
        assert np.max(np.abs(audio[5].to_ndarray())) > .05
        new_audio_start = audio[0].time
        assert new_video_start - new_audio_start == pytest.approx(old_video_start - old_audio_start, abs=.001)
    assert not (directory / "media.remux.mp4.part").exists()


@pytest.mark.parametrize("failure", ["cancel", "timeout", "oversize"])
def test_twitch_hls_remux_failure_keeps_input_and_cleans_partial_output(client, monkeypatch, failure):
    import time
    from telemetry import imports as module

    importer = client.app.state.imports
    directory = importer.folder / "failed-remux"
    directory.mkdir()
    path = directory / "media.ts"
    _make_twitch_transport_stream(path)
    before = path.read_bytes()
    stop = threading.Event()
    check = importer._check
    calls = 0

    def interrupt_during_remux(event):
        nonlocal calls
        calls += 1
        if calls == 4:
            if failure == "cancel":
                event.set()
            elif failure == "timeout":
                monkeypatch.setattr(module, "MAX_DOWNLOAD_SECONDS", -1)
            else:
                monkeypatch.setattr(module, "MAX_BYTES", -1)
        check(event)

    monkeypatch.setattr(importer, "_check", interrupt_during_remux)
    expected = module.ImportCancelled if failure == "cancel" else ValueError
    with pytest.raises(expected):
        importer._remux_twitch_hls(path, stop, time.monotonic())
    assert calls >= 4
    assert path.read_bytes() == before
    assert not (directory / "media.mp4").exists()
    assert not (directory / "media.remux.mp4.part").exists()
