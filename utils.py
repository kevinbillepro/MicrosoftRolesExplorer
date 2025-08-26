import pandas as pd

def roles_to_dataframe(roles):
    if not roles:
        return pd.DataFrame()
    return pd.DataFrame(roles)
