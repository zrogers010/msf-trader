"""Discover .mp4 files and map them to modules / chapter order from config."""
from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .knowledge.store import VideoMeta


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug or "video"


def build_catalog(config: Config) -> list[VideoMeta]:
    """Return ordered VideoMeta for every .mp4 in the videos dir.

    Ordering: by module order, then by explicit file list position, then by
    filename. Files are assigned to a module by (1) explicit `files` list, then
    (2) the module `match` substring, else placed in an "Unassigned" bucket.
    """
    videos_dir = config.paths.videos_dir
    mp4s = sorted(p for p in videos_dir.glob("*.mp4"))
    by_name = {p.name: p for p in mp4s}

    assigned: dict[str, tuple[int, int, Path]] = {}  # name -> (module_order, within, path)

    # 1) explicit file lists win
    for module in config.modules:
        for within, fname in enumerate(module.files):
            if fname in by_name:
                assigned[fname] = (module.order, within, by_name[fname])

    # 2) substring match for anything not explicitly assigned
    for p in mp4s:
        if p.name in assigned:
            continue
        for module in config.modules:
            if module.match and module.match.lower() in p.name.lower():
                assigned[p.name] = (module.order, 10_000, p)
                break

    # 3) unassigned bucket
    max_order = max((m.order for m in config.modules), default=0)
    for p in mp4s:
        if p.name not in assigned:
            assigned[p.name] = (max_order + 1, 20_000, p)

    order_to_name = {m.order: m.name for m in config.modules}

    ordered = sorted(assigned.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0]))

    catalog: list[VideoMeta] = []
    for chapter_order, (fname, (mod_order, _within, path)) in enumerate(ordered, start=1):
        module_name = order_to_name.get(mod_order, "Unassigned")
        catalog.append(
            VideoMeta(
                video_id=_slugify(path.stem),
                filename=path.name,
                module=module_name,
                chapter_order=chapter_order,
                path=str(path),
            )
        )
    return catalog
