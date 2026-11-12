"""Extract timestamped native video crops for independent landmark inspection."""
import argparse
from pathlib import Path

import av
import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("timestamps", type=float, nargs="+")
    parser.add_argument("--crop", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--width", type=int, default=800)
    parser.add_argument("--columns", type=int, default=3)
    args = parser.parse_args()
    tiles = []
    with av.open(str(args.video)) as container:
        stream = container.streams.video[0]
        for timestamp in args.timestamps:
            container.seek(int(timestamp / float(stream.time_base)), stream=stream, backward=True)
            for frame in container.decode(stream):
                if float(frame.time or 0) + .0001 >= timestamp:
                    image = frame.to_ndarray(format="bgr24")
                    if args.crop:
                        x, y, width, height = args.crop
                        image = image[y:y + height, x:x + width]
                    image = cv2.resize(image, (args.width, round(image.shape[0] * args.width / image.shape[1])))
                    tile = np.zeros((image.shape[0] + 30, image.shape[1], 3), dtype=np.uint8)
                    tile[30:] = image
                    cv2.putText(tile, f"PTS {frame.time:.3f} s", (8, 22), cv2.FONT_HERSHEY_SIMPLEX,
                                .6, (255, 255, 255), 1, cv2.LINE_AA)
                    tiles.append(tile)
                    break
    if not tiles:
        raise SystemExit("No frames at these timestamps")
    while len(tiles) % args.columns:
        tiles.append(np.zeros_like(tiles[0]))
    output = np.concatenate([np.concatenate(tiles[index:index + args.columns], axis=1)
                             for index in range(0, len(tiles), args.columns)])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), output):
        raise SystemExit("Could not write contact sheet")


if __name__ == "__main__":
    main()
