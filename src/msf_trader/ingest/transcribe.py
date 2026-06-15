"""Local transcription with faster-whisper (Apple Silicon: CPU/int8 by default).

Produces timestamped segments. Word timestamps are requested when configured so
citations can be made precise later if needed.
"""
from __future__ import annotations

from pathlib import Path

from ..analysis.schema import Transcript, TranscriptSegment
from ..config import Config


_model_cache: dict[tuple, object] = {}


def _get_model(model_name: str, compute_type: str):
    key = (model_name, compute_type)
    if key not in _model_cache:
        from faster_whisper import WhisperModel  # imported lazily (heavy)

        # device "auto" lets CTranslate2 pick CPU on macOS; int8 keeps it fast.
        _model_cache[key] = WhisperModel(model_name, device="auto", compute_type=compute_type)
    return _model_cache[key]


def transcribe_audio(
    audio_path: str | Path,
    video_id: str,
    module: str,
    config: Config,
) -> Transcript:
    tcfg = config.transcription
    model = _get_model(tcfg.get("model", "medium"), tcfg.get("compute_type", "int8"))

    segments_iter, info = model.transcribe(
        str(audio_path),
        language=tcfg.get("language") or None,
        word_timestamps=bool(tcfg.get("word_timestamps", True)),
        vad_filter=True,
    )

    segments: list[TranscriptSegment] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(TranscriptSegment(start=float(seg.start), end=float(seg.end), text=text))

    return Transcript(
        video_id=video_id,
        module=module,
        language=getattr(info, "language", None),
        duration=getattr(info, "duration", None),
        segments=segments,
    )
