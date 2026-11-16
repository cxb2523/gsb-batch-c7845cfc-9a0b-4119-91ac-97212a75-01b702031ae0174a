import { describe, expect, it } from 'vitest';
import rawScript from '../../tools/get-place-names-script.gs?raw';
import {
  BASE_URL,
  NAME_COL,
  PLACE_ID_COL,
  buildSummary,
  retryDelay,
  runFromActiveSheet,
  runPlaceNameLookup,
  type FetchResponse,
  type PlaceNameLookupOptions,
  type SheetLike,
  type SpreadsheetAppLike,
  type UrlFetchAppLike,
} from './placeNameLookup';

/** Builds the 11-column rows the exported CSV produces, with Name in B and PlaceId in H. */
const makeRow = (name: string, placeId: string): string[] => {
  const row = new Array<string>(11).fill('');
  row[NAME_COL] = name;
  row[PLACE_ID_COL] = placeId;
  return row;
};

const makeSheet = (values: string[][]) => {
  const writes: Array<{ row: number; column: number; value: string }> = [];
  const sheet: SheetLike = {
    getDataRange: () => ({ getValues: () => values.map((row) => [...row]) }),
    getRange: (row, column) => ({
      setValue: (value: string) => {
        writes.push({ row, column, value });
        values[row - 1][column - 1] = value;
      },
    }),
  };
  return { sheet, writes };
};

const makeSpreadsheetApp = (sheet: SheetLike) => {
  const alerts: string[] = [];
  const spreadsheetApp: SpreadsheetAppLike = {
    getActiveSpreadsheet: () => ({ getActiveSheet: () => sheet }),
    getUi: () => ({
      alert: (message: string) => {
        alerts.push(message);
      },
    }),
  };
  return { spreadsheetApp, alerts };
};

const okBody = (name: string): FetchResponse => ({
  getResponseCode: () => 200,
  getContentText: () => JSON.stringify({ status: 'OK', result: { name } }),
});

const statusBody = (status: string): FetchResponse => ({
  getResponseCode: () => 200,
  getContentText: () => JSON.stringify({ status }),
});

const httpBody = (code: number, body = 'rate limited'): FetchResponse => ({
  getResponseCode: () => code,
  getContentText: () => body,
});

/**
 * Scripted UrlFetchApp: `handler` decides the response for each call and the
 * fake records every URL so tests can assert what was (and was not) requested.
 */
const makeFetcher = (handler: (url: string, callIndex: number) => FetchResponse) => {
  const urls: string[] = [];
  const urlFetchApp: UrlFetchAppLike = {
    fetch: (url) => {
      urls.push(url);
      return handler(url, urls.length - 1);
    },
  };
  return { urlFetchApp, urls };
};

const makeSleep = () => {
  const calls: number[] = [];
  return { calls, sleep: (ms: number) => calls.push(ms) };
};

const baseOptions = (
  sheet: SheetLike,
  urlFetchApp: UrlFetchAppLike,
  overrides: Partial<PlaceNameLookupOptions> = {},
): PlaceNameLookupOptions => ({
  apiKey: 'TEST_KEY',
  sheet,
  urlFetchApp,
  logger: { log: () => undefined },
  sleep: () => undefined,
  delayMs: 10,
  ...overrides,
});

