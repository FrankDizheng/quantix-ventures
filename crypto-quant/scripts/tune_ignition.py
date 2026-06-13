"""Grid search Ignition params on a symbol universe. Run: python scripts/tune_ignition.py

Prerequisite: `cq sync-data` so OHLCV is cached locally.
Tuning is OHLCV-only (Dune off) to stay fast and offline-safe.
"""

from __future__ import annotations

import itertools

from crypto_quant.backtest.batch import run_batch
from crypto_quant.backtest.engine import BacktestConfig
from crypto_quant.config import data_dir, load_config
from crypto_quant.strategy import IgnitionConfig

# EVM-friendly small caps on OKX (Dune netflow works for mapped symbols).
GRID = {
    "breakout_hours": [36, 48],
    "vol_mult": [1.3, 1.5, 1.8],
    "max_dist_to_cost_pct": [10.0, 15.0],
    "atr_stop_mult": [2.5, 3.5],
    "trail_pct": [10.0, 12.0],
}

SYMBOLS = [
    "PEPE/USDT:USDT",
    "FLOKI/USDT:USDT",
    "WIF/USDT:USDT",
    "EDEN/USDT:USDT",
    "BERA/USDT:USDT",
    "APE/USDT:USDT",
    "GRASS/USDT:USDT",
    "AXS/USDT:USDT",
    "ASTER/USDT:USDT",
    "LIT/USDT:USDT",
]


def main() -> None:
    cfg = load_config()
    sc = cfg["strategy"]
    bt_cfg = BacktestConfig(**sc.get("backtest", {}))
    base_params = sc.get("params", {})
    out = data_dir(cfg)
    days = sc.get("lookback_days", 90)

    keys = list(GRID.keys())
    combos = list(itertools.product(*(GRID[k] for k in keys)))
    print(f"Tuning {len(combos)} combos x {len(SYMBOLS)} symbols ({days}d, Dune off)...")

    rows: list[dict] = []
    for i, values in enumerate(combos, 1):
        overrides = dict(zip(keys, values))
        params = {**base_params, **overrides}
        strat_cfg = IgnitionConfig.from_dict(params)
        result = run_batch(
            SYMBOLS,
            exchange="okx",
            timeframe=sc.get("timeframe", "1h"),
            days=days,
            strat_cfg=strat_cfg,
            bt_cfg=bt_cfg,
            out_root=out,
            use_cache=True,
            dune_cfg=None,
            log=False,
        )
        if result.summary.empty:
            continue
        s = result.summary
        rows.append(
            {
                **overrides,
                "symbols": len(s),
                "avg_return": s["total_return_pct"].mean(),
                "avg_bnh": s["buy_hold_pct"].mean(),
                "avg_edge": s["edge_vs_bnh_pct"].mean(),
                "beat_bnh": int((s["edge_vs_bnh_pct"] > 0).sum()),
                "avg_trades": s["trades"].mean(),
                "avg_win_rate": s["win_rate_pct"].mean(),
                "avg_dd": s["max_drawdown_pct"].mean(),
            }
        )
        if i % 20 == 0:
            print(f"  {i}/{len(combos)} done...")

    if not rows:
        print("No results")
        return

    import pandas as pd

    df = pd.DataFrame(rows).sort_values(
        ["avg_edge", "beat_bnh", "avg_return"], ascending=False
    )
    print("\n=== Top 10 param sets (by avg edge vs buy & hold) ===")
    print(df.head(10).to_string(index=False))
    best = df.iloc[0].to_dict()
    print("\nBest params:")
    for k in keys:
        print(f"  {k}: {best[k]}")


if __name__ == "__main__":
    main()
