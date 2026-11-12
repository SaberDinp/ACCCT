"""Licensed map centerlines for estimated along-lap progress, never vehicle paths.

Track geometry and labels use ACC-specific community datasets. Annotation
positions are approximate; a recording still needs its own confirmed alignment.
"""

from __future__ import annotations

from bisect import bisect_right
import copy
from functools import lru_cache
import hashlib
import json
import math
import re
from pathlib import Path


ASSETS = Path(__file__).resolve().parents[1] / "assets" / "tracks"
COMMIT = "f1551541a01f2711ba01f4d1020babf78a2b90ff"
REPOSITORY = "https://github.com/SpeedHQ/RaceIQ"
ANNOTATIONS_COMMIT = "eb4af9572aeb247e6406bc154d73a3bd16d8c28a"
ANNOTATIONS_REPOSITORY = "https://github.com/Lovely-Sim-Racing/lovely-track-data"
_CATALOG = json.loads((ASSETS / "index.json").read_text(encoding="utf-8"))


def _finite(value, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a finite number") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def validate_geometry(rows: list[list[float]], length_m: float | None = None,
                      *, coordinate_system: str = "cartesian_xy") -> dict:
    """Validate metric centerline rows [x, y/z, right width, left width].

    Self-intersections are permitted: Suzuka has a real grade-separated crossing.
    A short final gap is closed; an open or implausibly scaled path is rejected.
    Raw ACC x/z has z increasing downward on the map; Cartesian y increases up.
    Treating ACC z as Cartesian y reflects every corner's handedness.
    """
    if coordinate_system not in {"cartesian_xy", "acc_xz"}:
        raise ValueError("Unsupported track coordinate system")
    if not isinstance(rows, (list, tuple)) or not 4 <= len(rows) <= 20000:
        raise ValueError("A track needs between 4 and 20000 centerline points")
    cleaned = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) not in {2, 4}:
            raise ValueError("Track rows require x/y and optional right/left widths")
        values = [_finite(value, "Track coordinate") for value in row]
        if any(abs(value) > 100000 for value in values[:2]):
            raise ValueError("Metric track coordinates must use a local origin")
        if len(values) == 4 and any(not 0 < width <= 50 for width in values[2:]):
            raise ValueError("Source half-widths must be positive and no more than 50 metres")
        if not cleaned or values[:2] != cleaned[-1][:2]:
            cleaned.append(values)
    if len(cleaned) < 4:
        raise ValueError("A track needs at least four distinct centerline points")
    open_length = sum(math.dist(a[:2], b[:2]) for a, b in zip(cleaned, cleaned[1:]))
    closure = math.dist(cleaned[-1][:2], cleaned[0][:2])
    if closure > max(20.0, open_length * 0.02):
        raise ValueError("Track centerline is not a closed circuit")
    if closure > 0:
        cleaned.append(cleaned[0][:])
    measured = open_length + closure
    if not 300 <= measured <= 30000:
        raise ValueError("Track centerline length must be between 300 m and 30 km")
    nominal = measured if length_m is None else _finite(length_m, "Nominal track length")
    if nominal <= 0 or not 0.8 <= nominal / measured <= 1.2:
        raise ValueError("Nominal length differs too far from the supplied geometry")
    x_values, y_values = [row[0] for row in cleaned], [row[1] for row in cleaned]
    span_x, span_y = max(x_values) - min(x_values), max(y_values) - min(y_values)
    if min(span_x, span_y) < 20:
        raise ValueError("Track centerline has no usable two-dimensional extent")
    scale = max(span_x, span_y) / 0.92
    center_x, center_y = (min(x_values) + max(x_values)) / 2, (min(y_values) + max(y_values)) / 2
    points, cumulative = [], 0.0
    screen_y_sign = 1 if coordinate_system == "acc_xz" else -1
    for index, row in enumerate(cleaned):
        if index:
            cumulative += math.dist(cleaned[index - 1][:2], row[:2])
        point = {"x": 0.5 + (row[0] - center_x) / scale,
                 "y": 0.5 + screen_y_sign * (row[1] - center_y) / scale,
                 "distance_m": cumulative / measured * nominal}
        if len(row) == 4:
            point.update(source_width_right_m=row[2], source_width_left_m=row[3])
        points.append(point)
    points[-1]["distance_m"] = nominal
    return {"points": points, "length_m": nominal, "geometry_length_m": measured,
            "normalization": {"metres_per_unit": scale, "y_axis": "down", "aspect_preserved": True,
                              "source_coordinate_system": coordinate_system,
                              "padding_fraction": 0.04}, "closed": True}


