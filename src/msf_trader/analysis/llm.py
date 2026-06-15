"""Thin cloud-LLM wrapper for vision + reasoning.

Supports OpenAI and Anthropic behind one interface so the rest of the codebase
never imports a provider directly. This is also where a local vision model
(e.g. via Ollama) could be slotted in later for a fully-offline deploy.
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config


@dataclass
class LLMResponse:
    text: str

    def json(self) -> dict | list:
        return parse_json(self.text)


def parse_json(text: str) -> dict | list:
    """Best-effort extraction of a JSON object/array from model output."""
    text = text.strip()
    # strip code fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fall back to first {...} or [...] block
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return {}


def _encode_image(path: str) -> tuple[str, str]:
    data = Path(path).read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    ext = Path(path).suffix.lower().lstrip(".") or "jpeg"
    media = "jpeg" if ext in ("jpg", "jpeg") else ext
    return b64, media


class LLMClient:
    def __init__(self, config: Config):
        self.config = config
        self.provider = config.llm.provider
        self._client = None

    # provider lazy init ----------------------------------------------------
    def _openai(self):
        if self._client is None:
            from openai import OpenAI

            if not os.getenv("OPENAI_API_KEY"):
                raise RuntimeError("OPENAI_API_KEY not set (see .env.example).")
            self._client = OpenAI()
        return self._client

    def _anthropic(self):
        if self._client is None:
            from anthropic import Anthropic

            if not os.getenv("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY not set (see .env.example).")
            self._client = Anthropic()
        return self._client

    # embeddings ------------------------------------------------------------
    def supports_embeddings(self) -> bool:
        return self.provider == "openai"

    def embed_texts(self, texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]:
        """Embed texts (OpenAI only). Batched to stay within request limits."""
        if not self.supports_embeddings():
            raise RuntimeError(
                "Embeddings require the OpenAI provider. Set MSF_LLM_PROVIDER=openai "
                "or disable retrieval.use_embeddings in config.yaml."
            )
        client = self._openai()
        out: list[list[float]] = []
        batch_size = 256
        for i in range(0, len(texts), batch_size):
            batch = [t if t.strip() else " " for t in texts[i : i + batch_size]]
            resp = client.embeddings.create(model=model, input=batch)
            out.extend([d.embedding for d in resp.data])
        return out

    # public API ------------------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        image_paths: list[str] | None = None,
    ) -> LLMResponse:
        image_paths = image_paths or []
        if self.provider == "anthropic":
            return self._complete_anthropic(system, user, image_paths)
        return self._complete_openai(system, user, image_paths)

    # OpenAI ----------------------------------------------------------------
    def _complete_openai(self, system: str, user: str, image_paths: list[str]) -> LLMResponse:
        client = self._openai()
        content: list[dict] = [{"type": "text", "text": user}]
        for p in image_paths:
            b64, media = _encode_image(p)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{media};base64,{b64}"},
                }
            )
        resp = client.chat.completions.create(
            model=self.config.llm.openai_model,
            temperature=self.config.llm.temperature,
            max_tokens=self.config.llm.max_output_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        return LLMResponse(text=resp.choices[0].message.content or "")

    # Anthropic -------------------------------------------------------------
    def _complete_anthropic(self, system: str, user: str, image_paths: list[str]) -> LLMResponse:
        client = self._anthropic()
        content: list[dict] = []
        for p in image_paths:
            b64, media = _encode_image(p)
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": f"image/{media}", "data": b64},
                }
            )
        content.append({"type": "text", "text": user})
        resp = client.messages.create(
            model=self.config.llm.anthropic_model,
            system=system,
            max_tokens=self.config.llm.max_output_tokens,
            temperature=self.config.llm.temperature,
            messages=[{"role": "user", "content": content}],
        )
        parts = [block.text for block in resp.content if getattr(block, "type", "") == "text"]
        return LLMResponse(text="".join(parts))
