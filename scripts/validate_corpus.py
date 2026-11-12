"""Discover, sample and fully analyze a resumable public ACC video corpus.

Never modifies the personal session database. See --help for bounded commands.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import av
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from telemetry.analysis import SessionAnalyzer
from telemetry.validation import (CHANNELS, choose_videos, coverage_report, extractor_fingerprint,
                                  index_entries, read_json, save_video_result, utc_now, write_json)
from telemetry.vision import HUDExtractor


def youtube_command():
    result = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--socket-timeout", "15",
              "--retries", "1", "--fragment-retries", "1", "--no-warnings"]
    for runtime in ("deno", "node"):
        if shutil.which(runtime):
            result += ["--js-runtimes", runtime]
            break
    return result


def evict_cache(folder, max_bytes, reserve=0):
    """Only evict this tool's generated media; keep provenance and results."""
    files = sorted((folder / "cache").glob("*/media.*"), key=lambda p: p.stat().st_mtime)
    files = [p for p in files if p.suffix in {".mp4", ".webm", ".mkv", ".part", ".ytdl"}]
    total = sum(p.stat().st_size for p in files)
    for path in files:
        if total + reserve <= max_bytes:
            break
        total -= path.stat().st_size
        path.unlink()
    if total + reserve > max_bytes:
        raise ValueError("Cache budget too small for a 1 GB video reservation")


def cached_video(folder, video):
    directory = folder / "cache" / video["id"]
    return next((directory / ("media" + extension) for extension in (".mp4", ".mkv", ".webm")
                 if (directory / ("media" + extension)).is_file()), None)


def download(folder, video, cache_gb):
    directory = folder / "cache" / video["id"]
    directory.mkdir(parents=True, exist_ok=True)
    existing = cached_video(folder, video)
    if existing is not None:
        existing.touch()
        return existing
    evict_cache(folder, int(cache_gb * 1024**3), reserve=1024**3)
    if shutil.disk_usage(folder).free < 5 * 1024**3:
        raise ValueError("Less than 5 GB free disk space; download stopped")
    command = youtube_command() + ["--no-playlist", "--no-progress", "--write-info-json",
               "--match-filter", "!is_live & duration <= 1800", "--max-filesize", str(1024**3),
               "-f", "bv[height<=1080][ext=mp4]/bv[height<=1080]/b[height<=1080]",
               "-o", str(directory / "media.%(ext)s"), video["url"]]
    with (directory / "download.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        start = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - start > 240:
                    raise ValueError("Download exceeded four-minute budget")
                if sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) > 1.1 * 1024**3:
                    raise ValueError("Download exceeded 1 GB budget")
                time.sleep(.2)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    if process.returncode:
        tail = (directory / "download.log").read_text(errors="replace")[-1200:]
        raise ValueError("YouTube download failed: " + tail)
    files = [p for p in directory.glob("media.*") if p.suffix in {".mp4", ".mkv", ".webm"}]
    if len(files) != 1:
        raise ValueError("No single downloadable public video found")
    return files[0]


def smoke_frames(path, count):
    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        duration = capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps else 0
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Cannot determine video duration")
        # Include the middle/tail: an absent HUD/setup screen is not a driving failure.
        for stamp in np.linspace(min(5, duration / 10), max(0, duration - 2), count):
            capture.set(cv2.CAP_PROP_POS_MSEC, stamp * 1000)
            ok, frame = capture.read()
            if ok:
                yield float(stamp), frame
    finally:
        capture.release()


def full_frames(path):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        stream.codec_context.thread_count = 4
        origin, last = None, None
        for frame in container.decode(stream):
            if frame.time is None:
                raise ValueError("Missing source presentation timestamp")
            if origin is None:
                origin = float(frame.time)
            stamp = float(frame.time) - origin
            if last is not None and stamp - last < 1 / 30 - 1e-4:
                continue
            last = stamp
            yield stamp, frame.to_ndarray(format="bgr24")


def analyze(folder, video, path, mode, frame_count):
    """Write immutable run evidence, including diagnostics for failed attempts."""
    run_id = uuid.uuid4().hex
    evidence = folder / "frames" / video["id"] / mode / run_id
    trace_path = folder / "traces" / video["id"] / mode / (run_id + ".jsonl")
    evidence.mkdir(parents=True, exist_ok=False)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = _analyze_run(folder, video, path, mode, frame_count, evidence, trace_path)
    except Exception as exc:
        write_json(evidence / "failure.json", {
            "status": "error", "mode": mode, "run_id": run_id, "video_id": video["id"],
            "error": str(exc), "failed_at": utc_now(),
            "trace_path": str(trace_path.relative_to(folder)),
            "evidence_path": str(evidence.relative_to(folder)),
            "scope": "Incomplete failed attempt; not successful extraction evidence."})
        raise
    result["run_id"] = run_id
    return result


