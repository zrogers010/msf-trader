# msf-trader — Course Ingestion System

Local-first pipeline that ingests `.mp4` lectures from a trading-education course
(E-mini S&P 500 / `/ES` intraday day trading), extracts **spoken** and **visual**
information, builds a **timestamp-cited knowledge base**, and generates a formal
**strategy playbook** plus supporting docs. It also provides a **CLI Q&A
assistant** that answers questions with citations into the videos.

> Scope (v1): understanding, extraction, citation, and rule formalization only.
> There is **no** live trading, brokerage integration, or order execution. The
> strategy is **not** assumed to be profitable and has not been validated.
> Inferred interpretations are kept separate from explicit course rules, and no
> missing rules are invented.

## How it works

```
.mp4 ──ffmpeg──► audio.wav ──faster-whisper──► timestamped transcript
 │
 └─PySceneDetect─► representative frames ──tesseract OCR──► text layer
                                          └─vision LLM────► chart/slide/setup notes
                                                            │
transcript + visual notes ──LLM──► cited KB items (confirmed vs inferred)
                                   │
                                   ├─► docs/*.md  (6 strategy documents)
                                   └─► hybrid index ──► CLI Q&A with citations
                                       (BM25 + embeddings, RRF fusion)
```

- **Transcription** runs locally (Apple Silicon friendly) via `faster-whisper`.
- **Vision + reasoning + Q&A** use a cloud LLM (OpenAI or Anthropic) behind a thin
  wrapper in `analysis/llm.py`, so a local vision model can be slotted in later.
- **Storage** is plain JSON/JSONL files under `data/` — no database in v1.

## Prerequisites

System tools (macOS / Homebrew):

```bash
brew install ffmpeg tesseract
```

Python 3.10+ and dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
# or: pip install -r requirements.txt
```

## Configure

1. Copy env and add your API key:

```bash
cp .env.example .env
# edit .env: set MSF_LLM_PROVIDER=openai (or anthropic) and the matching API key
```

2. Create your config from the template (the real `config.yaml` is gitignored
   because it holds your video filenames):

```bash
cp config.example.yaml config.yaml
```

3. Put your course videos in `data/videos/` (created on first run).

4. Edit `config.yaml` so each file maps to the right module / chapter order.
   You can either list files explicitly under each module's `files:` (recommended
   for correct ordering) or rely on the `match:` substring on the filename.

```bash
# Verify how files map to modules before running the heavy steps:
msf-trader catalog
```

## Run the pipeline

```bash
# Full pipeline (ingest -> transcribe -> frames -> vision -> KB -> index -> docs)
msf-trader run-all

# Or run stages individually (each stage caches and is resumable):
msf-trader transcribe      # local, no API key needed
msf-trader frames          # local, no API key needed
msf-trader vision          # cloud LLM (uses API key)
msf-trader build-kb        # cloud LLM: synthesize cited KB items
msf-trader docs            # regenerate the six docs from the KB
```

Re-running skips cached stages. Use `--overwrite` to recompute a stage.

## Generated documents (`docs/`, local only)

- `COURSE_MAP.md` — modules, chapters, durations, item counts.
- `MODULE_SUMMARIES.md` — per-module cited summaries.
- `STRATEGY_PLAYBOOK.md` — narrative of the method as taught.
- `STRATEGY_RULES.md` — objective rules (instrument, sessions, context, setups,
  entries, stops, targets, management, invalidation, risk, no-trade, examples,
  ambiguities), each tagged CONFIRMED / INFERRED with a citation.
- `BACKTEST_PLAN.md` — how the strategy *could* be tested later (data, timeframe,
  indicators, assumptions to avoid, slippage/commissions, out-of-sample,
  walk-forward, paper trading, risk controls). Spec only — no backtester yet.
- `AMBIGUITIES_AND_OPEN_QUESTIONS.md` — underspecified / contradictory points.

## Ask questions

```bash
msf-trader ask "What are the exact entry rules?"
msf-trader ask "What does the course say about managing trades?"
msf-trader ask "What indicators are used and why?" --show-evidence
msf-trader ask "When should I avoid trading?"
```

Answers cite `[file @ mm:ss]` and flag each rule as EXPLICIT or INFERRED. If the
evidence doesn't cover the question, the assistant says so instead of guessing.

Retrieval is hybrid: BM25 (keyword) fused with OpenAI embeddings (semantic) via
reciprocal-rank fusion for better recall on paraphrased questions. Tune
`qa.top_k` and `retrieval.*` in `config.yaml`; set `retrieval.use_embeddings:
false` (or use the Anthropic provider) to fall back to BM25-only.

## Backtest (Phase 2)

Simulation only — no orders are ever placed, and the strategy is **not assumed
profitable**. The engine executes the human-reviewed rules in
`docs/STRATEGY_RULES_REVIEWED.md`. Parameters the course never specified
(session clock times, S/R definition, runner trail, costs) are explicit,
tunable assumptions in `backtest/spec.py` and are printed with every run.

```bash
# 1a) prototype bars (free yfinance, ~60d of /ES resampled to 10m)
msf-trader fetch-data --period 60d

