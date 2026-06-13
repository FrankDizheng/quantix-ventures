"""One-off probe for Dune SQL table names. Run: python scripts/probe_dune_sql.py"""
from crypto_quant.data import DuneClient

QUERIES = {
    "cex.flows_sample": """
SELECT * FROM cex.flows WHERE blockchain = 'ethereum' LIMIT 3
""",
    "cex.flows_pepe": """
SELECT token_address, flow_type, count(*) n
FROM cex.flows
WHERE blockchain = 'ethereum'
  AND token_symbol = 'PEPE'
  AND block_time >= now() - interval '90' day
GROUP BY 1, 2
""",
    "cex.flows": """
SELECT date_trunc('day', block_time) AS day,
  coalesce(sum(case when flow_type = 'Inflow' then amount_usd else 0 end), 0) AS inflow_usd,
  coalesce(sum(case when flow_type = 'Outflow' then amount_usd else 0 end), 0) AS outflow_usd
FROM cex.flows
WHERE blockchain = 'ethereum'
  AND token_symbol = 'PEPE'
  AND block_time >= now() - interval '30' day
GROUP BY 1 ORDER BY 1 LIMIT 5
""",
}

if __name__ == "__main__":
    with DuneClient() as client:
        for name, sql in QUERIES.items():
            print(f"--- {name} ---")
            try:
                df = client.execute_sql(sql)
                if name == "cex.flows_sample" and not df.empty:
                    print("columns:", list(df.columns))
                print(df)
            except Exception as e:
                print("FAIL", e)
