import pandas as pd


def load_data_from_gsheet(sheet_name, worksheet_name):
    # Imported lazily so CSV/SQLite runs don't require gspread installed.
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "config/credentials.json", scope)
    client = gspread.authorize(creds)

    print("📥 Fetching data from Google Sheets...")
    sheet = client.open(sheet_name)
    worksheet = sheet.worksheet(worksheet_name)

    data = worksheet.get_all_records()
    # Raw load only — date parsing/cleaning is done centrally
    # by src.data_cleaning.clean_sales_data.
    df = pd.DataFrame(data)

    print(f"✅ Loaded {len(df)} rows from {sheet_name}/{worksheet_name}")
    return df
