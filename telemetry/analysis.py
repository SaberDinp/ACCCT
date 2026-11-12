"""Deterministic analysis of observed HUD channels, with explicit data gaps."""

from __future__ import annotations

import bisect
from collections import deque
import math
from collections.abc import Iterable
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _pedal(value: Any) -> float | None:
    value = _number(value)
    return min(100.0, max(0.0, value)) if value is not None else None


def _plausible_speed_change(before: float, after: float, seconds: float) -> bool:
    # Integer HUD speeds can differ by one extra km/h because both endpoints are rounded.
    return abs(after - before) <= 25 * 3.6 * seconds + 1.0


def _timer(value: Any) -> float | None:
    if isinstance(value, str) and ":" in value:
        try:
            minutes, seconds = value.split(":", 1)
            minutes, seconds = float(minutes), float(seconds)
            total = minutes * 60 + seconds
            return total if math.isfinite(total) and minutes >= 0 and 0 <= seconds < 60 else None
        except ValueError:
            return None
    value = _number(value)
    return value if value is not None and value >= 0 else None


def _category_seconds(previous: dict, sample: dict, dt: float) -> dict[str, float]:
    """Integrate pedal categories over linear intervals, including threshold crossings."""
    breaks = {0.0, 1.0}
    for channel, thresholds in (("brake", (3.0,)), ("throttle", (3.0, 95.0))):
        start, end = previous[channel], sample[channel]
        if start != end:
            for threshold in thresholds:
                crossing = (threshold - start) / (end - start)
                if 0 < crossing < 1:
                    breaks.add(crossing)
    totals = dict(full_throttle=0.0, braking=0.0, coasting=0.0, overlap=0.0)
    points = sorted(breaks)
    for left, right in zip(points, points[1:]):
        middle = (left + right) / 2
        brake = previous["brake"] + (sample["brake"] - previous["brake"]) * middle
        throttle = previous["throttle"] + (sample["throttle"] - previous["throttle"]) * middle
        seconds = (right - left) * dt
        totals["full_throttle"] += seconds * (throttle >= 95)
        totals["braking"] += seconds * (brake > 3)
        totals["coasting"] += seconds * (brake <= 3 and throttle <= 3)
        totals["overlap"] += seconds * (brake > 3 and throttle > 3)
    return totals


