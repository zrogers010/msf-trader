"""Generate the six strategy docs, grounded strictly in the cited KB.

COURSE_MAP is deterministic (structure only). The analytical docs are written by
the LLM but constrained to the supplied KB items, each of which already carries a
`[file @ timecode]` citation and a confirmed/inferred tag. The model is told to
preserve citations and to never add un-cited claims.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..analysis.llm import LLMClient
from ..analysis.schema import ItemType, seconds_to_timecode
from ..config import Config
from ..knowledge.store import Store, VideoMeta

DISCLAIMER = (
    "> NOTE: This document is auto-generated from course videos for study and "
    "research only. It is not trading advice. The strategy is NOT assumed to be "
    "profitable and has not been validated. Rules tagged `inferred` are "
    "interpretations, not explicit course statements.\n"
)


def _cite(record: dict) -> str:
    item = record["item"]
    src = item["sources"][0] if item.get("sources") else None
    if not src:
        return f"[{record['filename']}]"
    return f"[{record['filename']} @ {seconds_to_timecode(src['t_start'])}]"


def _format_items(records: list[dict]) -> str:
    lines = []
    for r in records:
        item = r["item"]
        tag = item["confidence"].upper()
        lines.append(f"- ({tag}) {item['text']} {_cite(r)}")
    return "\n".join(lines)


def _by_module(records: list[dict], modules_order: list[str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        grouped[r["module"]].append(r)
    ordered = {}
    for m in modules_order:
        if m in grouped:
            ordered[m] = grouped[m]
    for m, recs in grouped.items():
        if m not in ordered:
            ordered[m] = recs
    return ordered


def _by_type(records: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        grouped[r["item"]["type"]].append(r)
    return grouped


# ---------------------------------------------------------------------------
# COURSE_MAP.md (deterministic)
# ---------------------------------------------------------------------------
def gen_course_map(config: Config, store: Store, catalog: list[VideoMeta]) -> str:
    course = config.course
    lines = [
        f"# Course Map: {course.get('title', 'Trading Course')}",
        "",
        DISCLAIMER,
        "",
        f"- Instrument: {course.get('instrument', 'N/A')}",
        f"- Trading style: {course.get('trading_style', 'N/A')}",
        f"- Videos ingested: {len(catalog)}",
        "",
        "## Modules and chapters",
        "",
    ]
    by_mod: dict[str, list[VideoMeta]] = defaultdict(list)
    for v in catalog:
        by_mod[v.module].append(v)

    module_order = [m.name for m in config.modules] + [
        k for k in by_mod if k not in {m.name for m in config.modules}
    ]
    for mod in module_order:
        vids = by_mod.get(mod)
        if not vids:
            continue
        lines.append(f"### {mod}")
        for v in sorted(vids, key=lambda x: x.chapter_order):
            t = store.load_transcript(v.video_id)
            dur = seconds_to_timecode(t.duration) if (t and t.duration) else "?"
            notes = store.load_notes(v.video_id)
            n_items = len(notes.items) if notes else 0
            lines.append(
                f"- Chapter {v.chapter_order}: `{v.filename}` (id `{v.video_id}`) "
                f"-- duration {dur}, {n_items} KB items"
            )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM-backed docs
# ---------------------------------------------------------------------------
def _strip_leading_h1(body: str) -> str:
    """Drop a redundant top-level H1 the model sometimes echoes (the doc title
    is added by us). Only removes a single leading `# ...` line."""
    lines = body.lstrip("\n").splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def _llm_doc(llm: LLMClient, system: str, user: str) -> str:
    try:
        return _strip_leading_h1(llm.complete(system, user).text.strip())
    except Exception as exc:
        return f"_Document generation failed: {exc}_"


GROUNDING_SYSTEM = (
    "You write precise study documents for an E-mini S&P 500 (/ES) day-trading "
    "course. Use ONLY the provided knowledge-base items. Do not add facts, rules, "
    "or numbers that are not in the items. Preserve every citation in the form "
    "`[file @ mm:ss]`. Keep the distinction between CONFIRMED (explicit) and "
    "INFERRED (interpretation) items. If information is missing, say so explicitly "
    "rather than guessing. Do NOT add a top-level '# ' title heading -- the "
    "document title is added separately; start directly with the content or '## ' "
    "sections."
)


def gen_module_summaries(config: Config, llm: LLMClient, records: list[dict]) -> str:
    modules_order = [m.name for m in config.modules]
    grouped = _by_module(records, modules_order)
    sections = ["# Module Summaries", "", DISCLAIMER, ""]
    for module, recs in grouped.items():
        user = (
            f"Write a concise summary of the module '{module}' using only these items. "
            f"Group by themes, keep citations, and separate confirmed vs inferred where useful.\n\n"
            f"ITEMS:\n{_format_items(recs)}"
        )
        body = _llm_doc(llm, GROUNDING_SYSTEM, user)
        sections.append(f"## {module}\n\n{body}\n")
    return "\n".join(sections)


def gen_strategy_playbook(config: Config, llm: LLMClient, records: list[dict]) -> str:
    user = (
        "Write STRATEGY_PLAYBOOK.md: a narrative explanation of the trading method as "
        "taught in the course (market context, how setups are found, how trades are "
        "entered, managed, and exited). Use only the provided items and keep citations. "
        "Clearly mark inferred reasoning.\n\n"
        f"ITEMS:\n{_format_items(records)}"
    )
    body = _llm_doc(llm, GROUNDING_SYSTEM, user)
    return f"# Strategy Playbook\n\n{DISCLAIMER}\n{body}\n"


STRATEGY_RULES_SECTIONS = """Produce STRATEGY_RULES.md with these exact section headers (use '## '), filling each from the items. If the course does not specify a section, write 'Not specified in the course.' under it. Keep citations and tag lines (CONFIRMED) or (INFERRED).

