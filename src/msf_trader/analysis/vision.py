"""Vision-LLM analysis of selected frames (charts, slides, setups).

Returns structured VisualNote objects. The prompt is domain-tuned for /ES day
trading and explicitly instructs the model NOT to invent content that is not
visible in the frame.
"""
from __future__ import annotations

from ..analysis.schema import FrameRecord, VisualNote, seconds_to_timecode
from .llm import LLMClient

VISION_SYSTEM = (
    "You are a meticulous analyst extracting information from frames of an "
    "E-mini S&P 500 (/ES) futures day-trading course. Describe ONLY what is "
    "actually visible in the image. Never guess values, rules, or labels that "
    "are not shown. If something is unclear, say so. Pay special attention to: "
    "charts, candlesticks, indicators, support/resistance lines, trendlines, "
    "arrows, annotations, entry/exit markers, stop placements, and any rules or "
    "bullet points printed on slides."
)

VISION_USER = """Analyze this single frame and return a JSON object with EXACTLY these keys:
{
  "description": "concise description of what is shown",
  "on_screen_text": "verbatim text/bullets visible on the slide (empty if none)",
  "contains_chart": true/false,
  "indicators": ["names of indicators visibly labeled or clearly shown"],
  "rules_shown": ["any trading rule or instruction printed on screen, verbatim or close"],
  "setup_elements": ["visible setup elements: e.g. 'support line', 'breakout arrow', 'stop below swing low'"]
}
Only include items you can actually see. Use empty strings/lists when nothing applies.
Return ONLY the JSON object."""


def analyze_frames(
    frames: list[FrameRecord],
    llm: LLMClient,
) -> list[VisualNote]:
    notes: list[VisualNote] = []
    for fr in frames:
        try:
            resp = llm.complete(VISION_SYSTEM, VISION_USER, image_paths=[fr.path])
            data = resp.json()
        except Exception as exc:  # keep pipeline resilient; record an empty note
            data = {"description": f"[vision analysis failed: {exc}]"}
        if not isinstance(data, dict):
            data = {}

        notes.append(
            VisualNote(
                video_id=fr.video_id,
                module=fr.module,
                timestamp=fr.timestamp,
                frame_path=fr.path,
                description=str(data.get("description", "")),
                on_screen_text=str(data.get("on_screen_text", "") or fr.ocr_text),
                contains_chart=bool(data.get("contains_chart", fr.is_chart)),
                indicators=[str(x) for x in data.get("indicators", []) or []],
                rules_shown=[str(x) for x in data.get("rules_shown", []) or []],
                setup_elements=[str(x) for x in data.get("setup_elements", []) or []],
            )
        )
    return notes


def visual_notes_to_text(notes: list[VisualNote]) -> str:
    """Flatten visual notes into a timestamped text block for KB synthesis."""
    lines: list[str] = []
    for n in notes:
        tc = seconds_to_timecode(n.timestamp)
        lines.append(f"[VISUAL {tc}] {n.description}")
        if n.on_screen_text:
            lines.append(f"  on-screen text: {n.on_screen_text}")
        if n.indicators:
            lines.append(f"  indicators: {', '.join(n.indicators)}")
        if n.rules_shown:
            lines.append(f"  rules shown: {' | '.join(n.rules_shown)}")
        if n.setup_elements:
            lines.append(f"  setup elements: {', '.join(n.setup_elements)}")
    return "\n".join(lines)
