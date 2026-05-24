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
from crypto_quant.pool import PoolConfig, build_pool
from crypto_quant.scan import scan_exchanges
from crypto_quant.scan.perp_scanner import ScanConfig, format_scan_table
from crypto_quant.strategy import CostZoneConfig, IgnitionConfig, IgnitionStrategy


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


def _ohlcv_cache_path(out: Path, exchange: str, timeframe: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    return out / "ccxt" / exchange / timeframe / f"{safe}.parquet"


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
  -> Enter at NEXT bar OPEN with slippage (no lookahead)

EXIT (binding stop = max of these two):
  - Initial stop: entry - {p.get('atr_stop_mult', 2.5)} * ATR{p.get('atr_hours', 24)}
  - Trailing stop: highest_close_since_entry * (1 - {p.get('trail_pct', 8.0)}%)
  - Time stop:     {p.get('max_hold_hours')} bars (backstop only)
  - NO fixed take-profit — trail captures real trend length

Edit params in config/default.yaml under strategy.params
"""
    )


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
    strat_cfg = IgnitionConfig.from_dict(sc.get("params", {}))
    bt_cfg = BacktestConfig(**sc.get("backtest", {}))
    out_root: Path = ctx.obj["out"]
    cache_path = _ohlcv_cache_path(out_root, exchange, timeframe, symbol)

    df = None
    if use_cache and cache_path.exists():
        click.echo(f"Loading cache {cache_path}")
        df = load_dataframe(cache_path)

    if df is None or df.empty:
        click.echo(f"Fetching {symbol} {timeframe} ({lookback}d) from {exchange}...")
        since = datetime.now(timezone.utc) - timedelta(days=lookback)
        fetcher = CCXTFetcher(exchange, rate_limit_ms=200)
        df = fetcher.fetch_ohlcv(symbol, timeframe, since=since)
        if df.empty:
            raise click.ClickException(f"No data for {symbol} on {exchange}")
        save_dataframe(df, cache_path)
        click.echo(f"Cached {len(df)} bars -> {cache_path}")

    signals_df = IgnitionStrategy(strat_cfg).generate_signals(df)
    n_signals = int(signals_df["entry_signal"].sum())
    n_triggers = int(signals_df["entry_trigger"].sum())
    click.echo(
        f"Signals: {n_signals} bars match conditions; "
        f"{n_triggers} fresh triggers (of {len(signals_df)} bars)"
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
    strat_cfg = IgnitionConfig.from_dict(sc.get("params", {}))
    bt_cfg = BacktestConfig(**sc.get("backtest", {}))
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

Raw files land under ./data/ (parquet or csv). Configure defaults in config/default.yaml.
"""
    )


if __name__ == "__main__":
    main()
