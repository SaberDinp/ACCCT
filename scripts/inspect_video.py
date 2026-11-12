"""Reproduce image-template assets from labeled frames in the supplied recording."""

import argparse
from pathlib import Path
import sys

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from telemetry.profiles import default_profile
from telemetry.vision import ASSETS, CANONICAL, glyph_components, normalize_glyph, timer_mask, white_mask


SPEED_LABELS = {0: "0", 24: "10", 26: "48", 28: "83", 30: "112", 32: "149", 34: "175",
    36: "197", 38: "213", 40: "226", 44: "167", 48: "137", 50: "141", 52: "172", 54: "193",
    56: "208", 60: "229", 62: "236", 64: "217", 66: "154", 68: "138", 70: "153", 72: "181",
    74: "203", 76: "139", 78: "74", 80: "89", 82: "100", 86: "164", 88: "183", 90: "204",
    92: "218", 94: "209", 96: "194", 98: "207", 100: "213", 104: "105", 106: "85",
    108: "125", 110: "81", 112: "93", 114: "135", 116: "164", 118: "183", 120: "204", 126: "242"}
GEAR_LABELS = {0: "N", 24: "1", 28: "1", 30: "2", 32: "3", 34: "4", 38: "5", 40: "5", 48: "3", 66: "3", 76: "2", 126: "6"}
RPM_LABELS = {0: "0", 30: "5731", 40: "6801", 60: "6852", 90: "7041", 120: "7043", 180: "6509", 240: "6326", 300: "7060", 420: "6653", 480: "6505", 530: "5923"}


def crop(frame, name):
    x, y, w, h = default_profile()["rois"][name]
    height, width = frame.shape[:2]
    image = frame[round(y * height):round((y + h) * height), round(x * width):round((x + w) * width)]
    return cv2.resize(image, CANONICAL[name])


def build_templates(video):
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise SystemExit(f"Cannot open {video}")
    ASSETS.mkdir(parents=True, exist_ok=True)
    counts = {}
    for kind, labels in (("speed", SPEED_LABELS), ("gear", GEAR_LABELS), ("rpm", RPM_LABELS)):
        folder = ASSETS / kind
        folder.mkdir(exist_ok=True)
        counts[kind] = 0
        for timestamp, text in labels.items():
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            if timestamp == 30:
                cv2.imwrite(str(ASSETS / "labels.png"), cv2.cvtColor(crop(frame, "labels"), cv2.COLOR_BGR2GRAY))
                cv2.imwrite(str(ASSETS / "lap_labels.png"), cv2.cvtColor(crop(frame, "lap_labels"), cv2.COLOR_BGR2GRAY))
            parts = glyph_components(white_mask(crop(frame, kind), 185 if kind == "rpm" else 205), kind)
            if len(parts) != len(text):
                print(f"Skip {kind} {timestamp}: expected {text}, found {len(parts)} components")
                continue
            for index, (character, (_, glyph)) in enumerate(zip(text, parts)):
                cv2.imwrite(str(folder / f"{character}_{timestamp:03d}_{index}.png"), normalize_glyph(glyph))
                counts[kind] += 1
    # The lap clock has the same condensed font, with its own antialiasing.
    folder = ASSETS / "lap_time"
    folder.mkdir(exist_ok=True)
    for timestamp, text in ((10, "0024176"), (30, "0043518"), (40, "0053486"), (60, "0113456"),
                            (90, "0143456"), (120, "0213516"), (180, "0032312"), (240, "0132337"),
                            (300, "0030000"), (420, "0228500"), (480, "0328510"), (530, "0020597")):
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ok, frame = capture.read()
        if not ok:
            continue
        parts = glyph_components(timer_mask(crop(frame, "lap_time")), "lap_time")
        if len(parts) != len(text):
            continue
        for index, (character, (_, glyph)) in enumerate(zip(text, parts)):
            cv2.imwrite(str(folder / f"{character}_{timestamp:03d}_{index}.png"), normalize_glyph(glyph))
    # Supply remaining digits with normalized speed glyphs; both use the same font.
    existing = {path.stem.split("_")[0] for path in folder.glob("*.png")}
    for path in (ASSETS / "speed").glob("*.png"):
        if path.stem.split("_")[0] not in existing:
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            cv2.imwrite(str(folder / path.name), image)
    capture.release()
    print("Template glyph counts:", counts)


def build_reference_templates(video):
    """Capture the German label anchor from the explicitly supplied reference."""
    import av
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        container.seek(int(37.333333 / float(stream.time_base)), stream=stream)
        for frame in container.decode(stream):
            if float(frame.time) >= 37.333333:
                break
        image = frame.to_ndarray(format="bgr24")
    anchor = cv2.cvtColor(image[900:929, 1730:1769], cv2.COLOR_BGR2GRAY)
    ASSETS.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(ASSETS / "labels_de.png"), cv2.resize(anchor, (44, 33)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--build-templates", action="store_true")
    parser.add_argument("--reference-templates", action="store_true")
    args = parser.parse_args()
    if args.build_templates:
        build_templates(args.video)
    elif args.reference_templates:
        build_reference_templates(args.video)
    else:
        parser.error("Choose --build-templates or --reference-templates")
