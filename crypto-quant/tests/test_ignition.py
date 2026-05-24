import pandas as pd

from crypto_quant.backtest import BacktestConfig, run_backtest
from crypto_quant.strategy import IgnitionConfig, IgnitionStrategy


def _synthetic_uptrend(n: int = 200) -> pd.DataFrame:
  ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
  close = pd.Series(range(100, 100 + n), dtype=float)
  return pd.DataFrame(
    {
      "timestamp": ts,
      "open": close - 0.5,
      "high": close + 1,
      "low": close - 1,
      "close": close,
      "volume": 1000 + (close % 10) * 50,
    }
  )


def test_backtest_runs_on_signals() -> None:
  cfg = IgnitionConfig(ret_min_pct=0.1, ret_max_pct=50, vol_mult=0.5)
  df = IgnitionStrategy(cfg).generate_signals(_synthetic_uptrend())
  result = run_backtest(df, strat_cfg=cfg, bt_cfg=BacktestConfig(initial_capital=1000))
  assert result.equity_curve.shape[0] == len(df)