# 1b) REAL /ES futures (overnight session intact) via Databento, any timeframe.
#     Needs DATABENTO_API_KEY (free signup credit covers it).
msf-trader fetch-es --timeframe 10 --months 24      # -> data/market/ES_10m.csv

# 1c) BIGGER free sample: ~2 years of SPY 10-min via Alpaca (free IEX feed), read
#     as a /ES-equivalent (SPY x10). Needs APCA_API_KEY_ID + APCA_API_SECRET_KEY.
msf-trader fetch-alpaca --months 24 --out data/market/SPY_es_equiv_10m.csv

# 1c) bring your own vendor CSV (any schema) and normalize it
msf-trader normalize-data path/to/vendor.csv --out data/market/ES_10m.csv --resample 10 --rth-only

# 2) run the backtest (use --data to point at any of the above)
msf-trader backtest --show-trades

# 3) stress-test the assumptions (parameter sweep)
msf-trader sweep

# 4) cross-instrument robustness gate: same strategy on /ES vs SPYx10 should AGREE
msf-trader compare-data --a data/market/ES_10m.csv --b data/market/SPY_es_equiv_10m.csv
```

The `sweep` command re-runs the backtest across a grid of the assumption
parameters (S/R strength, proximity, trade size, runner trail). If net P&L flips
sign across the grid, the "edge" is in the assumptions, not the course — treat
the baseline number with suspicion.

`fetch-data` is for wiring/sanity only (Yahoo intraday is capped at ~60 days and
is not a clean continuous contract). For a sample big enough to actually evaluate
the strategy, use `fetch-spy` (Alpha Vantage, ~2 years) or a vendor CSV
(`timestamp,open,high,low,close,volume`) via `normalize-data`; see
`docs/BACKTEST_PLAN.md` for sources and the SPY⇄/ES contract math. Because SPY and
/ES proxy the same index, comparing the backtest on both is a useful robustness
check (they should agree if the edge is real). Market data lands in
`data/market/` and stays gitignored.

## Profitable strategy: RSI(2) swing (Phase 3)

The course's intraday method showed **no durable edge** on /ES or any equity/ETF,
and a rigorous cross-family search (opening-range breakout, intraday momentum,
VWAP mean-reversion) found nothing that survives realistic costs + execution on
full-volume data. The one strategy that *did* validate out-of-sample is a
**multi-day swing**: RSI(2) mean-reversion on liquid ETFs. See
`docs/SWING_STRATEGY.md` and `docs/EQUITY_STRATEGY_RESEARCH.md`.

```bash
# 1) fetch ~20y split/div-adjusted daily bars for the ETF universe
msf-trader fetch-daily

# 2) re-validate (Sharpe ~0.7 both halves, +20/23 years, maxDD ~-20%)
msf-trader swing-backtest

