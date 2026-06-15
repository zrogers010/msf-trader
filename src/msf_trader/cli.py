"""Command-line interface for the course-ingestion system.

Examples:
  msf-trader catalog                 # show discovered videos + module mapping
  msf-trader run-all                 # full pipeline + docs
  msf-trader transcribe              # only audio + transcription
  msf-trader docs                    # regenerate the six docs from existing KB
  msf-trader ask "What are the exact entry rules?"
"""
from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from . import pipeline
from .catalog import build_catalog
from .config import load_config
from .knowledge.store import Store

app = typer.Typer(add_completion=False, help="Ingest trading-course videos into a cited knowledge base.")
console = Console()

ConfigOpt = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml")


@app.command()
def catalog(config: str = ConfigOpt):
    """Show discovered .mp4 files and their module / chapter mapping."""
    cfg = load_config(config)
    items = build_catalog(cfg)
    if not items:
        console.print(f"[yellow]No .mp4 files in {cfg.paths.videos_dir}[/yellow]")
        raise typer.Exit()
    table = Table(title="Course catalog")
    table.add_column("Ch")
    table.add_column("Module")
    table.add_column("File")
    table.add_column("video_id")
    for v in items:
        table.add_row(str(v.chapter_order), v.module, v.filename, v.video_id)
    console.print(table)


@app.command(name="run-all")
def run_all(
    config: str = ConfigOpt,
    overwrite: bool = typer.Option(False, "--overwrite", help="Recompute cached stages"),
    no_docs: bool = typer.Option(False, "--no-docs", help="Skip doc generation"),
):
    """Run the full pipeline: ingest -> transcribe -> frames -> vision -> KB -> index -> docs."""
    cfg = load_config(config)
    pipeline.run_all(cfg, overwrite=overwrite, build_docs=not no_docs)


def _run_stages(config: str, stages: tuple[str, ...], overwrite: bool):
    cfg = load_config(config)
    pipeline.run_all(cfg, stages=stages, overwrite=overwrite, build_docs=False)


@app.command()
def transcribe(config: str = ConfigOpt, overwrite: bool = typer.Option(False, "--overwrite")):
    """Extract audio and transcribe all videos (local Whisper)."""
    _run_stages(config, ("audio", "transcribe"), overwrite)


@app.command()
def frames(config: str = ConfigOpt, overwrite: bool = typer.Option(False, "--overwrite")):
    """Extract representative frames and run OCR."""
    _run_stages(config, ("frames",), overwrite)


@app.command()
def vision(config: str = ConfigOpt, overwrite: bool = typer.Option(False, "--overwrite")):
    """Run vision-LLM analysis on selected frames (requires API key)."""
    _run_stages(config, ("vision",), overwrite)


@app.command(name="build-kb")
def build_kb(config: str = ConfigOpt, overwrite: bool = typer.Option(False, "--overwrite")):
    """Synthesize cited KB items per video and aggregate them."""
    cfg = load_config(config)
    cfg.ensure_dirs()
    store = Store(cfg)
    catalog_items = build_catalog(cfg)
    from .analysis.llm import LLMClient

    llm = LLMClient(cfg)
    for meta in catalog_items:
        pipeline.process_video(meta, cfg, store, llm, stages=("notes",), overwrite=overwrite)
    pipeline.aggregate_kb(cfg, store, catalog_items)
    from .knowledge.index import build_index

    build_index(cfg, store, catalog_items)
    console.print("KB and index updated.")


@app.command()
def index(config: str = ConfigOpt):
    """(Re)build the BM25 retrieval index from existing KB + transcripts."""
    cfg = load_config(config)
    store = Store(cfg)
    catalog_items = build_catalog(cfg)
    from .knowledge.index import build_index

    path = build_index(cfg, store, catalog_items)
    console.print(f"Index written: {path}")


