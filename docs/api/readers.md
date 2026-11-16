# Readers

Readers are responsible for parsing raw data into JavaScript objects.

## `CSVReader<T>`
Reads Comma-Separated Values.
- **Constructor**: `new CSVReader(source, options?)`
- **Methods**:
  - `setFieldNamesInFirstRow(boolean)`
  - `setFieldSeparator(string)`

## `JsonReader<T>`
Reads a JSON array of objects.
- **Constructor**: `new JsonReader(source, options?)`

## `HttpReader<T>`
Reads records from a paginated REST endpoint.
- **Constructor**: `new HttpReader(url, options?)`
- **Options**:
  - `method`, `headers`, `body`: request configuration.
  - `resultsPath(response)`: extracts the record array from the response body.
  - `nextPageUrl(response, currentUrl)`: returns the next page URL, or `null` to stop.
  - `schema`: optional schema for validation and type inference.
  - `delayMs`: wait between page requests in milliseconds. Defaults to `0`.
  - `maxRetries`: retries for `429`/`503` responses. Each retry waits for the `Retry-After` header when present, otherwise backs off exponentially. Defaults to `0`.
  - `onPage(pageNumber, pageRecordCount, totalRecordCount)`: called after each successfully parsed page. Throwing from it aborts the collection.
  - `onPageError(url, pageNumber, error)`: called when a page cannot be collected, including after retries are exhausted. Return `'continue'` to skip the page, or `'stop'` to end collection while keeping already-yielded records consumable. When omitted, the error is thrown.

```ts
const reader = new HttpReader('https://api.example.com/users', {
  resultsPath: (res) => res.data,
  nextPageUrl: (res) => res.next,
  delayMs: 200,
  maxRetries: 3,
  onPage: (page, count, total) => console.log(`page ${page}: ${count} (${total} total)`),
  onPageError: (url, page, error) => {
    console.warn(`page ${page} failed at ${url}: ${error.message}`);
    return page < 5 ? 'continue' : 'stop';
  }
});
```

## `ParquetReader<T>`
Reads Apache Parquet files. Requires `parquetjs-lite`.
- **Constructor**: `new ParquetReader(source, options?)`

## `SQLReader<T>`
Reads from a SQL database using a driver-agnostic connection.
- **Constructor**: `new SQLReader(dbClient, query, params?, options?)`
- **Methods**:
  - `setChunkSize(number)`: For paginated reading.
  - `setOrderBy(string)`: Required for stable pagination.

## `XlsxReader<T>`
Reads Excel files. Requires `exceljs`.
- **Constructor**: `new XlsxReader(source, options?)`
