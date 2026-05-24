# crypto-quant

Toolkit for **crypto quantitative research**: open-source market data, small-cap perpetual scanning, cost-zone proxies (VWAP), Ignition strategy (Donchian breakout + ATR stop + trailing exit), and batch backtesting.

**No API keys required** for default public-data flows.

Part of the [quantix-ventures](https://github.com/FrankDizheng/quantix-ventures) monorepo (company site at repo root).

## Data sources

| Source | Access | What you get |
|--------|--------|--------------|
| [CCXT](https://github.com/ccxt/ccxt) | MIT, public REST | OHLCV, trades, funding rates |
| [Binance Data Collection](https://data.binance.vision/) | Public archive | Bulk klines, aggTrades |
| [CoinGecko API](https://www.coingecko.com/en/api) | Free tier | Aggregated prices, OHLC |

## Quick start

```bash
cd crypto-quant
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e .

cq strategy-rules
cq build-pool                     # liquidity filter + cost-zone tags
cq backtest-batch                 # Ignition v3 on pool symbols
cq backtest --symbol WIF/USDT:USDT
```

Output lands under `data/` (gitignored).

## CLI commands

| Command | Purpose |
|---------|---------|
| `cq fetch-ccxt` | Live/historical OHLCV from exchanges |
| `cq fetch-binance-vision` | Bulk historical klines |
| `cq scan-perps` | Multi-exchange small-cap perpetual scan |
| `cq build-pool` | Candidate pool with VWAP distance + stage |
| `cq backtest` | Single-symbol Ignition backtest |
| `cq backtest-batch` | Backtest all symbols in latest pool CSV |

## Project layout

```
crypto-quant/
├── config/default.yaml
├── src/crypto_quant/
│   ├── cli.py
│   ├── data/           # CCXT, Binance Vision, CoinGecko
│   ├── scan/           # perp scanner
│   ├── strategy/       # Ignition + cost_zone
│   ├── backtest/       # engine + batch
│   └── pool.py         # candidate pool builder
└── tests/
```

## License

MIT
