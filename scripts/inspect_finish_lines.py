"""Contact sheets around possible finish-line crossings in supplied recordings."""

from pathlib import Path
import sys

import av
import cv2
import numpy as np


def frame_at(container, stream, stamp):
    container.seek(int(stamp / float(stream.time_base)), stream=stream)
    for frame in container.decode(stream):
        if float(frame.time or 0) + .02 >= stamp:
            return frame.to_ndarray(format="bgr24")
    return None


def sheet(path, stamps, output):
    tiles = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for stamp in stamps:
            frame = frame_at(container, stream, stamp)
            if frame is None:
                continue
            tile = cv2.resize(frame, (640, 360))
            cv2.rectangle(tile, (0, 0), (160, 28), (0, 0, 0), -1)
            cv2.putText(tile, f"{stamp:.3f} s", (8, 21), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 1)
            tiles.append(tile)
    while len(tiles) % 3:
        tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(str(output), np.concatenate([np.concatenate(tiles[i:i + 3], axis=1) for i in range(0, len(tiles), 3)]))


if __name__ == "__main__":
    sheet(Path(sys.argv[1]), [float(value) for value in sys.argv[3:]], Path(sys.argv[2]))
