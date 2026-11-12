"""Import two recordings through the HTTP API and summarize observed coverage."""
import argparse
import json
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--name", default="Reference recording")
    parser.add_argument("--original-url", default=None)
    parser.add_argument("--role", choices=["current", "reference"], default="reference")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output", default="artifacts/reference-pipeline.json")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        response = client.post("/api/imports", json={"kind": "file", "path": args.path,
                                                    "name": args.name, "url": args.original_url,
                                                    "role": args.role, "auto_calibrate": True})
        response.raise_for_status()
        job_id = response.json()["id"]
        while True:
            job = client.get("/api/imports/" + job_id).json()
            if job["state"] in {"completed", "error", "cancelled"}:
                break
            time.sleep(1)
        if job["state"] != "completed":
            raise RuntimeError(job)
        result = client.get("/api/sessions/" + job["session_id"]).json()
    observed = [sample for sample in result["samples"] if sample.get("hud_visible")]
    processing = sorted(sample["processing_ms"] for sample in result["samples"])
    report = {"job": job, "summary": result["summary"], "laps": result["laps"],
              "metadata": result["session"]["metadata"], "hud_observations": len(observed),
              "coverage_given_hud_pct": {key: round(100 * sum(sample.get(key) is not None for sample in observed) / max(1, len(observed)), 2)
                                          for key in ("speed", "gear", "brake", "throttle", "steering", "rpm", "lap_time_seconds")},
              "processing_ms_median": processing[len(processing) // 2] if processing else None,
              "processing_ms_p95": processing[int(len(processing) * .95)] if processing else None,
              "note": "Observed non-null coverage is not accuracy against raw telemetry."}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