describe('runPlaceNameLookup', () => {
  it('fills only rows whose Name is empty and PlaceId exists, requesting only the name field', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-1'),
      makeRow('Already named', 'place-2'),
      makeRow('', ''),
      makeRow('   ', 'place-3'),
      makeRow('', '   '),
    ];
    const { sheet, writes } = makeSheet(values);
    const { urlFetchApp, urls } = makeFetcher((url) =>
      url.includes('place-1') ? okBody('Cafe One') : okBody('Park Three'),
    );

    const result = runPlaceNameLookup(baseOptions(sheet, urlFetchApp));

    expect(urls).toHaveLength(2);
    expect(urls[0]).toBe(`${BASE_URL}?place_id=place-1&fields=name&key=TEST_KEY`);
    expect(urls[1]).toBe(`${BASE_URL}?place_id=place-3&fields=name&key=TEST_KEY`);
    expect(urls.every((url) => url.includes('&fields=name&'))).toBe(true);

    // Name goes into column B (index 1) of the matching sheet rows (1-based).
    expect(writes).toEqual([
      { row: 2, column: 2, value: 'Cafe One' },
      { row: 5, column: 2, value: 'Park Three' },
    ]);
    expect(result).toEqual({
      updatedCount: 2,
      notFoundCount: 0,
      requestsMade: 2,
      aborted: false,
      abortedRow: null,
    });
  });

  it('records NOT_FOUND and ZERO_RESULTS as misses without retrying, and counts them in the summary', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-missing'),
      makeRow('', 'place-empty'),
    ];
    const { sheet, writes } = makeSheet(values);
    const { urlFetchApp, urls } = makeFetcher((url) =>
      url.includes('place-missing') ? statusBody('NOT_FOUND') : statusBody('ZERO_RESULTS'),
    );
    const { calls, sleep } = makeSleep();

    const result = runPlaceNameLookup(baseOptions(sheet, urlFetchApp, { sleep }));

    // One request per row: deterministic failures are never retried.
    expect(urls).toHaveLength(2);
    expect(writes).toEqual([]);
    expect(calls).toEqual([10, 10]); // only the inter-row pause, no backoff
    expect(result).toEqual({
      updatedCount: 0,
      notFoundCount: 2,
      requestsMade: 2,
      aborted: false,
      abortedRow: null,
    });

    const summary = buildSummary(result);
    expect(summary).toContain('Filled in: 0 place names.');
    expect(summary).toContain('Not found: 2');
    expect(summary).toContain('API requests made: 2.');
  });

  it('backs off and recovers after consecutive 429 responses, still filling the name', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-throttled'),
    ];
    const { sheet, writes } = makeSheet(values);
    const { urlFetchApp, urls } = makeFetcher((_url, callIndex) => {
      if (callIndex === 0) return httpBody(429);
      if (callIndex === 1) return httpBody(429);
      return okBody('Recovered Place');
    });
    const { calls, sleep } = makeSleep();

    const result = runPlaceNameLookup(baseOptions(sheet, urlFetchApp, { sleep }));

    expect(urls).toHaveLength(3);
    expect(calls).toEqual([500, 1000, 10]); // backoff, backoff, inter-row pause
    expect(writes).toEqual([{ row: 2, column: 2, value: 'Recovered Place' }]);
    expect(result.updatedCount).toBe(1);
    expect(result.notFoundCount).toBe(0);
    expect(result.requestsMade).toBe(3);
    expect(result.aborted).toBe(false);
  });

  it('retries OVER_QUERY_LIMIT and transient network errors', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-throttle-status'),
      makeRow('', 'place-network'),
    ];
    const { sheet, writes } = makeSheet(values);
    const { urlFetchApp, urls } = makeFetcher((url, callIndex) => {
      if (url.includes('place-throttle-status')) {
        return callIndex === 0 ? statusBody('OVER_QUERY_LIMIT') : okBody('Throttle Survivor');
      }
      throw new Error('DNS resolution failed');
    });
    const { calls, sleep } = makeSleep();

    const result = runPlaceNameLookup(baseOptions(sheet, urlFetchApp, { sleep }));

    // Row 2: OVER_QUERY_LIMIT then OK. Row 3: network error twice then OK - but
    // the scripted fetcher always throws for that PlaceId, so retries exhaust.
    expect(urls.filter((url) => url.includes('place-throttle-status'))).toHaveLength(2);
    expect(urls.filter((url) => url.includes('place-network'))).toHaveLength(3);
    expect(writes).toEqual([{ row: 2, column: 2, value: 'Throttle Survivor' }]);
    expect(calls).toEqual([500, 10, 500, 1000]);
    expect(result.updatedCount).toBe(1);
    expect(result.aborted).toBe(true);
    expect(result.abortedRow).toBe(3);
  });

  it('stops the run once retries are exhausted and never requests later rows', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-first'),
      makeRow('', 'place-stuck'),
      makeRow('', 'place-never'),
      makeRow('', 'place-after'),
    ];
    const { sheet, writes } = makeSheet(values);
    const { urlFetchApp, urls } = makeFetcher((url) =>
      url.includes('place-first') ? okBody('First Place') : httpBody(429),
    );
    const { calls, sleep } = makeSleep();

    const result = runPlaceNameLookup(
      baseOptions(sheet, urlFetchApp, { sleep, delayMs: 10, maxAttempts: 3 }),
    );

    // place-first once, place-stuck three times, and nothing after the abort.
    expect(urls.map((url) => url.split('place_id=')[1].split('&')[0])).toEqual([
      'place-first',
      'place-stuck',
      'place-stuck',
      'place-stuck',
    ]);
    expect(urls.some((url) => url.includes('place-never'))).toBe(false);
    expect(urls.some((url) => url.includes('place-after'))).toBe(false);

    // The earlier success is kept; the failing row is not written.
    expect(writes).toEqual([{ row: 2, column: 2, value: 'First Place' }]);
    expect(calls).toEqual([10, 500, 1000]); // inter-row pause, then backoff only

    expect(result).toEqual({
      updatedCount: 1,
      notFoundCount: 0,
      requestsMade: 4,
      aborted: true,
      abortedRow: 3,
    });

    const summary = buildSummary(result);
    expect(summary).toContain('Filled in: 1 place name.');
    expect(summary).toContain('Not found: 0');
    expect(summary).toContain('Stopped at row 3');
    expect(summary).toContain('Unprocessed rows start at row 3');
    expect(summary).toContain('no rollback');
    expect(summary).toContain('Run the lookup again');
    expect(summary).toContain('row 3');
  });

  it('retries an unparseable body and records it as a miss only after retries run out', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-truncated'),
      makeRow('', 'place-later'),
    ];
    const { sheet, writes } = makeSheet(values);
    const { urlFetchApp, urls } = makeFetcher(() => ({
      getResponseCode: () => 200,
      getContentText: () => '<html>gateway error</html>',
    }));
    const { calls, sleep } = makeSleep();

    const result = runPlaceNameLookup(baseOptions(sheet, urlFetchApp, { sleep, maxAttempts: 3 }));

    // Three attempts on the first row, then abort; the second row is untouched.
    expect(urls).toHaveLength(3);
    expect(urls.every((url) => url.includes('place-truncated'))).toBe(true);
    expect(writes).toEqual([]);
    expect(calls).toEqual([500, 1000]);
    expect(result).toEqual({
      updatedCount: 0,
      notFoundCount: 0,
      requestsMade: 3,
      aborted: true,
      abortedRow: 2,
    });
  });

  it('does not retry deterministic 4xx answers such as an invalid key', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-bad-key'),
    ];
    const { sheet, writes } = makeSheet(values);
    const { urlFetchApp, urls } = makeFetcher(() => httpBody(400));
    const { calls, sleep } = makeSleep();

    const result = runPlaceNameLookup(baseOptions(sheet, urlFetchApp, { sleep }));

    expect(urls).toHaveLength(1);
    expect(writes).toEqual([]);
    expect(calls).toEqual([10]); // no backoff
    expect(result.notFoundCount).toBe(1);
    expect(result.aborted).toBe(false);
  });

  it('never writes an error marker into the Name column', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-denied'),
    ];
    const { sheet, writes } = makeSheet(values);
    const { urlFetchApp } = makeFetcher(() => statusBody('REQUEST_DENIED'));

    const result = runPlaceNameLookup(baseOptions(sheet, urlFetchApp));

    expect(writes).toEqual([]);
    expect(result.notFoundCount).toBe(1);
  });
});

