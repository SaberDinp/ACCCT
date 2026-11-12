"""Read-only regression checks on the supplied Silverstone video recordings."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from telemetry.visual_laps import find_visual_laps, match_visual_landmark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, default=Path("acc_video.mp4"))
    parser.add_argument("--reference", type=Path, default=Path("data/references/c0SJgXUahEM/media.mp4"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/visual-laps-checks.json"))
    args = parser.parse_args()
    reference = find_visual_laps(args.reference, 1.75, 31.75, 130)
    own = find_visual_laps(args.current, 147.727, 177.727, 285)
    landmark = match_visual_landmark(args.current, 210, args.reference, 52, 70)
    assert reference["candidates"] and own["candidates"] and landmark["candidates"]
    assert abs(reference["candidates"][0]["end_time"] - 118.6) < .35
    assert abs(own["candidates"][0]["end_time"] - 270.082) < .25
    assert abs(landmark["candidates"][0]["target_time"] - 61.15) < .4
    assert len(reference["candidates"]) == 1
    assert all(not point["confirmed"] and point["requires_confirmation"]
               for result in (reference, own, landmark) for point in result["candidates"])
    result = {"reference_finish": reference, "current_finish": own, "cross_view_landmark": landmark,
              "source_videos_modified": False, "sessions_modified": False,
              "accuracy_note": "Checks reproducible proposals only; these are not measured timing-beam errors or proof of world-space track position."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
