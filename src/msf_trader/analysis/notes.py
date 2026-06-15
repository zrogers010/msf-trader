"""Fuse transcript + visual notes into typed, cited KB items.

The LLM is asked to extract structured items, each grounded by a timestamp range
and modality, and tagged confirmed (explicitly stated/shown) vs inferred. We
construct Source objects from those timestamps -- items without usable timestamps
are dropped so nothing un-citable enters the KB.
"""
from __future__ import annotations

import uuid

from ..config import Config
from .llm import LLMClient
from .schema import (
    Confidence,
    ItemType,
    KBItem,
    Modality,
    Source,
    Transcript,
    VideoNotes,
    VisualNote,
    seconds_to_timecode,
)

NOTES_SYSTEM = (
    "You extract a structured knowledge base from an E-mini S&P 500 (/ES) "
    "futures day-trading course. Be precise and conservative. NEVER invent rules, "
    "numbers, or details that are not present in the provided material. "
    "Distinguish explicit course content (confidence='confirmed') from your own "
    "interpretation (confidence='inferred'). Every item MUST cite the timestamp "
    "(in seconds) of the supporting transcript or visual note and its modality."
)

ALLOWED_TYPES = ", ".join(t.value for t in ItemType)

NOTES_USER_TMPL = """Module: {module}
Video: {filename}

Below is a timestamped timeline of this video. Lines marked [AUDIO t0-t1] are spoken transcript; lines marked [VISUAL t] describe a slide/chart frame.

Extract a JSON array of knowledge items. Each item:
{{
  "type": one of [{types}],
  "text": "clear, self-contained statement of the definition/concept/rule/etc.",
  "confidence": "confirmed" | "inferred",
  "modality": "audio" | "visual",
  "t_start": number (seconds),
  "t_end": number (seconds),
  "tags": ["optional", "keywords"]
}}

Focus on items relevant to day-trading strategy extraction: market structure, session timing, trend/range context, support/resistance, price-action examples, indicators, entry setups, confirmation rules, profit conditions, stop-loss placement, trade management, scaling, targets, risk/reward, no-trade conditions, common mistakes, and chart examples.

Rules:
- Only extract what is supported by the timeline. If unsure whether a rule is explicit, mark it "inferred".
- Use the timestamp(s) of the supporting line(s) for t_start/t_end.
- Do NOT fabricate timestamps; use values present in the timeline.
- Return ONLY the JSON array.

TIMELINE:
{timeline}
"""


def _build_timeline(transcript: Transcript, visuals: list[VisualNote]) -> list[tuple[float, float, str]]:
    events: list[tuple[float, float, str]] = []
    for seg in transcript.segments:
        events.append((seg.start, seg.end, f"[AUDIO {seg.start:.1f}-{seg.end:.1f}] {seg.text}"))
    for n in visuals:
        parts = [n.description]
        if n.on_screen_text:
            parts.append(f"on-screen text: {n.on_screen_text}")
        if n.indicators:
            parts.append(f"indicators: {', '.join(n.indicators)}")
        if n.rules_shown:
            parts.append(f"rules shown: {' | '.join(n.rules_shown)}")
        if n.setup_elements:
            parts.append(f"setup: {', '.join(n.setup_elements)}")
        events.append((n.timestamp, n.timestamp, f"[VISUAL {n.timestamp:.1f}] " + "; ".join(p for p in parts if p)))
    events.sort(key=lambda e: e[0])
    return events


def _chunk_events(events: list[tuple[float, float, str]], max_chars: int = 7000) -> list[list[tuple[float, float, str]]]:
    chunks: list[list[tuple[float, float, str]]] = []
    cur: list[tuple[float, float, str]] = []
    size = 0
    for ev in events:
        line_len = len(ev[2]) + 1
        if size + line_len > max_chars and cur:
            chunks.append(cur)
            cur = []
            size = 0
        cur.append(ev)
        size += line_len
    if cur:
        chunks.append(cur)
    return chunks


def _coerce_type(value: str) -> ItemType:
    value = (value or "").strip().lower()
    for t in ItemType:
        if t.value == value:
            return t
    return ItemType.concept


def _coerce_confidence(value: str) -> Confidence:
    return Confidence.confirmed if str(value).strip().lower() == "confirmed" else Confidence.inferred


def _coerce_modality(value: str) -> Modality:
    return Modality.visual if str(value).strip().lower() == "visual" else Modality.audio


def synthesize_notes(
    video_id: str,
    filename: str,
    module: str,
    chapter_order: int,
    transcript: Transcript,
    visuals: list[VisualNote],
    llm: LLMClient,
    config: Config,
) -> VideoNotes:
    events = _build_timeline(transcript, visuals)
    duration = transcript.duration or (events[-1][1] if events else 0.0)
    items: list[KBItem] = []

    for chunk in _chunk_events(events):
        timeline_text = "\n".join(e[2] for e in chunk)
        user = NOTES_USER_TMPL.format(
            module=module,
            filename=filename,
            types=ALLOWED_TYPES,
            timeline=timeline_text,
        )
        try:
            data = llm.complete(NOTES_SYSTEM, user).json()
        except Exception:
            continue
        if isinstance(data, dict):
            data = data.get("items", []) if "items" in data else [data]
        if not isinstance(data, list):
            continue

        for raw in data:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            try:
                t_start = float(raw.get("t_start"))
                t_end = float(raw.get("t_end", raw.get("t_start")))
            except (TypeError, ValueError):
                continue  # drop un-citable items
            if t_end < t_start:
                t_start, t_end = t_end, t_start
            t_start = max(0.0, min(t_start, duration or t_start))
            t_end = max(t_start, min(t_end, duration or t_end))
            modality = _coerce_modality(raw.get("modality"))

            frame_path = None
            if modality == Modality.visual:
                frame_path = _nearest_frame(visuals, t_start)

            items.append(
                KBItem(
                    id=uuid.uuid4().hex[:12],
                    type=_coerce_type(raw.get("type")),
                    text=text,
                    confidence=_coerce_confidence(raw.get("confidence")),
                    tags=[str(x) for x in raw.get("tags", []) or []],
                    sources=[
                        Source(
                            video_id=video_id,
                            module=module,
                            t_start=t_start,
                            t_end=t_end,
                            modality=modality,
                            frame_path=frame_path,
                        )
                    ],
                )
            )

    return VideoNotes(
        video_id=video_id,
        module=module,
        chapter_order=chapter_order,
        filename=filename,
        items=items,
    )


def _nearest_frame(visuals: list[VisualNote], t: float) -> str | None:
    if not visuals:
        return None
    best = min(visuals, key=lambda n: abs(n.timestamp - t))
    if abs(best.timestamp - t) <= 30:
        return best.frame_path
    return None