## Instrument
## Trading style
## Allowed sessions / times of day
## Market context requirements
## Setup conditions
## Indicator conditions
## Entry trigger
## Stop-loss logic
## Profit target logic
## Trade management logic
## Invalidation rules
## Risk management
## Max loss rules
## No-trade conditions
## Examples of valid setups
## Examples of invalid setups
## Unresolved ambiguities
"""


def gen_strategy_rules(config: Config, llm: LLMClient, records: list[dict]) -> str:
    course = config.course
    preface = (
        f"Known fixed facts (use as-is): instrument = {course.get('instrument')}, "
        f"trading style = {course.get('trading_style')}.\n\n"
    )
    user = (
        preface
        + STRATEGY_RULES_SECTIONS
        + "\nUse ONLY these items for everything else.\n\nITEMS:\n"
        + _format_items(records)
    )
    body = _llm_doc(llm, GROUNDING_SYSTEM, user)
    return f"# Strategy Rules\n\n{DISCLAIMER}\n{body}\n"


def gen_ambiguities(config: Config, llm: LLMClient, records: list[dict]) -> str:
    by_type = _by_type(records)
    open_qs = by_type.get(ItemType.open_question.value, [])
    inferred = [r for r in records if r["item"]["confidence"] == "inferred"]
    user = (
        "Write AMBIGUITIES_AND_OPEN_QUESTIONS.md. List every place where the course is "
        "underspecified, contradictory, or where rules had to be inferred. Cite where each "
        "topic is discussed. Be specific about what is missing so it can be clarified later.\n\n"
        f"OPEN QUESTION ITEMS:\n{_format_items(open_qs)}\n\n"
        f"INFERRED ITEMS (interpretations that may need confirmation):\n{_format_items(inferred)}"
    )
    body = _llm_doc(llm, GROUNDING_SYSTEM, user)
    return f"# Ambiguities and Open Questions\n\n{DISCLAIMER}\n{body}\n"


def gen_backtest_plan(config: Config, llm: LLMClient, records: list[dict]) -> str:
    by_type = _by_type(records)
    relevant = (
        by_type.get(ItemType.indicator.value, [])
        + by_type.get(ItemType.session_timing.value, [])
        + by_type.get(ItemType.setup.value, [])
        + by_type.get(ItemType.rule.value, [])
        + by_type.get(ItemType.risk.value, [])
    )
    course = config.course
    user = (
        "Write BACKTEST_PLAN.md describing how the extracted strategy COULD eventually be "
        "tested (do NOT claim it is profitable; it is unvalidated). Include these sections "
        "with '## ' headers: Overview and caveats, Required market data, Timeframe(s) needed, "
        "Indicator calculations, Assumptions to avoid (look-ahead, survivorship, overfitting), "
        "Slippage and commission modeling, Out-of-sample testing, Walk-forward testing, "
        "Paper-trading plan, Risk controls, Open data/spec gaps. Ground indicator/session/setup "
        "details in the items and cite them; for general methodology you may use standard "
        f"backtesting best practices. Instrument is {course.get('instrument')}.\n\n"
        f"RELEVANT ITEMS:\n{_format_items(relevant)}"
    )
    body = _llm_doc(llm, GROUNDING_SYSTEM, user)
    return f"# Backtest Plan\n\n{DISCLAIMER}\n{body}\n"


# ---------------------------------------------------------------------------
def generate_all_docs(config: Config, store: Store, catalog: list[VideoMeta]) -> dict[str, Path]:
    llm = LLMClient(config)
    records = store.load_kb()
    docs_dir = config.paths.docs_dir
    docs_dir.mkdir(parents=True, exist_ok=True)

    outputs = {
        "COURSE_MAP.md": gen_course_map(config, store, catalog),
        "MODULE_SUMMARIES.md": gen_module_summaries(config, llm, records),
        "STRATEGY_PLAYBOOK.md": gen_strategy_playbook(config, llm, records),
        "STRATEGY_RULES.md": gen_strategy_rules(config, llm, records),
        "BACKTEST_PLAN.md": gen_backtest_plan(config, llm, records),
        "AMBIGUITIES_AND_OPEN_QUESTIONS.md": gen_ambiguities(config, llm, records),
    }

    written: dict[str, Path] = {}
    for name, content in outputs.items():
        path = docs_dir / name
        path.write_text(content)
        written[name] = path
    return written
