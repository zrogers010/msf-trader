"""Extract a 16kHz mono WAV from an .mp4 using ffmpeg."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_audio(video_path: str | Path, out_wav: str | Path, overwrite: bool = False) -> Path:
    video_path = Path(video_path)
    out_wav = Path(out_wav)
    if out_wav.exists() and not overwrite:
        return out_wav
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg not found on PATH. Install it (macOS: `brew install ffmpeg`)."
        )
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y" if overwrite else "-n",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        str(out_wav),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and not out_wav.exists():
        raise RuntimeError(f"ffmpeg failed for {video_path}:\n{result.stderr[-2000:]}")
    return out_wav