# 3) today's order plan: print only | local sim | real Alpaca paper account
msf-trader swing-plan --broker dry
msf-trader swing-plan --broker paper
msf-trader swing-plan --broker alpaca   # needs ALPACA_PAPER_KEY_ID/SECRET in .env
```

Live execution targets **Robinhood Agentic Trading (MCP)** — an AI agent with the
Robinhood Trading MCP connected reads the daily plan and places orders via
`review_equity_order` → `place_equity_order`. `RobinhoodMCPBroker` maps the actions
onto those tools; `PaperBroker` runs the identical loop locally. Research only —
not investment advice; paper-trade first.

## Deploy (daily paper run on an always-on machine, e.g. a Mac Mini)

```bash
# 1) clone + one-time setup (creates venv, installs, runs tests)
git clone <your-repo-url> msf-trader && cd msf-trader
./scripts/setup.sh                      # then fill .env with ALPACA_PAPER_* and re-run

# 2) verify (places nothing), then test the live paper path during market hours
msf-trader swing-plan --broker dry
msf-trader swing-plan --broker alpaca --dollars 1000 --slots 10

# 3) schedule Mon-Fri ~15:45 ET via launchd (edit paths/time in the plist first)
cp deploy/com.msf.swing.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.msf.swing.plist
launchctl start com.msf.swing       # manual test trigger
```

`scripts/run_swing.sh` is the scheduled wrapper (activates the venv, runs the Alpaca
paper plan, appends to `logs/`). The CLI's market-clock guard skips closed days, so
over-scheduling is safe. **Timezone note:** launchd fires on the machine's *local*
time — set the plist Hour/Minute to whatever 15:45 ET is locally, or set the machine
to `America/New_York`. Run from the repo root so `data/alpaca_paper_state.json` (the
time-stop counter) persists.

## Data layout

```
data/
  videos/                 # your input .mp4 files
  work/<video_id>/
    audio.wav
    transcript.json
    frames/*.jpg
    frames.json
    visual_notes.json
    notes.json            # per-video cited KB items
  kb/kb.jsonl             # aggregated knowledge base
  index/corpus.json       # retrieval corpus (BM25 rebuilt in memory)
  index/embeddings.npy    # semantic vectors for hybrid retrieval (OpenAI)
docs/                     # generated markdown (gitignored — regenerated locally)
```

All of `data/` and `docs/` is gitignored and stays on your machine (see Privacy).

## Project structure

```
src/msf_trader/
  config.py               # config.yaml + .env loading
  catalog.py              # discover videos, map to modules/chapters
  cli.py                  # Typer CLI
  pipeline.py             # resumable orchestration
  ingest/                 # audio, transcribe, frames, ocr
  analysis/               # schema, llm wrapper, vision, KB synthesis (notes)
  knowledge/              # store (JSON), index (hybrid BM25 + embeddings)
  docgen/                 # the six document generators
  qa/                     # cited Q&A
  backtest/               # spec, data loader, indicators, bar-by-bar engine
```

## Privacy — what is and isn't in git

This repo is designed so that **no course material, extracted data, or
identifying details ever reach GitHub**. Only the source code and sanitized
templates are tracked.

Committed (safe):

- `src/**`, `README.md`, `pyproject.toml`, `requirements.txt`
- `.env.example` (no keys) and `config.example.yaml` (generic placeholders, no
  real filenames)

Never committed (gitignored — stays on your machine):

- `data/` — your videos, extracted audio, frames, transcripts, OCR text, the
  knowledge base, and the embeddings index
- `docs/` — the generated documents (they quote course content + filenames)
- `config.yaml` — your real config, which lists your actual `.mp4` filenames
- `.env` and any `*.key` — your API keys
- All media/artifact types by extension as a backstop
  (`*.mp4 *.mov *.wav *.mp3 *.jpg *.png *.npy`, `transcript.json`, `kb.jsonl`, …)

Notes:

- Verify before your first push: `git status --porcelain` should list only the
  safe files above. You can also run `git check-ignore -v data docs config.yaml`
  to confirm those paths are ignored.
- The blanket image ignore means any intentional asset (e.g. a logo) needs
  `git add -f` to be tracked.
- Because the real `config.yaml` is ignored, copy it from the template on each
  machine: `cp config.example.yaml config.yaml`.

## Roadmap (later)

- Optional fully-offline mode (local vision model via Ollama) for a self-hosted
  Mac Mini deploy.
- A real backtester module (`src/msf_trader/backtest/`) that reads
  `STRATEGY_RULES.md`. Not built in v1.