class SessionAnalyzer:
    """Analyze consecutive valid observations; never integrate across missing footage.

    Time is source-video time in seconds. Pedals are percentages, speed is km/h.
    Distance and longitudinal acceleration are estimates from visible speed only.
    """

    def __init__(self, max_gap: float = 1.0, minimum_confidence: float = 0.35,
                 stationary_timeout: float = 3.0, retain_samples: bool = True):
        self.max_gap = max_gap
        self.minimum_confidence = minimum_confidence
        self.stationary_timeout = stationary_timeout
        self.samples: list[dict] = []
        self._retain_samples = retain_samples
        self._sample_count = 0
        self.events: list[dict] = []
        self.laps: list[dict] = []
        self._previous: dict | None = None
        self._last_time: float | None = None
        self._segment_id = 0
        self._pending_gap: str | None = None
        self._in_gap = False
        self._stationary_since: float | None = None
        self._active_seconds = 0.0
        self._elapsed_seconds = 0.0
        self._distance = 0.0
        self._speed_seconds = 0.0
        self._speed_integral = 0.0
        self._maximum_speed: float | None = None
        self._throttle_integral = 0.0
        self._brake_integral = 0.0
        self._categories = dict(full_throttle=0.0, braking=0.0, coasting=0.0, overlap=0.0)
        self._braking: dict | None = None
        self._brake_integral_event = 0.0
        self._brake_local_peak = 0.0
        self._brake_trough = 0.0
        self._brake_falling = False
        self._lap_start: dict | None = None
        self._lap_sequence = 0
        self._lap_interrupted = False
        self._gap_count = 0
        self._gap_event: dict | None = None
        self._timer_reset_candidate: tuple[float, float] | None = None
        self._boundary_history: deque[dict] = deque(maxlen=512)
        self._last_observed_timer: tuple[float, float] | None = None
        self._last_credible_speed: tuple[float, float] | None = None
        self._lap_valid: bool | None = None

    def discontinuity(self, reason: str) -> None:
        """Break continuity on a controller pause, seek, or calibration change."""
        self._break(reason, self._last_time)
        self._pending_gap = reason
        self._stationary_since = None

    def _record_sample(self, sample: dict) -> None:
        self._sample_count += 1
        if self._retain_samples:
            self.samples.append(sample)

    def _break(self, reason: str, time: float | None) -> None:
        self._finish_braking(self._previous, truncated=True)
        if not self._in_gap:
            self._gap_count += 1
            self._gap_event = {"type": "gap", "reason": reason, "time": time,
                               "start_time": self._previous["time"] if self._previous else time,
                               "end_time": None, "duration": None,
                               "segment_id": self._segment_id or None}
            self.events.append(self._gap_event)
        self._in_gap = True
        self._previous = None
        self._timer_reset_candidate = None
        self._last_observed_timer = None
        self._last_credible_speed = None
        self._boundary_history.clear()
        if self._lap_start is not None:
            self._lap_interrupted = True

    def process(self, sample: dict) -> dict:
        sample = dict(sample)
        timestamp = _number(sample.get("time", sample.get("timestamp")))
        if timestamp is None:
            raise ValueError("A sample requires a finite source time in seconds")
        sample["time"] = timestamp
        sample["brake"] = _pedal(sample.get("brake"))
        sample["throttle"] = _pedal(sample.get("throttle"))
        speed = _number(sample.get("speed"))
        sample["speed"] = speed if speed is not None and 0 <= speed <= 500 else None
        sample["confidence"] = max(0.0, min(1.0, _number(sample.get("confidence")) or 0.0))
        sample["hud_visible"] = bool(sample.get("hud_visible", False))
        timer_value = sample.get("lap_time_seconds")
        if timer_value is None:
            timer_value = sample.get("lap_time")
        sample["lap_time_seconds"] = _timer(timer_value)
        sample["raw_lap_time"] = sample.get("lap_time") if isinstance(sample.get("lap_time"), str) else _number(sample.get("lap_time"))
        sample["raw_lap_time_seconds"] = sample["lap_time_seconds"]
        if sample["lap_time_seconds"] is None:
            sample["lap_time"] = None

        gap_reason = self._pending_gap
        self._pending_gap = None
        if self._last_time is not None:
            delta = timestamp - self._last_time
            if delta <= 0:
                gap_reason = "seek" if delta < 0 else "stale"
            elif delta > self.max_gap:
                gap_reason = "source_gap"
            # The timeline sums forward intervals, but seeks do not add fictitious driving time.
            if delta > 0 and delta <= self.max_gap:
                self._elapsed_seconds += delta
        if gap_reason:
            self._break(gap_reason, timestamp)
            self._stationary_since = None
        self._last_time = timestamp

        reason = "stale" if gap_reason == "stale" else None
        if sample.get("stale"):
            reason = "stale"
        elif not sample["hud_visible"]:
            reason = "hud_missing"
        elif sample["confidence"] < self.minimum_confidence:
            reason = "low_confidence"
        elif sample["brake"] is None or sample["throttle"] is None:
            reason = "inputs_missing"
        if reason is None and sample["speed"] is not None:
            if self._last_credible_speed is not None:
                speed_time, credible_speed = self._last_credible_speed
                speed_dt = timestamp - speed_time
                if 0 < speed_dt <= self.max_gap and not _plausible_speed_change(credible_speed, sample["speed"], speed_dt):
                    sample["raw_speed"] = sample["speed"]
                    sample["speed"] = None
                    sample["speed_outlier"] = True
            if sample["speed"] is not None:
                self._last_credible_speed = (timestamp, sample["speed"])
        if reason is None and sample["speed"] is not None and sample["speed"] <= 2:
            if self._stationary_since is None:
                self._stationary_since = timestamp
            if timestamp - self._stationary_since >= self.stationary_timeout:
                reason = "stationary"
        else:
            self._stationary_since = None

        if reason:
            self._break(reason, timestamp)
            sample.update(active=False, status=reason, segment_id=None, gap_reason=reason,
                          distance=self._distance, distance_m=self._distance,
                          acceleration=None, lap_id=self._lap_sequence or None)
            self._record_sample(sample)
            return sample

        previous = self._previous
        if previous is None:
            self._segment_id += 1
        if self._gap_event is not None:
            self._gap_event["end_time"] = timestamp
            gap_duration = timestamp - self._gap_event["start_time"] if self._gap_event["start_time"] is not None else None
            self._gap_event["duration"] = gap_duration if gap_duration is not None and gap_duration >= 0 else None
            self._gap_event = None
        sample.update(active=True, status="driving", segment_id=self._segment_id,
                      gap_reason=gap_reason, acceleration=None)
        if previous is not None:
            dt = timestamp - previous["time"]
            self._active_seconds += dt
            self._throttle_integral += (previous["throttle"] + sample["throttle"]) * 0.5 * dt
            self._brake_integral += (previous["brake"] + sample["brake"]) * 0.5 * dt
            for category, duration in _category_seconds(previous, sample, dt).items():
                self._categories[category] += duration
            if previous["speed"] is not None and sample["speed"] is not None:
                average_speed = (previous["speed"] + sample["speed"]) * 0.5
                acceleration = (sample["speed"] - previous["speed"]) / (3.6 * dt)
                # OCR digit errors must not create extreme distance or acceleration spikes.
                if _plausible_speed_change(previous["speed"], sample["speed"], dt):
                    self._distance += average_speed * dt / 3.6
                    self._speed_seconds += dt
                    self._speed_integral += average_speed * dt
                    sample["acceleration"] = acceleration
                else:
                    sample["speed_outlier"] = True
        if sample["speed"] is not None and not sample.get("speed_outlier"):
            self._maximum_speed = max(self._maximum_speed or 0.0, sample["speed"])
        sample["distance"] = sample["distance_m"] = self._distance
        self._boundary_history.append({"time": timestamp, "distance": self._distance,
                                       "active_seconds": self._active_seconds,
                                       "sample_index": self._sample_count})
        while self._boundary_history and timestamp - self._boundary_history[0]["time"] > 6:
            self._boundary_history.popleft()

        old_timer = None
        timer_dt = None
        if self._last_observed_timer is not None:
            timer_time, timer_value = self._last_observed_timer
            if timestamp - timer_time <= self.max_gap:
                old_timer = timer_value
                timer_dt = timestamp - timer_time
        new_timer = sample.get("lap_time_seconds")
        confirmed_reset = False
        pending_reset = False
        reset_time = None
        if self._timer_reset_candidate is not None:
            candidate_time, candidate_timer = self._timer_reset_candidate
            candidate_dt = timestamp - candidate_time
            if candidate_dt > self.max_gap:
                self._timer_reset_candidate = None
            elif new_timer is not None:
                plausible = candidate_timer <= new_timer <= 3 and abs(new_timer - candidate_timer - candidate_dt) <= 0.4
                if plausible and new_timer > candidate_timer:
                    confirmed_reset = True
                    reset_time = ((candidate_time - candidate_timer) + (timestamp - new_timer)) / 2
                    self._timer_reset_candidate = None
                elif plausible:
                    pending_reset = True
                else:
                    self._timer_reset_candidate = None
        if confirmed_reset:
            self._lap_boundary(sample, source="timer", timer_origin=reset_time)
        elif (new_timer is not None and 0 <= new_timer <= 3
              and (old_timer is None or old_timer >= 20 or pending_reset)
              and (self._lap_start is None or timestamp - self._lap_start["time"] >= 20
                   or timestamp < self._lap_start["time"])):
            if not pending_reset:
                self._timer_reset_candidate = (timestamp, new_timer)
            pending_reset = True
        elif old_timer is not None and new_timer is not None and abs(new_timer - old_timer - timer_dt) > 0.4:
            sample["timer_outlier"] = True
            new_timer = None
        if pending_reset:
            sample["timer_pending"] = True
            new_timer = None
        if new_timer is None:
            sample["lap_time"] = sample["lap_time_seconds"] = None
        else:
            self._last_observed_timer = (timestamp, new_timer)
        if self._lap_start is not None:
            if sample.get("lap_valid") is False:
                self._lap_valid = False
            elif sample.get("lap_valid") is True and self._lap_valid is None:
                self._lap_valid = True
        sample["lap_id"] = self._lap_sequence or None
        self._update_braking(sample, previous)
        self._record_sample(sample)
        self._previous = sample
        self._in_gap = False
        return sample

    def _update_braking(self, sample: dict, previous: dict | None) -> None:
        brake = sample["brake"]
        if brake <= 3:
            if self._braking is not None:
                if previous is not None:
                    self._brake_integral_event += (previous["brake"] + brake) * 0.5 * (sample["time"] - previous["time"])
                self._finish_braking(sample, truncated=False)
            return
        if self._braking is None:
            self._braking = {"type": "brake", "segment_id": sample["segment_id"],
                             "lap_id": sample.get("lap_id"), "start_time": sample["time"],
                             "peak_time": sample["time"], "peak_brake": brake,
                             "entry_speed": sample["speed"], "min_speed": sample["speed"],
                             "reapplications": 0}
            self._brake_integral_event = 0.0
            self._brake_local_peak = brake
            self._brake_trough = brake
            self._brake_falling = False
            return
        event = self._braking
        if previous is not None:
            self._brake_integral_event += (previous["brake"] + brake) * 0.5 * (sample["time"] - previous["time"])
        if brake >= event["peak_brake"]:
            event["peak_brake"] = brake
            event["peak_time"] = sample["time"]
        if sample["speed"] is not None:
            event["min_speed"] = min(event["min_speed"], sample["speed"]) if event["min_speed"] is not None else sample["speed"]
        if self._brake_falling:
            self._brake_trough = min(self._brake_trough, brake)
            if brake - self._brake_trough >= 5:
                event["reapplications"] += 1
                self._brake_falling = False
                self._brake_local_peak = brake
        else:
            self._brake_local_peak = max(self._brake_local_peak, brake)
            if self._brake_local_peak - brake >= 5:
                self._brake_falling = True
                self._brake_trough = brake

    def _finish_braking(self, sample: dict | None, truncated: bool) -> None:
        if self._braking is None:
            return
        event = self._braking
        end = sample["time"] if sample is not None else event["start_time"]
        if sample is not None and sample.get("speed") is not None:
            event["min_speed"] = min(event["min_speed"], sample["speed"]) if event["min_speed"] is not None else sample["speed"]
        duration = max(0.0, end - event["start_time"])
        event.update(end_time=end, duration=duration, exit_speed=sample.get("speed") if sample else None,
                     average_brake=self._brake_integral_event / duration if duration else event["peak_brake"],
                     release_seconds=None if truncated else max(0.0, end - event["peak_time"]),
                     truncated=truncated)
        self.events.append(event)
        self._braking = None

    def _lap_boundary(self, sample: dict, source: str, timer_origin: float | None = None) -> dict | None:
        completed = None
        sample_index = self._sample_count - 1 if sample is self._previous else self._sample_count
        boundary_time, boundary_distance, boundary_active = sample["time"], self._distance, self._active_seconds
        partial_start = False
        if timer_origin is not None and self._boundary_history:
            history = list(self._boundary_history)
            partial_start = timer_origin < history[0]["time"] - 0.05
            boundary_time = max(history[0]["time"], min(sample["time"], timer_origin))
            right = next((index for index, point in enumerate(history) if point["time"] >= boundary_time), len(history) - 1)
            before, after = history[max(0, right - 1)], history[right]
            duration = after["time"] - before["time"]
            fraction = (boundary_time - before["time"]) / duration if duration else 0
            boundary_distance = before["distance"] + fraction * (after["distance"] - before["distance"])
            boundary_active = before["active_seconds"] + fraction * (after["active_seconds"] - before["active_seconds"])
            sample_index = after["sample_index"]
        if self._lap_start is not None:
            duration = boundary_time - self._lap_start["time"]
            completed = {"id": self._lap_sequence, "start_time": self._lap_start["time"],
                         "end_time": boundary_time, "duration": max(0.0, duration),
                         "active_seconds": boundary_active - self._lap_start["active_seconds"],
                         "distance_m": boundary_distance - self._lap_start["distance"],
                         "start_sample": self._lap_start["sample_index"], "end_sample": sample_index,
                         "source": self._lap_start["source"],
                         "valid": self._lap_valid,
                         "complete": not self._lap_interrupted and duration > 0,
                         "interrupted": self._lap_interrupted or duration <= 0}
            if timer_origin is not None:
                completed.update(boundary_method="timer_backprojection", end_observed_at=sample["time"])
            self.laps.append(completed)
        self._lap_sequence += 1
        self._lap_start = {"time": boundary_time, "active_seconds": boundary_active,
                           "distance": boundary_distance, "source": source, "sample_index": sample_index}
        self._lap_interrupted = partial_start
        self._lap_valid = sample.get("lap_valid") if isinstance(sample.get("lap_valid"), bool) else None
        return completed

    def mark_lap(self) -> dict | None:
        """First mark starts timing; later marks complete the previous measured lap."""
        if self._previous is None:
            return None
        completed = self._lap_boundary(self._previous, source="manual")
        self._previous["lap_id"] = self._lap_sequence
        return completed

    def finalize(self) -> None:
        """Close a brake event at the final observed frame, without inventing a release."""
        self._finish_braking(self._previous, truncated=True)
        self._previous = None
        self._in_gap = True

    def summary(self) -> dict:
        active = self._active_seconds
        summary = {"active_seconds": active, "elapsed_seconds": self._elapsed_seconds,
                   "distance_m": self._distance,
                   "average_speed_kph": self._speed_integral / self._speed_seconds if self._speed_seconds else None,
                   "max_speed_kph": self._maximum_speed,
                   "avg_throttle": self._throttle_integral / active if active else None,
                   "avg_brake": self._brake_integral / active if active else None,
                   "braking_events": sum(event["type"] == "brake" for event in self.events),
                   "discontinuities": self._gap_count, "laps": len(self.laps),
                   "samples": self._sample_count, "speed_coverage_pct": 100 * self._speed_seconds / active if active else 0.0,
                   "estimated_channels": ["distance_m", "acceleration"],
                   "current_lap": self._lap_sequence or None}
        summary.update({name + "_pct": 100 * seconds / active if active else 0.0 for name, seconds in self._categories.items()})
        return summary

    def reference_points(self, lap_id: int | None = None) -> list[dict]:
        selected = self.samples
        if lap_id is not None:
            lap = next((lap for lap in self.laps if lap["id"] == lap_id), None)
            if lap is not None:
                selected = self.samples[lap["start_sample"]:lap["end_sample"] + 1]
            elif lap_id == self._lap_sequence and self._lap_start is not None:
                selected = self.samples[self._lap_start["sample_index"]:]
            else:
                selected = []
        selected = [sample for sample in selected if sample["active"]]
        if not selected:
            return []
        start_time, start_distance = selected[0]["time"], selected[0]["distance"]
        return [dict(sample, elapsed=sample["time"] - start_time, distance=sample["distance"] - start_distance) for sample in selected]

    def compare_laps(self, reference_id: int, current_id: int, axis: str = "distance") -> dict:
        if axis not in {"distance", "elapsed"}:
            raise ValueError("Comparison axis must be distance or elapsed")
        reference = self.reference_points(reference_id)
        current = self.reference_points(current_id)
        if not reference or not current:
            raise ValueError("Both laps need valid recorded observations")
        positions = [point[axis] for point in current]
        return {"axis": axis, "reference_lap": reference_id, "current_lap": current_id,
                "reference": resample_trace(reference, positions, axis), "current": current,
                "estimated_alignment": axis == "distance"}


