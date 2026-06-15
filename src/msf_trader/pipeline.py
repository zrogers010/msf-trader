"""End-to-end, resumable pipeline orchestration.

Each per-video stage skips work that already exists (unless `overwrite=True`), so
re-running is cheap and the expensive cloud-LLM stages aren't repeated needlessly.
"""
from __future__ import annotations

from rich.console import Console

from .analysis.llm import LLMClient
from .analysis.notes import synthesize_notes
from .analysis.vision import analyze_frames
from .catalog import build_catalog
from .config import Config
from .ingest.audio import extract_audio
from .ingest.frames import extract_frames
from .ingest.ocr import ocr_frames, select_frames_for_vision
from .ingest.transcribe import transcribe_audio
from .knowledge.index import build_index
from .knowledge.store import Store, VideoMeta

console = Console()


def stage_audio(meta: VideoMeta, store: Store, overwrite: bool) -> None:
    out = store.audio_path(meta.video_id)
    if out.exists() and not overwrite:
        console.print(f"  audio: cached")
        return
    extract_audio(meta.path, out, overwrite=overwrite)
    console.print(f"  audio: extracted")


def stage_transcribe(meta: VideoMeta, store: Store, config: Config, overwrite: bool) -> None:
    if store.transcript_path(meta.video_id).exists() and not overwrite:
        console.print("  transcript: cached")
        return
    audio = store.audio_path(meta.video_id)
    if not audio.exists():
        extract_audio(meta.path, audio)
    transcript = transcribe_audio(audio, meta.video_id, meta.module, config)
    store.save_transcript(transcript)
    console.print(f"  transcript: {len(transcript.segments)} segments")


def stage_frames(meta: VideoMeta, store: Store, config: Config, overwrite: bool) -> None:
    if store.frames_index_path(meta.video_id).exists() and not overwrite:
        console.print("  frames: cached")
        return
    frames = extract_frames(
        meta.path, meta.video_id, meta.module, store.frames_dir(meta.video_id), config
    )
    frames = ocr_frames(frames, config)
    store.save_frames(meta.video_id, frames)
    console.print(f"  frames: {len(frames)} kept")


def stage_vision(meta: VideoMeta, store: Store, config: Config, llm: LLMClient, overwrite: bool) -> None:
    if store.visual_notes_path(meta.video_id).exists() and not overwrite:
        console.print("  vision: cached")
        return
    frames = store.load_frames(meta.video_id)
    selected = select_frames_for_vision(frames, config)
    console.print(f"  vision: analyzing {len(selected)} frames...")
    notes = analyze_frames(selected, llm)
    store.save_visual_notes(meta.video_id, notes)
    console.print(f"  vision: {len(notes)} visual notes")


def stage_notes(meta: VideoMeta, store: Store, config: Config, llm: LLMClient, overwrite: bool) -> None:
    if store.notes_path(meta.video_id).exists() and not overwrite:
        console.print("  notes: cached")
        return
    transcript = store.load_transcript(meta.video_id)
    if not transcript:
        console.print("  notes: skipped (no transcript)")
        return
    visuals = store.load_visual_notes(meta.video_id)
    notes = synthesize_notes(
        meta.video_id,
        meta.filename,
        meta.module,
        meta.chapter_order,
        transcript,
        visuals,
        llm,
        config,
    )
    store.save_notes(notes)
    console.print(f"  notes: {len(notes.items)} KB items")


# ---------------------------------------------------------------------------
PER_VIDEO_STAGES = ("audio", "transcribe", "frames", "vision", "notes")


def process_video(
    meta: VideoMeta,
    config: Config,
    store: Store,
    llm: LLMClient,
    stages: tuple[str, ...] = PER_VIDEO_STAGES,
    overwrite: bool = False,
) -> None:
    console.print(f"[bold]{meta.chapter_order}. {meta.filename}[/bold] ({meta.module})")
    if "audio" in stages:
        stage_audio(meta, store, overwrite)
    if "transcribe" in stages:
        stage_transcribe(meta, store, config, overwrite)
    if "frames" in stages:
        stage_frames(meta, store, config, overwrite)
    if "vision" in stages:
        stage_vision(meta, store, config, llm, overwrite)
    if "notes" in stages:
        stage_notes(meta, store, config, llm, overwrite)


def aggregate_kb(config: Config, store: Store, catalog: list[VideoMeta]) -> int:
    all_notes = []
    for v in catalog:
        n = store.load_notes(v.video_id)
        if n:
            all_notes.append(n)
    count = store.rebuild_kb(all_notes)
    console.print(f"KB aggregated: {count} items -> {store.kb_path()}")
    return count


def run_all(
    config: Config,
    stages: tuple[str, ...] = PER_VIDEO_STAGES,
    overwrite: bool = False,
    build_docs: bool = True,
) -> None:
    config.ensure_dirs()
    store = Store(config)
    catalog = build_catalog(config)
    if not catalog:
        console.print(f"[yellow]No .mp4 files found in {config.paths.videos_dir}[/yellow]")
        return

    needs_llm = ("vision" in stages) or ("notes" in stages)
    llm = LLMClient(config) if needs_llm else None

    for meta in catalog:
        process_video(meta, config, store, llm, stages=stages, overwrite=overwrite)

    aggregate_kb(config, store, catalog)
    build_index(config, store, catalog)
    console.print("Retrieval index built.")

    if build_docs:
        from .docgen.generators import generate_all_docs

        written = generate_all_docs(config, store, catalog)
        for name, path in written.items():
            console.print(f"  doc: {path}")
