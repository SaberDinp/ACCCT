import threading

import cv2
import numpy as np
import pytest

from telemetry.visual_laps import (LandmarkMatcher, VisualSearchCancelled,
                                   find_visual_laps, match_visual_landmark, validate_roi)


def scenery():
    image = np.zeros((540, 960, 3), np.uint8)
    rng = np.random.default_rng(91)
    for _ in range(450):
        x, y = rng.integers([100, 60], [860, 360])
        color = tuple(int(value) for value in rng.integers(80, 256, size=3))
        cv2.rectangle(image, (int(x), int(y)), (int(x + 7), int(y + 7)), color, -1)
    return image


def shifted(image, x=0, scale=1):
    transform = cv2.getRotationMatrix2D((480, 270), 0, scale)
    transform[0, 2] += x
    return cv2.warpAffine(image, transform, (960, 540))


def test_same_view_match_uses_scale_not_lateral_pixel_position():
    anchor = scenery()
    matcher = LandmarkMatcher(anchor, shifted(anchor, x=6))
    repeat = matcher.score(shifted(anchor, x=22))
    approaching = matcher.score(shifted(anchor, x=0, scale=.84))
    assert repeat and approaching
    assert repeat["score"] > .2
    assert repeat["score"] > approaching["score"] * 2
    assert repeat["inliers"] >= 20
    assert matcher.score(np.zeros_like(anchor)) is None


def test_cross_view_match_does_not_require_identical_field_of_view():
    anchor = scenery()
    matcher = LandmarkMatcher(anchor, shifted(anchor, x=6))
    new_camera = shifted(anchor, x=10, scale=.8)
    same_view = matcher.score(new_camera)
    cross_view = matcher.score(new_camera, same_view=False)
    assert cross_view["score"] > .2
    assert cross_view["score"] > same_view["score"] * 2


def test_static_menu_cannot_calibrate_a_finish_landmark():
    anchor = scenery()
    with pytest.raises(ValueError, match="moving track landmarks"):
        LandmarkMatcher(anchor, anchor)


def test_cross_view_accepts_a_different_frame_aspect_ratio():
    anchor = scenery()
    matcher = LandmarkMatcher(anchor, shifted(anchor, x=6))
    taller = cv2.copyMakeBorder(anchor, 20, 160, 0, 0, cv2.BORDER_CONSTANT)
    assert matcher.score(taller) is None
    assert matcher.score(taller, same_view=False)["score"] > .2


@pytest.mark.parametrize("roi", [[-.1, 0, .3, .3], [0, 0, 1.1, .3], [0, 0, .01, .3],
                                 [0, 0, .3], [False, 0, .3, .3], [0, 0, float("nan"), .3]])
def test_invalid_regions_are_rejected(roi):
    with pytest.raises(ValueError):
        validate_roi(roi)


class SyntheticReader:
    instances = []

    def __init__(self, path):
        self.duration = 80
        self.path = path
        self.closed = False
        self.image = scenery()
        self.instances.append(self)

    def frame(self, stamp):
        phase = stamp % 40
        if phase > 20:
            phase -= 40
        displacement = phase * 15
        if self.path == "paused" and stamp >= 35:
            displacement = 0
        return float(stamp), shifted(self.image, x=displacement)

    def close(self):
        self.closed = True


def test_search_proposes_but_never_confirms_a_repeated_scene(monkeypatch):
    monkeypatch.setattr("telemetry.visual_laps._Reader", SyntheticReader)
    updates = []
    result = find_visual_laps("moving", 0, 38, 42, progress=updates.append)
    assert len(result["candidates"]) == 1
    match = result["candidates"][0]
    assert match["end_time"] == pytest.approx(40, abs=.11)
    assert match["duration"] == pytest.approx(40, abs=.11)
    assert match["requires_confirmation"] and not match["confirmed"]
    assert match["timing_accuracy_s"] is None
    assert updates[-1]["progress"] == 1
    assert SyntheticReader.instances[-1].closed


def test_static_target_screenshot_is_not_a_crossing(monkeypatch):
    monkeypatch.setattr("telemetry.visual_laps._Reader", SyntheticReader)
    result = find_visual_laps("paused", 0, 38, 42)
    assert result["candidates"] == []


def test_lap_picker_orders_crossings_by_time_not_later_replay_similarity(monkeypatch):
    monkeypatch.setattr("telemetry.visual_laps._Reader", SyntheticReader)
    matches = [{"time": 70., "score": .95, "inliers": 90},
               {"time": 40., "score": .35, "inliers": 30}]
    monkeypatch.setattr("telemetry.visual_laps._search", lambda *args: (list(matches), 50))
    result = find_visual_laps("moving", 0, 30, 75)
    assert result["candidate_order"] == "chronological"
    assert [candidate["end_time"] for candidate in result["candidates"]] == [40., 70.]
    assert [candidate["score"] for candidate in result["candidates"]] == [.35, .95]
    assert all(candidate["requires_confirmation"] and not candidate["confirmed"]
               for candidate in result["candidates"])
    assert any("Replay cuts" in warning for warning in result["warnings"])


