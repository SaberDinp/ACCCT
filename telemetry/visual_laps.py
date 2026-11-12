"""Opt-in same-view landmark recurrence, not semantic finish-line recognition.

A user chooses a start/finish frame. Classical image features then propose the
next return to that scene. Candidates remain unconfirmed: different lines,
camera motion, and replay cuts can move or duplicate the visual signature.
"""
from __future__ import annotations

import math
from pathlib import Path
import time

import av
import cv2
import numpy as np

from .catalog import lap_search_defaults


DEFAULT_ROI = (.18, .12, .68, .52)
WORK_WIDTH = 960
MAX_LAP_SECONDS = 1800


class VisualSearchCancelled(Exception):
    pass


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise VisualSearchCancelled()


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite.")
    return float(value)


def validate_roi(roi):
    values = DEFAULT_ROI if roi is None else roi
    if not isinstance(values, (tuple, list)) or len(values) != 4:
        raise ValueError("Landmark ROI must be [x, y, width, height].")
    x, y, width, height = [_finite(value, "ROI coordinate") for value in values]
    if x < 0 or y < 0 or width < .03 or height < .03 or x + width > 1 or y + height > 1:
        raise ValueError("Landmark ROI must lie inside the frame and be at least 3% wide and high.")
    return x, y, width, height


def _gray(frame):
    if not isinstance(frame, np.ndarray) or frame.size == 0 or frame.ndim not in (2, 3):
        raise ValueError("A nonempty video frame is required.")
    frame = cv2.resize(frame, (WORK_WIDTH, round(frame.shape[0] * WORK_WIDTH / frame.shape[1])))
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame


class LandmarkMatcher:
    def __init__(self, anchor, neighbor, roi=None):
        self.roi = validate_roi(roi)
        self.anchor = _gray(anchor)
        neighbor = _gray(neighbor)
        if neighbor.shape != self.anchor.shape:
            raise ValueError("Calibration frames must have the same aspect ratio.")
        height, width = self.anchor.shape
        x, y, w, h = self.roi
        self.mask = np.zeros_like(self.anchor)
        self.mask[round(y * height):round((y + h) * height), round(x * width):round((x + w) * width)] = 255
        self.orb = cv2.ORB_create(nfeatures=1600, fastThreshold=10, edgeThreshold=12)
        self.keypoints, self.descriptors = self.orb.detectAndCompute(self.anchor, self.mask)
        if self.descriptors is not None:
            # Brightness fades change static dashboards too; track actual feature
            # motion instead of treating every changed pixel as road scenery.
            points = np.float32([point.pt for point in self.keypoints]).reshape(-1, 1, 2)
            moved, status, error = cv2.calcOpticalFlowPyrLK(self.anchor, neighbor, points, None,
                                                         winSize=(21, 21), maxLevel=3)
            if moved is not None and status is not None:
                displacement = np.linalg.norm(moved.reshape(-1, 2) - points.reshape(-1, 2), axis=1)
                keep = (status.ravel() == 1) & (displacement > 2.5) & (displacement < 140) & (error.ravel() < 35)
                self.keypoints = [point for point, included in zip(self.keypoints, keep) if included]
                self.descriptors = self.descriptors[keep]
            else:
                self.keypoints, self.descriptors = [], None
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        if self.descriptors is None or len(self.keypoints) < 20:
            raise ValueError("Too few moving track landmarks at this frame. Choose a moving, unobstructed finish-line view.")

    def score(self, frame, same_view=True):
        gray = _gray(frame)
        if gray.shape != self.anchor.shape and same_view:
            return None
        mask = self.mask
        if gray.shape != mask.shape:
            height, width = gray.shape
            x, y, w, h = self.roi
            mask = np.zeros_like(gray)
            mask[round(y * height):round((y + h) * height), round(x * width):round((x + w) * width)] = 255
        keypoints, descriptors = self.orb.detectAndCompute(gray, mask)
        if descriptors is None or len(keypoints) < 12:
            return None
        pairs = self.matcher.knnMatch(self.descriptors, descriptors, k=2)
        good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < .72 * pair[1].distance]
        if len(good) < 12:
            return None
        source = np.float32([self.keypoints[match.queryIdx].pt for match in good])
        target = np.float32([keypoints[match.trainIdx].pt for match in good])
        transform, inliers = cv2.findHomography(source, target, cv2.RANSAC, 3, maxIters=600, confidence=.995)
        if transform is None or inliers is None or not np.isfinite(transform).all():
            return None
        included = inliers.ravel().astype(bool)
        count = int(included.sum())
        ratio = count / len(good)
        if count < 10 or ratio < .4:
            return None
        span = np.ptp(source[included], axis=0)
        if span[0] < 45 or span[1] < 15:
            return None
        displacement = float(np.median(np.linalg.norm(target[included] - source[included], axis=1)))
        affine, _ = cv2.estimateAffinePartial2D(source[included], target[included], method=cv2.RANSAC, ransacReprojThreshold=3)
        scale = math.sqrt(float(affine[0, 0] ** 2 + affine[0, 1] ** 2)) if affine is not None else None
        # Equal landmark scale identifies longitudinal return despite lateral
        # displacement. Pixel displacement alone picks an early crossing when
        # the driver approaches the line nearer the opposite edge of the road.
        if same_view:
            if scale is None or scale <= 0:
                return None
            score = min(1., count / 65) * ratio * math.exp(-abs(math.log(scale)) / .09) / (1 + displacement / 180)
        else:
            score = min(1., count / 65) * ratio
        return {"score": score, "inliers": count, "inlier_ratio": ratio, "displacement_px": displacement, "scale": scale}


