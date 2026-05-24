# Dune integration

[Dune](https://dune.com) indexes on-chain data into SQL tables. Use it for exchange netflows, holder stats, and Smart Money–style queries that Binance APIs cannot provide.

## API key (never commit the real value)

1. Create a key at [Dune → Settings → API](https://dune.com/settings/api).
2. Copy `.env.example` → `.env` in `crypto-quant/`:

   ```bash
   cp .env.example .env
   # Edit .env and set:
   DUNE_API_KEY=your_key_here
   ```

3. `config/default.yaml` reads the key from the **`DUNE_API_KEY`** environment variable (see `dune.api_key_env`).

If a key was pasted into chat or committed by mistake, **rotate it** in Dune settings immediately.

## Python / `cq` CLI

```powershell
cd crypto-quant
$env:DUNE_API_KEY = "your_key"   # or use .env + python-dotenv later
cq dune-check                    # verify key works
```

Config block in `config/default.yaml`:

```yaml
dune:
  api_key_env: DUNE_API_KEY
  api_base: https://api.dune.com/api/v1
  mcp_url: https://api.dune.com/mcp/v1
  default_chain: ethereum
```

## Cursor MCP

Add to your **user-level** Cursor MCP config (not in git). Example: `config/cursor-mcp.example.json`.

Merge into Cursor settings → MCP:

```json
{
  "mcpServers": {
    "dune": {
      "url": "https://api.dune.com/mcp/v1",
      "headers": {
        "X-DUNE-API-KEY": "<paste your key locally only>"
      }
    }
  }
}
```

Use the same key as `DUNE_API_KEY` in `.env` so Python and Cursor stay in sync.

**Do not** put the live API key in this repository.

## SQL queries in-repo

Versioned query templates live under `queries/dune/`. Run them from Dune’s UI first, then wire results into `data/onchain/` via `cq fetch-onchain` (coming next).

## REST vs MCP

| Tool | Use for |
|------|---------|
| **MCP in Cursor** | Explore schemas, draft SQL with the agent |
| **Python `DuneClient`** | Automate execution, save parquet for backtests |

Both use header `X-DUNE-API-KEY`.