def _source_json(item: dict, prefix: str) -> dict:
    raw = (ASSETS / item[f"{prefix}_file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != item[f"{prefix}_sha256"]:
        raise ValueError("Track source checksum does not match the bundled dataset")
    return json.loads(raw)


def _fraction(value) -> float | None:
    try:
        number = _finite(value, "Annotation progress")
    except ValueError:
        return None
    return number if 0 <= number <= 1 else None


def _interval_midpoint(start: float, end: float) -> float:
    return (start + (end - start) % 1 / 2) % 1


def _annotations(track_id: str, data: dict) -> dict:
    """Keep source positions and flag approximate repairs instead of inventing apexes."""
    supplements_file = ASSETS / "corner-numbering.json"
    supplements = json.loads(supplements_file.read_text(encoding="utf-8")).get(track_id, {}) if supplements_file.exists() else {}
    overrides = supplements.get("turns", [])
    corners, warnings, seen = [], [], set()
    for index, turn in enumerate(data.get("turn", [])):
        override = overrides[index] if index < len(overrides) else {}
        if override is None or override.get("omit"):
            continue
        name = override.get("name", turn.get("name", ""))
        number = override.get("number", turn.get("number"))
        if "number" not in override and number is None and re.fullmatch(r"T?\d+", name):
            number = int(name.lstrip("T"))
        marker = _fraction(override.get("progress", turn.get("marker")))
        start, end = _fraction(turn.get("start")), _fraction(turn.get("end"))
        kind = override.get("position_kind", "source_normalized_lap_estimate")
        if start is not None and end is not None and start != end:
            within = marker is not None and (start <= marker <= end if start < end else marker >= start or marker <= end)
            if not within and "progress" not in override:
                marker = _interval_midpoint(start, end)
                kind = "source_interval_midpoint_estimate"
                warnings.append(f"Turn row {index}: source apex absent or outside its interval; using interval midpoint.")
        if marker is None:
            warnings.append(f"Turn row {index}: no sourced position; annotation omitted.")
            continue
        key = (name, marker)
        if key in seen:
            warnings.append(f"Turn row {index}: duplicate source annotation omitted.")
            continue
        seen.add(key)
        corner = {"number": number, "name": name or f"Turn {number}", "progress": marker % 1,
                  "source_index": index, "position_kind": kind}
        if start is not None and end is not None:
            corner.update(start=start, end=end)
        if override:
            corner["source_urls"] = override.get("source_urls", supplements.get("source_urls", []))
        corners.append(corner)
    for extra in supplements.get("extra_corners", []):
        marker = _fraction(extra.get("progress"))
        if marker is None or not extra.get("name") or extra.get("number") is None:
            raise ValueError("Supplemental corner requires a name, number and normalized progress")
        corners.append({**extra, "progress": marker % 1,
                        "position_kind": extra.get("position_kind", "named_interval_midpoint_estimate"),
                        "source_urls": extra.get("source_urls", supplements.get("source_urls", []))})
    corners.sort(key=lambda corner: corner["progress"])
    straights = []
    for index, straight in enumerate(data.get("straight", [])):
        start, end = _fraction(straight.get("start")), _fraction(straight.get("end"))
        if start is None or end is None or start == end:
            warnings.append(f"Straight row {index}: no usable source interval; annotation omitted.")
            continue
        # The source's Brands Hatch Brabham [0,.6] typo spans multiple corners.
        if (end - start) % 1 > 0.4:
            warnings.append(f"Straight row {index}: implausibly long source interval; annotation omitted.")
            continue
        straights.append({"name": straight["name"], "progress": _interval_midpoint(start, end),
                          "start": start, "end": end, "position_kind": "source_interval_midpoint_estimate"})
    for straight in supplements.get("straights", []):
        marker = _fraction(straight.get("progress"))
        if marker is None:
            raise ValueError("Supplemental straight requires normalized progress")
        straights.append({**straight, "progress": marker % 1,
                          "position_kind": straight.get("position_kind", "named_interval_midpoint_estimate")})
    timing = [{"kind": "start_finish", "label": "Start / finish", "progress": 0.0,
               "sector": 3, "position_kind": "acc_source_lap_origin_estimate"}]
    for sector in data.get("sector", []):
        marker = _fraction(sector.get("marker"))
        if marker is None or marker in {0, 1}:
            continue
        timing.append({"kind": "sector", "label": f"Sector {sector['name']}", "sector": int(sector["name"]),
                       "progress": marker, "position_kind": "source_normalized_lap_estimate"})
    source_urls = set(supplements.get("source_urls", []))
    for annotation in corners + straights:
        source_urls.update(annotation.get("source_urls", []))
    names_index = ASSETS / "name-source-index.json"
    name_sources = json.loads(names_index.read_text(encoding="utf-8")) if names_index.exists() else {}
    supplementary_sources = []
    for url in sorted(source_urls):
        if url in name_sources:
            source = name_sources[url]
            if hashlib.sha256((ASSETS / source["local_file"]).read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError("Track name source checksum does not match the bundled dataset")
            supplementary_sources.append({**source, "file_url": url})
    return {"corners": corners, "straights": straights, "timing_lines": timing,
            "annotation_warnings": warnings, "annotation_positions_kind": "approximate_normalized_lap_projection",
            "corner_numbering_note": supplements.get("numbering_note", "Source corner numbering where available"),
            "annotation_source_urls": sorted(source_urls), "supplementary_annotation_sources": supplementary_sources}


@lru_cache(maxsize=len(_CATALOG))
def _load_track(track_id: str) -> dict:
    if track_id not in _CATALOG:
        raise KeyError(track_id)
    item = _CATALOG[track_id]
    data = _source_json(item, "geometry")
    left, right = data["leftEdge"], data["rightEdge"]
    if data.get("source") != "acc-extracted" or data.get("coordSystem") != "acc":
        raise ValueError("Track geometry must be an ACC-specific extraction")
    if len(left) != len(right) or len(left) != data.get("nodeCount"):
        raise ValueError("Track boundary point counts do not match")
    rows = [[(_finite(a[axis], "Boundary coordinate") + _finite(b[axis], "Boundary coordinate")) / 2
             for axis in ("x", "z")] for a, b in zip(left, right)]
    track = validate_geometry(rows, item["length_m"], coordinate_system="acc_xz")
    track.update(_annotations(track_id, _source_json(item, "annotations")))
    track.update(id=track_id, name=item["name"], layout=item["layout"],
                 start_finish_verified=False, origin_kind="acc_source_start_finish_estimate",
                 requires_origin_confirmation=True, direction="source_order",
                 geometry_kind="approximate_acc_boundary_midpoint", racing_line_available=False,
                 widths_kind="not_exposed", distance_kind="nominal_length_parameterization",
                 length_source=item["length_source"],
                 source={"name": "RaceIQ ACC track geometry", "url": REPOSITORY,
                         "file_url": f"{REPOSITORY}/blob/{COMMIT}/{item['geometry_source_path']}",
                         "local_file": item["geometry_file"],
                         "commit": COMMIT, "sha256": item["geometry_sha256"], "license": "AGPL-3.0",
                         "license_url": f"{REPOSITORY}/blob/{COMMIT}/LICENSE",
                         "attribution": "SpeedHQ / RaceIQ contributors; ACC geometry extracted from game fastlane data",
                         "transformation": "Midpoint of paired source boundaries; close loop; raw ACC x to screen right, z to screen down; preserve aspect ratio"},
                 annotation_source={"name": "Lovely Track Data", "url": ANNOTATIONS_REPOSITORY,
                         "file_url": f"{ANNOTATIONS_REPOSITORY}/blob/{ANNOTATIONS_COMMIT}/{item['annotations_source_path']}",
                         "local_file": item["annotations_file"],
                         "commit": ANNOTATIONS_COMMIT, "sha256": item["annotations_sha256"],
                         "license": "CC-BY-NC-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                         "attribution": "Lovely Sim Racing; corner/straight name sources credited to Racing Circuits by upstream",
                         "transformation": "ACC normalized lap markers projected onto map; interval midpoints used where noted"})
    track["origin"] = dict(track["points"][0])
    return track


def get_track(track_id: str) -> dict:
    """Return an independent, JSON-compatible track geometry and provenance record."""
    if not isinstance(track_id, str):
        raise ValueError("Track id must be a string")
    return copy.deepcopy(_load_track(track_id))


def list_tracks() -> list[dict]:
    return [{key: copy.deepcopy(value) for key, value in _load_track(track_id).items()
             if key not in {"points", "normalization"}} for track_id in _CATALOG]


@lru_cache(maxsize=len(_CATALOG))
def _distance_index(track_id: str) -> tuple[float, ...]:
    return tuple(point["distance_m"] for point in _load_track(track_id)["points"])


def point_at_distance(track_id: str, distance_m: float, origin_m: float = 0.0, reverse: bool = False) -> dict:
    """Locate an estimated distance on the centerline relative to a confirmed origin."""
    if not isinstance(reverse, bool):
        raise ValueError("reverse must be a boolean")
    track = _load_track(track_id)
    distance = _finite(distance_m, "Distance")
    origin = _finite(origin_m, "Origin")
    position = (origin + (-distance if reverse else distance)) % track["length_m"]
    points = track["points"]
    distances = _distance_index(track_id)
    right = min(len(points) - 1, bisect_right(distances, position))
    before, after = points[right - 1], points[right]
    fraction = (position - before["distance_m"]) / (after["distance_m"] - before["distance_m"])
    result = {key: before[key] + (after[key] - before[key]) * fraction for key in ("x", "y")}
    dx, dy = after["x"] - before["x"], after["y"] - before["y"]
    magnitude = math.hypot(dx, dy)
    direction = -1 if reverse else 1
    result.update(distance_m=position, progress=position / track["length_m"],
                  tangent_x=direction * dx / magnitude, tangent_y=direction * dy / magnitude,
                  kind="estimated_centerline_progress")
    return result


def point_at_progress(track_id: str, progress: float, origin_m: float = 0.0, reverse: bool = False) -> dict:
    track = _load_track(track_id)
    return point_at_distance(track_id, _finite(progress, "Progress") * track["length_m"], origin_m, reverse)


def resample_closed_path(track_id: str, count: int = 256) -> list[dict]:
    """Return count equal-distance segments and their repeated closing vertex."""
    if isinstance(count, bool) or not isinstance(count, int) or not 16 <= count <= 4096:
        raise ValueError("Track resampling count must be between 16 and 4096")
    length = _load_track(track_id)["length_m"]
    points = [point_at_progress(track_id, index / count) for index in range(count)]
    points.append({**points[0], "distance_m": length, "progress": 1.0})
    return points


def nearest_progress(track_id: str, x: float, y: float) -> dict:
    """Project a normalized map click, not a camera or vehicle observation."""
    x, y = _finite(x, "Map x"), _finite(y, "Map y")
    if not 0 <= x <= 1 or not 0 <= y <= 1:
        raise ValueError("Map selection coordinates must be normalized between 0 and 1")
    track = _load_track(track_id)
    candidates = []
    for index, (before, after) in enumerate(zip(track["points"], track["points"][1:])):
        dx, dy = after["x"] - before["x"], after["y"] - before["y"]
        fraction = max(0.0, min(1.0, ((x - before["x"]) * dx + (y - before["y"]) * dy) / (dx * dx + dy * dy)))
        px, py = before["x"] + fraction * dx, before["y"] + fraction * dy
        candidates.append({"x": px, "y": py, "distance_m": before["distance_m"] + fraction * (after["distance_m"] - before["distance_m"]),
                           "distance_to_centerline_m": math.hypot(x - px, y - py) * track["normalization"]["metres_per_unit"],
                           "segment_index": index})
    best = min(candidates, key=lambda point: point["distance_to_centerline_m"])
    length = track["length_m"]
    best["distance_m"] %= length
    best["progress"] = best["distance_m"] / length
    best["kind"] = "map_selection"
    best["ambiguous"] = any(abs(candidate["distance_to_centerline_m"] - best["distance_to_centerline_m"]) < 3
                            and 50 < abs(candidate["distance_m"] - best["distance_m"]) < length - 50
                            for candidate in candidates)
    return best
