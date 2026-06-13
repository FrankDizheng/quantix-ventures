"""Aggregate all honest_* backtest summaries into one comparison table."""

from pathlib import Path

import pandas as pd

ROOT = Path("/Users/mico/Projects/quantix-ventures/crypto-quant/data/backtests")

rows = []
for p in sorted(ROOT.glob("honest_*_summary.csv")):
    df = pd.read_csv(p)
    if df.empty:
        continue
    name = p.stem.replace("honest_okx_", "").replace("_summary", "")
    # Only include named ablations (a0..a7) for the table -- skip raw timestamp runs.
    parts = name.split("_", 1)
    if len(parts) < 2:
        continue
    tag = parts[1]
    avg_ret = df["oos_return_pct"].mean()
    avg_bnh = df["oos_bnh_pct"].mean()
    # All ablations a0..a7 used position_fraction=0.25, slip=0.15%, funding=0.03%.
    pos_frac = 0.25
    fair_bnh = avg_bnh * pos_frac
    rows.append(
        {
            "variant": tag,
            "trades": int(df["oos_trades"].sum()),
            "ret_pct": round(avg_ret, 2),
            "bnh_100": round(avg_bnh, 2),
            "fair_bnh_25": round(fair_bnh, 2),
            "fair_edge": round(avg_ret - fair_bnh, 2),
            "beat_bnh_100": f"{int((df['oos_edge_pct'] > 0).sum())}/{len(df)}",
            "dd_pct": round(df["oos_max_dd_pct"].mean(), 2),
            "ret/dd": round(avg_ret / abs(df["oos_max_dd_pct"].mean()), 2)
            if df["oos_max_dd_pct"].mean() != 0
            else 0,
        }
    )

out = pd.DataFrame(rows).sort_values("fair_edge", ascending=False)
print("All ablations use position_fraction=25%, slippage=0.15%/side, funding=0.03%/8h\n")
print(out.to_string(index=False))