def _analyze_run(folder, video, path, mode, frame_count, evidence, trace_path):
    extractor = HUDExtractor()
    analyzer = SessionAnalyzer(retain_samples=False)
    counts, layouts, units = Counter(), Counter(), Counter()
    durations, samples = [], []
    started = time.monotonic()
    iterator = full_frames(path) if mode == "full" else smoke_frames(path, frame_count)
    last_frame_saved, last_stamp = -100, 0
    last_revision = last_thumb = unchanged_since = None
    with trace_path.open("w", encoding="utf-8") as trace:
        for stamp, frame in iterator:
            if time.monotonic() - started > 900:
                raise ValueError("Analysis exceeded 15-minute budget")
            before = time.monotonic()
            sample = extractor.extract(frame, stamp)
            durations.append((time.monotonic() - before) * 1000)
            if mode == "full":
                calibration = sample.get("calibration") or {}
                if calibration.get("status") == "locked":
                    revision = calibration.get("revision")
                    if last_revision is not None and revision != last_revision:
                        analyzer.discontinuity("calibration_changed")
                    last_revision = revision
                thumb = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA)
                if last_thumb is not None and np.array_equal(thumb, last_thumb):
                    if unchanged_since is None:
                        unchanged_since = stamp
                    if stamp - unchanged_since > 1.5:
                        sample["stale"] = True
                        for key in (*CHANNELS, "rpm", "lap_time", "lap_time_seconds", "lap_valid"):
                            sample[key] = None
                        sample.update(steering_status="unavailable", steering_confidence=0)
                else:
                    unchanged_since = None
                last_thumb = thumb
            analyzed = analyzer.process(sample) if mode == "full" else sample
            trace.write(json.dumps(analyzed, allow_nan=False) + "\n")
            counts["frames"] += 1
            counts["visible"] += bool(sample.get("hud_visible"))
            for channel in CHANNELS:
                counts[channel] += sample.get(channel) is not None
            layouts[sample.get("hud_layout") or "unknown"] += 1
            units[sample.get("hud_speed_unit") or "unknown"] += 1
            if mode == "smoke" or stamp - last_frame_saved >= 30:
                if len(samples) < 64:
                    filename = f"{stamp:.3f}.jpg"
                    cv2.imwrite(str(evidence / filename), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
                    samples.append({"frame": filename, "sample": sample})
                    last_frame_saved = stamp
            last_stamp = stamp
    if not counts["frames"]:
        raise ValueError("No video frames decoded")
    analyzer.finalize()
    elapsed = time.monotonic() - started
    visible = counts["visible"]
    core = ("speed", "gear", "brake", "throttle")
    diagnostic = ("no_supported_hud_observed" if not visible else
                  "core_channels_missing" if any(not counts[channel] for channel in core) else
                  "channels_observed_needs_accuracy_review")
    return {"status": "completed", "mode": mode, "source_seconds": last_stamp,
            "diagnostic": diagnostic, "trace_path": str(trace_path.relative_to(folder)),
            "evidence_path": str(evidence.relative_to(folder)),
            "frames": counts["frames"], "wall_seconds": round(elapsed, 3),
            "realtime_factor": round(last_stamp / elapsed, 2) if mode == "full" else None,
            "extraction_ms": {"median": round(float(np.median(durations)), 3), "p95": round(float(np.percentile(durations, 95)), 3)},
            "hud_visible_pct": round(100 * counts["visible"] / counts["frames"], 2),
            "coverage": {channel: round(100 * counts[channel] / counts["frames"], 2) for channel in CHANNELS},
            "visible_hud_coverage": {channel: round(100 * counts[channel] / visible, 2) if visible else None for channel in CHANNELS},
            "layouts": dict(layouts), "hud_speed_units": dict(units), "frame_samples": samples,
            "summary": analyzer.summary() if mode == "full" else None,
            "laps": analyzer.laps if mode == "full" else None,
            "scope": "Machine extraction, not independent accuracy validation. Coverage includes menus and non-driving footage. Full mode decodes and analyzes at <=30 Hz with calibration/frozen-frame discontinuities; storage/UI/network excluded. Smoke timestamps are sparse; they cannot validate lap boundaries, distance or delta."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=ROOT / "data/validation")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index", help="Index actual YouTube flat-playlist JSON")
    index.add_argument("files", type=Path, nargs="+")
    search = sub.add_parser("search", help="Search missing pairings; bounded, resumable searches")
    search.add_argument("--limit", type=int, default=25)
    search.add_argument("--per-pair", type=int, default=6)
    run = sub.add_parser("run", help="Download and test candidates outside the session library")
    run.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    run.add_argument("--limit", type=int, default=10)
    run.add_argument("--frames", type=int, default=12)
    run.add_argument("--max-cache-gb", type=float, default=12)
    run.add_argument("--retry", action="store_true")
    run.add_argument("--cached-only", action="store_true", help="Recheck downloaded clips without new transfers")
    run.add_argument("--video-id", action="append", default=[])
    sub.add_parser("report", help="Refresh the complete car x circuit coverage matrix")
    args = parser.parse_args()
    folder = args.folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if args.command == "index":
        for path in args.files:
            document = read_json(path)
            print(path.name, "added", index_entries(folder, document, document.get("webpage_url") or str(path)), flush=True)
    elif args.command == "search":
        if not 1 <= args.limit <= 1000 or not 1 <= args.per_pair <= 20:
            parser.error("Search limits must be 1..1000 pairs and 1..20 results")
        pairs = sorted(coverage_report(folder)["pairs"], key=lambda p: p["candidates"])
        attempted = 0
        for pair in pairs:
            if pair["candidates"] >= 3:
                continue
            output = folder / "discovery" / (pair["car_id"] + "--" + pair["track_id"] + ".json")
            if output.exists():
                continue
            query = f'ytsearch{args.per_pair}:ACC {pair["car_name"]} {pair["track_name"]} hotlap'
            print(query, flush=True)
            try:
                reply = subprocess.run(youtube_command() + ["--flat-playlist", "--dump-single-json", query],
                                       text=True, capture_output=True, timeout=90, check=True)
                document = json.loads(reply.stdout)
                write_json(output, document)
                index_entries(folder, document, query)
            except (subprocess.SubprocessError, ValueError) as exc:
                write_json(output.with_suffix(".error.json"), {"query": query, "error": str(exc), "time": utc_now()})
            attempted += 1
            if attempted >= args.limit:
                break
    elif args.command == "run":
        if not 1 <= args.limit <= 10000 or not 2 <= args.frames <= 64 or not math.isfinite(args.max_cache_gb) or args.max_cache_gb < 2:
            parser.error("Use 1..10000 videos, 2..64 frames, and at least 2 GB cache")
        videos = choose_videos(folder, args.mode, 10000 if args.cached_only else args.limit, args.retry)
        if args.cached_only:
            videos = [video for video in videos if cached_video(folder, video) is not None][:args.limit]
        if args.video_id:
            manifest = read_json(folder / "manifest.json", {"videos": {}})
            unknown = set(args.video_id) - manifest["videos"].keys()
            if unknown:
                parser.error("Unknown video IDs: " + ", ".join(sorted(unknown)))
            videos = [manifest["videos"][identity] for identity in dict.fromkeys(args.video_id)]
            if args.cached_only:
                videos = [video for video in videos if cached_video(folder, video) is not None]
            videos = videos[:args.limit]
        fingerprint = extractor_fingerprint(ROOT)
        for index, video in enumerate(videos):
            print(f'{index + 1}/{len(videos)} {video["id"]} {video["title"]}', flush=True)
            started = time.monotonic()
            try:
                if args.cached_only:
                    path = cached_video(folder, video)
                    if path is None:
                        raise ValueError("Cached video is unavailable; --cached-only forbids downloading it")
                else:
                    path = download(folder, video, args.max_cache_gb)
                result = analyze(folder, video, path, args.mode, args.frames)
                print("  coverage", result["coverage"], "wall", result["wall_seconds"], flush=True)
            except Exception as exc:
                result = {"status": "error", "mode": args.mode, "error": str(exc), "wall_seconds": time.monotonic() - started}
                print("  ERROR", str(exc)[-400:], flush=True)
            result.update(video_id=video["id"], fingerprint=fingerprint, completed_at=utc_now())
            save_video_result(folder, video["id"], result)
            write_json(folder / "coverage.json", coverage_report(folder))
    result = coverage_report(folder)
    write_json(folder / "coverage.json", result)
    print(json.dumps({key: value for key, value in result.items() if key not in {"pairs", "searches"}}, indent=2))


if __name__ == "__main__":
    main()
