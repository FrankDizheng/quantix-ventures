from __future__ import annotations

from collections import Counter
from math import sqrt

import pandas as pd

from crypto_quant.backtest.engine import BacktestResult


# 1h bars: ~24*365 = 8760 per year for annualization.
ANNUALIZATION_FACTOR_1H = 8760


def max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min() * 100)


def buy_and_hold_pct(price_df: pd.DataFrame | None) -> float | None:
    if price_df is None or price_df.empty:
        return None
    first = float(price_df["close"].iloc[0])
    last = float(price_df["close"].iloc[-1])
    if first <= 0:
        return None
    return (last / first - 1) * 100


def annualized_sharpe(equity: pd.Series, periods_per_year: int = ANNUALIZATION_FACTOR_1H) -> float | None:
    if len(equity) < 2:
        return None
    ret = equity.pct_change().dropna()
    if ret.empty or ret.std() == 0:
        return None
    return float(ret.mean() / ret.std() * sqrt(periods_per_year))


def format_report(
    result: BacktestResult,
    *,
    symbol: str,
    price_df: pd.DataFrame | None = None,
) -> str:
    eq = result.equity_curve["equity"]
    trades = result.trades
    lines: list[str] = [f"=== Backtest: {symbol} ==="]

    bnh = buy_and_hold_pct(price_df)
    lines.append(f"Bars:            {len(eq)}")
    lines.append(f"Trades:          {len(trades)}")

    if trades:
        wins = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]
        gross_profit = sum(t.pnl_usd for t in wins)
        gross_loss = abs(sum(t.pnl_usd for t in losses))
        win_rate = len(wins) / len(trades) * 100
        avg_win = (gross_profit / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        expectancy = sum(t.pnl_usd for t in trades) / len(trades)
        avg_bars = sum(t.bars_held for t in trades) / len(trades)
        lines += [
            f"Win rate:        {win_rate:.1f}%",
            f"Profit factor:   {pf:.2f}" if pf != float("inf") else "Profit factor:   inf",
            f"Avg win:         ${avg_win:,.2f}",
            f"Avg loss:       -${avg_loss:,.2f}",
            f"Expectancy/trd:  ${expectancy:,.2f}",
            f"Avg bars held:   {avg_bars:.1f}",
        ]

    mdd = max_drawdown_pct(eq)
    sharpe = annualized_sharpe(eq)
    lines += [
        f"Total return:    {result.total_return_pct:+.2f}%",
        f"Max drawdown:    {mdd:.2f}%",
    ]
    if sharpe is not None:
        lines.append(f"Sharpe (annual): {sharpe:.2f}")
    if bnh is not None:
        edge = result.total_return_pct - bnh
        lines.append(f"Buy & hold:      {bnh:+.2f}%   (edge: {edge:+.2f}%)")
    if not eq.empty:
        lines.append(f"Final equity:    ${eq.iloc[-1]:,.2f}")

    if trades:
        lines.append("")
        lines.append("Exit reasons:")
        for r, n in Counter(t.exit_reason for t in trades).most_common():
            lines.append(f"  {r}: {n}")
        lines.append("")
        lines.append("Last 5 trades:")
        for t in trades[-5:]:
            lines.append(
                f"  {t.entry_time}  ->  {t.exit_time}  "
                f"{t.pnl_pct:+6.2f}%  ({t.exit_reason})"
            )
    else:
        lines.append("  (no trades — loosen params or use longer history)")

    return "\n".join(lines)
