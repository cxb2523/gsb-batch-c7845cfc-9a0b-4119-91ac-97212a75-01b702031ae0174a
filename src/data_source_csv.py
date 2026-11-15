import pandas as pd


def load_data_from_csv(filepath="data/sales_data.csv"):
    # Raw load only — date parsing/cleaning is done centrally
    # by src.data_cleaning.clean_sales_data.
    return pd.read_csv(filepath)
