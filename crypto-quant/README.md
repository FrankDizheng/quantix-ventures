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
| [Dune Analytics](https://dune.com) | API key (`DUNE_API_KEY`) | On-chain SQL — see [docs/DUNE.md](docs/DUNE.md) |

## Quick start

```bash
cd crypto-quant
python -m venv .venv
source .venv/bin/activate      # macOS/Linux
pip install -e .
cp .env.example .env           # add DUNE_API_KEY for on-chain queries

cq build-pool                  # scan universe (network, ~30s)
cq sync-data                   # download OHLCV + Dune → data/ (network, once)
cq backtest-batch              # read local cache only (~seconds)
```

**You do not need to manually fetch before every backtest.** Data lands in `data/` (gitignored). Re-run `cq sync-data` once per day (or let backtest auto-refresh stale cache).

```bash
cq strategy-rules
cq dune-check                  # verify Dune key (optional)
cq backtest --symbol PEPE/USDT:USDT
```

## CLI commands

| Command | Purpose |
|---------|---------|
| `cq fetch-ccxt` | Live/historical OHLCV from exchanges |
| `cq fetch-binance-vision` | Bulk historical klines |
| `cq scan-perps` | Multi-exchange small-cap perpetual scan |
| `cq build-pool` | Candidate pool with VWAP distance + stage |
| `cq sync-data` | Sync OHLCV + Dune netflow to local `data/` |
| `cq backtest` | Single-symbol Ignition backtest |
| `cq backtest-batch` | Backtest all symbols in latest pool CSV |
| `cq dune-check` | Verify `DUNE_API_KEY` |

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