@app.command()
def docs(config: str = ConfigOpt):
    """Regenerate the six strategy docs from the existing KB."""
    cfg = load_config(config)
    store = Store(cfg)
    catalog_items = build_catalog(cfg)
    from .docgen.generators import generate_all_docs

    written = generate_all_docs(cfg, store, catalog_items)
    for name, p in written.items():
        console.print(f"  {name}: {p}")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Your question about the course"),
    config: str = ConfigOpt,
    show_evidence: bool = typer.Option(False, "--show-evidence", help="Print retrieved snippets"),
):
    """Ask a question and get an answer with timestamp citations."""
    cfg = load_config(config)
    from .qa.ask import answer_question

    ans = answer_question(question, cfg)
    console.print(Markdown(ans.text))
    if show_evidence:
        console.print("\n[dim]--- evidence ---[/dim]")
        for e in ans.evidence:
            console.print(f"[dim]{e['citation']} ({e.get('kind')}): {e['text'][:160]}[/dim]")


@app.command(name="fetch-data")
def fetch_data(
    out: str = typer.Option("data/market/ES_10m.csv", "--out", help="Output CSV path"),
    period: str = typer.Option("60d", "--period", help="History window (e.g. 30d, 60d)"),
    base_interval: str = typer.Option("5m", "--interval", help="Source interval to resample from"),
    symbol: str = typer.Option("ES=F", "--symbol", help="Yahoo symbol (e.g. ES=F, SPY)"),
    scale: float = typer.Option(1.0, "--scale", help="Multiply OHLC (use 10 for SPY -> /ES-equivalent)"),
):
    """Prototype only: fetch intraday bars via yfinance and resample to 10m."""
    from .backtest.fetch_yf import fetch_es_csv

    p = fetch_es_csv(out, period=period, base_interval=base_interval, target_minutes=10,
                     symbol=symbol, price_scale=scale)
    console.print("[yellow]Prototype data (yfinance, not for production research).[/yellow]")
    console.print(
        "[dim]yfinance caps sub-hourly history at ~60 days. For a larger free sample use "
        "`fetch-spy` (Alpha Vantage, ~2 years), or a vendor + `normalize-data` "
        "(see docs/BACKTEST_PLAN.md).[/dim]"
    )
    console.print(f"Wrote bars -> {p}")


@app.command(name="fetch-spy")
def fetch_spy(
    out: str = typer.Option("data/market/SPY_es_equiv_10m.csv", "--out", help="Output CSV path"),
    months: int = typer.Option(24, "--months", help="How many months back (~2 yrs free)"),
    interval: str = typer.Option("5min", "--interval", help="Alpha Vantage base interval"),
    scale: float = typer.Option(10.0, "--scale", help="SPY x10 ~= S&P index ~= /ES chart"),
    extended_hours: bool = typer.Option(False, "--extended-hours", help="Include pre/post market"),
    sleep: float = typer.Option(15.0, "--sleep", help="Seconds between month requests (free-tier rate limit)"),
):
    """Fetch multi-year SPY intraday via Alpha Vantage, scaled to a /ES-equivalent chart.

    Needs ALPHAVANTAGE_API_KEY in .env (free key). Leans on the course premise
    that 'all charts act and react the same way': SPY x10 ~= the S&P index ~= /ES,
    so the point-based spec stays valid. Simulation/research data only.
    """
    from .backtest.fetch_av import fetch_av_csv

    console.print(
        f"[dim]Fetching ~{months} months of SPY {interval} from Alpha Vantage "
        f"(~{sleep:.0f}s between months for the free-tier rate limit)...[/dim]"
    )
    try:
        p = fetch_av_csv(out, symbol="SPY", months=months, interval=interval,
                         target_minutes=10, price_scale=scale, extended_hours=extended_hours,
                         sleep_seconds=sleep)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print("[yellow]Research data only. SPY read as a /ES-equivalent (x10).[/yellow]")
    console.print(f"Wrote bars -> {p}")