def resample_trace(samples: Iterable[dict], positions: Iterable[float], axis: str = "distance") -> list[dict | None]:
    """Interpolate only within one continuous segment, with no endpoint extrapolation."""
    if axis not in {"distance", "elapsed"}:
        raise ValueError("Comparison axis must be distance or elapsed")
    groups: list[list[dict]] = []
    interrupted = False
    for sample in samples:
        coordinate = _number(sample.get(axis))
        if not sample.get("active", True) or coordinate is None or sample.get("segment_id") is None:
            interrupted = True
            continue
        if interrupted or not groups or sample["segment_id"] != groups[-1][-1]["segment_id"] or coordinate < groups[-1][-1][axis]:
            groups.append([])
        groups[-1].append(sample)
        interrupted = False
    indexes = [(group, [point[axis] for point in group]) for group in groups]
    result = []
    for position in positions:
        position = _number(position)
        match = None
        for group, coordinates in indexes:
            if position is None or position < coordinates[0] or position > coordinates[-1]:
                continue
            right = bisect.bisect_left(coordinates, position)
            if coordinates[right] == position:
                match = dict(group[right])
                break
            left = right - 1
            fraction = (position - coordinates[left]) / (coordinates[right] - coordinates[left])
            match = {axis: position, "segment_id": group[left]["segment_id"], "active": True}
            for channel in ("time", "elapsed", "distance", "throttle", "brake", "speed", "acceleration"):
                a, b = _number(group[left].get(channel)), _number(group[right].get(channel))
                match[channel] = a + (b - a) * fraction if a is not None and b is not None else None
            match["gear"] = group[left].get("gear")
            break
        result.append(match)
    return result
