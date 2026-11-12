"""Run and verify a complete recording through the actual HTTP server and SQLite store."""
import argparse
import json
from pathlib import Path
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--video", default="acc_video.mp4")
    args = parser.parse_args()

    def api(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(args.url + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    started = time.perf_counter()
    source = api("/api/source", {"kind": "file", "path": args.video, "realtime": False})
    session_id = source["session_id"]
    previous_progress = started
    while True:
        status = api("/api/status")
        if status["state"] in {"ended", "error"}:
            break
        if time.perf_counter() - started > 600:
            raise TimeoutError("Recording analysis exceeded 10 minutes")
        if time.perf_counter() - previous_progress > 10:
            print(f"Analyzed {status['source_time']:.1f} / {status['duration'] or 0:.1f} source seconds", flush=True)
            previous_progress = time.perf_counter()
        time.sleep(.2)
    elapsed = time.perf_counter() - started
    assert not status["error"], status["error"]
    detail = api("/api/sessions/" + session_id)
    samples = detail["samples"]
    assert len(samples) == status["summary"]["samples"]
    assert detail["session"]["status"] == "completed"
    assert status["source_time"] >= status["duration"] - .2
    assert any(not sample["hud_visible"] for sample in samples)
    assert any(sample.get("active") and sample.get("brake", 0) > 90 for sample in samples)
    assert any(sample.get("active") and sample.get("throttle", 0) > 90 for sample in samples)
    timings = sorted(sample["processing_ms"] for sample in samples)
    report = {"session_id": session_id, "source_seconds": status["source_time"], "wall_seconds": round(elapsed, 3),
              "realtime_multiple": round(status["source_time"] / elapsed, 2), "recorded_frames": len(samples),
              "processing_median_ms": timings[len(timings) // 2], "processing_p95_ms": timings[int(len(timings) * .95)],
              "summary": detail["summary"], "laps": detail["laps"],
              "timer_outliers_suppressed": sum(bool(s.get("timer_outlier")) for s in samples),
              "scope": "Actual server decoding, extraction, analysis, preview generation and SQLite persistence. Excludes source broadcast delay and physical phone latency."}
    output = Path("artifacts/full-pipeline.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
