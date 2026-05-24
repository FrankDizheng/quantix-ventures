"""Bar-by-bar backtest with realistic execution + ATR stop + trailing exit.

Execution choices (documented for learning):
  - Entry fills at *next* bar's open after a trigger (no lookahead).
  - Initial stop: entry - atr_stop_mult * ATR_at_entry.
  - Trailing stop: highest CLOSE since entry * (1 - trail_pct/100).
    Updated only AT bar close (so it never references intra-bar future highs).
  - On the entry bar we check the same bar's L for stop hit (rare but possible).
  - If both initial-stop and trail-stop are touched in the same bar, the
    HIGHER (closer to price) wins — that is the actual binding stop.
  - Slippage is symmetric (entry pays up, exit pays down).
  - Mark-to-market: equity curve uses bar close; max drawdown reflects
    intra-trade pain, not just realized PnL.
  - One position at a time, fixed fraction of equity per trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from crypto_quant.strategy.ignition import IgnitionConfig


@dataclass
class BacktestConfig:
    initial_capital: float = 10_000.0
    fee_rate: float = 0.0004        # per side (taker)
    slippage_pct: float = 0.05      # per side, in percent (0.05 = 5 bps)
    position_fraction: float = 1.0


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    bars_held: int
    pnl_pct: float
    pnl_usd: float
    exit_reason: str


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    config: BacktestConfig = field(default_factory=BacktestConfig)
    strategy_config: IgnitionConfig = field(default_factory=IgnitionConfig)

    @property
    def num_trades(self) -> int:
        return len(self.trades)

    @property
    def total_return_pct(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        start = self.config.initial_capital
        end = float(self.equity_curve["equity"].iloc[-1])
        return (end / start - 1) * 100

    @property
    def win_rate_pct(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl_usd > 0)
        return wins / len(self.trades) * 100


def run_backtest(
    df: pd.DataFrame,
    *,
    strat_cfg: IgnitionConfig,
    bt_cfg: BacktestConfig | None = None,
) -> BacktestResult:
    bt_cfg = bt_cfg or BacktestConfig()
    trigger_col = "entry_trigger" if "entry_trigger" in df.columns else "entry_signal"
    if trigger_col not in df.columns:
        raise ValueError("DataFrame must include entry_trigger or entry_signal")
    if "atr" not in df.columns:
        raise ValueError(
            "DataFrame missing 'atr' column. Run IgnitionStrategy.generate_signals first."
        )

    equity = bt_cfg.initial_capital
    equity_rows: list[dict] = []
    trades: list[Trade] = []
    position: dict | None = None
    last_exit_i = -(10**9)

    slip = bt_cfg.slippage_pct / 100
    max_bars = strat_cfg.max_hold_hours
    cooldown = strat_cfg.cooldown_bars
    atr_mult = strat_cfg.atr_stop_mult
    trail_frac = strat_cfg.trail_pct / 100 if strat_cfg.trail_pct > 0 else 0.0

    triggers = df[trigger_col].astype(bool).tolist()
    opens = df["open"].astype(float).tolist()
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    closes = df["close"].astype(float).tolist()
    atr = df["atr"].astype(float).tolist()
    times = df["timestamp"].tolist()

    for i in range(len(df)):
        ts = times[i]

        # 1) Open from previous bar's trigger
        if (
            position is None
            and i > 0
            and triggers[i - 1]
            and (i - last_exit_i) > cooldown
        ):
            fill = opens[i] * (1 + slip)
            atr_at_entry = atr[i - 1] if pd.notna(atr[i - 1]) else 0.0
            initial_stop = (
                fill - atr_mult * atr_at_entry
                if atr_mult > 0 and atr_at_entry > 0
                else fill * (1 - max(strat_cfg.stop_loss_pct, 6.0) / 100)
            )
            position = {
                "entry_i": i,
                "entry_time": ts,
                "entry_price": fill,
                "size": equity * bt_cfg.position_fraction,
                "initial_stop": initial_stop,
                "highest_close": fill,  # init for trail; updated at end of bar
            }

        # 2) Manage position using THIS bar's H/L/C
        if position is not None:
            bars_held = i - position["entry_i"]
            entry_px = position["entry_price"]
            size = position["size"]

            # Active stop = max(initial_stop, trailing_stop_from_highest_close)
            trail_stop = (
                position["highest_close"] * (1 - trail_frac) if trail_frac > 0 else 0.0
            )
            active_stop = max(position["initial_stop"], trail_stop)

            exit_px: float | None = None
            reason = ""
            if lows[i] <= active_stop:
                exit_px = active_stop * (1 - slip)
                reason = (
                    "trail_stop" if trail_stop > position["initial_stop"] else "initial_stop"
                )
            elif bars_held >= max_bars:
                exit_px = closes[i] * (1 - slip)
                reason = "max_hold"

            if exit_px is not None:
                gross_ret = (exit_px - entry_px) / entry_px
                entry_notional = size
                exit_notional = size * (1 + gross_ret)
                fees = (entry_notional + exit_notional) * bt_cfg.fee_rate
                pnl_usd = size * gross_ret - fees
                equity += pnl_usd
                trades.append(
                    Trade(
                        entry_time=position["entry_time"],
                        exit_time=ts,
                        entry_price=entry_px,
                        exit_price=exit_px,
                        bars_held=bars_held,
                        pnl_pct=gross_ret * 100,
                        pnl_usd=pnl_usd,
                        exit_reason=reason,
                    )
                )
                position = None
                last_exit_i = i

        # 3) Update trailing reference & mark-to-market at bar close
        if position is not None:
            if closes[i] > position["highest_close"]:
                position["highest_close"] = closes[i]
            unreal = (closes[i] - position["entry_price"]) / position["entry_price"]
            mtm = equity + position["size"] * unreal
        else:
            mtm = equity
        equity_rows.append({"timestamp": ts, "equity": mtm})

    return BacktestResult(
        trades=trades,
        equity_curve=pd.DataFrame(equity_rows),
        config=bt_cfg,
        strategy_config=strat_cfg,
    )
