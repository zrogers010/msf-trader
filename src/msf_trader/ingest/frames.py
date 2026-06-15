"""Frame extraction with slide/scene-change detection.

Strategy:
  1. Use PySceneDetect ContentDetector to find hard scene/slide cuts.
  2. Add a periodic time-grid so the chart is sampled as it evolves (the
     instructor draws arrows / support-resistance lines and price action plays
     out while the SAME chart layout stays on screen).
  3. Merge candidates that are too close together in TIME.

Note on de-duplication: we intentionally do NOT use perceptual-hash (phash)
de-dup here. For this course, an annotated /ES chart that changes meaningfully
(arrows, S/R lines, new candles) hashes IDENTICALLY (phash distance 0) to its
earlier state because the chart layout dominates the low-frequency hash. phash
de-dup therefore discards exactly the annotated teaching frames we care about.
Time-proximity de-dup keeps temporal coverage; the vision-frame selector and the
`max_vision_frames` cap control cost downstream.

Each saved frame keeps its timestamp so every visual claim can be cited.
"""
from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore

from ..analysis.schema import FrameRecord, seconds_to_timecode
from ..config import Config


def _detect_scene_times(video_path: str, threshold: float) -> list[float]:
    """Return scene start times in seconds using PySceneDetect."""
    from scenedetect import ContentDetector, SceneManager, open_video  # type: ignore

    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))
    scene_manager.detect_scenes(video, show_progress=False)
    scene_list = scene_manager.get_scene_list()
    times = [scene[0].get_seconds() for scene in scene_list]
    if not times:
        times = [0.0]
    elif times[0] > 0.5:
        times.insert(0, 0.0)
    return times


def _video_duration(video_path: str) -> float:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return total_frames / fps if fps else 0.0


def _candidate_timestamps(video_path: str, config: Config) -> list[float]:
    fcfg = config.frames
    threshold = float(fcfg.get("scene_threshold", 27.0))
    periodic = float(fcfg.get("periodic_seconds", 30))

    try:
        scene_times = _detect_scene_times(video_path, threshold)
    except Exception:
        scene_times = [0.0]

    duration = _video_duration(video_path)

    # representative frame ~0.7s into each scene to avoid transition blur
    times = {round(min(t + 0.7, max(duration - 0.1, 0.0)), 2) for t in scene_times}

    # periodic time-grid so evolving charts are sampled over time
    if duration and periodic > 0:
        t = 0.0
        while t < duration:
            times.add(round(t, 2))
            t += periodic

    return sorted(times)


def _dedup_by_time(timestamps: list[float], min_gap: float) -> list[float]:
    """Greedily keep timestamps spaced at least `min_gap` seconds apart."""
    kept: list[float] = []
    last = float("-inf")
    for ts in timestamps:
        if ts - last >= min_gap:
            kept.append(ts)
            last = ts
    return kept


def extract_frames(
    video_path: str | Path,
    video_id: str,
    module: str,
    frames_dir: str | Path,
    config: Config,
) -> list[FrameRecord]:
    video_path = str(video_path)
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    fcfg = config.frames
    min_gap = float(fcfg.get("min_gap_seconds", 10))

    timestamps = _dedup_by_time(_candidate_timestamps(video_path, config), min_gap)

    cap = cv2.VideoCapture(video_path)
    records: list[FrameRecord] = []

    for ts in timestamps:
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue

        fname = f"{seconds_to_timecode(ts).replace(':', '')}_{int(ts*1000):08d}.jpg"
        out_path = frames_dir / fname
        cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])

        records.append(
            FrameRecord(
                video_id=video_id,
                module=module,
                timestamp=float(ts),
                path=str(out_path),
            )
        )

    cap.release()
    records.sort(key=lambda r: r.timestamp)
    return records