class _Reader:
    def __init__(self, path):
        self.container = av.open(str(path))
        if not self.container.streams.video:
            self.container.close()
            raise ValueError("The recording contains no video stream.")
        self.stream = self.container.streams.video[0]
        self.base = float(self.stream.time_base)
        self.duration = (float(self.stream.duration) * self.base if self.stream.duration else
                         float(self.container.duration or 0) / av.time_base)
        self.iterator = None
        self.last_time = -math.inf

    def frame(self, stamp):
        if self.iterator is None or stamp <= self.last_time or stamp > self.last_time + 2:
            self.container.seek(int(stamp / self.base), stream=self.stream, backward=True)
            self.iterator = iter(self.container.decode(self.stream))
        for frame in self.iterator:
            actual = float(frame.time or 0)
            self.last_time = actual
            if actual + .01 >= stamp:
                return actual, frame.to_ndarray(format="bgr24")
        raise ValueError("No video frame at this source time.")

    def close(self):
        self.container.close()


def _search(matcher, reader, start, end, same_view, progress, cancel_event):
    observations = []
    scanned = 0
    for stamp in np.arange(start, end + .0001, .5):
        _check_cancel(cancel_event)
        actual, frame = reader.frame(float(stamp))
        result = matcher.score(frame, same_view=same_view)
        scanned += 1
        if result is not None and result["score"] >= .14:
            observations.append(dict(result, time=actual))
        if progress is not None:
            progress({"progress": .8 * (stamp - start) / (end - start), "scanned_frames": scanned,
                      "stage": "scan", "source_time": actual})
    # Retain separated local maxima; a nearby sign often persists for seconds.
    peaks = []
    for point in sorted(observations, key=lambda point: point["score"], reverse=True):
        if all(abs(point["time"] - peak["time"]) > 5 for peak in peaks):
            peaks.append(point)
        if len(peaks) == 5:
            break
    candidates = []
    for peak_index, peak in enumerate(peaks):
        fine = []
        for stamp in np.arange(max(start, peak["time"] - .6), min(end, peak["time"] + .6) + .001, .05):
            _check_cancel(cancel_event)
            actual, frame = reader.frame(float(stamp))
            result = matcher.score(frame, same_view=same_view)
            scanned += 1
            if result is not None:
                fine.append(dict(result, time=actual))
        if progress is not None:
            progress({"progress": .8 + .2 * (peak_index + 1) / len(peaks), "scanned_frames": scanned,
                      "stage": "refine", "source_time": peak["time"]})
        best = max(fine + [peak], key=lambda point: point["score"])
        if best["score"] < .2:
            continue
        # A paused replay or a menu containing a screenshot is not a crossing.
        if reader.duration and best["time"] + .4 >= reader.duration:
            continue
        _, frame = reader.frame(best["time"])
        _, neighbor = reader.frame(best["time"] + .4)
        try:
            LandmarkMatcher(frame, neighbor, matcher.roi)
        except ValueError:
            continue
        candidates.append(best)
    candidates.sort(key=lambda point: point["score"], reverse=True)
    if progress is not None:
        progress({"progress": 1., "scanned_frames": scanned, "stage": "complete", "source_time": end})
    return candidates, scanned


