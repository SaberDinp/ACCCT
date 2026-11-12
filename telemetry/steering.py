"""Track the visible ACC steering marker, expressed along its displayed axis."""

import cv2
import numpy as np


def extract_steering(crop: np.ndarray) -> tuple[float | None, float]:
    if crop.size == 0:
        return None, 0.0
    image = cv2.resize(crop, (204, 17), interpolation=cv2.INTER_LINEAR)
    channels = image.astype(np.int16)
    white = ((channels.min(axis=2) >= 205) & (np.ptp(channels, axis=2) < 65)).astype(np.uint8)
    _, labels, stats, centers = cv2.connectedComponentsWithStats(white)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    candidates = []
    for index, (x, y, width, height, area) in enumerate(stats[1:], start=1):
        center_x, center_y = centers[index]
        if not (4 <= width <= 12 and 4 <= height <= 12 and 16 <= area <= 100 and 4 <= center_y <= 13):
            continue
        if not 0.55 <= width / height <= 1.7:
            continue
        left, right = max(0, x - 3), min(204, x + width + 3)
        top, bottom = max(0, y - 3), min(17, y + height + 3)
        patch = gray[top:bottom, left:right]
        ring = np.ones(patch.shape, bool)
        ring[y - top:y - top + height, x - left:x - left + width] = False
        surround = float(np.median(patch[ring])) if np.any(ring) else 255
        intensity = float(gray[labels == index].mean())
        contrast = intensity - surround
        if contrast < 45:
            continue
        compactness = area / (width * height)
        score = min(1.0, contrast / 130) * 0.45 + min(1.0, compactness / 0.72) * 0.35 + max(0, 1 - abs(center_y - 8.5) / 8.5) * 0.2
        candidates.append((score, center_x))
    candidates.sort(reverse=True)
    if not candidates or candidates[0][0] < 0.65:
        return None, 0.0
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.1:
        return None, 0.0
    confidence, center = candidates[0]
    # Three pixels at either end leave room for the marker's own radius.
    value = max(-100.0, min(100.0, (center - 102) / 99 * 100))
    return round(float(value), 1), round(float(confidence), 3)
