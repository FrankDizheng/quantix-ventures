-- Daily CEX net flow for a token (Inflow = into exchange, Outflow = leaving).
-- Table: Dune spellbook cex.flows (flow_type values: Inflow, Outflow, Internal, ...).
-- Params replaced by Python: {{CHAIN}}, {{TOKEN_FILTER}}, {{DAYS}}

SELECT
  date_trunc('day', block_time) AS day,
  coalesce(sum(CASE WHEN flow_type = 'Inflow' THEN amount_usd ELSE 0 END), 0) AS inflow_usd,
  coalesce(sum(CASE WHEN flow_type = 'Outflow' THEN amount_usd ELSE 0 END), 0) AS outflow_usd,
  coalesce(sum(CASE WHEN flow_type = 'Inflow' THEN amount_usd ELSE 0 END), 0)
    - coalesce(sum(CASE WHEN flow_type = 'Outflow' THEN amount_usd ELSE 0 END), 0) AS net_inflow_usd
FROM cex.flows
WHERE blockchain = '{{CHAIN}}'
  AND {{TOKEN_FILTER}}
  AND block_time >= now() - interval '{{DAYS}}' day
GROUP BY 1
ORDER BY 1
