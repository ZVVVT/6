"""Frozen FITC enhancement and skeleton helpers extracted from the verified scripts."""
from __future__ import annotations

import cv2
import numpy as np


def read_fitc(path):
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise FileNotFoundError(path)
    if image.ndim == 2:
        green = image
    elif image.ndim == 3:
        green = image[:, :, 1]
    else:
        raise ValueError(
            "FITC image must be two-dimensional or three-channel: {}".format(
                path
            )
        )
    if green.dtype != np.uint8:
        green = np.clip(green, 0, 255).astype(np.uint8)
    return image, green


def corrected_signal(g: np.ndarray, sigma: float = 18.0) -> np.ndarray:
    bg = cv2.GaussianBlur(g, (0, 0), sigma)
    signal = cv2.subtract(g, bg)
    return cv2.GaussianBlur(signal, (0, 0), 0.8)


def baseline_threshold(signal: np.ndarray):
    nz = signal[signal > 0]
    otsu, _ = cv2.threshold(signal, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    floor = int(np.percentile(nz, 70)) if nz.size else 1
    return max(int(otsu), floor, 4), int(otsu), floor


def clean_mask(raw: np.ndarray) -> np.ndarray:
    raw = cv2.morphologyEx((raw > 0).astype(np.uint8) * 255, cv2.MORPH_CLOSE,
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(raw, 8)
    clean = np.zeros_like(raw)
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        width = int(stats[i, cv2.CC_STAT_WIDTH])
        height = int(stats[i, cv2.CC_STAT_HEIGHT])
        if area >= 24 and max(width, height) >= 10:
            clean[labels == i] = 255
    return clean


def enhanced_mask(g: np.ndarray) -> np.ndarray:
    signal = corrected_signal(g)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 16)).apply(signal)
    lut = np.array([round(255 * (i / 255) ** 0.70) for i in range(256)], np.uint8)
    enhanced = cv2.LUT(clahe, lut)
    _, otsu, _ = baseline_threshold(enhanced)
    threshold = max(otsu, 4)
    return clean_mask(enhanced >= threshold)


def skeleton(mask: np.ndarray) -> np.ndarray:
    return cv2.ximgproc.thinning(mask, thinningType=cv2.ximgproc.THINNING_ZHANGSUEN)
