# The back-end part of the Scraper project:

It scraps customer-defined data and sends it to the front-end app. Since the
scraping operation could take a significant amount of time, all communications
between the server and front-end run through socket connections.

Even if the front-end app disconnects, the server saves data until the front-end
app reconnects.

The front-end app saves scraped data to an XLSX file locally on the user's disk.

## Partial failures

Scraping is resilient to local failures:

- A product page that fails no longer produces an empty report row; the rest of
  its batch is still collected and following batches always run.
- When one page of product links fails, the links already collected for that
  subcategory are kept and only its pagination stops.
- After each subcategory, a `subcategorySummary` socket message reports
  `successCount`, `failedCount` and a `failures` array
  (`{ link, error, category, subcategory }`). It is sent in addition to — never
  instead of — the existing `reportGenerated` data message.

## Tests

`npm test` runs the local suite (no network or database required). It uses
controllable fake requests to cover partial product failures, a fully failed
batch, a failed pagination page, and the settlement message.

Upcoming changes:

1. [ ] TypeScript;
2. [x] Store data to DB;
3. [ ] Compare scraped data versions to highlight changes;
