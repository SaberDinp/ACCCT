"""Evaluate manually labeled, held-out source frames without touching sessions.

This measures visible-HUD agreement, not raw game telemetry or entire-lap
registration. Missing videos are reported explicitly; no network access occurs.
"""

import argparse
import json
from pathlib import Path
import statistics
import sys

import av
import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from telemetry.vision import HUDExtractor


def read_frame(container, stream, timestamp):
    container.seek(int(timestamp / float(stream.time_base)), stream=stream)
    for frame in container.decode(stream):
        if frame.time >= timestamp:
            return float(frame.time), frame.to_ndarray(format="bgr24")
    raise ValueError(f"No frame at {timestamp}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=ROOT / "tests/fixtures/multicar_hud_labels.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/multicar-hud-evaluation.json")
    parser.add_argument("--render", action="store_true", help="Save complete decoded frames alongside the report")
    args = parser.parse_args()
    manifest = json.loads(args.labels.read_text())
    results, failed, missing = [], [], []
    for video in manifest["videos"]:
        path = ROOT / video["path"]
        if not path.is_file():
            missing.append(video["video_id"])
            continue
        extractor = HUDExtractor()
        frames = []
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            for label in video["frames"]:
                timestamp, image = read_frame(container, stream, label["time"])
                sample = extractor.extract(image, timestamp)
                checks = {channel: sample[channel] == label[channel] for channel in ("speed", "gear")}
                checks.update({channel: sample[channel] is not None and label[f"{channel}_range"][0] <= sample[channel] <= label[f"{channel}_range"][1]
                               for channel in ("brake", "throttle", "steering")})
                checks.update(hud_layout=sample["hud_layout"] == video["hud_layout"],
                              hud_speed_unit=sample["hud_speed_unit"] == video["hud_speed_unit"])
                if not all(checks.values()):
                    failed.append({"video_id": video["video_id"], "time": timestamp, "checks": checks})
                frames.append({"requested_time": label["time"], "decoded_time": timestamp, "checks": checks,
                               "observed": {key: sample[key] for key in (*checks, "processing_ms")}})
                if args.render:
                    target = args.output.parent / "heldout-frames" / video["video_id"]
                    target.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(target / f"{timestamp:.6f}.png"), image)
            # Repeated frames isolate extraction cost from codec/network latency.
            benchmark = HUDExtractor()
            cold = benchmark.extract(image, 0)["processing_ms"]
            warm = [benchmark.extract(image, (index + 1) / 30)["processing_ms"] for index in range(60)]
        results.append({"video_id": video["video_id"], "car_id": video["car_id"], "track_id": video["track_id"],
                        "source_url": video["url"], "frames": frames, "cold_extraction_ms": cold,
                        "warm_extraction_median_ms": statistics.median(warm),
                        "warm_extraction_p95_ms": sorted(warm)[56]})
    report = {"scope": "Independent frame-level visible-HUD agreement, not complete-lap accuracy",
              "label_method": manifest["label_method"], "videos": results, "failed": failed, "missing": missing,
              "checked_frames": sum(len(result["frames"]) for result in results),
              "complete": not failed and not missing}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checked_frames": report["checked_frames"], "failed": len(failed), "missing": missing,
                      "report": str(args.output)}, indent=2))
    raise SystemExit(0 if report["complete"] else 1)


if __name__ == "__main__":
    main()
