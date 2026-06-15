"""Cited Q&A over the course knowledge base.

Retrieves with BM25, then asks the LLM to answer using ONLY the retrieved
evidence, with `[file @ mm:ss]` citations and explicit confirmed-vs-inferred
flags. Refuses to answer beyond the evidence.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..analysis.llm import LLMClient
from ..analysis.schema import seconds_to_timecode
from ..config import Config
from ..knowledge.index import Retriever

QA_SYSTEM = (
    "You answer questions about an E-mini S&P 500 (/ES) day-trading course using "
    "ONLY the provided evidence snippets. Every claim must include a citation in "
    "the form `[file @ mm:ss]` taken from the snippets. State whether each rule is "
    "EXPLICIT (confirmed in the course) or INFERRED. If the evidence does not "
    "answer the question, say so plainly. Never invent rules, numbers, or "
    "citations that are not in the evidence."
)


@dataclass
class Answer:
    question: str
    text: str
    evidence: list[dict]


def _format_evidence(units: list[dict]) -> str:
    lines = []
    for u in units:
        tc = seconds_to_timecode(u["t_start"])
        flag = "EXPLICIT" if u.get("confidence") == "confirmed" else "INFERRED"
        kind = u.get("kind")
        lines.append(
            f"- [{u['filename']} @ {tc}] ({flag}, {u.get('modality')}, {kind}) {u['text']}"
        )
    return "\n".join(lines)


def answer_question(question: str, config: Config) -> Answer:
    retriever = Retriever(config)
    top_k = int(config.qa.get("top_k", 12))
    evidence = retriever.search(question, top_k=top_k)

    if not evidence:
        return Answer(
            question=question,
            text="No relevant evidence was found in the ingested course material.",
            evidence=[],
        )

    llm = LLMClient(config)
    user = (
        f"Question: {question}\n\n"
        f"Evidence snippets (cite these):\n{_format_evidence(evidence)}\n\n"
        "Answer the question grounded only in the evidence above. Include citations "
        "and EXPLICIT/INFERRED flags. If incomplete, list what is missing."
    )
    resp = llm.complete(QA_SYSTEM, user)
    return Answer(question=question, text=resp.text.strip(), evidence=evidence)
