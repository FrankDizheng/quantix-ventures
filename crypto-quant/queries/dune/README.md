# Dune SQL queries

Put `.sql` files here. Reference from Python:

```python
from pathlib import Path
from crypto_quant.data.dune_client import DuneClient

sql = DuneClient.load_sql(Path("queries/dune/my_query.sql"))
with DuneClient() as dune:
    df = dune.execute_sql(sql)
```

Test queries in [Dune](https://dune.com) or via Cursor Dune MCP first.
