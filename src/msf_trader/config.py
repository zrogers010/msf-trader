"""Configuration loading: merges config.yaml with environment variables.

Everything is path/config-driven so the repo is portable (e.g. clone onto a
Mac Mini, set .env, and run). No machine-specific assumptions live in code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

DEFAULT_CONFIG_PATH = "config.yaml"


@dataclass
class ModuleSpec:
    name: str
    order: int
    files: list[str] = field(default_factory=list)
    match: str | None = None


@dataclass
class Paths:
    root: Path
    videos_dir: Path
    work_dir: Path
    kb_dir: Path
    index_dir: Path
    docs_dir: Path


@dataclass
class LLMConfig:
    provider: str
    openai_model: str
    anthropic_model: str
    max_output_tokens: int
    temperature: float


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path
    paths: Paths
    modules: list[ModuleSpec]
    llm: LLMConfig

    # convenience accessors -------------------------------------------------
    @property
    def course(self) -> dict[str, Any]:
        return self.raw.get("course", {})

    @property
    def transcription(self) -> dict[str, Any]:
        return self.raw.get("transcription", {})

    @property
    def frames(self) -> dict[str, Any]:
        return self.raw.get("frames", {})

    @property
    def ocr(self) -> dict[str, Any]:
        return self.raw.get("ocr", {})

    @property
    def qa(self) -> dict[str, Any]:
        return self.raw.get("qa", {})

    @property
    def retrieval(self) -> dict[str, Any]:
        return self.raw.get("retrieval", {})

    def ensure_dirs(self) -> None:
        for p in (
            self.paths.videos_dir,
            self.paths.work_dir,
            self.paths.kb_dir,
            self.paths.index_dir,
            self.paths.docs_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (root / p)


def load_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. Copy/edit config.yaml in the project root."
        )
    raw = yaml.safe_load(config_path.read_text()) or {}
    root = config_path.resolve().parent

    paths_raw = raw.get("paths", {})
    paths = Paths(
        root=root,
        videos_dir=_resolve(root, paths_raw.get("videos_dir", "data/videos")),
        work_dir=_resolve(root, paths_raw.get("work_dir", "data/work")),
        kb_dir=_resolve(root, paths_raw.get("kb_dir", "data/kb")),
        index_dir=_resolve(root, paths_raw.get("index_dir", "data/index")),
        docs_dir=_resolve(root, paths_raw.get("docs_dir", "docs")),
    )

    modules = [
        ModuleSpec(
            name=m["name"],
            order=int(m.get("order", i + 1)),
            files=list(m.get("files", []) or []),
            match=m.get("match"),
        )
        for i, m in enumerate(raw.get("modules", []))
    ]
    modules.sort(key=lambda m: m.order)

    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        provider=os.getenv("MSF_LLM_PROVIDER", llm_raw.get("provider", "openai")).lower(),
        openai_model=os.getenv("MSF_OPENAI_MODEL", "gpt-4o"),
        anthropic_model=os.getenv("MSF_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
        max_output_tokens=int(llm_raw.get("max_output_tokens", 4000)),
        temperature=float(llm_raw.get("temperature", 0.1)),
    )

    return Config(raw=raw, root=root, paths=paths, modules=modules, llm=llm)
