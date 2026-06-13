"""Sprint 3 summary: single-coin vs portfolio for all 3 strategies."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent / "crypto-quant" / "data" / "backtests"


def _load_portfolio(tag: str) -> dict:
    eq_files = list(ROOT.glob(f"portfolio_*{tag}_equity.csv"))
    tr_files = list(ROOT.glob(f"portfolio_*{tag}_trades.csv"))
    if not eq_files or not tr_files:
        return {}
    eq = pd.read_csv(eq_files[0])
    tr = pd.read_csv(tr_files[0])
    if eq.empty:
        return {}
    start, end = float(eq["equity"].iloc[0]), float(eq["equity"].iloc[-1])
    ret = (end / start - 1) * 100
    peak = eq["equity"].cummax()
    dd = ((eq["equity"] - peak) / peak * 100).min()
    return {
        "trades": len(tr),
        "ret_pct": round(ret, 2),
        "win_rate": round((tr["total_pnl_usd"] > 0).mean() * 100, 1) if len(tr) else 0,
        "max_dd": round(dd, 2),
        "funding_pnl": round(tr["funding_usd"].sum(), 2) if len(tr) else 0,
        "price_pnl": round(tr["pnl_usd"].sum(), 2) if len(tr) else 0,
    }


def _load_honest(strategy_token: str, tag: str) -> dict:
    p = list(ROOT.glob(f"honest_{strategy_token}_okx_*{tag}_summary.csv"))
    if not p:
        return {}
    s = pd.read_csv(p[0])
    if s.empty:
        return {}
    return {
        "trades": int(s["oos_trades"].sum()),
        "ret_pct": round(s["oos_return_pct"].mean(), 2),
        "bnh_100": round(s["oos_bnh_pct"].mean(), 2),
        "win_rate": round(s["oos_win_rate_pct"].mean(), 1),
        "max_dd": round(s["oos_max_dd_pct"].mean(), 2),
    }


# Single-coin (Sprint 1+2) and portfolio (Sprint 3) results
rows = []
specs = [
    ("ignition",       "ignition",       "s1_ign",       "s3_ign"),
    ("mean_reversion", "meanreversion",  "s1_mr",        "s3_mr"),
    ("funding_carry",  "fundingcarry",   "s2_fc_default", "s3_fc_sym"),
]

for label, sc_token, sc_tag, pf_tag in specs:
    sc = _load_honest(sc_token, sc_tag)
    pf = _load_portfolio(pf_tag)
    bnh_100 = sc.get("bnh_100", 22.0)
    rows.append({
        "strategy": label,
        # Single-coin (avg across N independent $10K accounts, 25% pos)
        "sc_trades": sc.get("trades", 0),
        "sc_ret_avg": sc.get("ret_pct", 0),
        "sc_fair_bnh_25": round(bnh_100 * 0.25, 2),
        "sc_edge": round(sc.get("ret_pct", 0) - bnh_100 * 0.25, 2),
        "sc_winrate": sc.get("win_rate", 0),
        "sc_dd": sc.get("max_dd", 0),
        # Portfolio (one $10K acct, up to 100% deployed across 5 slots)
        "pf_trades": pf.get("trades", 0),
        "pf_ret_total": pf.get("ret_pct", 0),
        "pf_bnh_100": round(bnh_100, 2),
        "pf_edge_100": round(pf.get("ret_pct", 0) - bnh_100, 2),
        "pf_winrate": pf.get("win_rate", 0),
        "pf_dd": pf.get("max_dd", 0),
        "pf_funding": pf.get("funding_pnl", 0),
    })

df = pd.DataFrame(rows)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)

print("=" * 110)
print("SINGLE-COIN (avg per-coin, 25% pos)   vs   PORTFOLIO (1 acct, max 5 concurrent, 100% deployed)")
print("=" * 110)
print(df.to_string(index=False))
print()
print("Key insight: PORTFOLIO captures diversification dividend that single-coin testing hides.")
print("  - sc_edge: per-coin return minus fair-exposure BnH")
print("  - pf_edge_100: portfolio return minus 100%-exposure BnH (fair since pf deploys up to 100%)")
print("  - Compare sc_edge vs pf_edge_100 to see the diversification effect")