def find_visual_laps(path: str | Path, anchor_time: float, search_start: float | None = None,
                     search_end: float | None = None, roi=None, min_lap_seconds: float | None = None,
                     max_lap_seconds: float | None = None, progress=None, cancel_event=None,
                     track_id: str | None = None) -> dict:
    """Propose a finish for one lap; never modify sessions or silently confirm it."""
    began = time.monotonic()
    anchor_time = _finite(anchor_time, "Anchor time")
    defaults = lap_search_defaults(track_id)
    min_lap_seconds = defaults["min_lap_seconds"] if min_lap_seconds is None else _finite(min_lap_seconds, "Minimum lap duration")
    max_lap_seconds = defaults["max_lap_seconds"] if max_lap_seconds is None else _finite(max_lap_seconds, "Maximum lap duration")
    if anchor_time < 0 or anchor_time > 172800 or min_lap_seconds < 10 or max_lap_seconds < min_lap_seconds or max_lap_seconds > MAX_LAP_SECONDS:
        raise ValueError(f"Use a nonnegative anchor and lap-duration bounds between 10 and {MAX_LAP_SECONDS} seconds.")
    roi = validate_roi(roi)
    start = anchor_time + min_lap_seconds if search_start is None else _finite(search_start, "Search start")
    end = anchor_time + max_lap_seconds if search_end is None else _finite(search_end, "Search end")
    if start < anchor_time + min_lap_seconds or end <= start or end > anchor_time + max_lap_seconds:
        raise ValueError("Search interval must follow the anchor and lie within the lap-duration bounds.")
    reader = _Reader(path)
    try:
        _check_cancel(cancel_event)
        if reader.duration and anchor_time + .5 >= reader.duration:
            raise ValueError("The anchor needs at least half a second of following video.")
        if reader.duration:
            end = min(end, reader.duration - .05)
        if end <= start:
            raise ValueError("No later lap fits inside the recording.")
        anchor_stamp, anchor = reader.frame(anchor_time)
        _, neighbor = reader.frame(anchor_time + .4)
        matcher = LandmarkMatcher(anchor, neighbor, roi)
        matches, scanned = _search(matcher, reader, start, end, True, progress, cancel_event)
        # A later replay can resemble the anchor more closely than the actual
        # first return. The lap picker promises the next crossing, not best image.
        matches.sort(key=lambda match: match["time"])
        candidates = [{"start_time": anchor_stamp, "end_time": best["time"],
                       "duration": best["time"] - anchor_stamp, "score": round(best["score"], 3),
                       "inliers": best["inliers"], "method": "visual_landmark", "confirmed": False,
                       "timing_resolution_s": .05, "timing_accuracy_s": None, "requires_confirmation": True}
                      for best in matches]
        return {"candidates": candidates, "anchor_time": anchor_stamp, "roi": list(roi),
                "search_start": start, "search_end": end, "scanned_frames": scanned,
                "track_id": track_id, "min_lap_seconds": min_lap_seconds, "max_lap_seconds": max_lap_seconds,
                "candidate_order": "chronological",
                "elapsed_seconds": round(time.monotonic() - began, 3),
                "warnings": ["Landmark recurrence is a proposal, not a detected timing beam. Confirm that both frames cross the same finish line.",
                             "Replay cuts, camera changes and a different driving line can shift or duplicate matches. Image similarity is not timing accuracy."]}
    finally:
        reader.close()


def match_visual_landmark(anchor_path: str | Path, anchor_time: float, target_path: str | Path,
                          search_start: float, search_end: float, roi=None, progress=None,
                          cancel_event=None) -> dict:
    """Find inspectable cross-view scene matches, never automatic distance anchors."""
    began = time.monotonic()
    anchor_time = _finite(anchor_time, "Anchor time")
    start = _finite(search_start, "Search start")
    end = _finite(search_end, "Search end")
    if anchor_time < 0 or anchor_time > 172800 or start < 0 or end <= start or end - start > 600 or end > 172800:
        raise ValueError("Use a nonnegative anchor and a search window of at most 600 seconds.")
    roi = validate_roi(roi)
    _check_cancel(cancel_event)
    anchor_reader = _Reader(anchor_path)
    try:
        if anchor_reader.duration and anchor_time + .5 >= anchor_reader.duration:
            raise ValueError("The anchor needs at least half a second of following video.")
        anchor_stamp, anchor = anchor_reader.frame(anchor_time)
        _, neighbor = anchor_reader.frame(anchor_time + .4)
        matcher = LandmarkMatcher(anchor, neighbor, roi)
    finally:
        anchor_reader.close()
    target = _Reader(target_path)
    try:
        if target.duration:
            end = min(end, target.duration - .05)
        if end <= start:
            raise ValueError("Search interval lies outside the target recording.")
        matches, scanned = _search(matcher, target, start, end, False, progress, cancel_event)
        return {"anchor_time": anchor_stamp, "search_start": start, "search_end": end, "roi": list(roi),
                "scanned_frames": scanned, "elapsed_seconds": round(time.monotonic() - began, 3),
                "candidates": [{"anchor_time": anchor_stamp, "target_time": best["time"],
                                "score": round(best["score"], 3), "inliers": best["inliers"],
                                "method": "cross_view_landmark", "confirmed": False,
                                "requires_confirmation": True, "timing_accuracy_s": None} for best in matches],
                "warnings": ["The same landmark can be visible from different track positions. Confirm the exact physical crossing before using an alignment anchor.",
                             "Different camera settings and scenery can prevent reliable matching; an empty result is preferable to an invented correspondence."]}
    finally:
        target.close()
