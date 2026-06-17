# Small-Cap Pair Finding Strategy

> Status: research framework. This document defines how we find robust small-cap
> perp pairs before any trading or arbitrage backtest.

## Goal

Find pairs that are:

1. economically plausible,
2. statistically stable over the current small-cap lifecycle,
3. not obviously destroyed by funding,
4. executable at small size,
5. persistent enough to monitor dynamically.

The goal is not to find pairs that look profitable in a backtest. The first goal
is to reject false relationships before trading logic exists.

## Universe Philosophy

Small-cap crypto pairs are not permanent. We assume most useful relationships
live for roughly 2-8 weeks, and only a few survive 3+ months.

That means:

- 30d is the core relationship window.
- 7-14d rolling metrics are used to detect recent breakdowns.
- 90-120d history is background context, not a fitted relationship.
- candidate pools must be refreshed frequently.

## Pipeline

### 1. Group first, then score

We do not start with all market combinations. We first group coins by narrative
or trading behavior in `config/pairs.yaml`, then evaluate within those groups.

Examples:

- `infra_l1`: `ASTER`, `BERA`, `LIT`, `IP`, `EGLD`, `STRK`, `VANA`
- `market_infra`: `AUCTION`, `BERA`, `IP`, `ENS`, `ENSO`
- `gaming_metaverse`: `APE`, `AXS`, `ENJ`, `YGG`, `GMT`
- `ai_data`: `GRASS`, `KAITO`, `EDEN`, `VIRTUAL`, `LPT`

`--all-combos` is allowed only as a diagnostic check, not the default research
mode.

### 2. Relationship filters

Each pair must pass:

- enough overlapping hourly bars,
- full-sample log-price correlation,
- rolling correlation stability,
- stable rolling hedge beta,
- half-life in a tradable range,
- enough z-score excursions,
- acceptable convergence rate.

Rejected pairs are retained with `reject_reason`, so the same statistical noise
does not keep getting rediscovered.

### 3. Cost and carry diagnostics

The framework now records two cost views:

- `cost_edge_ratio`: fee plus conservative fixed slippage assumption.
- `liquidity_cost_edge_ratio`: fee plus current order-book spread.

Funding is diagnostic-only for now:

- `mean_abs_funding_diff_pct`
- `p95_abs_funding_diff_pct`
- `latest_funding_diff_pct`
- `half_life_funding_drag_ratio`

Current active pairs have low funding drag, so funding is not the first-order
risk in this pool.

### 4. Liquidity gates

Order-book snapshots are used to reject pairs that are statistically attractive
but not executable.

Current gates:

- max pair spread: 20 bps,
- minimum weakest-side depth inside 25 bps: $10k,
- minimum liquidity-adjusted cost edge: 1.5x.

This rejected `EDEN / KAITO`: it had interesting spread behavior, but weakest
25 bps depth was only about $5.3k.

## Current Result

Latest expanded OKX grouped run:

- 41 symbols from `config/pairs.yaml`
- 161 grouped candidate pairs
- 18 latest-snapshot `active_research`
- 9 latest-snapshot `watchlist`
- 134 rejected with retained reasons

Persistence-adjusted pool:

| Tier | Pair | Why it survives |
|---|---|---|
| persistent_active | `BERA / IP` | passed 6 of 6 snapshots as active with stable correlation, beta, liquidity, and cost edge |
| persistent_active | `AUCTION / IP` | passed 6 of 6 snapshots as active with the strongest stability score in the original core |
| persistent_watchlist | `AUCTION / BERA` | passed every snapshot but remains below active stability threshold |
| persistent_watchlist | `APE / AXS` | clean narrative pair, but rolling stability is not active-grade |

The expanded scan produced 21 `new_candidate` pairs, including `BERA / ENS`,
`EGLD / VANA`, `GMX / UMA`, and `LPT / VIRTUAL`. They are not promoted yet:
they need repeated snapshots before becoming persistent candidates.

## Dynamic Candidate Maintenance

The pool should be maintained as:

- `persistent_active`: repeatedly active across snapshots; eligible for future trading research.
- `persistent_watchlist`: repeatedly passes, but below active stability/score threshold.
- `new_candidate`: passes the latest expanded run, but has not persisted yet.
- `rejected_or_decayed`: failed, quarantined, or degraded; keep with reasons.

The `pair-persistence` command exports direct candidate-pool files:

- `pairs_okx_persistent_active_latest.csv`: the only pool eligible for initial trading research.
- `pairs_okx_persistent_watchlist_latest.csv`: monitor and retest, but do not trade-research yet.
- `pairs_okx_new_candidate_latest.csv`: newly discovered relationships that need repeated snapshots.

Suggested cadence:

- refresh data daily,
- review active/watchlist every 2-3 days,
- promote only after repeated passing snapshots,
- downgrade immediately on rolling correlation breakdown, beta drift, weak
  convergence, funding drag, or liquidity deterioration.

## Next Research Step

Next, run the expanded universe repeatedly and compare `new_candidate` pairs
against the persistent core. A pair should only graduate when it survives several
snapshots without rolling-correlation breakdown, beta drift, liquidity decay, or
weak convergence.
