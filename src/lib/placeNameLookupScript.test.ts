/// <reference types="node" />
import { describe, expect, it } from 'vitest';
import vm from 'node:vm';
import rawScript from '../../tools/get-place-names-script.gs?raw';
import {
  buildSummary as moduleSummary,
  runPlaceNameLookup as runModuleLookup,
} from './placeNameLookup';

interface Write {
  row: number;
  column: number;
  value: string;
}

interface ResponseLike {
  code: number;
  body: string;
}

/**
 * Loads the shipping .gs file into an isolated VM context with fake Apps Script
 * globals, so the exact code users paste into the editor is exercised.
 */
const loadScript = (
  values: string[][],
  respond: (url: string, callIndex: number) => ResponseLike,
  options: { throwOnCall?: (callIndex: number) => boolean } = {},
) => {
  const writes: Write[] = [];
  const urls: string[] = [];
  const alerts: string[] = [];
  const sleeps: number[] = [];

  const sheet = {
    getDataRange: () => ({ getValues: () => values.map((row) => [...row]) }),
    getRange: (row: number, column: number) => ({
      setValue: (value: string) => {
        writes.push({ row, column, value });
        values[row - 1][column - 1] = value;
      },
    }),
  };

  const context = {
    console,
    SpreadsheetApp: {
      getActiveSpreadsheet: () => ({ getActiveSheet: () => sheet }),
      getUi: () => ({
        alert: (message: string) => {
          alerts.push(message);
        },
      }),
    },
    UrlFetchApp: {
      fetch: (url: string) => {
        urls.push(url);
        const callIndex = urls.length - 1;
        if (options.throwOnCall?.(callIndex)) {
          throw new Error('Timed out while contacting the server');
        }
        const { code, body } = respond(url, callIndex);
        return {
          getResponseCode: () => code,
          getContentText: () => body,
        };
      },
    },
    Utilities: {
      sleep: (ms: number) => {
        sleeps.push(ms);
      },
    },
    Logger: { log: () => undefined },
  };

  vm.createContext(context);
  new vm.Script(rawScript).runInContext(context);

  const lookup = (context as unknown as {
    PlaceNameLookup: {
      run: (options: Record<string, unknown>) => {
        updatedCount: number;
        notFoundCount: number;
        requestsMade: number;
        aborted: boolean;
        abortedRow: number | null;
      };
      buildSummary: (result: unknown) => string;
      runFromActiveSheet: (options: Record<string, unknown>) => unknown;
    };
  }).PlaceNameLookup;

  return { lookup, writes, urls, alerts, sleeps };
};

const makeRow = (name: string, placeId: string): string[] => {
  const row = new Array<string>(11).fill('');
  row[1] = name; // column B
  row[7] = placeId; // column H
  return row;
};

const ok = (name: string): ResponseLike => ({
  code: 200,
  body: JSON.stringify({ status: 'OK', result: { name } }),
});
const status = (value: string): ResponseLike => ({
  code: 200,
  body: JSON.stringify({ status: value }),
});
const http = (code: number): ResponseLike => ({ code, body: 'quota exceeded' });

const placeIdsOf = (urls: string[]): string[] =>
  urls.map((url) => url.split('place_id=')[1].split('&')[0]);

