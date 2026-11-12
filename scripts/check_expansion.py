"""Evaluate independent GT3 frame labels and summarize current corpus evidence.

Missing and wrong readings are counted separately; skipped downloads never pass.
Run after validate_corpus.py to regenerate artifacts/expansion-evaluation.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import av

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_hud_corpus import read_frame
from telemetry.validation import coverage_report, extractor_fingerprint, read_json, video_result, write_json
from telemetry.vision import HUDExtractor


def markdown_report(result):
    totals, coverage = result["full_video_totals"], result["coverage"]
    lines = ["# ACC GT3 expansion: footage evidence", "",
        f"Fully analyzed **{totals['videos']} public YouTube recordings**, "
        f"**{totals['source_minutes']} minutes**, and **{totals['frames']:,} sampled frames**.", "",
        f"The catalog supports 31 GT3 variants and 25 circuits. Current machine tests span "
        f"{coverage['tested_cars']} car variants and {coverage['tested_circuits']} circuits by "
        "unverified video-title hints. This is not independent identity or full-lap accuracy certification.", "",
        f"The separate 20-frame transcription check has **{result['exact_frames']} exact matches**, "
        f"**{result['missing_readings']} missing readings**, **{result['wrong_readings']} wrong readings**, "
        f"and **{result['missing_videos']} missing source files**.", "",
        "See [VALIDATION.md](../VALIDATION.md) for all test scopes, independent pedal/steering checks, "
        "performance measurements, and remaining limitations.", "",
        "## Footage not yet tested", "",
        ("Saved download attempts include YouTube sign-in rejections; those missing clips were not tested. "
         "Existing cached videos remained usable. No accounts or cookies were used."
         if any("sign in" in error.get("error", "").lower() for error in result["download_errors"])
         else "This list reflects the current saved corpus results, not a claim that footage is unavailable."), "",
        "Cars: " + "; ".join(result["untested_cars"]) + ".", "",
        "Circuits: " + "; ".join(result["untested_circuits"]) + ".", "",
        "## Speed observation changes", "",
        "Percentages use the entire video or the same sparse sampling schedule, including "
        "menus and replays. These measure availability, not correctness. Only changed cases appear.", "",
        "| Source | Mode | Before | After |", "| --- | --- | ---: | ---: |"]
    for row in result["before_after"]:
        before, after = row["before_coverage"]["speed"], row["after_coverage"]["speed"]
        if before != after:
            identity = row["video_id"]
            lines.append(f"| [{identity}](https://www.youtube.com/watch?v={identity}) | {row['mode']} | {before}% | {after}% |")
    lines += ["", "## Complete video analyses", "",
        "Speed availability below uses frames with a detected HUD. A dash means no supported HUD "
        "was observed. Gear/pedal/steering details and immutable evidence paths are in the JSON report. "
        "Car/circuit names remain discovery hints.", "",
        "| Source | Car hint | Circuit hint | Minutes | Speed / visible HUD |", "| --- | --- | --- | ---: | ---: |"]
    for row in sorted(result["full_videos"], key=lambda row: (row.get("car_id") or "", row.get("track_id") or "")):
        speed = (row.get("visible_hud_coverage") or {}).get("speed")
        value = "—" if speed is None else f"{speed:.2f}%"
        lines.append(f"| [{row['video_id']}]({row['url']}) | {(row.get('car_id') or 'unknown').replace('_', ' ')} | "
                     f"{(row.get('track_id') or 'unknown').replace('_', ' ')} | {row['source_seconds'] / 60:.2f} | {value} |")
    return "\n".join(lines) + "\n"


def evaluate(output):
    labels = read_json(ROOT / "tests/fixtures/expansion_hud_labels.json")
    rows = []
    for label in labels["frames"]:
        row = {key: label[key] for key in ("video_id", "car_id", "track_id", "url", "time")}
        path = ROOT / label["path"]
        if not path.is_file():
            rows.append({**row, "status": "video_missing"})
            continue
        with av.open(str(path)) as container:
            stamp, frame = read_frame(container, container.streams.video[0], label["time"])
        sample = HUDExtractor().extract(frame, stamp)
        expected = {key: label[key] for key in ("speed", "gear", "hud_speed_unit")}
        observed = {key: sample[key] for key in expected}
        missing = [key for key in expected if observed[key] is None]
        wrong = [key for key in expected if observed[key] is not None and observed[key] != expected[key]]
        rows.append({**row, "status": "wrong" if wrong else "missing" if missing else "exact",
                     "decoded_time": stamp, "expected": expected, "observed": observed,
                     "missing_channels": missing, "wrong_channels": wrong})
    folder = ROOT / "data/validation"
    coverage = coverage_report(folder)
    fingerprint = coverage["extractor_fingerprint"]
    full_videos, download_errors = [], []
    for video in read_json(folder / "manifest.json", {"videos": {}})["videos"].values():
        latest = video_result(folder, video["id"], fingerprint)
        if latest.get("status") == "error":
            download_errors.append({"video_id": video["id"], "error": latest.get("error", ""),
                                    "attempted_at": latest.get("completed_at")})
        full = video_result(folder, video["id"], fingerprint, "full")
        if full.get("status") == "completed" and full.get("fingerprint") == fingerprint:
            full_videos.append({"video_id": video["id"], "url": video["url"],
                                "car_id": video.get("car_id"), "track_id": video.get("track_id"),
                                "identity_basis": video.get("identity_basis"),
                                **{key: full.get(key) for key in ("frames", "source_seconds", "diagnostic",
                                   "coverage", "visible_hud_coverage", "trace_path", "evidence_path")}})
    checked_pairs = [pair for pair in coverage["pairs"] if pair["smoke_checked"] or pair["analyzed"]]
    tested_cars = {pair["car_id"] for pair in checked_pairs}
    tested_tracks = {pair["track_id"] for pair in checked_pairs}
    comparisons = []
    for path in sorted((ROOT / "artifacts/expansion-baseline").glob("*.json")):
        before = read_json(path)
        after = video_result(folder, path.stem, coverage["extractor_fingerprint"], before.get("mode"), exact_mode=True)
        if not (before.get("coverage") and after.get("coverage") and
                before.get("mode") == after.get("mode") and after.get("fingerprint") == coverage["extractor_fingerprint"]):
            continue
        comparisons.append({"video_id": path.stem, "mode": after["mode"],
                            "before_coverage": before["coverage"], "after_coverage": after["coverage"],
                            "visible_hud_coverage": after.get("visible_hud_coverage"),
                            "diagnostic": after.get("diagnostic"),
                            "realtime_factor": after.get("realtime_factor")})
    result = {"scope": labels["label_method"], "fingerprint": extractor_fingerprint(ROOT),
              "labeled_frames": len(rows), "exact_frames": sum(row["status"] == "exact" for row in rows),
              "missing_readings": sum(row["status"] == "missing" for row in rows),
              "wrong_readings": sum(row["status"] == "wrong" for row in rows),
              "missing_videos": sum(row["status"] == "video_missing" for row in rows),
              "frames": rows, "before_after": comparisons,
              "full_video_totals": {"videos": len(full_videos),
                  "frames": sum(video["frames"] for video in full_videos),
                  "source_minutes": round(sum(video["source_seconds"] for video in full_videos) / 60, 2)},
              "full_videos": full_videos, "download_errors": download_errors,
              "untested_cars": sorted({pair["car_name"] for pair in coverage["pairs"] if pair["car_id"] not in tested_cars}),
              "untested_circuits": sorted({pair["track_name"] for pair in coverage["pairs"] if pair["track_id"] not in tested_tracks}),
              "coverage": {key: value for key, value in coverage.items() if key not in {"pairs", "searches"}}}
    write_json(output, result)
    output.with_suffix(".md").write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("labeled_frames", "exact_frames", "missing_readings", "wrong_readings", "missing_videos")}, indent=2))
    print("Report:", output)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/expansion-evaluation.json")
    args = parser.parse_args()
    report = evaluate(args.output)
    # Coverage misses are visible in the report, not quietly presented as pass.
    raise SystemExit(1 if report["wrong_readings"] or report["missing_videos"] or report["missing_readings"] else 0)
