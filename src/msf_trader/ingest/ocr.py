"""Cheap OCR text layer over extracted frames.

Two purposes:
  1. Capture rules/labels printed on slides as searchable text.
  2. Act as a prefilter: only frames with enough text (or detected as charts)
     are worth sending to the (paid) vision LLM.
"""
from __future__ import annotations

import shutil

import cv2  # type: ignore
import numpy as np
from PIL import Image

from ..analysis.schema import FrameRecord
from ..config import Config


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _looks_like_chart(path: str) -> bool:
    """Heuristic: charts tend to have many long horizontal/vertical edges
    (axes, grid lines, candles). Cheap signal to flag chart frames for vision."""
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False
    edges = cv2.Canny(img, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=120, minLineLength=80, maxLineGap=10)
    if lines is None:
        return False
    h_v = 0
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if abs(x2 - x1) < 8 or abs(y2 - y1) < 8:
            h_v += 1
    return h_v >= 8


def ocr_frames(frames: list[FrameRecord], config: Config) -> list[FrameRecord]:
    ocfg = config.ocr
    if not ocfg.get("enabled", True):
        return frames
    if not tesseract_available():
        # OCR is optional; continue without it but still flag charts.
        for fr in frames:
            fr.is_chart = _looks_like_chart(fr.path)
        return frames

    import pytesseract  # lazy import

    for fr in frames:
        try:
            text = pytesseract.image_to_string(Image.open(fr.path)) or ""
        except Exception:
            text = ""
        fr.ocr_text = " ".join(text.split())
        fr.is_chart = _looks_like_chart(fr.path)
    return frames


def select_frames_for_vision(frames: list[FrameRecord], config: Config) -> list[FrameRecord]:
    ocfg = config.ocr
    fcfg = config.frames
    min_chars = int(ocfg.get("min_text_chars", 8))
    max_vision = int(fcfg.get("max_vision_frames", 60))

    candidates = [
        fr for fr in frames if fr.is_chart or len(fr.ocr_text) >= min_chars
    ]
    # If OCR/heuristics found nothing, fall back to all frames.
    if not candidates:
        candidates = list(frames)

    # Cap cost: keep evenly spaced frames if over the budget.
    if len(candidates) > max_vision:
        step = len(candidates) / max_vision
        candidates = [candidates[int(i * step)] for i in range(max_vision)]
    return candidates
