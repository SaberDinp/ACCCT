"""Measure local decode, HUD extraction and analysis without realtime pacing."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from time import perf_counter

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from telemetry.analysis import SessionAnalyzer
from telemetry.vision import HUDExtractor


def distribution(values):
    return {"mean_ms": round(float(np.mean(values)), 3),
            "median_ms": round(float(np.median(values)), 3),
            "p95_ms": round(float(np.percentile(values, 95)), 3),
            "max_ms": round(float(max(values)), 3)} if values else None


def benchmark(video: Path, seconds: float | None, stride: int, *, decoder_threads: int = 4) -> dict:
    setup_start = perf_counter()
    extractor = HUDExtractor()
    analyzer = SessionAnalyzer(retain_samples=False)
    setup_ms = (perf_counter() - setup_start) * 1000
    extract_times, analysis_times, conversion_times, decode_times = [], [], [], []
    counts = Counter()
    absent_ranges, speed_outliers, timer_changes = [], [], []
    absent_start = None
    previous = None
    previous_clock = None
    source_start, source_end = None, None
    start = perf_counter()
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        stream.codec_context.thread_count = decoder_threads
        source_fps = float(stream.average_rate) if stream.average_rate else None
        width, height = stream.width, stream.height
        iterator = container.decode(stream)
        while True:
            decode_start = perf_counter()
            try:
                frame = next(iterator)
            except StopIteration:
                break
            decode_times.append((perf_counter() - decode_start) * 1000)
            if frame.time is None:
                raise RuntimeError("Benchmark requires source presentation timestamps")
            pts = float(frame.time)
            if source_start is None:
                source_start = pts
            stamp = pts - source_start
            if seconds is not None and stamp >= seconds:
                break
            source_end = stamp
            counts["decoded"] += 1
            if (counts["decoded"] - 1) % stride:
                continue
            conversion_start = perf_counter()
            image = frame.to_ndarray(format="bgr24")
            conversion_times.append((perf_counter() - conversion_start) * 1000)
            extraction_start = perf_counter()
            sample = extractor.extract(image, stamp)
            extract_times.append((perf_counter() - extraction_start) * 1000)
            analysis_start = perf_counter()
            analyzed = analyzer.process(sample)
            analysis_times.append((perf_counter() - analysis_start) * 1000)
            counts["extracted"] += 1
            if sample["hud_visible"]:
                counts["visible"] += 1
                if absent_start is not None:
                    absent_ranges.append({"start": round(absent_start, 3), "end": round(stamp, 3)})
                    absent_start = None
                for field in ("brake", "throttle", "speed", "gear", "rpm", "lap_time_seconds"):
                    counts[field] += sample[field] is not None
                counts["invalid_lap"] += sample["lap_valid"] is False
            elif absent_start is None:
                absent_start = stamp
            if analyzed.get("speed_outlier"):
                speed_outliers.append({"time": round(stamp, 3), "speed": sample["speed"],
                                       "previous_speed": previous["speed"] if previous else None})
            if not sample["hud_visible"]:
                previous_clock = None
            elif sample["lap_time_seconds"] is not None:
                if previous_clock is not None:
                    old_time, old = previous_clock
                    new, elapsed_clock = sample["lap_time_seconds"], stamp - old_time
                    if elapsed_clock <= 1 and abs(new - old - elapsed_clock) > 1:
                        timer_changes.append({"time": round(stamp, 3), "previous": old, "current": new})
                previous_clock = (stamp, sample["lap_time_seconds"])
            previous = sample
    elapsed = perf_counter() - start
    analyzer.finalize()
    if absent_start is not None:
        absent_ranges.append({"start": round(absent_start, 3), "end": round(source_end or 0, 3)})
    return {
        "video": video.name, "width": width, "height": height, "source_fps": round(source_fps, 3) if source_fps else None,
        "stride": stride, "decoder_threads": decoder_threads,
        "source_seconds": round(source_end or 0, 3), "wall_seconds": round(elapsed, 3),
        "realtime_multiple": round((source_end or 0) / elapsed, 2), "template_setup_ms": round(setup_ms, 3),
        "decoded_frames": counts["decoded"], "extracted_frames": counts["extracted"],
        "decoded_fps": round(counts["decoded"] / elapsed, 2), "extracted_fps": round(counts["extracted"] / elapsed, 2),
        "decode": distribution(decode_times), "bgr_conversion": distribution(conversion_times),
        "extraction": distribution(extract_times), "analysis": distribution(analysis_times),
        "visible_hud_samples": counts["visible"],
        "coverage_percent_of_visible": {name: round(100 * counts[name] / max(1, counts["visible"]), 2)
                                        for name in ("brake", "throttle", "speed", "gear", "rpm", "lap_time_seconds")},
        "invalid_lap_samples": counts["invalid_lap"], "hud_absent_ranges": absent_ranges,
        "speed_outlier_count": len(speed_outliers), "speed_outliers_first_50": speed_outliers[:50],
        "timer_discontinuities": timer_changes[:50], "summary": analyzer.summary(), "laps": analyzer.laps,
        "scope": "Includes local video decode, BGR conversion, extraction and analysis. Excludes preview, storage, UI, network, stream buffering and realtime pacing.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--seconds", type=float, help="Stop after this many source seconds; omitted means entire video")
    parser.add_argument("--stride", type=int, default=1, help="Extract every Nth frame; all frames are decoded")
    parser.add_argument("--decoder-threads", type=int, default=4,
                        help="Bound FFmpeg decoder workers (default: 4, matching offline imports)")
    parser.add_argument("--output", type=Path, help="Also save the JSON report at this path")
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error("Video file does not exist")
    if args.seconds is not None and (not np.isfinite(args.seconds) or args.seconds <= 0):
        parser.error("--seconds must be finite and positive")
    if args.stride < 1:
        parser.error("--stride must be positive")
    if not 1 <= args.decoder_threads <= 32:
        parser.error("--decoder-threads must be between 1 and 32")
    report = json.dumps(benchmark(args.video, args.seconds, args.stride,
                                  decoder_threads=args.decoder_threads), indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