describe('get-place-names-script.gs (loaded as shipped)', () => {
  it('fills empty names, skips filled rows, and requests only the name field', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-1'),
      makeRow('Already named', 'place-2'),
      makeRow('', ''),
    ];
    const { lookup, writes, urls } = loadScript(values, () => ok('Cafe One'));

    const result = lookup.run({ apiKey: 'TEST_KEY' });

    expect(urls).toHaveLength(1);
    expect(urls[0]).toContain('place_id=place-1');
    expect(urls[0]).toContain('&fields=name&');
    expect(writes).toEqual([{ row: 2, column: 2, value: 'Cafe One' }]);
    expect(result.updatedCount).toBe(1);
    expect(result.notFoundCount).toBe(0);
    expect(result.aborted).toBe(false);
  });

  it('treats NOT_FOUND and ZERO_RESULTS as misses with no retry', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-missing'),
      makeRow('', 'place-empty'),
    ];
    const { lookup, writes, urls } = loadScript(values, (url) =>
      url.includes('place-missing') ? status('NOT_FOUND') : status('ZERO_RESULTS'),
    );

    const result = lookup.run({ apiKey: 'TEST_KEY' });

    expect(placeIdsOf(urls)).toEqual(['place-missing', 'place-empty']);
    expect(writes).toEqual([]);
    expect(result).toEqual({
      updatedCount: 0,
      notFoundCount: 2,
      requestsMade: 2,
      aborted: false,
      abortedRow: null,
    });
  });

  it('recovers from consecutive 429s with backoff and still writes the name', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-throttled'),
    ];
    const { lookup, writes, urls, sleeps } = loadScript(values, (_url, callIndex) =>
      callIndex < 2 ? http(429) : ok('Recovered Place'),
    );

    const result = lookup.run({ apiKey: 'TEST_KEY' });

    expect(urls).toHaveLength(3);
    expect(sleeps).toEqual([500, 1000, 100]);
    expect(writes).toEqual([{ row: 2, column: 2, value: 'Recovered Place' }]);
    expect(result.updatedCount).toBe(1);
    expect(result.aborted).toBe(false);
  });

  it('retries temporary network errors thrown by UrlFetchApp', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-flaky'),
    ];
    const { lookup, writes, urls } = loadScript(
      values,
      () => ok('Flaky But Fine'),
      { throwOnCall: (callIndex) => callIndex === 0 },
    );

    const result = lookup.run({ apiKey: 'TEST_KEY' });

    expect(urls).toHaveLength(2);
    expect(writes).toEqual([{ row: 2, column: 2, value: 'Flaky But Fine' }]);
    expect(result.updatedCount).toBe(1);
  });

  it('aborts after retries are exhausted, keeps earlier writes, and reports the resume row', () => {
    const values = [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-first'),
      makeRow('', 'place-stuck'),
      makeRow('', 'place-never'),
    ];
    const { lookup, writes, urls, alerts } = loadScript(values, (url) =>
      url.includes('place-first') ? ok('First Place') : http(429),
    );

    const result = lookup.runFromActiveSheet({ apiKey: 'TEST_KEY' });

    expect(placeIdsOf(urls)).toEqual([
      'place-first',
      'place-stuck',
      'place-stuck',
      'place-stuck',
    ]);
    expect(writes).toEqual([{ row: 2, column: 2, value: 'First Place' }]);
    expect(result).toMatchObject({ updatedCount: 1, notFoundCount: 0, aborted: true, abortedRow: 3 });

    // One start alert, one summary alert.
    expect(alerts).toHaveLength(2);
    expect(alerts[1]).toContain('Filled in: 1 place name.');
    expect(alerts[1]).toContain('Not found: 0');
    expect(alerts[1]).toContain('Stopped at row 3');
    expect(alerts[1]).toContain('Run the lookup again');
  });

  it('uses the placeholder key and never contains a real Google API key', () => {
    expect(rawScript).toContain("var API_KEY = 'YOUR_API_KEY';");
    expect(rawScript).not.toMatch(/AIza[0-9A-Za-z_-]{35}/);
  });

  it('produces the same summary text as the TypeScript module it mirrors', () => {
    const cases = [
      { updatedCount: 2, notFoundCount: 0, requestsMade: 2, aborted: false, abortedRow: null },
      { updatedCount: 0, notFoundCount: 3, requestsMade: 3, aborted: false, abortedRow: null },
      { updatedCount: 4, notFoundCount: 1, requestsMade: 9, aborted: true, abortedRow: 12 },
    ];

    for (const scenario of cases) {
      const { lookup } = loadScript([['Name', 'PlaceId']], () => ok('unused'));
      expect(lookup.buildSummary(scenario)).toBe(moduleSummary(scenario));
    }
  });

  it('matches the TypeScript module row-for-row on a mixed run', () => {
    const buildRows = () => [
      ['Name', 'Address', '', '', '', '', '', 'PlaceId'],
      makeRow('', 'place-ok'),
      makeRow('Named already', 'place-skip'),
      makeRow('', 'place-missing'),
      makeRow('', 'place-throttle'),
      makeRow('', 'place-after'),
    ];

    // Each attempted URL maps to a scripted response sequence.
    const respond = (url: string): ResponseLike => {
      if (url.includes('place-ok')) return ok('OK Place');
      if (url.includes('place-missing')) return status('NOT_FOUND');
      if (url.includes('place-throttle')) return http(429);
      return ok('Never reached');
    };
    const callCounts = new Map<string, number>();
    const respondOnce = (url: string): ResponseLike => {
      const placeId = url.split('place_id=')[1].split('&')[0];
      callCounts.set(placeId, (callCounts.get(placeId) ?? 0) + 1);
      return respond(url);
    };

    const scriptRows = buildRows();
    const scripted = loadScript(scriptRows, respondOnce);
    const scriptResult = scripted.lookup.run({ apiKey: 'TEST_KEY', delayMs: 10 });

    const moduleRows = buildRows();
    const moduleResult = runModuleLookup({
      apiKey: 'TEST_KEY',
      delayMs: 10,
      sleep: () => undefined,
      logger: { log: () => undefined },
      sheet: {
        getDataRange: () => ({ getValues: () => moduleRows.map((row) => [...row]) }),
        getRange: (row, column) => ({
          setValue: (value: string) => {
            moduleRows[row - 1][column - 1] = value;
          },
        }),
      },
      urlFetchApp: {
        fetch: (url) => {
          const { code, body } = respondOnce(url);
          return { getResponseCode: () => code, getContentText: () => body };
        },
      },
    });

    expect(scriptResult).toEqual(moduleResult);
    expect(scripted.writes.map((write) => write.value)).toEqual(['OK Place']);
    expect(moduleRows).toEqual(scriptRows);
    expect(scriptResult).toEqual({
      updatedCount: 1,
      notFoundCount: 1,
      requestsMade: 5,
      aborted: true,
      abortedRow: 5,
    });
  });
});