describe('runPlaceNameLookup with Apps Script globals', () => {
  it('falls back to the injected UrlFetchApp and SpreadsheetApp globals', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-global'),
    ];
    const { sheet, writes } = makeSheet(values);
    const { spreadsheetApp } = makeSpreadsheetApp(sheet);
    const { urlFetchApp, urls } = makeFetcher(() => okBody('Global Place'));

    const globals = globalThis as unknown as Record<string, unknown>;
    const previous = {
      SpreadsheetApp: globals.SpreadsheetApp,
      UrlFetchApp: globals.UrlFetchApp,
      Utilities: globals.Utilities,
      Logger: globals.Logger,
    };
    globals.SpreadsheetApp = spreadsheetApp;
    globals.UrlFetchApp = urlFetchApp;
    globals.Utilities = { sleep: () => undefined };
    globals.Logger = { log: () => undefined };

    try {
      // Only the API key is passed: everything else comes from the globals the
      // same way it does inside Apps Script.
      const result = runPlaceNameLookup({ apiKey: 'TEST_KEY', delayMs: 0 });

      expect(urls).toHaveLength(1);
      expect(writes).toEqual([{ row: 2, column: 2, value: 'Global Place' }]);
      expect(result.updatedCount).toBe(1);
    } finally {
      for (const [name, value] of Object.entries(previous)) {
        if (value === undefined) {
          delete globals[name];
        } else {
          globals[name] = value;
        }
      }
    }
  });

  it('throws a clear error when no API key is provided', () => {
    const values = [['Name', 'Address', '', '', '', '', '', 'PlaceId']];
    const { sheet } = makeSheet(values);
    const { urlFetchApp } = makeFetcher(() => okBody('x'));

    expect(() => runPlaceNameLookup(baseOptions(sheet, urlFetchApp, { apiKey: '' }))).toThrow(
      /API key/,
    );
  });
});

