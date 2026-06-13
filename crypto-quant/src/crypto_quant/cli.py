"""Command-line interface for fetching open-source crypto market data."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import click
import pandas as pd

from crypto_quant.config import data_dir, load_config
from crypto_quant.data import (
    BinanceVisionFetcher,
    CCXTFetcher,
    CoinGeckoFetcher,
    DuneClient,
    save_dataframe,
)
from crypto_quant.backtest import (
    BacktestConfig,
    format_batch_report,
    format_report,
    run_backtest,
    run_batch,
)
from crypto_quant.data.storage import load_dataframe
from crypto_quant.pairs import PairResearchConfig, format_pair_report, rank_pairs
from crypto_quant.pool import PoolConfig, build_pool
from crypto_quant.scan import scan_exchanges
from crypto_quant.scan.perp_scanner import ScanConfig, format_scan_table
from crypto_quant.onchain.netflow import (
    DuneFilterConfig,
    ensure_netflow_for_symbol,
    symbol_base,
)
from crypto_quant.strategy import (
    CostZoneConfig,
    FundingCarryConfig,
    IgnitionConfig,
    IgnitionStrategy,
    MeanReversionConfig,
)


@click.group()
@click.option("--config", "config_path", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def main(ctx: click.Context, config_path: Path | None) -> None:
    """crypto-quant — extract raw trading data from open sources."""
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = load_config(config_path)
    ctx.obj["out"] = data_dir(ctx.obj["cfg"])


@main.command("fetch-ccxt")
@click.option("--exchange", default=None, help="CCXT exchange id (default: binance)")
@click.option("--symbol", multiple=True, help="Trading pair, e.g. BTC/USDT")
@click.option("--timeframe", default=None, help="Candle interval, e.g. 1h")
@click.option("--days", default=7, show_default=True, help="Lookback days")
@click.option("--format", "fmt", type=click.Choice(["parquet", "csv"]), default="parquet")
@click.pass_context
def fetch_ccxt(
    ctx: click.Context,
    exchange: str | None,
    symbol: tuple[str, ...],
    timeframe: str | None,
    days: int,
    fmt: str,
) -> None:
    """Fetch OHLCV via CCXT (live exchange public APIs)."""
    cfg = ctx.obj["cfg"]["ccxt"]
    exchange = exchange or cfg["exchange"]
    symbols = list(symbol) or cfg["default_symbols"]
    timeframe = timeframe or cfg["default_timeframe"]
    out_root: Path = ctx.obj["out"] / "ccxt" / exchange

    since = datetime.now(timezone.utc) - timedelta(days=days)
    fetcher = CCXTFetcher(exchange, rate_limit_ms=cfg.get("rate_limit_ms", 200))

    for sym in symbols:
        click.echo(f"Fetching {sym} {timeframe} from {exchange}...")
        df = fetcher.fetch_ohlcv(sym, timeframe, since=since)
        safe = sym.replace("/", "_")
        path = out_root / timeframe / f"{safe}.{fmt}"
        save_dataframe(df, path, format=fmt)
        click.echo(f"  -> {len(df)} rows saved to {path}")


@main.command("fetch-binance-vision")
@click.option("--symbol", multiple=True, help="e.g. BTCUSDT (no slash)")
@click.option("--interval", default=None)
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), default=None)
@click.option("--market", type=click.Choice(["spot", "um", "cm"]), default=None)
@click.option("--format", "fmt", type=click.Choice(["parquet", "csv"]), default="parquet")
@click.pass_context
def fetch_binance_vision(
    ctx: click.Context,
    symbol: tuple[str, ...],
    interval: str | None,
    start: datetime,
    end: datetime | None,
    market: str | None,
    fmt: str,
) -> None:
    """Bulk download klines from data.binance.vision (official public archive)."""
    cfg = ctx.obj["cfg"]["binance_vision"]
    symbols = list(symbol) or cfg["symbols"]
    interval = interval or cfg["interval"]
    market = market or cfg["market"]
    end_d = (end or datetime.now(timezone.utc)).date()
    start_d = start.date()
    out_root: Path = ctx.obj["out"] / "binance_vision" / market
    cache = Path(ctx.obj["cfg"].get("cache_dir", "caches")) / "binance_vision"
    if not cache.is_absolute():
        from crypto_quant.config import repo_root
        cache = repo_root() / cache

    with BinanceVisionFetcher() as fetcher:
        for sym in symbols:
            click.echo(f"Downloading {sym} {interval} [{start_d} .. {end_d}]...")
            df = fetcher.fetch_klines_range(
                sym, interval, start_d, end_d, market=market, cache_dir=cache
            )
            path = out_root / interval / f"{sym}.{fmt}"
            save_dataframe(df, path, format=fmt)
            click.echo(f"  -> {len(df)} rows saved to {path}")


@main.command("fetch-coingecko")
@click.option("--coin-id", multiple=True, help="e.g. bitcoin")
@click.option("--days", default=30, show_default=True)
@click.option("--ohlc/--chart", default=False, help="Use OHLC endpoint instead of price chart")
@click.option("--format", "fmt", type=click.Choice(["parquet", "csv"]), default="parquet")
@click.pass_context
def fetch_coingecko(
    ctx: click.Context,
    coin_id: tuple[str, ...],
    days: int,
    ohlc: bool,
    fmt: str,
) -> None:
    """Fetch aggregated market data from CoinGecko public API."""
    cfg = ctx.obj["cfg"]["coingecko"]
    coins = list(coin_id) or cfg["coin_ids"]
    vs = cfg["vs_currency"]
    out_root: Path = ctx.obj["out"] / "coingecko"

    with CoinGeckoFetcher() as fetcher:
        for cid in coins:
            click.echo(f"Fetching {cid} ({days}d)...")
            if ohlc:
                df = fetcher.ohlc(cid, vs, days=min(days, 90))
                sub = "ohlc"
            else:
                df = fetcher.market_chart(cid, vs, days=days)
                sub = "market_chart"
            path = out_root / sub / f"{cid}.{fmt}"
            save_dataframe(df, path, format=fmt)
            click.echo(f"  -> {len(df)} rows saved to {path}")


@main.command("scan-perps")
@click.option(
    "--exchange",
    multiple=True,
    help="CCXT id: binanceusdm, bybit, okx, gate, bitget",
)
@click.option("--top", default=20, show_default=True, help="Rows per exchange")
@click.option("--min-volume", default=500_000, show_default=True, help="Min 24h quote volume USD")
@click.option("--save", is_flag=True, help="Write scan parquet under data/scans/")
@click.pass_context
def scan_perps(
    ctx: click.Context,
    exchange: tuple[str, ...],
    top: int,
    min_volume: float,
    save: bool,
) -> None:
    """Scan small-cap USDT perpetuals (like whale-follow dashboards, exchange data only)."""
    cfg = ScanConfig(min_quote_volume_usd=min_volume, top_n=top)
    ids = list(exchange) if exchange else None
    df = scan_exchanges(ids, cfg=cfg)
    click.echo(format_scan_table(df))
    if save and not df.empty:
        out: Path = ctx.obj["out"] / "scans"
        out.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        path = out / f"perp_scan_{ts}.parquet"
        save_dataframe(df, path)
        click.echo(f"\nSaved {path}")


from crypto_quant.data.sync import (
    SyncConfig,
    ensure_ohlcv,
    ohlcv_cache_path,
    resolve_pool_symbols,
    resolve_token_map_symbols,
    sync_symbols,
)


def _dune_rules(sc: dict) -> str:
    d = sc.get("dune", {})
    if not d.get("enabled", False):
        return "  - (Dune on-chain filter disabled)\n"
    return (
        f"  - Dune: 7d rolling CEX net inflow <= ${d.get('max_rolling_net_inflow_usd', 0):,.0f}\n"
        f"    require_net_outflow={d.get('require_net_outflow', False)} "
        f"(token_map.yaml + DUNE_API_KEY)\n"
    )


def _strategy_cfgs(sc: dict) -> tuple[IgnitionConfig, DuneFilterConfig | None]:
    strat_cfg = IgnitionConfig.from_dict(sc.get("params", {}))
    dune_cfg = DuneFilterConfig.from_dict(sc.get("dune", {}))
    return strat_cfg, dune_cfg if dune_cfg.enabled else None


@main.command("fetch-onchain")
@click.option("--symbol", multiple=True, required=True, help="e.g. PEPE/USDT:USDT")
@click.option("--days", default=None, type=int)
@click.option("--fetch/--use-cache", "force_fetch", default=False)
@click.pass_context
def fetch_onchain_cmd(
    ctx: click.Context,
    symbol: tuple[str, ...],
    days: int | None,
    force_fetch: bool,
) -> None:
    """Fetch Dune daily CEX netflow for symbols in token_map.yaml."""
    sc = ctx.obj["cfg"]["strategy"]
    dune_cfg = DuneFilterConfig.from_dict(sc.get("dune", {}))
    lookback = days or dune_cfg.lookback_days
    out_root: Path = ctx.obj["out"]
    for sym in symbol:
        base = symbol_base(sym)
        click.echo(f"Fetching on-chain netflow for {sym} ({base}, {lookback}d)...")
        df = ensure_netflow_for_symbol(
            sym,
            out_root,
            days=lookback,
            use_cache=not force_fetch,
        )
        if df is None:
            click.echo(f"  -> skip: no entry in config/token_map.yaml for {base}")
            continue
        if df.empty:
            click.echo(f"  -> 0 days (no CEX flow rows in lookback window)")
            continue
        click.echo(f"  -> {len(df)} days, latest net_inflow_usd={df['net_inflow_usd'].iloc[-1]:,.0f}")


@main.command("strategy-rules")
@click.pass_context
def strategy_rules(ctx: click.Context) -> None:
    """Print the default Ignition strategy rules (for learning)."""
    sc = ctx.obj["cfg"].get("strategy", {})
    p = sc.get("params", {})
    htf = p.get("htf_trend_hours") or 0
    max_dist = p.get("max_dist_to_cost_pct") or 0
    vwap_h = p.get("vwap_hours", 168)
    htf_line = (
        f"  - Close > {htf}h SMA (higher-timeframe trend filter)\n"
        if htf
        else "  - (HTF trend filter disabled)\n"
    )
    cost_line = (
        f"  - Distance to {vwap_h}h VWAP <= {max_dist}% (cost-zone filter)\n"
        if max_dist > 0
        else "  - (Cost-zone / VWAP distance filter disabled)\n"
    )
    click.echo(
        f"""
