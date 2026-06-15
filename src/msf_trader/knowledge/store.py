"""Local-first persistence. No database in v1 -- just JSON/JSONL on disk.

Layout (relative to config paths):
  work_dir/<video_id>/audio.wav
  work_dir/<video_id>/transcript.json
  work_dir/<video_id>/frames.json          (list[FrameRecord])
  work_dir/<video_id>/frames/*.jpg
  work_dir/<video_id>/visual_notes.json    (list[VisualNote])
  work_dir/<video_id>/notes.json           (VideoNotes)
  kb_dir/kb.jsonl                          (aggregated KBItem with video metadata)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from ..analysis.schema import (
    FrameRecord,
    KBItem,
    Transcript,
    VideoNotes,
    VisualNote,
)
from ..config import Config


class VideoMeta(BaseModel):
    video_id: str
    filename: str
    module: str
    chapter_order: int
    path: str


def _write_json(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2))


def _read_json(path: Path, model_cls: type[BaseModel]) -> BaseModel | None:
    if not path.exists():
        return None
    return model_cls.model_validate_json(path.read_text())


class Store:
    def __init__(self, config: Config):
        self.config = config
        self.work_dir = config.paths.work_dir
        self.kb_dir = config.paths.kb_dir

    # paths -----------------------------------------------------------------
    def video_dir(self, video_id: str) -> Path:
        return self.work_dir / video_id

    def audio_path(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "audio.wav"

    def frames_dir(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "frames"

    def transcript_path(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "transcript.json"

    def frames_index_path(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "frames.json"

    def visual_notes_path(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "visual_notes.json"

    def notes_path(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "notes.json"

    def kb_path(self) -> Path:
        return self.kb_dir / "kb.jsonl"

    # transcript ------------------------------------------------------------
    def save_transcript(self, t: Transcript) -> None:
        _write_json(self.transcript_path(t.video_id), t)

    def load_transcript(self, video_id: str) -> Transcript | None:
        return _read_json(self.transcript_path(video_id), Transcript)  # type: ignore[return-value]

    # frames ----------------------------------------------------------------
    def save_frames(self, video_id: str, frames: list[FrameRecord]) -> None:
        path = self.frames_index_path(video_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [f.model_dump() for f in frames]
        path.write_text(json.dumps(payload, indent=2))

    def load_frames(self, video_id: str) -> list[FrameRecord]:
        path = self.frames_index_path(video_id)
        if not path.exists():
            return []
        return [FrameRecord.model_validate(d) for d in json.loads(path.read_text())]

    # visual notes ----------------------------------------------------------
    def save_visual_notes(self, video_id: str, notes: list[VisualNote]) -> None:
        path = self.visual_notes_path(video_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([n.model_dump() for n in notes], indent=2))

    def load_visual_notes(self, video_id: str) -> list[VisualNote]:
        path = self.visual_notes_path(video_id)
        if not path.exists():
            return []
        return [VisualNote.model_validate(d) for d in json.loads(path.read_text())]

    # per-video notes -------------------------------------------------------
    def save_notes(self, notes: VideoNotes) -> None:
        _write_json(self.notes_path(notes.video_id), notes)

    def load_notes(self, video_id: str) -> VideoNotes | None:
        return _read_json(self.notes_path(video_id), VideoNotes)  # type: ignore[return-value]

    # aggregated KB ---------------------------------------------------------
    def rebuild_kb(self, all_notes: Iterable[VideoNotes]) -> int:
        path = self.kb_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w") as fh:
            for vn in all_notes:
                for item in vn.items:
                    record = {
                        "video_id": vn.video_id,
                        "filename": vn.filename,
                        "module": vn.module,
                        "chapter_order": vn.chapter_order,
                        "item": item.model_dump(),
                    }
                    fh.write(json.dumps(record) + "\n")
                    count += 1
        return count

    def load_kb(self) -> list[dict]:
        path = self.kb_path()
        if not path.exists():
            return []
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def load_kb_items(self) -> list[KBItem]:
        return [KBItem.model_validate(r["item"]) for r in self.load_kb()]
