"""Independent speed/gear transcription across car variants; no identity inference."""
import json
from pathlib import Path

import pytest

from tests.test_hud_variants import read_frame
from telemetry.vision import HUDExtractor

ROOT = Path(__file__).resolve().parents[1]
LABELS = json.loads((ROOT / "tests/fixtures/expansion_hud_labels.json").read_text())["frames"]


@pytest.mark.parametrize("label", LABELS, ids=lambda label: label["car_id"])
def test_visible_digits_across_gt3_variants(label):
    path = ROOT / label["path"]
    if not path.is_file():
        pytest.skip("Public footage is not cached; run the corpus download campaign")
    sample = HUDExtractor().extract(read_frame(path, label["time"]), label["time"])
    assert sample["hud_visible"]
    assert sample["gear"] == label["gear"]
    if label.get("known_cold_frame_gap"):
        # The evaluation counts these explicitly as misses, never accuracy passes.
        assert sample["hud_speed_unit"] in (None, label["hud_speed_unit"])
        assert sample["speed"] in (None, label["speed"])
    else:
        assert sample["hud_speed_unit"] == label["hud_speed_unit"]
        assert sample["speed"] == label["speed"]
