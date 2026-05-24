-- Daily CEX net flow for a token (deposit = into exchange, withdrawal = out).
-- Tables: Dune spellbook cex.flows (adjust if your workspace uses a different name).
-- Params replaced by Python: {{CHAIN}}, {{TOKEN_ADDRESS}}, {{DAYS}}

SELECT
  date_trunc('day', block_time) AS day,
  coalesce(sum(CASE WHEN flow_type = 'deposit' THEN amount_usd ELSE 0 END), 0) AS inflow_usd,
  coalesce(sum(CASE WHEN flow_type = 'withdrawal' THEN amount_usd ELSE 0 END), 0) AS outflow_usd,
  coalesce(sum(CASE WHEN flow_type = 'deposit' THEN amount_usd ELSE 0 END), 0)
    - coalesce(sum(CASE WHEN flow_type = 'withdrawal' THEN amount_usd ELSE 0 END), 0) AS net_inflow_usd
FROM cex.flows
WHERE blockchain = '{{CHAIN}}'
  AND token_address = {{TOKEN_ADDRESS}}
  AND block_time >= now() - interval '{{DAYS}}' day
GROUP BY 1
ORDER BY 1
