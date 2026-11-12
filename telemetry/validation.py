"""Auditable video corpus; discovery and self-consistency are not ground truth."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

from .catalog import catalog, list_cars, list_circuits

CHANNELS = ("speed", "gear", "brake", "throttle", "steering")
ROOT = Path(__file__).resolve().parents[1]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def video_result(folder, identity, fingerprint=None, mode=None, exact_mode=False):
    """Prefer current full evidence; a later smoke run must not erase a full run."""
    directory = Path(folder) / "results"
    reports = [read_json(directory / (identity + ".json"), {})]
    reports.extend(read_json(directory / identity / (kind + ".json"), {}) for kind in ("smoke", "full"))
    reports.extend(read_json(directory / identity / (kind + ".success.json"), {}) for kind in ("smoke", "full"))
    reports = [report for report in reports if report and
               (not (exact_mode or mode == "full") or report.get("mode") == mode)]
    selected = max(reports, default={}, key=lambda report: (
        report.get("fingerprint") == fingerprint,
        report.get("status") == "completed",
        report.get("mode") == "full",
        report.get("completed_at") or ""))
    latest = read_json(directory / identity / (str(selected.get("mode")) + ".json"), {})
    if selected.get("status") == "completed" and latest.get("status") == "error" and latest.get("fingerprint") == selected.get("fingerprint"):
        selected = {**selected, "last_attempt_error": latest.get("error"), "last_attempt_at": latest.get("completed_at")}
    return selected


def save_video_result(folder, identity, result):
    """Retain each analysis mode and migrate an existing report before replacing it."""
    directory = Path(folder) / "results"
    previous = read_json(directory / (identity + ".json"), {})
    if previous.get("mode") in ("smoke", "full"):
        retained = directory / identity / (previous["mode"] + ".json")
        if not retained.exists():
            write_json(retained, previous)
        successful = directory / identity / (previous["mode"] + ".success.json")
        if previous.get("status") == "completed" and not successful.exists():
            write_json(successful, previous)
    write_json(directory / identity / (result["mode"] + ".json"), result)
    if result.get("status") == "completed":
        write_json(directory / identity / (result["mode"] + ".success.json"), result)
    write_json(directory / (identity + ".json"), video_result(folder, identity, result.get("fingerprint")))


def _words(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def title_candidates(title):
    """Conservative title hints for discovery only, never recording identity."""
    text = _words(title)
    if not re.search(r"\b(acc|assetto corsa competizione)\b", text) or re.search(r"\b(gt2|gt4|iracing|lmu|super trofeo|cup)\b", text):
        return {"car_id": None, "track_id": None}

    def match(items, kind):
        matches = []
        for item in items:
            aliases = [item["name"], *item["aliases"], item["id"].replace("_", " ")]
            spans = {found.span() for alias in aliases
                     for found in re.finditer(r"\b" + re.escape(_words(alias)) + r"\b", text)}
            if spans:
                matches.append((max(end - start for start, end in spans), item["id"], spans))
        matches.sort(key=lambda item: item[0], reverse=True)
        if not matches or len(matches) > 1 and matches[0][0] == matches[1][0]:
            return None
        identity = matches[0][1]
        # A base name inside one Evo name is not a second car; a separate car
        # or circuit elsewhere in a comparison title is genuinely ambiguous.
        winner_spans = matches[0][2]
        if any(not any(left <= start and end <= right for left, right in winner_spans)
               for _, _, spans in matches[1:] for start, end in spans):
            return None
        # Do not silently turn an unrecognized Evo suffix into the original car.
        if kind == "car" and " evo" in text and "evo" not in identity:
            return None
        if kind == "track" and identity == "nurburgring" and re.search(r"\b(nordschleife|24h|24 h)\b", text):
            return None
        return identity

    return {"car_id": match(list_cars(), "car"), "track_id": match(list_circuits(), "track")}


def index_entries(folder, document, source):
    folder = Path(folder)
    manifest = read_json(folder / "manifest.json", {"schema": 1, "videos": {}, "searches": []})
    added = 0
    for entry in document.get("entries") or []:
        identity = entry.get("id", "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", identity):
            continue
        title = entry.get("title") or ""
        hints = title_candidates(title)
        if not hints["car_id"] or not hints["track_id"]:
            continue
        if identity not in manifest["videos"]:
            added += 1
            manifest["videos"][identity] = {
                "id": identity, "url": "https://www.youtube.com/watch?v=" + identity,
                "title": title[:500], "duration": entry.get("duration"),
                **hints, "identity_basis": "unverified_title", "discovered_at": utc_now(),
                "discovery_source": source, "uploader": document.get("uploader") or document.get("channel") or entry.get("uploader"),
            }
    manifest["searches"].append({"source": source, "time": utc_now(), "entries": len(document.get("entries") or []), "added": added})
    write_json(folder / "manifest.json", manifest)
    return added


def reindex_title_hints(folder):
    """Refresh only inferred identities; retain every clip, review and result."""
    folder = Path(folder)
    path = folder / "manifest.json"
    manifest = read_json(path, {"schema": 1, "videos": {}, "searches": []})
    changed, unassigned = 0, 0
    for video in manifest["videos"].values():
        if video.get("identity_basis") != "unverified_title":
            continue
        hints = title_candidates(video.get("title") or "")
        if any(video.get(key) != value for key, value in hints.items()):
            video.update(hints, title_hints_updated_at=utc_now())
            changed += 1
        unassigned += not hints["car_id"] or not hints["track_id"]
    if changed:
        write_json(path, manifest)
    return {"changed": changed, "unassigned": unassigned, "videos": len(manifest["videos"])}


def extractor_fingerprint(root):
    root = Path(root)
    digest = hashlib.sha256()
    paths = [root / "telemetry" / name for name in ("vision.py", "profiles.py", "calibration.py", "steering.py", "analysis.py")]
    paths.append(root / "scripts" / "validate_corpus.py")
    paths.extend(sorted((root / "assets/templates").rglob("*.png")))
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _valid_fingerprint(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _review_channels(review, report):
    fingerprint = report.get("fingerprint")
    reviewer = review.get("reviewer")
    evidence = review.get("evidence")
    channels = review.get("channels")
    if not (report.get("status") == "completed" and report.get("mode") == "full"
            and review.get("result") == "pass"
            and review.get("identity_confirmed") is True
            and review.get("lap_boundaries_confirmed") is True
            and _valid_fingerprint(fingerprint) and review.get("fingerprint") == fingerprint
            and isinstance(reviewer, str) and reviewer.strip()
            and isinstance(evidence, (str, list, dict)) and evidence
            and isinstance(channels, dict)):
        return []
    if any(channels.get(channel) not in ("pass", "not_visible") for channel in CHANNELS):
        return []
    return [channel for channel in CHANNELS if channels[channel] == "pass"]


def coverage_report(folder, target=3, root=None):
    """No automatic path upgrades a self-reported confidence to a verified lap."""
    if isinstance(target, bool) or not isinstance(target, int) or target < 1:
        raise ValueError("Review target must be a positive integer")
    folder = Path(folder)
    fingerprint = extractor_fingerprint(ROOT if root is None else root)
    manifest = read_json(folder / "manifest.json", {"videos": {}, "searches": []})
    pairs = {}
    for car in list_cars():
        for track in list_circuits():
            pairs[(car["id"], track["id"])] = {
                "car_id": car["id"], "car_name": car["name"], "track_id": track["id"], "track_name": track["name"],
                "candidates": 0, "smoke_checked": 0, "analyzed": 0, "reviewed": 0,
                "partial_reviews": 0, "reviewed_channels": dict.fromkeys(CHANNELS, 0),
                "stale_results": 0, "historical_reviewed": 0,
                "errors": 0, "videos": [], "state": "not_tested",
            }
    totals = Counter()
    unassigned = 0
    for video in manifest["videos"].values():
        pair = pairs.get((video.get("car_id"), video.get("track_id")))
        if pair is None:
            unassigned += 1
            continue
        report = video_result(folder, video["id"], fingerprint)
        state = "candidate"
        current = report.get("fingerprint") == fingerprint
        if report.get("status") == "error":
            state = "error"
            pair["errors"] += 1
        elif report.get("status") == "completed":
            if current:
                state = "analyzed" if report.get("mode") == "full" else "smoke_checked"
                pair[state] += 1
            else:
                state = "stale_analysis"
                pair["stale_results"] += 1
        # An explicit independent review is kept separate from machine extraction.
        review = read_json(folder / "reviews" / (video["id"] + ".json"), {})
        reviewed_channels = _review_channels(review, report)
        if state == "analyzed" and reviewed_channels:
            for channel in reviewed_channels:
                pair["reviewed_channels"][channel] += 1
            if len(reviewed_channels) == len(CHANNELS):
                pair["reviewed"] += 1
                state = "reviewed"
            else:
                pair["partial_reviews"] += 1
                state = "channel_review_only"
        elif not current and len(reviewed_channels) == len(CHANNELS):
            pair["historical_reviewed"] += 1
        pair["candidates"] += 1
        totals[state] += 1
        pair["videos"].append({**video, "state": state, "hud_visible_pct": report.get("hud_visible_pct"),
                               "coverage": report.get("coverage"), "error": report.get("error") or report.get("last_attempt_error"),
                               "fingerprint": report.get("fingerprint"), "fingerprint_current": current,
                               "reviewed_channels": reviewed_channels if current else []})
    for pair in pairs.values():
        pair["state"] = ("review_target_met" if pair["reviewed"] >= target else "partially_reviewed" if pair["reviewed"]
                         else "channel_review_only" if pair["partial_reviews"]
                         else "analysis_needs_review" if pair["analyzed"] else "smoke_only" if pair["smoke_checked"]
                         else "stale_analysis" if pair["stale_results"] else "download_error" if pair["errors"]
                         else "discovered" if pair["candidates"] else "not_tested")
    checked = [pair for pair in pairs.values() if pair["smoke_checked"] or pair["analyzed"]]
    usable = [pair for pair in checked if any(video["fingerprint_current"] and
              all((video.get("coverage") or {}).get(channel, 0) > 0 for channel in ("speed", "gear", "brake", "throttle"))
              for video in pair["videos"])]
    return {"schema": 1, "generated_at": utc_now(), "catalog_counts": catalog()["counts"],
            "extractor_fingerprint": fingerprint,
            "total_indexed_videos": len(manifest["videos"]), "unassigned_candidates": unassigned,
            "target_per_pair": target, "total_candidates": sum(p["candidates"] for p in pairs.values()),
            "pairs_with_candidates": sum(p["candidates"] > 0 for p in pairs.values()),
            "pairs_review_target_met": sum(p["reviewed"] >= target for p in pairs.values()),
            "stale_results": sum(p["stale_results"] for p in pairs.values()),
            "historical_reviewed": sum(p["historical_reviewed"] for p in pairs.values()),
            "tested_cars": len({p["car_id"] for p in checked}),
            "tested_circuits": len({p["track_id"] for p in checked}),
            "pairs_with_observed_core_channels": len(usable),
            "cars_with_observed_core_channels": len({p["car_id"] for p in usable}),
            "circuits_with_observed_core_channels": len({p["track_id"] for p in usable}),
            "video_states": dict(totals), "pairs": list(pairs.values()), "searches": manifest["searches"],
            "scope": "Title hints are not verified identities. Smoke checks and complete-video extraction are not accuracy validation. Reviewed laps require independent channel and lap-boundary evidence; cross-video distance registration and delta holdouts require separate validation. No cell certifies all cameras, HUDs, or conditions."}


def choose_videos(folder, mode, limit, retry=False, root=None):
    """Cover new cars/circuits first, then balance the least-tested pairs."""
    folder = Path(folder)
    fingerprint = extractor_fingerprint(ROOT if root is None else root)
    videos = list(read_json(folder / "manifest.json", {"videos": {}})["videos"].values())
    car_ids = {car["id"] for car in list_cars()}
    track_ids = {track["id"] for track in list_circuits()}
    candidates, cars, tracks, pairs = [], Counter(), Counter(), Counter()
    for video in videos:
        if video.get("car_id") not in car_ids or video.get("track_id") not in track_ids:
            continue
        report = video_result(folder, video["id"], fingerprint, mode)
        complete = (report.get("status") == "completed" and report.get("fingerprint") == fingerprint
                    and (mode == "smoke" or report.get("mode") == "full"))
        if complete:
            cars[video["car_id"]] += 1
            tracks[video["track_id"]] += 1
            pairs[(video["car_id"], video["track_id"])] += 1
        elif report.get("status") != "error" or retry or report.get("fingerprint") != fingerprint:
            candidates.append(video)
    chosen = []
    while candidates and len(chosen) < limit:
        video = min(candidates, key=lambda v: (int(cars[v["car_id"]] > 0) + int(tracks[v["track_id"]] > 0),
                    pairs[(v["car_id"], v["track_id"])], cars[v["car_id"]] + tracks[v["track_id"]],
                    v.get("duration") or 600))
        candidates.remove(video)
        chosen.append(video)
        cars[video["car_id"]] += 1
        tracks[video["track_id"]] += 1
        pairs[(video["car_id"], video["track_id"])] += 1
    return chosen
