"""Pydantic models for the knowledge base.

Core principle: every claim is grounded by a citation and tagged as either an
explicit course rule (`confirmed`) or an interpretation (`inferred`). Nothing is
fabricated; items without a source are not allowed into the KB.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Modality(str, Enum):
    audio = "audio"      # spoken / transcript
    visual = "visual"    # slide / chart / on-screen text


class Confidence(str, Enum):
    confirmed = "confirmed"   # explicitly stated or shown in the course
    inferred = "inferred"     # reasonable interpretation, not explicit


class ItemType(str, Enum):
    definition = "definition"
    concept = "concept"
    example = "example"
    warning = "warning"
    rule = "rule"
    setup = "setup"
    indicator = "indicator"
    session_timing = "session_timing"
    risk = "risk"
    no_trade = "no_trade"
    mistake = "mistake"
    open_question = "open_question"


class Source(BaseModel):
    video_id: str
    module: str
    t_start: float = Field(..., description="Start time in seconds")
    t_end: float = Field(..., description="End time in seconds")
    modality: Modality
    frame_path: Optional[str] = None

    def timecode(self) -> str:
        return seconds_to_timecode(self.t_start)


class KBItem(BaseModel):
    id: str
    type: ItemType
    text: str
    confidence: Confidence
    sources: list[Source] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    video_id: str
    module: str
    language: Optional[str] = None
    duration: Optional[float] = None
    segments: list[TranscriptSegment] = Field(default_factory=list)


class FrameRecord(BaseModel):
    video_id: str
    module: str
    timestamp: float
    path: str
    scene_score: Optional[float] = None
    ocr_text: str = ""
    is_chart: bool = False


class VisualNote(BaseModel):
    """Structured analysis of a single frame from the vision LLM."""
    video_id: str
    module: str
    timestamp: float
    frame_path: str
    description: str = ""
    on_screen_text: str = ""
    contains_chart: bool = False
    indicators: list[str] = Field(default_factory=list)
    rules_shown: list[str] = Field(default_factory=list)
    setup_elements: list[str] = Field(default_factory=list)


class VideoNotes(BaseModel):
    """All KB items extracted for one video."""
    video_id: str
    module: str
    chapter_order: int
    filename: str
    items: list[KBItem] = Field(default_factory=list)


def seconds_to_timecode(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
