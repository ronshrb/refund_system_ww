import pandas as pd

def load_table(table_name: str) -> pd.DataFrame:
    return pd.read_csv(f"{table_name}.csv")