describe('runFromActiveSheet', () => {
  it('shows the start prompt and exactly one summary alert', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-1'),
      makeRow('', 'place-2'),
    ];
    const { sheet } = makeSheet(values);
    const { spreadsheetApp, alerts } = makeSpreadsheetApp(sheet);
    const { urlFetchApp } = makeFetcher((url) =>
      url.includes('place-1') ? okBody('One') : statusBody('NOT_FOUND'),
    );

    runFromActiveSheet({
      apiKey: 'TEST_KEY',
      spreadsheetApp,
      urlFetchApp,
      logger: { log: () => undefined },
      sleep: () => undefined,
      delayMs: 10,
    });

    expect(alerts).toHaveLength(2);
    expect(alerts[0]).toContain('Starting Place Name lookup');
    expect(alerts[1]).toContain('Filled in: 1 place name.');
    expect(alerts[1]).toContain('Not found: 1');
  });
});

describe('retryDelay', () => {
  it('doubles the backoff per attempt and caps it', () => {
    expect(retryDelay(1, 500, 4000)).toBe(500);
    expect(retryDelay(2, 500, 4000)).toBe(1000);
    expect(retryDelay(3, 500, 4000)).toBe(2000);
    expect(retryDelay(10, 500, 4000)).toBe(4000);
  });
});

describe('Apps Script file', () => {
  it('stays a self-contained, pastable script with a placeholder key', () => {
    expect(rawScript).not.toMatch(/^\s*(import|export)\s/m);
    expect(rawScript).toContain('var PlaceNameLookup = (function () {');
    expect(rawScript).toContain('function lookupMissingPlaceNames()');
    expect(rawScript).toContain('&fields=name&key=');
    expect(rawScript).toContain("var API_KEY = 'YOUR_API_KEY';");
    // No long Google API key patterns accidentally committed.
    expect(rawScript).not.toMatch(/AIza[0-9A-Za-z_-]{35}/);
  });
});