Strategy: Ignition v3 — breakout + trailing trend follow ({sc.get('timeframe', '1h')} bars)
Symbol default: {sc.get('symbol')} on {sc.get('exchange')}

ENTRY (all must be true on bar close, fresh trigger only):
  - Close > prior {p.get('breakout_hours', 48)}h HIGH (Donchian breakout)
  - Volume > {p.get('vol_mult')}x prior {p.get('vol_ma_hours')}h average
{htf_line}{cost_line}  - Cooldown: {p.get('cooldown_bars', 0)} bars after previous exit
{_dune_rules(sc)}
  -> Enter at NEXT bar OPEN with slippage (no lookahead)

EXIT (binding stop = max of these two):
  - Initial stop: entry - {p.get('atr_stop_mult', 2.5)} * ATR{p.get('atr_hours', 24)}
  - Trailing stop: highest_close_since_entry * (1 - {p.get('trail_pct', 8.0)}%)
  - Time stop:     {p.get('max_hold_hours')} bars (backstop only)
  - NO fixed take-profit — trail captures real trend length

Edit params in config/default.yaml under strategy.params
"""
    )


@main.command("sync-data")
@click.option(
    "--source",
    type=click.Choice(["pool", "token-map", "symbols"]),
    default="pool",
    help="pool=latest pool CSV; token-map=mapped EVM symbols; symbols=--symbol list",
)
@click.option("--symbol", "symbols", multiple=True, help="With --source symbols")
@click.option("--exchange", default=None)
@click.option("--days", default=None, type=int)
@click.option("--force", is_flag=True, help="Re-download even if cache is fresh")
@click.pass_context
def sync_data_cmd(
    ctx: click.Context,
    source: str,
    symbols: tuple[str, ...],
    exchange: str | None,
    days: int | None,
    force: bool,
) -> None:
    """Sync OHLCV + Dune netflow to local data/ (run once daily, backtests stay fast)."""
    cfg = ctx.obj["cfg"]
    sync_cfg = SyncConfig.from_cfg(cfg)
    exchange = exchange or sync_cfg.exchange
    lookback = days or sync_cfg.lookback_days
    out_root: Path = ctx.obj["out"]

    if source == "pool":
        syms = resolve_pool_symbols(out_root, exchange)
        if not syms:
            raise click.ClickException(
                "No pool CSV found. Run `cq build-pool` first or use --source symbols."
            )
        click.echo(f"Syncing {len(syms)} symbols from pool ({exchange})...")
    elif source == "token-map":
        syms = resolve_token_map_symbols()
        click.echo(f"Syncing {len(syms)} token_map symbols...")
    else:
        if not symbols:
            sc = cfg.get("strategy", {})
            syms = [sc.get("symbol", "PEPE/USDT:USDT")]
        else:
            syms = list(symbols)
        click.echo(f"Syncing {len(syms)} explicit symbols...")

    result = sync_symbols(
        syms,
        out_root=out_root,
        exchange=exchange,
        timeframe=sync_cfg.timeframe,
        days=lookback,
        dune_days=sync_cfg.dune_lookback_days,
        dune_enabled=sync_cfg.dune_enabled,
        max_stale_hours=sync_cfg.max_stale_hours,
        force=force,
    )
    click.echo(
        f"\nDone: ohlcv fetched={len(result.ohlcv_fetched)} cached={len(result.ohlcv_skipped)} "
        f"| dune fetched={len(result.onchain_fetched)} cached={len(result.onchain_skipped)}"
    )
    if result.errors:
        click.echo(f"Errors ({len(result.errors)}):")
        for e in result.errors[:5]:
            click.echo(f"  - {e}")


@main.command("backtest")
@click.option("--symbol", default=None, help="e.g. WIF/USDT:USDT")
@click.option("--exchange", default=None)
@click.option("--days", default=None, type=int, help="History length (default from config)")
@click.option("--use-cache/--fetch", default=True, help="Use cached parquet if present")
@click.pass_context
def backtest_cmd(
    ctx: click.Context,
    symbol: str | None,
    exchange: str | None,
    days: int | None,
    use_cache: bool,
) -> None:
    """Run Ignition strategy backtest on historical OHLCV (no API key)."""
    sc = ctx.obj["cfg"]["strategy"]
    exchange = exchange or sc["exchange"]
    symbol = symbol or sc["symbol"]
    timeframe = sc["timeframe"]
    lookback = days or sc.get("lookback_days", 90)
    strat_cfg, dune_cfg = _strategy_cfgs(sc)
    bt_cfg = BacktestConfig(**sc.get("backtest", {}))
    out_root: Path = ctx.obj["out"]
    cache_path = ohlcv_cache_path(out_root, exchange, timeframe, symbol)

    sync_cfg = SyncConfig.from_cfg(ctx.obj["cfg"])
    max_stale = sync_cfg.max_stale_hours

    df = None
    if use_cache:
        from crypto_quant.data.sync import cache_status, _timeframe_hours

        min_bars = int(lookback * 24 / _timeframe_hours(timeframe) * 0.9)
        needs, reason = cache_status(
            cache_path, min_bars=min_bars, max_stale_hours=max_stale
        )
        if not needs:
            click.echo(f"Loading cache {cache_path}")
            df = load_dataframe(cache_path)
        elif cache_path.exists():
            click.echo(f"Cache {reason}, refreshing...")

    if df is None or df.empty:
        if use_cache and not cache_path.exists():
            click.echo(f"No cache — fetching {symbol} {timeframe} ({lookback}d)...")
        elif not use_cache:
            click.echo(f"Fetching {symbol} {timeframe} ({lookback}d) from {exchange}...")
        df = ensure_ohlcv(
            out_root,
            exchange=exchange,
            timeframe=timeframe,
            symbol=symbol,
            days=lookback,
            max_stale_hours=max_stale,
            force=not use_cache or df is None,
        )
        if df.empty:
            raise click.ClickException(f"No data for {symbol} on {exchange}")
        click.echo(f"Cached {len(df)} bars -> {cache_path}")

    netflow = None
    if dune_cfg:
        dune_days = max(lookback, dune_cfg.lookback_days)
        click.echo(f"Loading Dune CEX netflow ({dune_days}d)...")
        netflow = ensure_netflow_for_symbol(
            symbol, out_root, days=dune_days, use_cache=use_cache
        )
        if netflow is None:
            if dune_cfg.skip_if_missing:
                click.echo(f"  -> no token_map for {symbol_base(symbol)}; Dune filter skipped")
            else:
                raise click.ClickException(f"No token_map entry for {symbol_base(symbol)}")
        else:
            click.echo(f"  -> {len(netflow)} daily on-chain rows")

    signals_df = IgnitionStrategy(strat_cfg, dune_cfg).generate_signals(
        df, netflow_daily=netflow
    )
    n_signals = int(signals_df["entry_signal"].sum())
    n_triggers = int(signals_df["entry_trigger"].sum())
    dune_blocked = 0
    if dune_cfg and "net_inflow_roll_usd" in signals_df.columns:
        raw = IgnitionStrategy(strat_cfg, None).generate_signals(df)
        dune_blocked = int(raw["entry_trigger"].sum()) - n_triggers
    click.echo(
        f"Signals: {n_signals} bars match; {n_triggers} triggers "
        f"(dune blocked ~{max(dune_blocked, 0)} prior triggers, of {len(signals_df)} bars)"
    )

    result = run_backtest(signals_df, strat_cfg=strat_cfg, bt_cfg=bt_cfg)
    click.echo(format_report(result, symbol=symbol, price_df=df))

    report_dir = out_root / "backtests"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    safe = symbol.replace("/", "_").replace(":", "_")

    eq_path = report_dir / f"{safe}_{ts}_equity.parquet"
    save_dataframe(result.equity_curve, eq_path)

    if result.trades:
        from dataclasses import asdict
        trades_df = pd.DataFrame([asdict(t) for t in result.trades])
        trades_path = report_dir / f"{safe}_{ts}_trades.csv"
        save_dataframe(trades_df, trades_path, format="csv")
        click.echo(f"Trades CSV:    {trades_path}")
    click.echo(f"Equity curve:  {eq_path}")


@main.command("build-pool")
@click.option("--exchange", default=None, help="CCXT id (default from config)")
@click.option("--max-candidates", default=None, type=int)
@click.option("--final-top", default=None, type=int)
@click.option("--days", default=None, type=int, help="OHLCV history per candidate")
@click.pass_context
def build_pool_cmd(
    ctx: click.Context,
    exchange: str | None,
    max_candidates: int | None,
    final_top: int | None,
    days: int | None,
) -> None:
    """Build a candidate pool: liquidity-filtered small caps + cost-zone tags."""
    pool_cfg_d = ctx.obj["cfg"].get("pool", {})
    cz_d = pool_cfg_d.get("cost_zone", {})
    cfg = PoolConfig(
        exchange=exchange or pool_cfg_d.get("exchange", "binanceusdm"),
        timeframe=pool_cfg_d.get("timeframe", "1h"),
        ohlcv_days=days or pool_cfg_d.get("ohlcv_days", 21),
        min_quote_volume_usd=pool_cfg_d.get("min_quote_volume_usd", 500_000),
        max_quote_volume_usd=pool_cfg_d.get("max_quote_volume_usd", 80_000_000),
        max_candidates=max_candidates or pool_cfg_d.get("max_candidates", 30),
        final_top=final_top or pool_cfg_d.get("final_top", 10),
        cost_zone=CostZoneConfig.from_dict(cz_d),
    )
    click.echo(
        f"Building pool on {cfg.exchange}: "
        f"max_candidates={cfg.max_candidates}, final_top={cfg.final_top}"
    )
    df = build_pool(cfg)
    if df.empty:
        raise click.ClickException("Pool came back empty (network or filters too strict)")

    cols = [
        "rank", "symbol", "stage", "score",
        "dist_to_cost_pct", "ret_window_pct", "box_width_pct",
        "quote_vol_24h_usd", "last_close",
    ]
    show = df[[c for c in cols if c in df.columns]].copy()
    for c in ("dist_to_cost_pct", "ret_window_pct", "box_width_pct"):
        if c in show.columns:
            show[c] = show[c].round(2)
    click.echo("")
    click.echo(show.to_string(index=False))

    out_dir = ctx.obj["out"] / "pools"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    path = out_dir / f"pool_{cfg.exchange}_{ts}.csv"
    save_dataframe(df, path, format="csv")
    latest = out_dir / f"pool_{cfg.exchange}_latest.csv"
    save_dataframe(df, latest, format="csv")
    click.echo(f"\nSaved pool: {path}")
    click.echo(f"Latest:     {latest}")


def _resolve_pool_csv(out_root: Path, exchange: str) -> Path | None:
    pool_dir = out_root / "pools"
    if not pool_dir.exists():
        return None
    latest = pool_dir / f"pool_{exchange}_latest.csv"
    if latest.exists():
        return latest
    candidates = sorted(pool_dir.glob(f"pool_{exchange}_*.csv"))
    return candidates[-1] if candidates else None


@main.command("research-pairs")
@click.option("--pool", "pool_path", type=click.Path(path_type=Path), default=None)
@click.option("--exchange", default=None)
@click.option("--symbol", "extra_symbols", multiple=True, help="Override pool with explicit symbols")
@click.option("--days", default=None, type=int, help="OHLCV days to load/fetch")
@click.option("--lookback-hours", default=720, type=int)
@click.option("--min-overlap", default=240, type=int)
@click.option("--min-corr", default=0.55, type=float)
@click.option("--z-window", default=120, type=int)
@click.option("--entry-z", default=2.0, type=float)
@click.option("--top", default=10, type=int)
@click.option("--use-cache/--fetch", default=True)
@click.pass_context
def research_pairs_cmd(
    ctx: click.Context,
    pool_path: Path | None,
    exchange: str | None,
    extra_symbols: tuple[str, ...],
    days: int | None,
    lookback_hours: int,
    min_overlap: int,
    min_corr: float,
    z_window: int,
    entry_z: float,
    top: int,
    use_cache: bool,
) -> None:
    """Rank small-cap perp pairs by spread mean-reversion patterns."""
    sc = ctx.obj["cfg"]["strategy"]
    exchange = exchange or sc["exchange"]
    timeframe = sc["timeframe"]
    lookback_days = days or max(sc.get("lookback_days", 90), int(lookback_hours / 24) + 7)
    out_root: Path = ctx.obj["out"]

    if extra_symbols:
        symbols = list(extra_symbols)
        click.echo(f"Using {len(symbols)} symbols from --symbol")
    else:
        path = pool_path or _resolve_pool_csv(out_root, exchange)
        if path is None or not path.exists():
            raise click.ClickException(
                "No pool CSV found. Run `cq build-pool` first or pass --symbol."
            )
        click.echo(f"Loading pool: {path}")
        symbols = pd.read_csv(path)["symbol"].dropna().astype(str).tolist()

    if len(symbols) < 2:
        raise click.ClickException("Need at least two symbols for pair research.")

    sync_cfg = SyncConfig.from_cfg(ctx.obj["cfg"])
    ohlcv_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = ensure_ohlcv(
                out_root,
                exchange=exchange,
                timeframe=timeframe,
                symbol=sym,
                days=lookback_days,
                max_stale_hours=sync_cfg.max_stale_hours,
                force=not use_cache,
            )
            if not df.empty:
                ohlcv_by_symbol[sym] = df
                click.echo(f"[pair] {sym}: {len(df)} bars")
        except Exception as e:
            click.echo(f"[pair] {sym}: skip ({e})")

    cfg = PairResearchConfig(
        lookback_hours=lookback_hours,
        min_overlap=min_overlap,
        min_corr=min_corr,
        z_window=z_window,
        entry_z=entry_z,
    )
    ranked = rank_pairs(ohlcv_by_symbol, cfg)
    click.echo("")
    click.echo(format_pair_report(ranked, top=top))

    out_dir = out_root / "pairs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    path = out_dir / f"pairs_{exchange}_{ts}.csv"
    latest = out_dir / f"pairs_{exchange}_latest.csv"
    save_dataframe(ranked, path, format="csv")
    save_dataframe(ranked, latest, format="csv")
    click.echo(f"\nSaved pairs: {path}")
    click.echo(f"Latest:      {latest}")


@main.command("backtest-batch")
@click.option("--pool", "pool_path", type=click.Path(path_type=Path), default=None)
@click.option("--exchange", default=None)
@click.option("--symbol", "extra_symbols", multiple=True, help="Override pool with explicit symbols")
@click.option("--days", default=None, type=int)
@click.option("--use-cache/--fetch", default=True)
@click.pass_context
def backtest_batch_cmd(
    ctx: click.Context,
    pool_path: Path | None,
    exchange: str | None,
    extra_symbols: tuple[str, ...],
    days: int | None,
    use_cache: bool,
) -> None:
    """Run Ignition strategy across all symbols in a pool (or --symbol overrides)."""
    sc = ctx.obj["cfg"]["strategy"]
    exchange = exchange or sc["exchange"]
    timeframe = sc["timeframe"]
    lookback = days or sc.get("lookback_days", 90)
    strat_cfg, dune_cfg = _strategy_cfgs(sc)
    bt_cfg = BacktestConfig(**sc.get("backtest", {}))
    out_root: Path = ctx.obj["out"]
    if dune_cfg:
        click.echo("Dune on-chain filter: enabled")

    if extra_symbols:
        symbols = list(extra_symbols)
        click.echo(f"Using {len(symbols)} symbols from --symbol")
    else:
        path = pool_path or _resolve_pool_csv(out_root, exchange)
        if path is None or not path.exists():
            raise click.ClickException(
                "No pool CSV found. Run `cq build-pool` first or pass --symbol."
            )
        click.echo(f"Loading pool: {path}")
        pool_df = pd.read_csv(path)
        symbols = pool_df["symbol"].tolist()
        click.echo(f"Pool has {len(symbols)} symbols")

    result = run_batch(
        symbols,
        exchange=exchange,
        timeframe=timeframe,
        days=lookback,
        strat_cfg=strat_cfg,
        bt_cfg=bt_cfg,
        out_root=out_root,
        use_cache=use_cache,
        dune_cfg=dune_cfg,
        max_stale_hours=SyncConfig.from_cfg(ctx.obj["cfg"]).max_stale_hours,
    )

    click.echo("")
    click.echo(format_batch_report(result))

    report_dir = out_root / "backtests"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    summary_path = report_dir / f"batch_{exchange}_{ts}_summary.csv"
    save_dataframe(result.summary, summary_path, format="csv")
    click.echo(f"\nSummary CSV: {summary_path}")
    if not result.trades.empty:
        trades_path = report_dir / f"batch_{exchange}_{ts}_trades.csv"
        save_dataframe(result.trades, trades_path, format="csv")
        click.echo(f"Trades CSV:  {trades_path}")


@main.command("backtest-honest")
@click.option("--exchange", default=None)
@click.option("--days", default=90, type=int, help="Total history days (IS + OOS)")
@click.option(
    "--oos-days", default=30, type=int, help="Hold out last N days as out-of-sample"
)
@click.option(
    "--position-fraction",
    default=0.25,
    type=float,
    help="Fraction of equity per trade (0.25 = 25%)",
)
@click.option(
    "--slippage-pct",
    default=0.15,
    type=float,
    help="Per-side slippage incl spread (small-cap perps: 0.10-0.30)",
)
@click.option(
    "--funding-per-8h-pct",
    default=0.03,
    type=float,
    help="Perp funding rate per 8h, longs pay (0.03 = ~33%/yr)",
)
@click.option(
    "--source",
    type=click.Choice(["pool", "symbols"]),
    default="pool",
)
@click.option("--symbol", "extra_symbols", multiple=True)
@click.option("--no-dune", is_flag=True, help="Force Dune filter off")
@click.option(
    "--btc-regime",
    is_flag=True,
    help="Only enter when BTC > EMA(168) — regime filter",
)
@click.option(
    "--max-dist-breakout",
    default=None,
    type=float,
    help="Skip extended breakouts (close > breakout_level*(1+x%)). e.g. 3.0",
)
@click.option(
    "--time-stop-hours",
    default=None,
    type=int,
    help="Override max_hold_hours (smaller = cut losers faster)",
)
@click.option(
    "--trail-pct",
    default=None,
    type=float,
    help="Override trailing stop %% (larger = let winners run further)",
)
@click.option(
    "--atr-stop-mult",
    default=None,
    type=float,
    help="Override initial ATR stop multiplier",
)
@click.option(
    "--strategy",
    "strategy_name",
    type=click.Choice(["ignition", "mean_reversion", "funding_carry"]),
    default="ignition",
    help="Which strategy to backtest",
)
@click.option(
    "--tag",
    default=None,
    type=str,
    help="Label for output filenames (e.g. baseline, btc, combo)",
)
@click.pass_context
def backtest_honest_cmd(
    ctx: click.Context,
    exchange: str | None,
    days: int,
    oos_days: int,
    position_fraction: float,
    slippage_pct: float,
    funding_per_8h_pct: float,
    source: str,
    extra_symbols: tuple[str, ...],
    no_dune: bool,
    btc_regime: bool,
    max_dist_breakout: float | None,
    time_stop_hours: int | None,
    trail_pct: float | None,
    atr_stop_mult: float | None,
    strategy_name: str,
    tag: str | None,
) -> None:
    """Gate 1: out-of-sample walk-forward backtest with realistic costs.

    Pass criteria: OOS edge > 0, ≥ 50 trades total, max DD < 30%.
    """
    sc = ctx.obj["cfg"]["strategy"]
    exchange = exchange or sc["exchange"]
    timeframe = sc["timeframe"]

    if strategy_name == "ignition":
        strat_cfg, dune_cfg = _strategy_cfgs(sc)
        if no_dune:
            dune_cfg = None
        if btc_regime:
            strat_cfg.require_btc_uptrend = True
        if max_dist_breakout is not None:
            strat_cfg.max_dist_to_breakout_pct = max_dist_breakout
    elif strategy_name == "mean_reversion":
        mr_dict = sc.get("mean_reversion", {})
        strat_cfg = MeanReversionConfig.from_dict(mr_dict.get("params", mr_dict))
        dune_cfg = None
        if btc_regime or max_dist_breakout is not None:
            click.echo(
                "Note: --btc-regime / --max-dist-breakout are ignored for "
                "mean_reversion strategy."
            )
    else:  # funding_carry
        fc_dict = sc.get("funding_carry", {})
        strat_cfg = FundingCarryConfig.from_dict(fc_dict.get("params", fc_dict))
        dune_cfg = None
        if btc_regime or max_dist_breakout is not None:
            click.echo(
                "Note: --btc-regime / --max-dist-breakout are ignored for "
                "funding_carry strategy."
            )
    if time_stop_hours is not None:
        strat_cfg.max_hold_hours = time_stop_hours
    if trail_pct is not None:
        strat_cfg.trail_pct = trail_pct
    if atr_stop_mult is not None:
        strat_cfg.atr_stop_mult = atr_stop_mult

    base_bt = sc.get("backtest", {})
    bt_cfg = BacktestConfig(
        initial_capital=base_bt.get("initial_capital", 10_000),
        fee_rate=base_bt.get("fee_rate", 0.0004),
        slippage_pct=slippage_pct,
        position_fraction=position_fraction,
        funding_rate_per_8h_pct=funding_per_8h_pct,
    )
    out_root: Path = ctx.obj["out"]

    if extra_symbols:
        symbols = list(extra_symbols)
    elif source == "pool":
        path = _resolve_pool_csv(out_root, exchange)
        if path is None:
            raise click.ClickException(
                "No pool CSV. Run `cq build-pool` or pass --symbol."
            )
        symbols = pd.read_csv(path)["symbol"].tolist()
    else:
        symbols = [sc.get("symbol", "PEPE/USDT:USDT")]

    click.echo(
        f"Honest backtest [{strategy_name}]: {len(symbols)} symbols, "
        f"{days}d total, OOS={oos_days}d"
    )
    click.echo(
        f"  pos={position_fraction*100:.0f}%  slip={slippage_pct}%/side  "
        f"fund={funding_per_8h_pct}%/8h  dune={'on' if dune_cfg else 'off'}"
    )
    click.echo(
        f"  time_stop={strat_cfg.max_hold_hours}h  "
        f"trail={strat_cfg.trail_pct}%  atr_mult={strat_cfg.atr_stop_mult}"
        + (f"  tag={tag}" if tag else "")
        + "\n"
    )

    from crypto_quant.backtest.honest import (
        format_honest_report,
        run_honest_backtest,
    )

    result = run_honest_backtest(
        symbols,
        exchange=exchange,
        timeframe=timeframe,
        total_days=days,
        oos_days=oos_days,
        strat_cfg=strat_cfg,
        bt_cfg=bt_cfg,
        out_root=out_root,
        dune_cfg=dune_cfg,
        strategy_name=strategy_name,
    )
    click.echo("")
    click.echo(format_honest_report(result))

    if not result.summary.empty:
        out_dir = out_root / "backtests"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        from crypto_quant.data import save_dataframe

        suffix = f"_{tag}" if tag else ""
        sname = strategy_name.replace("_", "")
        sp = out_dir / f"honest_{sname}_{exchange}_{ts}{suffix}_summary.csv"
        save_dataframe(result.summary, sp, format="csv")
        click.echo(f"\nSummary: {sp}")
        if not result.trades.empty:
            tp = out_dir / f"honest_{sname}_{exchange}_{ts}{suffix}_trades.csv"
            save_dataframe(result.trades, tp, format="csv")
            click.echo(f"Trades:  {tp}")


@main.command("backtest-portfolio")
@click.option("--strategy", "strategy_name",
    type=click.Choice(["ignition", "mean_reversion", "funding_carry"]),
    default="ignition")
@click.option("--exchange", default=None)
@click.option("--days", default=90, type=int)
@click.option("--oos-days", default=30, type=int)
@click.option("--max-concurrent", default=5, type=int,
    help="Max simultaneous open positions")
@click.option("--position-fraction", default=None, type=float,
    help="Per-trade fraction of CURRENT equity (default: 1/max_concurrent)")
@click.option("--slippage-pct", default=0.15, type=float)
@click.option("--funding-per-8h-pct", default=0.03, type=float,
    help="Constant funding (ignored if per-bar from FundingCarry)")
@click.option("--source", type=click.Choice(["pool", "symbols"]), default="pool")
@click.option("--symbol", "extra_symbols", multiple=True)
@click.option("--no-dune", is_flag=True)
@click.option("--tag", default=None, type=str)
@click.pass_context
def backtest_portfolio_cmd(
    ctx: click.Context,
    strategy_name: str,
    exchange: str | None,
    days: int,
    oos_days: int,
    max_concurrent: int,
    position_fraction: float | None,
    slippage_pct: float,
    funding_per_8h_pct: float,
    source: str,
    extra_symbols: tuple[str, ...],
    no_dune: bool,
    tag: str | None,
) -> None:
    """Portfolio backtest: one account, multiple symbols, concurrent positions."""
    from crypto_quant.backtest.honest import _make_strategy, _needs_funding
    from crypto_quant.backtest.portfolio import (
        PortfolioConfig,
        format_portfolio_report,
        run_portfolio_backtest,
    )
    from crypto_quant.data.sync import ensure_funding, ensure_ohlcv
    from crypto_quant.strategy import merge_funding_to_ohlcv

    sc = ctx.obj["cfg"]["strategy"]
    exchange = exchange or sc["exchange"]
    timeframe = sc["timeframe"]

    if strategy_name == "ignition":
        strat_cfg, dune_cfg = _strategy_cfgs(sc)
        if no_dune:
            dune_cfg = None
    elif strategy_name == "mean_reversion":
        mr = sc.get("mean_reversion", {})
        strat_cfg = MeanReversionConfig.from_dict(mr.get("params", mr))
        dune_cfg = None
    else:
        fc = sc.get("funding_carry", {})
        strat_cfg = FundingCarryConfig.from_dict(fc.get("params", fc))
        dune_cfg = None

    if position_fraction is None:
        position_fraction = 1.0 / max_concurrent

    bt_cfg = BacktestConfig(
        initial_capital=sc.get("backtest", {}).get("initial_capital", 10_000),
        fee_rate=sc.get("backtest", {}).get("fee_rate", 0.0004),
        slippage_pct=slippage_pct,
        funding_rate_per_8h_pct=funding_per_8h_pct,
    )
    pf_cfg = PortfolioConfig(
        max_concurrent=max_concurrent,
        position_fraction=position_fraction,
    )
    out_root: Path = ctx.obj["out"]

    if extra_symbols:
        symbols = list(extra_symbols)
    elif source == "pool":
        path = _resolve_pool_csv(out_root, exchange)
        if path is None:
            raise click.ClickException("No pool CSV. Run `cq build-pool` or pass --symbol.")
        symbols = pd.read_csv(path)["symbol"].tolist()
    else:
        symbols = [sc.get("symbol", "PEPE/USDT:USDT")]

    click.echo(
        f"Portfolio backtest [{strategy_name}]: {len(symbols)} symbols, "
        f"{days}d total, OOS={oos_days}d"
    )
    click.echo(
        f"  max_concur={max_concurrent}  per_pos={position_fraction*100:.0f}%  "
        f"slip={slippage_pct}%/side  fund={funding_per_8h_pct}%/8h"
    )

    strategy, _dir = _make_strategy(strategy_name, strat_cfg, dune_cfg)
    needs_fund = _needs_funding(strategy_name)
    signals_by_sym: dict[str, pd.DataFrame] = {}
    bnh_per_sym: dict[str, float] = {}
    cutoff = None

    for sym in symbols:
        try:
            df = ensure_ohlcv(
                out_root, exchange=exchange, timeframe=timeframe,
                symbol=sym, days=days, force=False,
            )
        except Exception as e:
            click.echo(f"  skip {sym}: {e}")
            continue
        if df.empty or len(df) < 200:
            click.echo(f"  skip {sym}: insufficient data")
            continue
        if cutoff is None:
            cutoff = df["timestamp"].max() - pd.Timedelta(days=oos_days)
        if needs_fund:
            try:
                fnd = ensure_funding(out_root, exchange=exchange, symbol=sym, days=days)
            except Exception:
                fnd = None
            if fnd is None or fnd.empty:
                continue
            df = merge_funding_to_ohlcv(df, fnd)
        sig = strategy.generate_signals(df)
        # Restrict to OOS window for the portfolio simulation
        sig_oos = sig[sig["timestamp"] >= cutoff].reset_index(drop=True)
        if not sig_oos.empty:
            signals_by_sym[sym] = sig_oos
            oos_close = sig_oos["close"]
            bnh_per_sym[sym] = (oos_close.iloc[-1] / oos_close.iloc[0] - 1) * 100

    if not signals_by_sym:
        raise click.ClickException("No symbols with usable signals.")

    result = run_portfolio_backtest(
        signals_by_sym, strat_cfg=strat_cfg, bt_cfg=bt_cfg, pf_cfg=pf_cfg,
    )
    # Equal-weight BnH across symbols, scaled by deployed exposure
    avg_bnh = sum(bnh_per_sym.values()) / len(bnh_per_sym)
    # Deployed exposure ≈ max_concurrent * position_fraction (cap at 1.0)
    deployed = min(1.0, max_concurrent * position_fraction)
    fair_bnh = avg_bnh * deployed
    click.echo("")
    click.echo(format_portfolio_report(result, bnh_ret_pct=fair_bnh,
        label=f"{strategy_name} {oos_days}d OOS"))

    if not result.trades:
        return
    out_dir = out_root / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    suffix = f"_{tag}" if tag else ""
    trades_df = pd.DataFrame([
        {**asdict_safely(t), "symbol": getattr(t, "symbol", "?")}
        for t in result.trades
    ])
    tp_path = out_dir / f"portfolio_{strategy_name}_{exchange}_{ts}{suffix}_trades.csv"
    eq_path = out_dir / f"portfolio_{strategy_name}_{exchange}_{ts}{suffix}_equity.csv"
    save_dataframe(trades_df, tp_path, format="csv")
    save_dataframe(result.equity_curve, eq_path, format="csv")
    click.echo(f"\nTrades:  {tp_path}")
    click.echo(f"Equity:  {eq_path}")


def asdict_safely(obj) -> dict:
    from dataclasses import asdict as _asdict
    return _asdict(obj)


@main.command("validate")
@click.option("--strategy", "strategy_name",
    type=click.Choice(["ignition", "mean_reversion", "funding_carry"]),
    default="ignition")
@click.option("--exchange", default=None)
@click.option("--total-days", default=365, type=int,
    help="Total cached history to consider")
@click.option("--window-days", default=30, type=int, help="Each OOS window length")
@click.option("--n-windows", default=6, type=int, help="Number of non-overlapping OOS windows")
@click.option("--max-concurrent", default=5, type=int)
@click.option("--position-fraction", default=None, type=float)
@click.option("--slippage-pct", default=0.15, type=float)
@click.option("--funding-per-8h-pct", default=0.03, type=float)
@click.option("--tiebreaker", type=click.Choice(["alpha", "rank"]), default="rank")
@click.option("--source", type=click.Choice(["pool", "symbols"]), default="pool")
@click.option("--symbol", "extra_symbols", multiple=True)
@click.option("--no-dune", is_flag=True)
@click.option("--tag", default=None, type=str)
@click.pass_context
def validate_cmd(
    ctx: click.Context,
    strategy_name: str,
    exchange: str | None,
    total_days: int,
    window_days: int,
    n_windows: int,
    max_concurrent: int,
    position_fraction: float | None,
    slippage_pct: float,
    funding_per_8h_pct: float,
    tiebreaker: str,
    source: str,
    extra_symbols: tuple[str, ...],
    no_dune: bool,
    tag: str | None,
) -> None:
    """Walk-forward validation across N non-overlapping windows.

    Verdict gates: hit_rate >= 67%, mean edge >= +5pp, worst DD > -25%.
    """
    from crypto_quant.backtest.honest import _make_strategy, _needs_funding
    from crypto_quant.backtest.portfolio import PortfolioConfig
    from crypto_quant.backtest.walk_forward import (
        format_walk_forward_report,
        run_walk_forward,
    )

    sc = ctx.obj["cfg"]["strategy"]
    exchange = exchange or sc["exchange"]
    timeframe = sc["timeframe"]

    if strategy_name == "ignition":
        strat_cfg, dune_cfg = _strategy_cfgs(sc)
        if no_dune:
            dune_cfg = None
    elif strategy_name == "mean_reversion":
        mr = sc.get("mean_reversion", {})
        strat_cfg = MeanReversionConfig.from_dict(mr.get("params", mr))
        dune_cfg = None
    else:
        fc = sc.get("funding_carry", {})
        strat_cfg = FundingCarryConfig.from_dict(fc.get("params", fc))
        dune_cfg = None

    if position_fraction is None:
        position_fraction = 1.0 / max_concurrent

    bt_cfg = BacktestConfig(
        initial_capital=sc.get("backtest", {}).get("initial_capital", 10_000),
        fee_rate=sc.get("backtest", {}).get("fee_rate", 0.0004),
        slippage_pct=slippage_pct,
        funding_rate_per_8h_pct=funding_per_8h_pct,
    )
    pf_cfg = PortfolioConfig(
        max_concurrent=max_concurrent,
        position_fraction=position_fraction,
        tiebreaker=tiebreaker,
    )
    out_root: Path = ctx.obj["out"]

    if extra_symbols:
        symbols = list(extra_symbols)
    elif source == "pool":
        path = _resolve_pool_csv(out_root, exchange)
        if path is None:
            raise click.ClickException("No pool CSV. Run `cq build-pool` first.")
        symbols = pd.read_csv(path)["symbol"].tolist()
    else:
        symbols = [sc.get("symbol", "PEPE/USDT:USDT")]

    strategy, _dir = _make_strategy(strategy_name, strat_cfg, dune_cfg)
    needs_fund = _needs_funding(strategy_name)

    click.echo(
        f"Validate [{strategy_name}]: {n_windows}x{window_days}d windows, "
        f"{len(symbols)} symbols, tiebreaker={tiebreaker}, "
        f"per_pos={position_fraction*100:.0f}%\n"
    )
    result = run_walk_forward(
        strategy, symbols,
        out_root=out_root, exchange=exchange, timeframe=timeframe,
        total_days=total_days, window_days=window_days, n_windows=n_windows,
        strat_cfg=strat_cfg, bt_cfg=bt_cfg, pf_cfg=pf_cfg,
        strategy_name=strategy_name, needs_funding=needs_fund,
    )
    click.echo("")
    click.echo(format_walk_forward_report(result))

    if result.windows:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        suffix = f"_{tag}" if tag else ""
        path = out_root / "backtests" / (
            f"validate_{strategy_name}_{exchange}_{ts}{suffix}_windows.csv"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        save_dataframe(result.summary(), path, format="csv")
        click.echo(f"\nWindows CSV: {path}")


@main.command("dune-check")
@click.pass_context
def dune_check(ctx: click.Context) -> None:
    """Verify DUNE_API_KEY (see docs/DUNE.md, .env.example)."""
    dune_cfg = ctx.obj["cfg"].get("dune", {})
    try:
        with DuneClient() as client:
            who = client.me()
        click.echo("Dune API: OK")
        click.echo(f"  MCP URL:  {dune_cfg.get('mcp_url', 'https://api.dune.com/mcp/v1')}")
        click.echo(f"  REST:     {dune_cfg.get('api_base')}")
        if isinstance(who, dict):
            for k in ("username", "email", "name"):
                if k in who:
                    click.echo(f"  {k}: {who[k]}")
    except Exception as e:
        raise click.ClickException(str(e)) from e


@main.command("sources")
def sources() -> None:
    """List integrated open data sources."""
    click.echo(
        """
Open-source / public crypto data sources in this repo:

  1. CCXT (MIT)
     Live + paginated historical OHLCV and trades from 100+ exchanges.
     Default: Binance public REST — no API key for market data.

  2. Binance Data Collection (data.binance.vision)
     Official bulk archives: klines, aggTrades, trades (spot & futures).
     https://github.com/binance/binance-public-data

  3. CoinGecko API v3 (free public tier)
     Aggregated prices, OHLC, market cap rankings.
     https://www.coingecko.com/en/api

  4. Dune Analytics (API key required)
     On-chain SQL — set DUNE_API_KEY in .env, see docs/DUNE.md
     Cursor MCP: config/cursor-mcp.example.json

Raw files land under ./data/ (parquet or csv). Configure defaults in config/default.yaml.
"""
    )


if __name__ == "__main__":
    main()