@app.command(name="fetch-es")
def fetch_es(
    out: str = typer.Option("", "--out", help="Output CSV path (default: data/market/ES_<tf>m.csv)"),
    timeframe: int = typer.Option(10, "--timeframe", help="Bar size in minutes (e.g. 5, 10, 15, 30)"),
    months: int = typer.Option(24, "--months", help="How many months of history"),
    symbol: str = typer.Option("ES.c.0", "--symbol", help="Databento continuous symbol"),
):
    """Fetch REAL /ES futures bars from Databento (continuous front-month, overnight
    session intact). Needs DATABENTO_API_KEY in .env. Research data only."""
    from .backtest.fetch_databento import fetch_es_databento

    out = out or f"data/market/ES_{timeframe}m.csv"
    console.print(f"[dim]Fetching ~{months} months of real /ES {timeframe}-min from Databento ({symbol})...[/dim]")
    try:
        p = fetch_es_databento(out, symbol=symbol, months=months, target_minutes=timeframe)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Wrote real /ES bars -> {p}")


@app.command(name="fetch-alpaca")
def fetch_alpaca(
    out: str = typer.Option("data/market/SPY_es_equiv_10m.csv", "--out", help="Output CSV path"),
    symbol: str = typer.Option("SPY", "--symbol", help="Equity symbol to read as /ES-equivalent"),
    months: int = typer.Option(24, "--months", help="How many months of history"),
    scale: float = typer.Option(10.0, "--scale", help="SPY x10 ~= S&P index ~= /ES chart"),
    feed: str = typer.Option("iex", "--feed", help="iex (free) or sip (paid)"),
    adjustment: str = typer.Option("all", "--adjustment", help="raw|split|dividend|all"),
):
    """Fetch multi-year SPY intraday from Alpaca (free IEX feed), scaled to a
    /ES-equivalent chart. Needs APCA_API_KEY_ID + APCA_API_SECRET_KEY in .env.

    Caveats: IEX feed = partial volume; SPY is RTH-only (no overnight session),
    so gap/prior-close levels differ from real /ES. Research data only.
    """
    from .backtest.fetch_alpaca import fetch_alpaca_csv

    console.print(f"[dim]Fetching ~{months} months of {symbol} {10}-min bars from Alpaca ({feed})...[/dim]")
    try:
        p = fetch_alpaca_csv(out, symbol=symbol, months=months, target_minutes=10,
                             price_scale=scale, feed=feed, adjustment=adjustment)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print("[yellow]Research data only. SPY read as a /ES-equivalent (x10), IEX volume is partial.[/yellow]")
    console.print(f"Wrote bars -> {p}")


@app.command(name="normalize-data")
def normalize_data(
    src: str = typer.Argument(..., help="Vendor OHLCV CSV to import"),
    out: str = typer.Option("data/market/ES_10m.csv", "--out", help="Output canonical CSV"),
    resample: int = typer.Option(0, "--resample", help="Resample to N minutes (e.g. 1-min -> 10); 0 = keep as-is"),
    rth_only: bool = typer.Option(False, "--rth-only", help="Keep only Regular Trading Hours bars"),
):
    """Convert an arbitrary vendor CSV into the canonical timestamp,OHLCV schema.

    Tolerates timestamp/datetime or date(+time) columns, epoch timestamps, and
    OHLCV aliases. Use this to drop in real /ES history from a data vendor.
    """
    from .backtest.spec import StrategySpec
    from .backtest.data import normalize_csv

    spec = StrategySpec()
    p = normalize_csv(src, out, spec, resample_minutes=(resample or None), rth_only=rth_only)
    console.print(f"Normalized {src} -> {p}")


