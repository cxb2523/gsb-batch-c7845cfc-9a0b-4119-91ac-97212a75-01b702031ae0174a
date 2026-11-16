# Google Sheets Place Name Lookup Script

This script is useful if you want to get the actual place name for newer timeline records. It automatically looks up and fills in missing **Place Names** in your sheet using the Google Maps Platform **Place IDs** you already have.

**Why Use This Script?** Records from older timeline data (semantic history) contained Name and Address columns. Newer records from `timeline.json` don't have this information. Because all timeline records have a **PlaceId**, you can retrieve the place names by using the Google Maps API.

## Setup and Installation

### 1. Prepare Your Data

Make sure your spreadsheet data is ready. If you downloaded the CSV from the app the columns should already be in the correct location.

* The **Name** column must be column **B**.
* The **PlaceId** column must be column **H**.

### 2. Install the Script

1. Upload your CSV to Google Sheets.
2.  In your Google Sheet, go to **Extensions > Apps Script**.
3.  Replace all the content in the default `Code.gs` file with the contents of the attached `get-place-names-script.gs` file.
4.  Save the script (File > Save).

### 3. Get Your API Key

The script needs a Google Maps Platform API Key to talk to the Place Details API.

1.  Go to the **Google Cloud Console**.
2.  Create a new project (or select an existing one).
3.  Enable the **Places API** for that project.
4.  Generate an **API Key**.

### 4. Insert the API Key

In the script you just pasted into `Code.gs`, find this line near the top: `var API_KEY = 'YOUR_API_KEY';`

Replace `'YOUR_API_KEY'` with the key you generated in the Google Cloud Console. Never commit a real key to this repository.

## Usage

1.  Return to your Google Sheet and **reload the page**. This is needed for the custom menu to appear.
2.  A new menu item will appear at the top: **Lookup Place Names**.
3.  Click the menu item, then click **Run Name Lookup**.
4.  The script starts and finishes with a single summary alert:
    * how many names were filled in,
    * how many rows had no usable name (`NOT_FOUND` / `ZERO_RESULTS`),
    * if it stopped early, the row where it stopped and the rows that were never requested.

## Rate Limits and Errors

Google may throttle the Place Details API. The script handles this instead of silently giving up:

* **Retried** (up to 3 attempts per row, 500 ms → 1000 ms backoff): HTTP 429, HTTP 5xx, the
  `OVER_QUERY_LIMIT` and `UNKNOWN_ERROR` statuses, and temporary network errors thrown by
  `UrlFetchApp`.
* **Not retried** (recorded as "not found"): `NOT_FOUND`, `ZERO_RESULTS`, `REQUEST_DENIED` and other
  deterministic answers, plus HTTP 4xx responses such as an invalid API key. Retrying these only
  spends quota.
* **Stops the run**: if a row still fails after all retries, the script stops immediately so it does
  not keep consuming quota. Rows after that point are never requested.
* **No rollback**: names written before the stop stay in the sheet.

Running the lookup again skips every row that already has a name, so you can safely re-run after a
throttle or after fixing your API key. After an abort, the summary names the row where it stopped;
the retry starts at that row and skips the names that were already filled in.

**PlaceIds that genuinely have no name** stay empty on every run. That is expected — the summary
counts them separately from API failures.

## Cost Optimization Note

This script is designed to be cost-effective. Google currently allows 10,000 free requests per month for this SKU.

* It only makes a lookup request if the `Name` column is empty and a `PlaceId` exists.
* It explicitly requests *only* the `name` field. This optimization ensures the request falls under the low-cost **Place Details Essentials** SKU.
* If your CSV contains less than 10k records then you should not incur any fees. That said, there have been changes to their pricing model recently, so you should double check the [current API pricing](https://mapsplatform.google.com/pricing/).

## Running the Tests

The lookup logic lives in `tools/get-place-names-script.gs` and is mirrored in
`src/lib/placeNameLookup.ts` so it can be imported by Vitest. The tests inject fake
`UrlFetchApp` / `SpreadsheetApp` objects — no Apps Script runtime and no real API key are needed.

```bash
npm install
npm test
```

* `src/lib/placeNameLookup.test.ts` — normal lookups, `NOT_FOUND`, repeated 429s followed by
  recovery, retry exhaustion, 4xx handling, and the summary counts.
* `src/lib/placeNameLookupScript.test.ts` — loads the actual `.gs` file in a Node VM with fake Apps
  Script globals and checks that the shipped script behaves identically to the module (same writes,
  same counters, same summary text).

