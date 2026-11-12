"""Compare observed ranges from independent sessions without stretching their pace."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import math

from .analysis import _category_seconds, _number, _plausible_speed_change


CHANNELS = ("speed", "brake", "throttle", "gear", "steering")
UNITS = {"speed": "km/h", "brake": "%", "throttle": "%", "gear": "gear"}
MAX_GAP = 1.0
MAX_SPEED_GAP = 0.5


def _active(sample: dict) -> bool:
    confidence = _number(sample.get("confidence"))
    return bool(sample.get("active", sample.get("hud_visible", True)) and
                sample.get("hud_visible", True) and (confidence is None or confidence >= 0.35))


def _boundary_sample(before: dict, after: dict, stamp: float) -> dict:
    """Represent a requested time exactly without extending data across a cut."""
    continuous = (_active(before) and _active(after) and
                  before.get("segment_id") == after.get("segment_id") and
                  0 < after["time"] - before["time"] <= MAX_GAP)
    if not continuous:
        return {"time": stamp, "active": False, "hud_visible": False, "segment_id": None}
    fraction = (stamp - before["time"]) / (after["time"] - before["time"])
    first, last = _channels(before), _channels(after)
    result = {"time": stamp, "active": True, "hud_visible": True,
              "segment_id": before.get("segment_id"),
              "gear": first["gear"] if last["gear"] is not None else None,
              "steering_unit": first["steering_unit"], "steering_status": "tracked"}
    for key in ("speed", "brake", "throttle", "steering", "steering_confidence", "rpm"):
        a, b = first[key], last[key]
        result[key] = a + fraction * (b - a) if a is not None and b is not None else None
    if first["steering_unit"] != last["steering_unit"]:
        result["steering"] = None
    return result


def _gear(value):
    if isinstance(value, str):
        value = value.strip().upper()
        if value in {"R", "N"}:
            return value
    number = _number(value)
    return str(int(number)) if number is not None and number.is_integer() and 1 <= number <= 8 else None


def _select(samples: list[dict], selection: dict | None) -> tuple[list[tuple[int, dict]], dict]:
    if not samples:
        raise ValueError("Each comparison session needs recorded samples.")
    selection = selection or {}
    if not isinstance(selection, dict):
        raise ValueError("A comparison range must be an object.")
    permitted = {"start_index", "end_index", "start_time", "end_time", "segment_id"}
    if set(selection) - permitted:
        raise ValueError("Select a lap by start_index/end_index, or use start_time/end_time.")
    indexed = "start_index" in selection or "end_index" in selection
    if indexed and ("start_time" in selection or "end_time" in selection):
        raise ValueError("Choose sample indices or source times for a range, not both.")
    if indexed:
        start, end = selection.get("start_index", 0), selection.get("end_index", len(samples) - 1)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end)):
            raise ValueError("Range sample indices must be integers.")
        if not 0 <= start < end < len(samples):
            raise ValueError("Range indices must include at least two recorded samples.")
        chosen = list(enumerate(samples[start:end + 1], start))
    else:
        start = _number(selection.get("start_time", samples[0].get("time")))
        end = _number(selection.get("end_time", samples[-1].get("time")))
        if start is None or end is None or end <= start:
            raise ValueError("Range source times must be finite and end after start.")
        eligible = [(index, sample) for index, sample in enumerate(samples)
                    if _number(sample.get("time")) is not None and
                    (selection.get("segment_id") is None or sample.get("segment_id") == selection["segment_id"])]
        eligible_times = [float(sample["time"]) for _, sample in eligible]
        recorded_times = [float(sample["time"]) for sample in samples if _number(sample.get("time")) is not None]
        ordered = not any(right < left for left, right in zip(eligible_times, eligible_times[1:]))
        chosen = [(index, sample) for index, sample in eligible if start <= float(sample["time"]) <= end]
        for stamp, beginning in ((start, True), (end, False)):
            right = bisect_left(eligible_times, stamp) if ordered else 0
            if 0 < right < len(eligible) and eligible_times[right] != stamp:
                left_index, before = eligible[right - 1]
                right_index, after = eligible[right]
                boundary = _boundary_sample(before, after, stamp)
                if right_index != left_index + 1:
                    boundary.update(active=False, hud_visible=False, segment_id=None)
                pair = (left_index if beginning else right_index, boundary)
                if beginning:
                    chosen.insert(0, pair)
                else:
                    chosen.append(pair)
            elif (ordered and eligible and selection.get("segment_id") is not None
                  and min(recorded_times) <= stamp <= max(recorded_times)
                  and (stamp < eligible_times[0] or stamp > eligible_times[-1])):
                # A segment filter limits observations, not the user's source
                # clock. Keep IN/OUT outside that segment as explicit gaps;
                # never borrow telemetry from another segment or snap the time.
                boundary = {"time": stamp, "active": False, "hud_visible": False, "segment_id": None}
                pair = (eligible[0 if beginning else -1][0], boundary)
                if beginning:
                    chosen.insert(0, pair)
                else:
                    chosen.append(pair)
    if indexed and selection.get("segment_id") is not None:
        chosen = [(index, sample) for index, sample in chosen if sample.get("segment_id") == selection["segment_id"]]
    if len(chosen) < 2:
        raise ValueError("The selected range contains fewer than two recorded samples.")
    times = [_number(sample.get("time")) for _, sample in chosen]
    if any(time is None for time in times):
        raise ValueError("The selected range contains an invalid source timestamp.")
    if any(right < left for left, right in zip(times, times[1:])):
        raise ValueError("This range crosses a seek. Select a single continuous segment or use sample indices.")
    return chosen, {"start_index": chosen[0][0], "end_index": chosen[-1][0],
                    "start_time": times[0], "end_time": times[-1],
                    "requested_start_time": selection.get("start_time"),
                    "requested_end_time": selection.get("end_time")}


def _integration_speeds(chosen: list[tuple[int, dict]]) -> tuple[list, list]:
    speeds = [_channels(sample)["speed"] if _active(sample) else None for _, sample in chosen]
    gaps = []
    index = 0
    while index < len(chosen):
        if speeds[index] is not None:
            index += 1
            continue
        first = index
        while index < len(chosen) and speeds[index] is None:
            index += 1
        if first == 0 or index == len(chosen):
            continue
        left, right = first - 1, index
        before, after = chosen[left][1], chosen[right][1]
        dt = after["time"] - before["time"]
        continuous = all(_active(chosen[j][1]) and
                         chosen[j][1].get("segment_id") == before.get("segment_id") and
                         chosen[j][0] == chosen[j - 1][0] + 1 and
                         chosen[j][1]["time"] > chosen[j - 1][1]["time"]
                         for j in range(first, right + 1))
        if not continuous or not 0 < dt <= MAX_SPEED_GAP or not _plausible_speed_change(speeds[left], speeds[right], dt):
            continue
        for j in range(first, right):
            fraction = (chosen[j][1]["time"] - before["time"]) / dt
            speeds[j] = speeds[left] + fraction * (speeds[right] - speeds[left])
        gaps.append({"start_time": before["time"], "end_time": after["time"],
                     "samples": right - first, "duration": dt})
    return speeds, gaps


def _channels(sample: dict) -> dict:
    result = {}
    for channel, upper in (("speed", 500), ("brake", 100), ("throttle", 100)):
        value = _number(sample.get(channel))
        result[channel] = value if value is not None and 0 <= value <= upper else None
    if sample.get("speed_outlier"):
        result["speed"] = None
    result["gear"] = _gear(sample.get("gear"))
    confidence = sample.get("steering_confidence")
    if confidence is None and isinstance(sample.get("channel_confidence"), dict):
        confidence = sample["channel_confidence"].get("steering")
    confidence = _number(confidence)
    steering = _number(sample.get("steering"))
    unit = sample.get("steering_unit")
    valid = (confidence is not None and 0.6 <= confidence <= 1 and unit in {"hud_percent", "degrees"}
             and sample.get("steering_status", "tracked") == "tracked")
    if unit == "hud_percent" and steering is not None and not -100 <= steering <= 100:
        valid = False
    result.update(steering=steering if valid else None,
                  steering_unit=unit if valid and steering is not None else None,
                  steering_confidence=confidence if confidence is not None and 0 <= confidence <= 1 else None)
    result["rpm"] = _number(sample.get("rpm"))
    return result


def _prepare(samples: list[dict], selection: dict | None) -> dict:
    chosen, bounds = _select(samples, selection)
    integration_speeds, speed_gaps = _integration_speeds(chosen)
    points, segments = [], []
    distance = 0.0
    segment = distance_segment = 0
    previous = None
    has_gaps = False
    distance_complete = True
    distance_estimated = False
    for (index, source), integration_speed in zip(chosen, integration_speeds):
        stamp = float(source["time"])
        active = _active(source)
        point = {"time": stamp, "elapsed": stamp - bounds["start_time"], "sample_index": index,
                 "active": bool(active), "source_segment_id": source.get("segment_id"), **_channels(source)}
        point["integration_speed"] = integration_speed
        point["distance_interpolated"] = integration_speed is not None and point["speed"] is None
        distance_estimated = distance_estimated or point["distance_interpolated"]
        point["distance_estimated"] = distance_estimated
        if not active:
            point.update({channel: None for channel in CHANNELS})
            point.update(distance=None, raw_distance=None, segment_id=None, distance_segment_id=None)
            has_gaps = True
            distance_complete = False
            previous = None
            points.append(point)
            continue
        continuous = (previous is not None and index == previous["sample_index"] + 1
                      and source.get("segment_id") == previous["source_segment_id"]
                      and 0 < stamp - previous["time"] <= MAX_GAP)
        if not continuous:
            segment += 1
            distance_segment += 1
            if points:
                has_gaps = True
                distance_complete = False
            segments.append({"id": segment, "source_segment_id": source.get("segment_id"),
                             "start_time": stamp, "start_elapsed": point["elapsed"], "start_distance": distance})
        elif integration_speed is not None and previous["integration_speed"] is not None:
            distance += (integration_speed + previous["integration_speed"]) * 0.5 * (stamp - previous["time"]) / 3.6
        else:
            distance_complete = False
            distance_segment += 1
        if integration_speed is None:
            distance_complete = False
        point.update(segment_id=segment,
                     distance=distance if integration_speed is not None else None,
                     raw_distance=distance if integration_speed is not None else None,
                     distance_segment_id=distance_segment if integration_speed is not None else None)
        segments[-1].update(end_time=stamp, end_elapsed=point["elapsed"], end_distance=distance)
        points.append(point)
        previous = point
    valid = [point for point in points if point["active"]]
    if len(valid) < 2:
        raise ValueError("Each comparison range needs at least two usable HUD observations.")
    units = {point["steering_unit"] for point in valid if point["steering"] is not None}
    mixed_steering = len(units) > 1
    if mixed_steering:
        for point in points:
            point["steering"] = None
    metrics, channels = _metrics(points)
    channels["steering"]["unit"] = next(iter(units)) if len(units) == 1 else None
    braking, shifts = _events(points)
    metrics.update(braking_events=len(braking), upshifts=sum(event["direction"] == "up" for event in shifts),
                   downshifts=sum(event["direction"] == "down" for event in shifts), distance_m=distance)
    distance_values = [point["distance"] for point in valid if point["distance"] is not None]
    strict_distance = len(distance_values) == len(valid) and all(b > a for a, b in zip(distance_values, distance_values[1:]))
    # Distance playback cannot represent time before/after observed movement,
    # including a stationary plateau. Let the UI retain the chosen video
    # boundaries by reviewing on the elapsed-time clock in those cases.
    distance_boundaries_complete = (
        points[0]["distance"] is not None and points[-1]["distance"] is not None
        and len(distance_values) > 1
        and distance_values[1] > distance_values[0]
        and distance_values[-1] > distance_values[-2]
    )
    return {"points": points, "selection": bounds, "segments": segments, "has_gaps": has_gaps,
            "distance_complete": distance_complete, "strict_distance": strict_distance,
            "distance_boundaries_complete": distance_boundaries_complete,
            "distance_estimated": distance_estimated, "speed_gap_interpolations": speed_gaps,
            "mixed_steering": mixed_steering, "metrics": metrics, "channels": channels,
            "braking_events": braking, "shifts": shifts,
            "duration": bounds["end_time"] - bounds["start_time"], "distance": distance, "raw_distance": distance}


def _metrics(points: list[dict]) -> tuple[dict, dict]:
    active_seconds = 0.0
    coverage = {channel: 0.0 for channel in CHANNELS}
    integrals = {channel: 0.0 for channel in ("speed", "brake", "throttle", "steering")}
    categories = dict(full_throttle=0.0, braking=0.0, coasting=0.0, overlap=0.0)
    paired_seconds = 0.0
    for previous, point in zip(points, points[1:]):
        if not previous["active"] or not point["active"] or previous["segment_id"] != point["segment_id"]:
            continue
        dt = point["time"] - previous["time"]
        if not 0 < dt <= MAX_GAP:
            continue
        active_seconds += dt
        for channel in CHANNELS:
            a, b = previous[channel], point[channel]
            if a is not None and b is not None:
                coverage[channel] += dt
                if channel in integrals:
                    integrals[channel] += (a + b) * 0.5 * dt
        brake_known = point["brake"] is not None and previous["brake"] is not None
        throttle_known = point["throttle"] is not None and previous["throttle"] is not None
        values = _category_seconds({channel: previous[channel] or 0.0 for channel in ("brake", "throttle")},
                                   {channel: point[channel] or 0.0 for channel in ("brake", "throttle")}, dt)
        if brake_known:
            categories["braking"] += values["braking"]
        if throttle_known:
            categories["full_throttle"] += values["full_throttle"]
        if brake_known and throttle_known:
            paired_seconds += dt
            categories["coasting"] += values["coasting"]
            categories["overlap"] += values["overlap"]
    channels = {}
    for channel in CHANNELS:
        count = sum(point[channel] is not None for point in points if point["active"])
        channels[channel] = {"available": count > 0, "samples": count, "observed_seconds": coverage[channel],
                             "coverage_pct": 100 * coverage[channel] / active_seconds if active_seconds else 0.0,
                             "unit": UNITS.get(channel)}
    metrics = {"active_seconds": active_seconds, "sample_count": len(points),
               "average_speed_kph": integrals["speed"] / coverage["speed"] if coverage["speed"] else None,
               "max_speed_kph": max((point["speed"] for point in points if point["active"] and point["speed"] is not None), default=None),
               "avg_brake": integrals["brake"] / coverage["brake"] if coverage["brake"] else None,
               "avg_throttle": integrals["throttle"] / coverage["throttle"] if coverage["throttle"] else None}
    for name, seconds in categories.items():
        denominator = coverage["brake"] if name == "braking" else coverage["throttle"] if name == "full_throttle" else paired_seconds
        metrics[name + "_pct"] = 100 * seconds / denominator if denominator else None
    return metrics, channels


def _events(points: list[dict]) -> tuple[list[dict], list[dict]]:
    braking, shifts = [], []
    event = None
    previous = None
    local_peak = trough = 0.0
    falling = False

    def finish(point, truncated):
        nonlocal event
        if event is None:
            return
        elapsed = point["elapsed"] if point else event["start_elapsed"]
        speed = point["speed"] if point else None
        if speed is not None:
            event["min_speed"] = min(event["min_speed"], speed) if event["min_speed"] is not None else speed
        event.update(end_time=point["time"] if point else event["start_time"], end_elapsed=elapsed,
                     end_distance=point["distance"] if point else None, exit_speed=speed,
                     duration=elapsed - event["start_elapsed"], truncated_end=truncated,
                     truncated=truncated or event["truncated_start"],
                     release_seconds=None if truncated or event["truncated_start"] else elapsed - event["peak_elapsed"])
        braking.append(event)
        event = None

    for point in points:
        continuous = previous is not None and previous["segment_id"] == point["segment_id"] and point["active"]
        if not continuous:
            finish(previous, True)
        if not point["active"]:
            previous = None
            continue
        if continuous and previous["gear"] is not None and point["gear"] is not None and previous["gear"] != point["gear"]:
            before, after = previous["gear"], point["gear"]
            direction = "up" if before.isdigit() and after.isdigit() and int(after) > int(before) else "down" if before.isdigit() and after.isdigit() else "neutral_or_reverse"
            shifts.append({"time": point["time"], "elapsed": point["elapsed"], "distance": point["distance"],
                           "segment_id": point["segment_id"], "from_gear": before, "to_gear": after,
                           "direction": direction, "speed": point["speed"], "rpm": point["rpm"],
                           "brake": point["brake"], "throttle": point["throttle"]})
        brake = point["brake"]
        if brake is None:
            finish(previous, True)
        elif brake <= 3:
            finish(point, False)
        elif event is None:
            event = {"start_time": point["time"], "start_elapsed": point["elapsed"],
                     "start_distance": point["distance"], "segment_id": point["segment_id"],
                     "peak_brake": brake, "peak_elapsed": point["elapsed"], "peak_distance": point["distance"], "entry_speed": point["speed"],
                     "min_speed": point["speed"], "reapplications": 0,
                     "truncated_start": not continuous or previous["brake"] is None}
            local_peak = trough = brake
            falling = False
        else:
            if brake >= event["peak_brake"]:
                event["peak_brake"], event["peak_elapsed"] = brake, point["elapsed"]
                event["peak_distance"] = point["distance"]
            if point["speed"] is not None:
                event["min_speed"] = min(event["min_speed"], point["speed"]) if event["min_speed"] is not None else point["speed"]
            if falling:
                trough = min(trough, brake)
                if brake - trough >= 5:
                    event["reapplications"] += 1
                    falling, local_peak = False, brake
            else:
                local_peak = max(local_peak, brake)
                if local_peak - brake >= 5:
                    falling, trough = True, brake
        previous = point
    finish(previous, True)
    return braking, shifts


def _index(points: list[dict], axis: str) -> tuple[list, list]:
    groups = []
    key = "distance_segment_id" if axis == "distance" else "segment_id"
    interrupted = False
    for point in points:
        if not point["active"] or point[axis] is None:
            interrupted = True
            continue
        if interrupted or not groups or point[key] != groups[-1][-1][key]:
            groups.append([])
        groups[-1].append(point)
        interrupted = False
    return [(group, [point[axis] for point in group]) for group in groups], [group[0][axis] for group in groups]


def _at(index, position: float, axis: str) -> dict | None:
    groups, starts = index
    group_number = bisect_right(starts, position) - 1
    if group_number < 0:
        return None
    group, coordinates = groups[group_number]
    if position > coordinates[-1]:
        return None
    right = bisect_left(coordinates, position)
    exact = coordinates[right] == position
    before = group[right] if exact else group[right - 1]
    after = group[right]
    fraction = 0.0 if exact else (position - before[axis]) / (after[axis] - before[axis])
    result = {}
    for channel in ("time", "elapsed", "distance", "raw_distance", "speed", "brake", "throttle", "steering", "steering_confidence", "rpm"):
        a, b = before[channel], after[channel]
        result[channel] = a + (b - a) * fraction if a is not None and b is not None else None
    result[axis] = position
    result["gear"] = before["gear"] if before["gear"] is not None and after["gear"] is not None else None
    result["steering_unit"] = before["steering_unit"] if before["steering_unit"] == after["steering_unit"] else None
    if result["steering_unit"] is None:
        result["steering"] = None
    result["segment_id"] = before["distance_segment_id" if axis == "distance" else "segment_id"]
    result["distance_segment_id"] = before["distance_segment_id"]
    result["source_segment_id"] = before["source_segment_id"]
    result["distance_estimated"] = before["distance_estimated"] or after["distance_estimated"]
    result["distance_interpolated"] = before["distance_interpolated"] or after["distance_interpolated"]
    return result


def _delta_index(points: list[dict]) -> tuple[tuple, list[float]]:
    """Index observed moving intervals without assigning a time to a plateau.

    A cut or stop may put several source times at the same integrated distance.
    That coordinate has no unique passage time. Split at it, and let the caller
    retain useful delta on either side instead of discarding the entire lap.
    """
    source_groups, _ = _index(points, "distance")
    groups, ambiguous = [], set()
    previous = None
    for source, _ in source_groups:
        group = []
        for point in source:
            if previous is not None and point["distance"] == previous["distance"]:
                ambiguous.add(point["distance"])
                if group:
                    groups.append(group)
                    group = []
            group.append(point)
            previous = point
        if group:
            groups.append(group)
    index = ([(group, [point["distance"] for point in group]) for group in groups],
             [group[0]["distance"] for group in groups])
    return index, sorted(ambiguous)


def _delta_at(lookup: tuple, distance: float) -> dict | None:
    index, ambiguous = lookup
    insertion = bisect_left(ambiguous, distance)
    if any(math.isclose(distance, value, rel_tol=1e-12, abs_tol=1e-9)
           for value in ambiguous[max(0, insertion - 1):insertion + 1]):
        return None
    result = _at(index, distance, "distance")
    if result is not None:
        result["segment_id"] = bisect_right(index[1], distance)
    return result


def _register_distance(reference: dict, current: dict, mode: str,
                       align_start: bool, align_end: bool, landmarks: list | None) -> dict:
    if mode not in {"raw", "lap_endpoints", "landmarks"}:
        raise ValueError("alignment_mode must be raw, lap_endpoints, or landmarks.")
    if not isinstance(align_end, bool):
        raise ValueError("align_end must be a boolean.")
    landmarks = [] if landmarks is None else landmarks
    if not isinstance(landmarks, list) or len(landmarks) > 32:
        raise ValueError("Provide at most 32 paired landmarks.")
    metadata = {"mode": mode, "applied": mode != "raw", "estimated": True,
                "coordinate": "reference_speed_integrated" if mode != "raw" else "independent_speed_integrated",
                "anchors": [], "scales": [], "max_correction_pct": 0.0}
    if mode == "raw":
        return metadata
    if not align_start or not align_end:
        raise ValueError("Distance registration requires confirmation of the same physical start and finish positions.")
    if mode == "landmarks" and not landmarks:
        raise ValueError("Landmark registration requires at least one paired interior landmark.")
    if mode == "lap_endpoints" and landmarks:
        raise ValueError("Choose landmark registration to use intermediate landmarks.")
    anchors = [{"label": "Start", "reference_time": reference["selection"]["start_time"],
                "current_time": current["selection"]["start_time"],
                "reference_distance_m": 0.0, "current_distance_m": 0.0}]
    # Invalid configuration must not be hidden by the data-quality fallback.
    # Check every landmark's schema, boundaries and time order before deciding
    # whether the observed distances can support the requested registration.
    for landmark in landmarks:
        if not isinstance(landmark, dict) or set(landmark) - {"current_time", "reference_time", "label"}:
            raise ValueError("Each landmark needs current_time, reference_time, and an optional label.")
        label = landmark.get("label", "Landmark")
        if not isinstance(label, str) or len(label) > 80:
            raise ValueError("A landmark label must contain at most 80 characters.")
        anchor = {"label": label}
        for side, trace in (("reference", reference), ("current", current)):
            stamp = _number(landmark.get(side + "_time"))
            if stamp is None or not trace["selection"]["start_time"] < stamp < trace["selection"]["end_time"]:
                raise ValueError("Landmark timestamps must lie strictly inside each selected range.")
            if stamp <= anchors[-1][side + "_time"]:
                raise ValueError("Landmark timestamps must increase in both recordings.")
            anchor[side + "_time"] = stamp
        anchors.append(anchor)

    def raw_fallback():
        return {**metadata, "mode": "raw", "applied": False, "requested_mode": mode,
                "coordinate": "independent_speed_integrated",
                "fallback_reason": "Recording gaps, missing speed, or stationary intervals prevent distance registration."}

    if any(trace["has_gaps"] or not trace["distance_complete"] or not trace["strict_distance"]
           for trace in (reference, current)):
        return raw_fallback()
    indices = {side: _index(trace["points"], "elapsed")
               for side, trace in (("reference", reference), ("current", current))}
    for anchor in anchors[1:]:
        for side, trace in (("reference", reference), ("current", current)):
            point = _at(indices[side], anchor[side + "_time"] - trace["selection"]["start_time"], "elapsed")
            if point is None or point["distance"] is None:
                return raw_fallback()
            anchor[side + "_distance_m"] = point["distance"]
    anchors.append({"label": "Finish", "reference_time": reference["selection"]["end_time"],
                    "current_time": current["selection"]["end_time"],
                    "reference_distance_m": reference["distance"], "current_distance_m": current["distance"]})
    current_distances = [anchor["current_distance_m"] for anchor in anchors]
    reference_distances = [anchor["reference_distance_m"] for anchor in anchors]
    if any(b <= a for values in (current_distances, reference_distances) for a, b in zip(values, values[1:])):
        raise ValueError("Landmarks must identify distinct increasing distances in both recordings.")
    scales = [(b - a) / (d - c) for a, b, c, d in
              zip(reference_distances, reference_distances[1:], current_distances, current_distances[1:])]

    def registered(distance):
        if distance is None:
            return None
        if distance >= current_distances[-1]:
            return reference_distances[-1]
        left = max(0, bisect_right(current_distances, distance) - 1)
        return reference_distances[left] + (distance - current_distances[left]) * scales[left]

    # Preserve each kink exactly even when a landmark falls between source frames.
    times = [point["time"] for point in current["points"]]
    for anchor in anchors[1:-1]:
        stamp = anchor["current_time"]
        right = bisect_left(times, stamp)
        if times[right] != stamp:
            point = {**current["points"][right - 1],
                     **_at(indices["current"], stamp - current["selection"]["start_time"], "elapsed")}
            point["time"] = stamp
            current["points"].insert(right, point)
            times.insert(right, stamp)
    for point in current["points"]:
        point["distance"] = registered(point["distance"])
    for event in current["braking_events"]:
        for edge in ("start", "peak", "end"):
            key = edge + "_distance"
            event["raw_" + key] = event[key]
            event[key] = registered(event[key])
    for event in current["shifts"]:
        event["raw_distance"] = event["distance"]
        event["distance"] = registered(event["distance"])
    for segment in current["segments"]:
        for edge in ("start", "end"):
            key = edge + "_distance"
            segment["raw_" + key] = segment[key]
            segment[key] = registered(segment[key])
    current["distance"] = reference["distance"]
    current["metrics"]["registered_distance_m"] = current["distance"]
    metadata.update(anchors=anchors, scales=scales,
                    max_correction_pct=max(abs(scale - 1) * 100 for scale in scales))
    return metadata


def _feature_positions(trace: dict, axis: str) -> dict[float, float]:
    """Retain observed narrow peaks, their widths, shifts, and missing-data edges."""
    features = {}

    def add(position, priority):
        if position is not None:
            features[position] = max(features.get(position, 0.0), priority)

    points = trace["points"]
    for event in trace["braking_events"]:
        for boundary in ("start", "peak", "end"):
            add(event.get(boundary + "_" + axis), 6.0)
    for before, after in zip(points, points[1:]):
        key = "distance_segment_id" if axis == "distance" else "segment_id"
        for channel in CHANNELS:
            missing_edge = (before[channel] is None) != (after[channel] is None)
            gear_change = channel == "gear" and before[channel] != after[channel]
            segment_change = before[key] != after[key]
            if missing_edge or gear_change or segment_change:
                priority = 9.0 if missing_edge or segment_change else 6.0
                add(before[axis], priority)
                add(after[axis], priority)
    scales = {"speed": 300.0, "brake": 100.0, "throttle": 100.0, "steering": 200.0}
    for left, point, right in zip(points, points[1:], points[2:]):
        if any(value[axis] is None or not value["active"] for value in (left, point, right)):
            continue
        if not left["segment_id"] == point["segment_id"] == right["segment_id"] or right[axis] <= left[axis]:
            continue
        fraction = (point[axis] - left[axis]) / (right[axis] - left[axis])
        for channel, scale in scales.items():
            if any(value[channel] is None for value in (left, point, right)):
                continue
            expected = left[channel] + (right[channel] - left[channel]) * fraction
            prominence = abs(point[channel] - expected) / scale
            if prominence >= 0.005:
                add(point[axis], 3 + prominence)
                add(left[axis], 2 + prominence)
                add(right[axis], 2 + prominence)
    return features


def _preserve_features(positions: list[float], traces: tuple[dict, dict], axis: str,
                       max_points: int, protected: set[float]) -> tuple[list[float], int]:
    priorities = {}
    for trace in traces:
        for position, priority in _feature_positions(trace, axis).items():
            priorities[position] = max(priorities.get(position, 0.0), priority)
    if len(set(positions) | set(priorities)) <= max_points:
        return sorted(set(positions) | set(priorities)), 0
    if not priorities:
        return positions, 0
    selected = set(protected)
    feature_budget = max(0, (max_points - len(selected)) * 2 // 3)
    ranked = sorted(priorities, key=lambda position: (-priorities[position], position))
    selected.update(ranked[:feature_budget])
    remaining = max_points - len(selected)
    if remaining > 1:
        selected.update(positions[round(index * (len(positions) - 1) / (remaining - 1))] for index in range(remaining))
    for position in positions:
        if len(selected) >= max_points:
            break
        selected.add(position)
    for position in ranked:
        if len(selected) >= max_points:
            break
        selected.add(position)
    return sorted(selected), sum(position not in selected for position in priorities)


def trace_session_range(samples: list[dict], selection: dict | None = None,
                        axis: str = "distance", max_points: int = 1200) -> dict:
    """Return a standalone recording using the same coordinates as comparisons."""
    if axis not in {"distance", "elapsed"}:
        raise ValueError("Comparison axis must be distance or elapsed.")
    if isinstance(max_points, bool) or not isinstance(max_points, int) or not 2 <= max_points <= 5000:
        raise ValueError("max_points must be an integer between 2 and 5000.")
    current = _prepare(samples, selection)
    extent = current["distance"] if axis == "distance" else current["duration"]
    if extent <= 0:
        raise ValueError("This range has no usable distance; select elapsed time or a moving range.")
    step = extent / (max_points - 1)
    positions = [index * step for index in range(max_points - 1)] + [extent]
    positions, omitted = _preserve_features(positions, (current,), axis, max_points, {0.0, extent})
    index = _index(current["points"], axis)
    points = [{"position": position, "current": _at(index, position, axis),
               "reference": None, "delta_time_s": None} for position in positions]
    warnings = []
    if current["has_gaps"]:
        warnings.append("Selected range contains data gaps; traces are split at each discontinuity.")
    if not current["distance_complete"]:
        warnings.append("Distance excludes unobserved movement; positions after missing data may be offset.")
    if current["distance_estimated"]:
        count = sum(gap["samples"] for gap in current["speed_gap_interpolations"])
        warnings.append(f"Distance bridges {count} missing speed observations within bounded gaps of at most {MAX_SPEED_GAP:g}s; raw speed remains missing.")
    if current["mixed_steering"]:
        warnings.append("Steering units change inside this range; steering trace is unavailable.")
    channels = {channel: {"current": details["available"], "reference": False,
                           "comparable": False, "unit": details["unit"]}
                for channel, details in current["channels"].items()}
    for event in current["braking_events"]:
        event["start_position"] = event["start_distance" if axis == "distance" else "start_elapsed"]
        event["end_position"] = event["end_distance" if axis == "distance" else "end_elapsed"]
    for event in current["shifts"]:
        event["position"] = event[axis]
    for key in ("points", "mixed_steering", "strict_distance"):
        current.pop(key)
    return {"axis": axis, "unit": "m" if axis == "distance" else "s", "extent": [0.0, extent],
            "common_extent": None, "step": step, "align_start": False, "align_end": False,
            "alignment": {"mode": "raw", "applied": False, "anchors": []},
            "estimated_alignment": axis == "distance", "delta_available": False,
            "delta_estimated": False, "points": points, "reference": None, "current": current,
            "channels": channels, "warnings": warnings,
            "sampling": {"method": "feature_preserving", "omitted_feature_positions": omitted}}


def compare_session_ranges(reference_samples: list[dict], current_samples: list[dict],
                           reference_range: dict | None = None, current_range: dict | None = None,
                           axis: str = "distance", align_start: bool = False,
                           step: float | None = None, max_points: int = 1200,
                           alignment_mode: str = "raw", align_end: bool = False,
                           landmarks: list[dict] | None = None) -> dict:
    """Compare selected ranges without changing source elapsed times.

    ``align_start`` is the user's explicit assertion that both selected starts show
    the same physical position. Optional registration uses confirmed physical
    endpoints/landmarks to correct integrated-distance drift, never pedal matching.
    Only bounded short speed dropouts are interpolated for distance integration.

    Delta is local to the available moving observations, not gated by an entire
    lap's coverage. The elapsed chart retains both original source clocks while
    its delta lane compares matching fractions of integrated lap distance.
    Unregistered ranges remain an explicitly approximate correspondence.
    """
    if axis not in {"distance", "elapsed"}:
        raise ValueError("Comparison axis must be distance or elapsed.")
    if not isinstance(align_start, bool):
        raise ValueError("align_start must be a boolean.")
    if isinstance(max_points, bool) or not isinstance(max_points, int) or not 2 <= max_points <= 5000:
        raise ValueError("max_points must be an integer between 2 and 5000.")
    reference = _prepare(reference_samples, reference_range)
    current = _prepare(current_samples, current_range)
    if alignment_mode == "landmarks" and isinstance(landmarks, list) and len(landmarks) + 2 > max_points:
        raise ValueError("max_points must be large enough to retain all registration landmarks.")
    alignment = _register_distance(reference, current, alignment_mode, align_start, align_end, landmarks)
    warnings = []
    for label, trace in (("Reference", reference), ("Current", current)):
        if trace["has_gaps"]:
            warnings.append(f"{label} range contains data gaps; traces are split at each discontinuity.")
        if not trace["distance_complete"]:
            warnings.append(f"{label} distance excludes unobserved movement; positions after missing data may be offset.")
        if trace["distance_estimated"]:
            count = sum(gap["samples"] for gap in trace["speed_gap_interpolations"])
            warnings.append(f"{label} distance bridges {count} missing speed observations within bounded gaps of at most {MAX_SPEED_GAP:g}s; delta is estimated and raw speed remains missing.")
        if trace["mixed_steering"]:
            warnings.append(f"{label} steering units change inside this range; steering comparison is unavailable.")
    if alignment["applied"]:
        warnings.append("Registered distance is an estimated shared coordinate between confirmed physical markers, not a measured racing line or exact track distance.")
        if alignment["max_correction_pct"] > 5:
            warnings.append(f"Distance registration changes at least one interval by {alignment['max_correction_pct']:.1f}%; verify its paired physical markers and speed readings.")
    elif alignment.get("fallback_reason"):
        warnings.append(alignment["fallback_reason"] + " Showing raw distance and approximate delta where observations are available.")
    channels = {}
    for channel in CHANNELS:
        a, b = reference["channels"][channel], current["channels"][channel]
        comparable = a["available"] and b["available"] and a["unit"] == b["unit"]
        channels[channel] = {"reference": a["available"], "current": b["available"],
                             "comparable": comparable, "unit": a["unit"] if comparable else a["unit"] or b["unit"]}
    unit_mismatch = (reference["channels"]["steering"]["available"] and current["channels"]["steering"]["available"]
                     and reference["channels"]["steering"]["unit"] != current["channels"]["steering"]["unit"])
    if unit_mismatch:
        warnings.append("Steering units differ between sessions; steering traces cannot be overlaid.")
    delta_mode = "registered" if alignment["applied"] else "approximate"
    delta_basis = "registered_distance" if alignment["applied"] else "distance" if axis == "distance" else "lap_progress"
    if not align_start:
        warnings.append("Estimated delta assumes the selected ranges begin at comparable positions; their physical start alignment is unconfirmed.")
    if axis == "elapsed" and not alignment["applied"]:
        warnings.append("Estimated delta compares matching fractions of each selected range's integrated distance, not simultaneous elapsed timestamps.")
    extents = [trace["distance"] if axis == "distance" else trace["duration"] for trace in (reference, current)]
    extent = max(extents)
    if extent <= 0:
        raise ValueError("This range has no usable distance; select elapsed time or a moving range.")
    if step is not None:
        step = _number(step)
        if step is None or step <= 0:
            raise ValueError("Comparison step must be finite and positive.")
    step = max(step or extent / (max_points - 1), extent / (max_points - 1))
    positions = [index * step for index in range(min(max_points - 1, math.ceil(extent / step)))]
    positions.append(extent)
    common_end = min(extents)
    if max_points > 2 and 0 < common_end < extent and common_end not in positions:
        if len(positions) >= max_points:
            closest = min(range(1, len(positions) - 1), key=lambda index: abs(positions[index] - common_end))
            positions.pop(closest)
        positions = sorted([*positions, common_end])
    protected = {0.0, extent}
    if max_points > 2:
        protected.add(common_end)
    anchor_positions = [anchor["reference_distance_m"] for anchor in alignment["anchors"]] if axis == "distance" else []
    if len(set(anchor_positions) | protected) > max_points:
        raise ValueError("max_points must be large enough to retain all registration landmarks.")
    protected.update(anchor_positions)
    positions = sorted(set(positions) | protected)
    while len(positions) > max_points:
        removable = [index for index, position in enumerate(positions) if position not in protected]
        index = min(removable, key=lambda i: min(positions[i] - positions[i - 1], positions[i + 1] - positions[i]))
        positions.pop(index)
    positions, omitted_features = _preserve_features(positions, (reference, current), axis, max_points, protected)
    reference_index, current_index = _index(reference["points"], axis), _index(current["points"], axis)
    reference_delta, current_delta = _delta_index(reference["points"]), _delta_index(current["points"])
    points = []
    for position in positions:
        a, b = _at(reference_index, position, axis), _at(current_index, position, axis)
        delta_reference = delta_current = None
        if axis == "distance":
            delta_reference = _delta_at(reference_delta, position)
            delta_current = _delta_at(current_delta, position)
        elif b is not None and b["distance"] is not None and min(reference["distance"], current["distance"]) > 0:
            # Keep the chart's original source times. The delta lane alone looks
            # up the reference's passage at this car's comparable lap progress.
            delta_current = _delta_at(current_delta, b["distance"])
            if delta_current is not None and not math.isclose(delta_current["elapsed"], b["elapsed"], abs_tol=1e-8):
                delta_current = None
            target = b["distance"] * reference["distance"] / current["distance"]
            target = min(reference["distance"], max(0.0, target))
            delta_reference = _delta_at(reference_delta, target)
        comparable = delta_current is not None and delta_reference is not None
        if unit_mismatch:
            for value in (a, b):
                if value is not None:
                    value["steering"] = value["steering_unit"] = None
        points.append({"position": position, "reference": a, "current": b,
                       "delta_time_s": delta_current["elapsed"] - delta_reference["elapsed"] if comparable else None,
                       "delta_segment_id": f'{delta_current["segment_id"]}:{delta_reference["segment_id"]}' if comparable else None,
                       "delta_current_time": delta_current["time"] if comparable else None,
                       "delta_reference_time": delta_reference["time"] if comparable else None})
    delta_count = sum(point["delta_time_s"] is not None for point in points)
    delta_available = delta_count > 0
    if not delta_available:
        warnings.append("Time delta has no comparable moving observations in these selected ranges.")
    for trace in (reference, current):
        for event in trace["braking_events"]:
            event["start_position"] = event["start_distance" if axis == "distance" else "start_elapsed"]
            event["end_position"] = event["end_distance" if axis == "distance" else "end_elapsed"]
        for event in trace["shifts"]:
            event["position"] = event[axis]
        for key in ("points", "mixed_steering", "strict_distance"):
            trace.pop(key)
    return {"axis": axis, "unit": "m" if axis == "distance" else "s", "extent": [0.0, extent],
            "common_extent": [0.0, min(extents)], "step": step, "align_start": align_start,
            "align_end": align_end, "alignment": alignment,
            "estimated_alignment": axis == "distance", "delta_available": delta_available,
            "delta_estimated": delta_available, "delta_mode": delta_mode, "delta_basis": delta_basis,
            "delta_sample_count": delta_count,
            "points": points, "reference": reference, "current": current,
            "channels": channels, "warnings": warnings,
            "sampling": {"method": "feature_preserving", "omitted_feature_positions": omitted_features}}
