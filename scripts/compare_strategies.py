"""Aggregate all honest_* backtest summaries by strategy.

Reads every honest_<strategy>_<exchange>_<ts>_<tag>_summary.csv and produces
a one-row-per-run table sorted by avg return.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent / "crypto-quant" / "data" / "backtests"


def parse_name(stem: str) -> dict:
    # Filenames come in two flavors:
    #   honest_<exchange>_<YYYYMMDD>_<HHMM>[_<tag>]_summary       (sprint 1 a0-a7)
    #   honest_<strategy>_<exchange>_<YYYYMMDD>_<HHMM>[_<tag>]_summary  (sprint 2+)
    name = stem.removesuffix("_summary")
    parts = name.split("_")
    out = {"raw": name}
    if parts[0] != "honest":
        return out
    rest = parts[1:]
    # Detect strategy token: alpha word (not exchange like 'okx').
    # Heuristic: if rest[1] looks like an 8-digit date, then rest[0] is exchange
    # (old format). Else rest[0] is strategy, rest[1] is exchange.
    if len(rest) >= 3 and len(rest[1]) == 8 and rest[1].isdigit():
        out["strategy"] = "ignition_legacy"
        out["exchange"] = rest[0]
        out["ts"] = f"{rest[1]}_{rest[2]}"
        out["tag"] = "_".join(rest[3:])
    elif len(rest) >= 4:
        out["strategy"] = rest[0]
        out["exchange"] = rest[1]
        out["ts"] = f"{rest[2]}_{rest[3]}"
        out["tag"] = "_".join(rest[4:])
    return out


rows = []
for p in sorted(ROOT.glob("honest_*_summary.csv")):
    df = pd.read_csv(p)
    if df.empty:
        continue
    meta = parse_name(p.stem)
    direction = df["direction"].iloc[0] if "direction" in df.columns else "long"
    avg_ret = df["oos_return_pct"].mean()
    avg_bnh = df["oos_bnh_pct"].mean()
    rows.append(
        {
            "strategy": meta.get("strategy", "?"),
            "tag": meta.get("tag", ""),
            "dir": direction,
            "n_sym": len(df),
            "n_active": int((df["oos_trades"] > 0).sum()),
            "trades": int(df["oos_trades"].sum()),
            "avg_ret_pct": round(avg_ret, 2),
            "avg_bnh_100": round(avg_bnh, 2),
            "fair_bnh_25": round(avg_bnh * 0.25, 2),
            "fair_edge_25": round(avg_ret - avg_bnh * 0.25, 2)
            if direction == "long"
            else None,
            "win_rate": round(df["oos_win_rate_pct"].mean(), 1)
            if "oos_win_rate_pct" in df
            else None,
            "max_dd": round(df["oos_max_dd_pct"].mean(), 2),
        }
    )

out = pd.DataFrame(rows)
out = out.sort_values(["strategy", "avg_ret_pct"], ascending=[True, False])

# Filter to S1+S2 sprint runs for clarity (head-to-head comparison)
mask = out["tag"].str.startswith("s1_") | out["tag"].str.startswith("s2_")
sprint_runs = out[mask]

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)

print("All honest backtests (filter: sprint-tagged runs):")
print(sprint_runs.to_string(index=False))
print()
print("Notes:")
print("  - All runs: 25% position, 0.15% slip/side, 0.03%/8h funding (or per-bar for FC)")
print("  - 11 OKX small-cap perps, 90d total, 30d OOS hold-out")
print("  - For LONG strategies: edge vs same-exposure BnH is the success metric")
print("  - For SHORT strategies: positive return is the success metric")
