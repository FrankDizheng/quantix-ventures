from pathlib import Path

import pandas as pd

from crypto_quant.data.storage import load_dataframe, save_dataframe


def test_parquet_roundtrip(tmp_path: Path) -> None:
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    path = tmp_path / "t.parquet"
    save_dataframe(df, path)
    loaded = load_dataframe(path)
    pd.testing.assert_frame_equal(df, loaded)