@app.command()
def backtest(
    data: str = typer.Option("data/market/ES_10m.csv", "--data", help="Bar CSV path"),
    show_trades: bool = typer.Option(False, "--show-trades", help="Print each trade"),
):
    """Run the bar-by-bar backtest of the reviewed strategy. Simulation only."""
    from .backtest.spec import StrategySpec, assumptions
    from .backtest.data import load_bars_csv
    from .backtest.engine import run_backtest

    spec = StrategySpec()
    bars = load_bars_csv(data, spec)
    result = run_backtest(bars, spec)
    m = result.metrics()

    console.print(
        "[bold red]DISCLAIMER:[/bold red] research simulation only. The strategy is "
        "NOT validated or assumed profitable. Results use limited prototype data and "
        "the documented assumptions below."
    )
    table = Table(title=f"Backtest: {len(bars)} bars, {m['trades']} trades")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Net P&L ($)", f"{m['net_pnl']:.2f}")
    table.add_row("Win rate", f"{m['win_rate']*100:.1f}%")
    table.add_row("Wins / Losses", f"{m['wins']} / {m['losses']}")
    table.add_row("Profit factor", f"{m['profit_factor']:.2f}")
    table.add_row("Avg win / loss ($)", f"{m['avg_win']:.0f} / {m['avg_loss']:.0f}")
    table.add_row("Max drawdown ($)", f"{m['max_drawdown']:.2f}")
    console.print(table)

    console.print("[dim]Assumptions baked into this run (not course-confirmed):[/dim]")
    for a in assumptions():
        console.print(f"[dim]  - {a}[/dim]")

    if show_trades:
        for t in result.trades:
            console.print(
                f"  {t.direction:5s} {t.entry_ts.strftime('%Y-%m-%d %H:%M')} @ {t.entry_price:.2f} "
                f"-> {t.exits[-1].reason:11s} ${t.pnl_dollars:.2f}"
            )


@app.command(name="compare-data")
def compare_data(
    a: str = typer.Option("data/market/ES_10m.csv", "--a", help="First bar CSV (e.g. /ES)"),
    b: str = typer.Option("data/market/SPY_es_equiv_10m.csv", "--b", help="Second bar CSV (e.g. SPYx10)"),
    label_a: str = typer.Option("ES", "--label-a"),
    label_b: str = typer.Option("SPYx10", "--label-b"),
):
    """Cross-instrument robustness gate: run the same strategy on two proxies for
    the same market (e.g. /ES vs SPYx10). A real edge should AGREE on both; a
    sign flip means the result is sample noise, not edge. Simulation only."""
    from .backtest.spec import StrategySpec
    from .backtest.data import load_bars_csv
    from .backtest.engine import run_backtest

    spec = StrategySpec()
    results = {}
    for label, path in ((label_a, a), (label_b, b)):
        bars = load_bars_csv(path, spec)
        m = run_backtest(bars, spec).metrics()
        results[label] = (len(bars), m)

    table = Table(title="Cross-instrument agreement (same spec, both proxies)")
    table.add_column("metric")
    for label in (label_a, label_b):
        table.add_column(label, justify="right")
    ma, mb = results[label_a][1], results[label_b][1]
    rows = [
        ("bars", lambda L: f"{results[L][0]}"),
        ("trades", lambda L: f"{results[L][1]['trades']}"),
        ("win rate", lambda L: f"{results[L][1]['win_rate']*100:.0f}%"),
        ("net $", lambda L: f"{results[L][1]['net_pnl']:.0f}"),
        ("profit factor", lambda L: (f"{results[L][1]['profit_factor']:.2f}" if results[L][1]['profit_factor'] != float('inf') else "inf")),
        ("max DD $", lambda L: f"{results[L][1]['max_drawdown']:.0f}"),
    ]
    for name, fn in rows:
        table.add_row(name, fn(label_a), fn(label_b))
    console.print(table)

    same_sign = (ma["net_pnl"] > 0) == (mb["net_pnl"] > 0)
    same_pf_side = (ma["profit_factor"] >= 1) == (mb["profit_factor"] >= 1)
    if same_sign and same_pf_side:
        console.print("[green]AGREE[/green]: both proxies point the same way "
                      "(necessary, not sufficient, for a real edge).")
    else:
        console.print("[red]DISAGREE[/red]: the two proxies of the SAME market "
                      "diverge -> the result is fragile / sample noise. Do not trust it; "
                      "get a larger sample before drawing conclusions.")


