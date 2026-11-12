"""Corpus retries preserve evidence and cached-only runs never transfer data."""

import json
from pathlib import Path
import sys

import numpy as np
import pytest

from scripts import validate_corpus as runner
from telemetry.validation import read_json, write_json


class Extractor:
    def extract(self, frame, stamp):
        return {"time": stamp, "speed": 144, "gear": "4", "brake": 0, "throttle": 100,
                "steering": None, "hud_visible": True, "confidence": 1,
                "hud_layout": "current", "hud_speed_unit": "km/h"}


@pytest.fixture
def decoder(monkeypatch):
    def frames(path):
        for index in range(3):
            yield index / 10, np.full((64, 96, 3), index, dtype=np.uint8)

    monkeypatch.setattr(runner, "full_frames", frames)
    monkeypatch.setattr(runner, "HUDExtractor", Extractor)


def test_failed_rerun_keeps_successful_trace_and_frame_evidence(tmp_path, monkeypatch, decoder):
    video = {"id": "abcdefghijk"}
    first = runner.analyze(tmp_path, video, Path("fixture.mp4"), "full", 12)
    trace = tmp_path / first["trace_path"]
    frame = tmp_path / first["evidence_path"] / first["frame_samples"][0]["frame"]
    before = trace.read_bytes(), frame.read_bytes()

    class FailingExtractor(Extractor):
        def extract(self, image, stamp):
            if stamp > 0:
                raise ValueError("fixture decoder interrupted")
            return super().extract(image, stamp)

    monkeypatch.setattr(runner, "HUDExtractor", FailingExtractor)
    with pytest.raises(ValueError, match="interrupted"):
        runner.analyze(tmp_path, video, Path("fixture.mp4"), "full", 12)

    assert (trace.read_bytes(), frame.read_bytes()) == before
    failures = list((tmp_path / "frames").rglob("failure.json"))
    assert len(failures) == 1
    failure = read_json(failures[0])
    assert failure["status"] == "error"
    assert failure["run_id"] != first["run_id"]
    assert failure["trace_path"] != first["trace_path"]
    assert len((tmp_path / failure["trace_path"]).read_text().splitlines()) == 1
    assert len(trace.read_text().splitlines()) == 3


def test_successful_reruns_also_have_distinct_immutable_evidence(tmp_path, decoder):
    video = {"id": "abcdefghijk"}
    first = runner.analyze(tmp_path, video, Path("fixture.mp4"), "full", 12)
    second = runner.analyze(tmp_path, video, Path("fixture.mp4"), "full", 12)
    assert first["run_id"] != second["run_id"]
    assert first["trace_path"] != second["trace_path"]
    assert first["evidence_path"] != second["evidence_path"]
    assert (tmp_path / first["trace_path"]).read_bytes() == (tmp_path / second["trace_path"]).read_bytes()
    assert not list((tmp_path / "frames").rglob("failure.json"))


@pytest.fixture
def command_fixture(tmp_path, monkeypatch):
    videos = {identity: {"id": identity, "title": "Fixture " + identity}
              for identity in ("abcdefghijk", "abcdefghijl")}
    write_json(tmp_path / "manifest.json", {"videos": videos})
    for identity in videos:
        path = tmp_path / "cache" / identity / "media.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture")
    monkeypatch.setattr(runner, "choose_videos", lambda *args: [])
    monkeypatch.setattr(runner, "extractor_fingerprint", lambda *args: "a" * 64)
    monkeypatch.setattr(runner, "coverage_report", lambda *args: {})
    saved = []
    monkeypatch.setattr(runner, "save_video_result", lambda folder, identity, result: saved.append((identity, result)))

    def download(*args):
        pytest.fail("A cached-only command must not enter the downloader")

    monkeypatch.setattr(runner, "download", download)
    monkeypatch.setattr(runner, "analyze", lambda *args: {
        "status": "completed", "mode": "smoke", "coverage": {}, "wall_seconds": 0})
    return videos, saved


def test_explicit_cached_ids_are_deduplicated_bounded_and_never_download(tmp_path, monkeypatch, command_fixture):
    videos, saved = command_fixture
    monkeypatch.setattr(sys, "argv", ["validate_corpus.py", "--folder", str(tmp_path), "run", "--cached-only",
                                      "--limit", "1", "--video-id", "abcdefghijk", "--video-id", "abcdefghijk",
                                      "--video-id", "abcdefghijl"])
    runner.main()
    assert [identity for identity, _ in saved] == ["abcdefghijk"]
    assert saved[0][1]["status"] == "completed"


def test_cached_file_disappearing_after_selection_cannot_trigger_download(tmp_path, monkeypatch, command_fixture):
    videos, saved = command_fixture
    calls = []

    def cache(folder, video):
        calls.append(video["id"])
        return folder / "cache" / video["id"] / "media.mp4" if len(calls) == 1 else None

    monkeypatch.setattr(runner, "cached_video", cache)
    monkeypatch.setattr(sys, "argv", ["validate_corpus.py", "--folder", str(tmp_path), "run", "--cached-only",
                                      "--video-id", "abcdefghijk"])
    runner.main()
    assert len(saved) == 1
    assert saved[0][1]["status"] == "error"
    assert "--cached-only forbids downloading" in saved[0][1]["error"]


def test_cached_only_skips_explicit_ids_without_media(tmp_path, monkeypatch, command_fixture):
    videos, saved = command_fixture
    (tmp_path / "cache/abcdefghijk/media.mp4").unlink()
    monkeypatch.setattr(sys, "argv", ["validate_corpus.py", "--folder", str(tmp_path), "run", "--cached-only",
                                      "--video-id", "abcdefghijk"])
    runner.main()
    assert saved == []
