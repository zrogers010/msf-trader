"""Local retrieval index over KB items + transcript segments.

Hybrid retrieval: BM25 (keyword) fused with embeddings (semantic) via
reciprocal-rank fusion (RRF). Embeddings are optional and OpenAI-only; without
them the retriever falls back to BM25 with a token-overlap gate (robust on small
corpora where BM25 IDF can go negative).

No database: the corpus is JSON on disk and embeddings are a numpy array.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from ..analysis.schema import seconds_to_timecode
from ..config import Config
from .store import Store, VideoMeta

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _build_units(store: Store, catalog: list[VideoMeta]) -> list[dict]:
    units: list[dict] = []

    # 1) KB items (synthesized, already cited)
    for r in store.load_kb():
        item = r["item"]
        src = item["sources"][0] if item.get("sources") else {}
        units.append(
            {
                "kind": "kb_item",
                "text": item["text"],
                "video_id": r["video_id"],
                "filename": r["filename"],
                "module": r["module"],
                "t_start": src.get("t_start", 0.0),
                "t_end": src.get("t_end", 0.0),
                "modality": src.get("modality", "audio"),
                "confidence": item["confidence"],
                "type": item["type"],
            }
        )

    # 2) transcript segments (raw coverage)
    for v in catalog:
        t = store.load_transcript(v.video_id)
        if not t:
            continue
        for seg in t.segments:
            units.append(
                {
                    "kind": "transcript",
                    "text": seg.text,
                    "video_id": v.video_id,
                    "filename": v.filename,
                    "module": v.module,
                    "t_start": seg.start,
                    "t_end": seg.end,
                    "modality": "audio",
                    "confidence": "confirmed",
                    "type": "transcript",
                }
            )
    return units


def build_index(config: Config, store: Store, catalog: list[VideoMeta]) -> Path:
    units = _build_units(store, catalog)

    index_dir = config.paths.index_dir
    index_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = index_dir / "corpus.json"
    corpus_path.write_text(json.dumps(units, indent=2))

    emb_path = index_dir / "embeddings.npy"
    emb_meta_path = index_dir / "embeddings.json"
    # remove any stale embeddings so a rebuild can't mix vectors with new corpus
    emb_path.unlink(missing_ok=True)
    emb_meta_path.unlink(missing_ok=True)

    rcfg = config.retrieval
    if rcfg.get("use_embeddings", True) and units:
        from ..analysis.llm import LLMClient

        llm = LLMClient(config)
        if llm.supports_embeddings():
            model = rcfg.get("embedding_model", "text-embedding-3-small")
            try:
                vectors = llm.embed_texts([u["text"] for u in units], model=model)
                arr = np.asarray(vectors, dtype=np.float32)
                # L2-normalize so dot product == cosine similarity
                norms = np.linalg.norm(arr, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                arr = arr / norms
                np.save(emb_path, arr)
                emb_meta_path.write_text(json.dumps({"model": model, "count": len(units), "dim": arr.shape[1]}))
            except Exception as exc:  # fall back to BM25-only
                print(f"[index] embeddings skipped ({exc}); using BM25 only.")
    return corpus_path


def _rrf(rank_lists: list[list[int]], k: int = 60) -> list[int]:
    """Reciprocal-rank fusion across multiple ranked index lists."""
    scores: dict[int, float] = {}
    for ranks in rank_lists:
        for position, idx in enumerate(ranks):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + position + 1)
    return sorted(scores, key=lambda i: scores[i], reverse=True)


class Retriever:
    def __init__(self, config: Config):
        self.config = config
        index_dir = config.paths.index_dir
        corpus_path = index_dir / "corpus.json"
        if not corpus_path.exists():
            raise FileNotFoundError(
                "Retrieval index not found. Run `msf-trader index` (or `run-all`) first."
            )
        self.units: list[dict] = json.loads(corpus_path.read_text())
        self._bm25 = BM25Okapi([_tokenize(u["text"]) for u in self.units]) if self.units else None

        self._embeddings = None
        self._emb_model = None
        emb_path = index_dir / "embeddings.npy"
        emb_meta_path = index_dir / "embeddings.json"
        if emb_path.exists() and emb_meta_path.exists():
            meta = json.loads(emb_meta_path.read_text())
            if meta.get("count") == len(self.units):
                self._embeddings = np.load(emb_path)
                self._emb_model = meta.get("model")

    def _decorate(self, idx: int) -> dict:
        u = dict(self.units[idx])
        u["citation"] = f"[{u['filename']} @ {seconds_to_timecode(u['t_start'])}]"
        return u

    def _bm25_ranking(self, query: str, pool: int) -> list[int]:
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        return sorted(range(len(self.units)), key=lambda i: scores[i], reverse=True)[:pool]

    def _embedding_ranking(self, query: str, pool: int) -> list[int]:
        if self._embeddings is None:
            return []
        from ..analysis.llm import LLMClient

        llm = LLMClient(self.config)
        qvec = np.asarray(llm.embed_texts([query], model=self._emb_model)[0], dtype=np.float32)
        n = np.linalg.norm(qvec) or 1.0
        sims = self._embeddings @ (qvec / n)
        return list(np.argsort(-sims)[:pool])

    def search(self, query: str, top_k: int = 25) -> list[dict]:
        if not self.units:
            return []
        pool = int(self.config.retrieval.get("candidate_pool", 50))

        if self._embeddings is not None:
            bm25_rank = self._bm25_ranking(query, pool)
            emb_rank = self._embedding_ranking(query, pool)
            fused = _rrf([bm25_rank, emb_rank])
            return [self._decorate(i) for i in fused[:top_k]]

        # BM25-only fallback with token-overlap gating (robust on small corpora)
        query_tokens = set(_tokenize(query))
        if not query_tokens:
            return []
        results: list[dict] = []
        for i in self._bm25_ranking(query, len(self.units)):
            if not (query_tokens & set(_tokenize(self.units[i]["text"]))):
                continue
            results.append(self._decorate(i))
            if len(results) >= top_k:
                break
        return results