@app.command()
def sweep(
    data: str = typer.Option("data/market/ES_10m.csv", "--data", help="Bar CSV path"),
    top: int = typer.Option(10, "--top", help="Show this many best/worst combos"),
):
    """Sweep the ASSUMPTION parameters and report how fragile the results are."""
    import statistics as st

    from .backtest.spec import StrategySpec
    from .backtest.data import load_bars_csv
    from .backtest.sweep import sweep as run_sweep, DEFAULT_GRID

    spec = StrategySpec()
    bars = load_bars_csv(data, spec)
    rows = run_sweep(bars, spec, DEFAULT_GRID)
    nets = [r["net_pnl"] for r in rows]

    console.print(
        "[bold red]Simulation only.[/bold red] A robust edge should survive these "
        "assumption changes; a result that flips sign is fragile."
    )
    console.print(
        f"[bold]{len(rows)} combos[/bold] · profitable {100*sum(n>0 for n in nets)/len(nets):.0f}% · "
        f"median {st.median(nets):.0f} · range {min(nets):.0f}..{max(nets):.0f}"
    )

    ranked = sorted(rows, key=lambda r: r["net_pnl"], reverse=True)
    grid_keys = list(DEFAULT_GRID)
    table = Table(title=f"Top / bottom {top} parameter combinations")
    table.add_column("net $", justify="right")
    table.add_column("PF", justify="right")
    table.add_column("trades", justify="right")
    table.add_column("win%", justify="right")
    for k in grid_keys:
        table.add_column(k)
    for r in ranked[:top] + ranked[-top:]:
        table.add_row(
            f"{r['net_pnl']:.0f}",
            (f"{r['profit_factor']:.2f}" if r["profit_factor"] is not None else "inf"),
            f"{r['trades']}",
            f"{r['win_rate']*100:.0f}",
            *[f"{r[k]}" for k in grid_keys],
        )
    console.print(table)


@app.command(name="fetch-daily")
def fetch_daily_cmd(
    symbols: str = typer.Option(
        "", "--symbols", help="Comma-separated tickers (default: the strategy universe)"
    ),
    out_dir: str = typer.Option("data/market", "--out-dir", help="Where to write {SYM}_daily.csv"),
    start: str = typer.Option("2004-01-01", "--start", help="History start date"),
):
    """Fetch split/dividend-adjusted DAILY bars (yfinance) for the swing strategy."""
    import yfinance as yf
    from pathlib import Path as _Path

    from .swing import DEFAULT_UNIVERSE

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] or DEFAULT_UNIVERSE
    _Path(out_dir).mkdir(parents=True, exist_ok=True)
    for s in syms:
        df = yf.download(s, start=start, auto_adjust=True, progress=False)
        if df is None or len(df) == 0:
            console.print(f"[yellow]{s}: no data[/yellow]")
            continue
        df = df.reset_index()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                "Low": "low", "Close": "close", "Volume": "volume"})
        df[["date", "open", "high", "low", "close", "volume"]].to_csv(f"{out_dir}/{s}_daily.csv", index=False)
        console.print(f"{s}: {len(df)} rows -> {out_dir}/{s}_daily.csv")


@app.command(name="swing-backtest")
def swing_backtest(
    data_dir: str = typer.Option("data/market", "--data-dir", help="Dir with {SYM}_daily.csv"),
    rsi_buy: float = typer.Option(10.0, "--rsi-buy", help="Enter when RSI(2) < this"),
    no_regime: bool = typer.Option(False, "--no-regime", help="Disable the 200-SMA filter"),
):
    """Backtest the RSI(2) swing portfolio over the daily-bar universe."""
    from .swing import Rsi2Params, portfolio_backtest

    p = Rsi2Params(rsi_buy=rsi_buy)
    res = portfolio_backtest(data_dir=data_dir, p=p, regime=not no_regime)
    console.print(
        "[bold red]Research backtest only.[/bold red] Past performance is not indicative "
        "of future results; forward/paper-test before risking capital."
    )
    table = Table(title="RSI(2) swing portfolio")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("CAGR", f"{res.cagr*100:.1f}%")
    table.add_row("Sharpe (ann.)", f"{res.sharpe:.2f}")
    table.add_row("Max drawdown", f"{res.max_drawdown*100:.1f}%")
    table.add_row("Time invested", f"{res.exposure*100:.0f}%")
    table.add_row("Return on deployed", f"{res.return_on_deployed*100:.0f}%")
    console.print(table)
    pos = sum(1 for v in res.by_year.values() if v > 0)
    console.print(f"[dim]Positive in {pos}/{len(res.by_year)} calendar years.[/dim]")


