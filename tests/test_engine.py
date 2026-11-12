"""Integration coverage for decoding, lifecycle ownership, and persistence."""

import queue
import json
import threading
import time
from types import SimpleNamespace

import av
import cv2
import numpy as np
import pytest

from telemetry.engine import TelemetryEngine
from telemetry.vision import HUDExtractor
from telemetry.profiles import default_profile


def observed_frame(self, frame, timestamp):
    return {"time": timestamp, "hud_visible": True, "confidence": 0.95,
            "brake": 0, "throttle": 100, "speed": 72, "gear": "3",
            "steering": -20, "steering_unit": "hud_percent", "steering_confidence": .95,
            "steering_status": "tracked",
            "lap_time": "01:30.125", "lap_time_seconds": 90.125}


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for telemetry worker")


def make_video(path, seconds=1, fps=20):
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
        for index in range(round(seconds * fps)):
            frame = av.VideoFrame.from_ndarray(np.full((180, 320, 3), index % 255, dtype=np.uint8), format="bgr24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(HUDExtractor, "extract", observed_frame)
    instance = TelemetryEngine(tmp_path)
    yield instance
    instance.close()


def test_file_playback_ends_and_persists_measured_session(engine):
    path = engine.root / "test.mp4"
    make_video(path)
    engine.start({"kind": "file", "path": str(path), "realtime": False})
    wait_until(lambda: engine.state in {"ended", "error"})
    status = engine.status()
    assert status["error"] is None
    assert status["summary"]["samples"] == 20
    assert status["summary"]["distance_m"] == pytest.approx(19)
    assert status["latest"]["lap_time"] == "01:30.125"
    saved = engine.store.get_session(status["session_id"])
    assert saved["sample_count"] == 20
    assert saved["status"] == "completed"
    # The bounded preview history and database own samples; the analyzer must
    # not retain another unbounded copy for long recordings/live sessions.
    assert len(engine.history) == 20
    assert engine.analyzer.samples == []


def test_recording_timestamp_reset_keeps_frames_and_breaks_continuity(engine, monkeypatch):
    from telemetry import engine as module

    path = engine.root / "clock-reset.mp4"
    make_video(path)
    original_open = module.av.open

    class ClockResetVideo:
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
                if index < 10:
                    frame.pts += int(100 / float(frame.time_base))
                yield frame

    monkeypatch.setattr(module.av, "open", ClockResetVideo)
    engine.start({"kind": "file", "path": str(path), "realtime": False})
    wait_until(lambda: engine.state in {"ended", "error"})
    assert engine.error is None
    assert engine.analyzer.summary()["samples"] == 20
    assert engine.latest["segment_id"] == 2
    assert engine.latest["time"] == pytest.approx(.95)
    assert any(event.get("reason") == "source_timestamp_reset" for event in engine.analyzer.events)
    assert engine.analyzer.summary()["active_seconds"] == pytest.approx(.9)


def test_pause_seek_resume_does_not_bridge_driving_metrics(engine):
    path = engine.root / "test.mp4"
    make_video(path, seconds=3)
    engine.start({"kind": "file", "path": str(path), "realtime": True})
    wait_until(lambda: engine.latest is not None)
    engine.control("pause")
    before = engine.analyzer.summary()["active_seconds"]
    engine.control("seek", 1.5)
    wait_until(lambda: engine.source_time >= 1.5)
    assert engine.state == "paused"
    assert engine.analyzer.summary()["active_seconds"] == before
    engine.control("resume")
    wait_until(lambda: engine.latest["time"] > 1.5)
    assert engine.latest["segment_id"] == 2
    assert engine.analyzer.summary()["active_seconds"] < 0.2
    engine.stop()
    assert engine.status()["stale"]


def test_browser_pause_stall_and_duplicate_frames(engine):
    engine.start({"kind": "browser"})
    image = cv2.imencode(".jpg", np.zeros((180, 320, 3), dtype=np.uint8))[1].tobytes()
    assert engine.browser_frame(image, 0)["accepted"]
    assert not engine.browser_frame(image, 0)["accepted"]
    engine.control("pause")
    engine.browser_frame(image, 0.1)
    assert engine.analyzer.summary()["samples"] == 1
    engine.control("resume")
    engine.browser_frame(image, 0.2)
    assert engine.latest["segment_id"] == 2
    engine.last_received = time.monotonic() - 2
    engine.browser_frame(image, 0.3)
    assert engine.latest["segment_id"] == 3
    assert engine.analyzer.summary()["active_seconds"] == 0


def test_reencoded_frozen_video_has_no_live_instrument_values(engine):
    engine.start({"kind": "browser"})
    image = cv2.imencode(".jpg", np.zeros((180, 320, 3), dtype=np.uint8))[1].tobytes()
    for stamp in (0, .5, 1.0, 1.5, 2.0, 2.5):
        engine.browser_frame(image, stamp)
    assert engine.latest["status"] == "stale"
    for field in ("brake", "throttle", "speed", "gear", "steering", "rpm", "lap_time_seconds", "lap_valid"):
        assert engine.latest[field] is None
    assert engine.latest["steering_confidence"] == 0
    assert engine.latest["steering_status"] == "unavailable"


def test_automatic_layout_change_breaks_metrics_continuity(engine, monkeypatch):
    def calibrated(self, frame, timestamp):
        sample = observed_frame(self, frame, timestamp)
        sample["calibration"] = {"status": "locked", "revision": 1 if timestamp < 1 else 2}
        return sample

    monkeypatch.setattr(HUDExtractor, "extract", calibrated)
    engine.start({"kind": "browser"})
    image = cv2.imencode(".jpg", np.zeros((180, 320, 3), dtype=np.uint8))[1].tobytes()
    engine.browser_frame(image, 0)
    engine.browser_frame(image, .5)
    engine.browser_frame(image, 1)
    assert engine.latest["segment_id"] == 2
    assert engine.analyzer.summary()["active_seconds"] == .5
    assert any(event.get("reason") == "calibration_changed" for event in engine.analyzer.events)


def test_saved_legacy_profile_gets_new_steering_fields_without_rewriting_it(tmp_path):
    profile = default_profile()
    del profile["rois"]["steering"]
    del profile["rois"]["lap_labels"]
    del profile["auto_calibrate"]
    folder = tmp_path / "data"
    folder.mkdir()
    original = json.dumps(profile)
    path = folder / "profile.json"
    path.write_text(original)
    engine = TelemetryEngine(tmp_path)
    try:
        assert "steering" in engine.profile["rois"]
        assert engine.profile["auto_calibrate"] is True
        assert path.read_text() == original
    finally:
        engine.close()


def test_browser_frame_from_old_session_cannot_contaminate_new_session(engine, monkeypatch):
    first = engine.start({"kind": "browser"})["session_id"]
    decode_entered, release_decode = threading.Event(), threading.Event()

    def delayed_decode(*_):
        decode_entered.set()
        assert release_decode.wait(2)
        return np.zeros((180, 320, 3), dtype=np.uint8)

    monkeypatch.setattr(cv2, "imdecode", delayed_decode)
    result = []
    worker = threading.Thread(target=lambda: result.append(engine.browser_frame(b"jpeg", 1)))
    worker.start()
    try:
        assert decode_entered.wait(2)
        second = engine.start({"kind": "browser"})["session_id"]
        assert second != first
    finally:
        release_decode.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert engine.store.get_samples(second) == []
    assert engine.analyzer.summary()["samples"] == 0
    assert result and not result[0]["accepted"]


def test_live_decoder_keeps_its_original_stop_event_and_queue(engine):
    entered, release = threading.Event(), threading.Event()
    original_stop = engine.stop_event
    original_queue = engine.frame_queue
    engine._reader_done = threading.Event()
    original_done = engine._reader_done

    class Container:
        streams = SimpleNamespace(video=[SimpleNamespace(thread_type=None)])

        def decode(self, *_):
            entered.set()
            assert release.wait(2)
            yield SimpleNamespace(time=0.0)

        def close(self):
            pass

    reader = threading.Thread(target=engine._decode_live, args=(Container(), original_stop, original_queue, original_done, engine.session_id))
    reader.start()
    assert entered.wait(2)
    original_stop.set()
    engine.stop_event = threading.Event()
    engine.frame_queue = queue.Queue(maxsize=1)
    engine._reader_done = threading.Event()
    release.set()
    reader.join(timeout=2)
    assert not reader.is_alive()
    assert original_queue.empty()
    assert engine.frame_queue.empty()
    assert original_done.is_set()
    assert not engine._reader_done.is_set()


@pytest.mark.parametrize("kind,url", [("url", "https://example.com/stream.m3u8"), ("twitch", "https://www.twitch.tv/test")])
def test_stream_failure_becomes_visible_and_durable(engine, monkeypatch, kind, url):
    def fail():
        raise OSError("test network unavailable")

    monkeypatch.setattr(engine, "_open_stream", fail)
    engine.start({"kind": kind, "url": url})
    wait_until(lambda: engine.state == "error")
    assert engine.error
    engine.worker.join(timeout=2)
    assert engine.store.get_session(engine.session_id)["status"] == "error"


def test_source_validation_does_not_stop_existing_session(engine):
    previous = engine.start({"kind": "browser"})["session_id"]
    for source in ({"kind": "file", "path": "../outside.mp4"},
                   {"kind": "url", "url": "file:///etc/passwd"},
                   {"kind": "twitch", "url": "https://example.com/channel"}):
        with pytest.raises(ValueError):
            engine.start(source)
        assert engine.session_id == previous
        assert engine.state == "running"


def test_twitch_vod_uses_lossless_recording_path(engine, monkeypatch):
    path = engine.root / "vod.mp4"
    make_video(path)
    monkeypatch.setattr(engine, "_open_stream", lambda: av.open(str(path)))
    engine.start({"kind": "twitch", "url": "https://www.twitch.tv/videos/12345", "realtime": False})
    wait_until(lambda: engine.state in {"ended", "error"})
    assert engine.error is None
    assert engine.source["recorded"] is True
    assert engine.analyzer.summary()["samples"] == 20
    assert engine.dropped_frames == 0