def test_cross_view_picker_keeps_strongest_match_first(monkeypatch):
    monkeypatch.setattr("telemetry.visual_laps._Reader", SyntheticReader)
    matches = [{"time": 70., "score": .95, "inliers": 90},
               {"time": 40., "score": .35, "inliers": 30}]
    monkeypatch.setattr("telemetry.visual_laps._search", lambda *args: (list(matches), 50))
    result = match_visual_landmark("moving", 0, "moving", 30, 75)
    assert [candidate["target_time"] for candidate in result["candidates"]] == [70., 40.]


def test_cross_view_proposals_remain_unconfirmed_and_readers_close(monkeypatch):
    monkeypatch.setattr("telemetry.visual_laps._Reader", SyntheticReader)
    result = match_visual_landmark("moving", 0, "moving", 38, 42)
    match = result["candidates"][0]
    assert match["method"] == "cross_view_landmark"
    assert match["requires_confirmation"] and not match["confirmed"]
    assert match["timing_accuracy_s"] is None
    assert all(reader.closed for reader in SyntheticReader.instances[-2:])


def test_cancel_closes_reader(monkeypatch):
    monkeypatch.setattr("telemetry.visual_laps._Reader", SyntheticReader)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(VisualSearchCancelled):
        find_visual_laps("moving", 0, 38, 42, cancel_event=cancelled)
    assert SyntheticReader.instances[-1].closed


@pytest.mark.parametrize("kwargs", [dict(anchor_time=float("nan")), dict(anchor_time=True),
    dict(anchor_time=-1), dict(anchor_time=1e100), dict(anchor_time=0, min_lap_seconds=0),
    dict(anchor_time=0, max_lap_seconds=1801), dict(anchor_time=0, search_start=2),
    dict(anchor_time=0, track_id="unknown-circuit"),
    dict(anchor_time=0, search_start=40, search_end=35)])
def test_invalid_scan_requests_fail_before_opening_video(kwargs):
    with pytest.raises(ValueError):
        find_visual_laps("missing-video", **kwargs)


def test_video_duration_bounds_are_respected(monkeypatch):
    monkeypatch.setattr("telemetry.visual_laps._Reader", SyntheticReader)
    with pytest.raises(ValueError, match="following video"):
        find_visual_laps("moving", 79.7)
    with pytest.raises(ValueError, match="No later lap"):
        find_visual_laps("moving", 60)
    with pytest.raises(ValueError, match="outside"):
        match_visual_landmark("moving", 0, "moving", 90, 95)
    assert all(reader.closed for reader in SyntheticReader.instances[-4:])


@pytest.mark.parametrize("track_id", [None, "nurburgring_24h"])
def test_default_search_includes_long_laps_with_or_without_track_label(monkeypatch, track_id):
    class LongReader(SyntheticReader):
        def __init__(self, path):
            super().__init__(path)
            self.duration = 1000

    def search(matcher, reader, start, end, *args):
        assert start == 30
        assert end == 900
        return [{"time": 720., "score": .7, "inliers": 45}], 1

    monkeypatch.setattr("telemetry.visual_laps._Reader", LongReader)
    monkeypatch.setattr("telemetry.visual_laps._search", search)
    result = find_visual_laps("moving", 0, track_id=track_id)
    assert result["candidates"][0]["duration"] == 720
    assert result["max_lap_seconds"] == 900
    assert result["track_id"] == track_id


def test_long_lap_landmark_matching_accepts_more_than_ten_minutes(monkeypatch):
    class LongReader(SyntheticReader):
        def __init__(self, path):
            super().__init__(path)
            self.duration = 1500

    monkeypatch.setattr("telemetry.visual_laps._Reader", LongReader)
    result = find_visual_laps("moving", 0, 718, 722, track_id="nurburgring_24h")
    assert result["candidates"][0]["end_time"] == pytest.approx(720, abs=.11)
    assert result["candidates"][0]["requires_confirmation"]
    overridden = find_visual_laps("moving", 0, 1198, 1202, max_lap_seconds=1800)
    assert overridden["candidates"][0]["duration"] == pytest.approx(1200, abs=.11)


def test_maximum_lap_scan_has_bounded_work_and_cooperative_cancellation():
    from telemetry.visual_laps import _search

    class Reader:
        def frame(self, stamp):
            return stamp, None

    class Matcher:
        def score(self, frame, same_view):
            return None

    updates = []
    matches, scanned = _search(Matcher(), Reader(), 10, 1800, True, updates.append, None)
    assert matches == []
    assert scanned == 3581
    assert updates[-1]["progress"] == 1
    cancel = threading.Event()

    def progress(update):
        if update["scanned_frames"] == 4:
            cancel.set()

    with pytest.raises(VisualSearchCancelled):
        _search(Matcher(), Reader(), 10, 1800, True, progress, cancel)