@app.command(name="swing-plan")
def swing_plan(
    broker: str = typer.Option("paper", "--broker", help="Execution venue: 'dry' (print only), 'paper' (local sim), or 'alpaca' (real Alpaca paper account)"),
    offline: bool = typer.Option(False, "--offline", help="Use cached daily CSVs instead of fetching"),
    target_positions: int = typer.Option(6, "--slots", help="Concurrent position slots"),
    max_weight: float = typer.Option(0.167, "--max-weight", help="Per-name cap as a fraction of equity (e.g. 0.167 ~= 6 names)"),
    dollars: float = typer.Option(0.0, "--dollars", help="Fixed $ per trade instead of %-of-equity (e.g. 1000). 0 = use --max-weight"),
    rsi_buy: float = typer.Option(10.0, "--rsi-buy"),
    state: str = typer.Option("data/paper_account.json", "--state", help="Local paper account state file"),
    force: bool = typer.Option(False, "--force", help="Place Alpaca orders even when the market is closed (overrides the clock guard)"),
):
    """Compute (and optionally execute) today's swing order plan.

    --broker alpaca routes to Alpaca's real paper account (needs ALPACA_PAPER_KEY_ID /
    ALPACA_PAPER_SECRET_KEY in .env). --broker paper is a zero-setup local simulation.
    For LIVE Robinhood trading, run --broker dry, then have your agent (with the
    Robinhood Trading MCP connected) place each order via review_equity_order ->
    place_equity_order. See docs/SWING_STRATEGY.md."""
    from .swing import Rsi2Params, run_daily

    broker = broker.lower()
    if broker not in ("dry", "paper", "alpaca"):
        raise typer.BadParameter("--broker must be one of: dry, paper, alpaca")

    market_closed = False
    if broker == "alpaca":
        from .swing.broker import AlpacaPaperBroker
        market_closed = not AlpacaPaperBroker().is_market_open() and not force

    plan = run_daily(
        params=Rsi2Params(rsi_buy=rsi_buy, max_weight=max_weight),
        target_positions=target_positions,
        broker=broker,
        offline=offline,
        state_path=state,
        force=force,
        fixed_notional=(dollars if dollars > 0 else None),
    )
    console.print(
        "[bold red]Not investment advice.[/bold red] Review every order before any live execution."
    )
    if not plan:
        console.print("[yellow]No orders today — no oversold setups / no exits triggered.[/yellow]")
        return
    if market_closed:
        mode = "ALPACA — MARKET CLOSED (not placed; use --force to override)"
    else:
        mode = {"dry": "DRY-RUN (not placed)", "paper": "LOCAL PAPER-EXECUTED",
                "alpaca": "ALPACA PAPER-EXECUTED"}[broker]
    table = Table(title=f"Today's swing plan — {mode}")
    table.add_column("Action")
    table.add_column("Symbol")
    table.add_column("Size", justify="right")
    table.add_column("~Price", justify="right")
    table.add_column("Reason")
    for o in plan:
        size = f"${o.notional:,.0f}" if o.action == "BUY" else f"{o.quantity:g} sh"
        table.add_row(o.action, o.symbol, size, f"{o.price:.2f}", o.reason)
    console.print(table)


if __name__ == "__main__":
    app()
