/**
 * Core logic for the Google Sheets "Lookup Place Names" Apps Script.
 *
 * `tools/get-place-names-script.gs` is a self-contained copy of this behaviour
 * for pasting into Apps Script. This module exists so the same loop, retry and
 * summary rules can be exercised locally with Vitest against fake
 * `UrlFetchApp` / `SpreadsheetApp` objects.
 */

/** Column B ("Name") and column H ("PlaceId") as 0-based indexes. */
export const NAME_COL = 1;
export const PLACE_ID_COL = 7;

export const BASE_URL = 'https://maps.googleapis.com/maps/api/place/details/json';

/** Pause between rows so high-volume runs stay below the per-second quota. */
export const DEFAULT_DELAY_MS = 100;
/** The first request plus two retries. */
export const DEFAULT_MAX_ATTEMPTS = 3;
export const DEFAULT_RETRY_BASE_DELAY_MS = 500;
export const DEFAULT_MAX_RETRY_DELAY_MS = 4000;

export interface FetchResponse {
  getResponseCode?: () => number;
  getContentText: () => string;
}

export interface UrlFetchAppLike {
  fetch: (url: string, params?: { muteHttpExceptions?: boolean }) => FetchResponse;
}

export interface SheetRangeLike {
  setValue: (value: string) => unknown;
}

export interface SheetLike {
  getDataRange: () => { getValues: () => unknown[][] };
  getRange: (row: number, column: number) => SheetRangeLike;
}

export interface SpreadsheetAppLike {
  getActiveSpreadsheet: () => { getActiveSheet: () => SheetLike };
  getUi: () => { alert: (message: string) => unknown };
}

export interface LoggerLike {
  log: (message: string) => unknown;
}

export interface UtilitiesLike {
  sleep: (ms: number) => unknown;
}

export interface PlaceNameLookupOptions {
  apiKey?: string;
  sheet?: SheetLike;
  spreadsheetApp?: SpreadsheetAppLike;
  urlFetchApp?: UrlFetchAppLike;
  utilities?: UtilitiesLike;
  logger?: LoggerLike;
  sleep?: (ms: number) => unknown;
  delayMs?: number;
  maxAttempts?: number;
  retryBaseDelayMs?: number;
  maxRetryDelayMs?: number;
}

export interface PlaceNameLookupResult {
  /** Rows where a name was written. */
  updatedCount: number;
  /** Rows where the API answered deterministically without a usable name. */
  notFoundCount: number;
  /** HTTP requests actually issued (retries included). */
  requestsMade: number;
  /** True when the run stopped early and later rows were never requested. */
  aborted: boolean;
  /** 1-based sheet row where the run stopped, or null when it finished. */
  abortedRow: number | null;
}

type LookupOutcome =
  | { kind: 'ok'; name: string }
  | { kind: 'miss'; status: string }
  | { kind: 'exhausted'; reason: string };

const isBlank = (value: unknown): boolean => value === null || value === undefined || String(value).trim() === '';

const readGlobal = (name: string): unknown =>
  typeof globalThis !== 'undefined' ? (globalThis as unknown as Record<string, unknown>)[name] : undefined;

const requireGlobal = <T>(name: string): T => {
  const value = readGlobal(name);
  if (!value) {
    throw new Error(`${name} is not available. Run this script inside Google Sheets.`);
  }
  return value as T;
};

// The legacy API reports throttling either as HTTP 429 or as an OVER_QUERY_LIMIT
// status in a 200 response. UNKNOWN_ERROR is also transient per the API docs.
const isRetryableStatus = (status: unknown): boolean =>
  status === 'OVER_QUERY_LIMIT' || status === 'UNKNOWN_ERROR';

export const retryDelay = (attempt: number, baseDelayMs: number, maxDelayMs: number): number =>
  Math.min(baseDelayMs * 2 ** (attempt - 1), maxDelayMs);

interface RequestConfig {
  urlFetchApp: UrlFetchAppLike;
  url: string;
  rowNumber: number;
  maxAttempts: number;
  baseDelayMs: number;
  maxDelayMs: number;
  sleep: (ms: number) => unknown;
  log: (message: string) => void;
  onRequest: () => void;
}

/** Requests one PlaceId, retrying temporary failures with exponential backoff. */
const requestPlaceName = (config: RequestConfig): LookupOutcome => {
  let attempt = 0;

  while (attempt < config.maxAttempts) {
    attempt += 1;
    let retryReason: string | null = null;

    try {
      config.onRequest();
      const response = config.urlFetchApp.fetch(config.url, { muteHttpExceptions: true });
      const httpCode = typeof response.getResponseCode === 'function' ? response.getResponseCode() : 200;
      const body = typeof response.getContentText === 'function' ? response.getContentText() : '';

      if (httpCode === 429 || httpCode === 408 || httpCode >= 500) {
        retryReason = `HTTP ${httpCode}`;
      } else if (httpCode >= 400) {
        // Other 4xx answers (bad key, bad PlaceId request) will not improve on retry.
        return { kind: 'miss', status: `HTTP ${httpCode}` };
      } else {
        let json: { status?: string; result?: { name?: string } };
        try {
          json = (JSON.parse(body) ?? {}) as typeof json;
        } catch {
          // A truncated or proxied body is a temporary transport problem.
          retryReason = 'unparseable response body';
          json = {};
        }

        if (retryReason === null) {
          const status = json.status ?? 'NO_STATUS';
          if (status === 'OK' && json.result?.name) {
            return { kind: 'ok', name: json.result.name };
          }
          if (isRetryableStatus(status)) {
            retryReason = status;
          } else {
            // NOT_FOUND, ZERO_RESULTS, REQUEST_DENIED and friends are deterministic:
            // retrying only spends quota.
            return { kind: 'miss', status: status === 'OK' ? 'OK_WITHOUT_NAME' : status };
          }
        }
      }
    } catch (error) {
      // UrlFetchApp throws on network/DNS/timeout failures; those are temporary.
      retryReason = error instanceof Error ? error.message : String(error);
    }

    if (attempt >= config.maxAttempts) {
      return { kind: 'exhausted', reason: retryReason ?? 'retry budget exhausted' };
    }

    const backoffMs = retryDelay(attempt, config.baseDelayMs, config.maxDelayMs);
    config.log(
      `Row ${config.rowNumber}: ${retryReason}. Retrying in ${backoffMs} ms ` +
        `(attempt ${attempt + 1} of ${config.maxAttempts}).`,
    );
    config.sleep(backoffMs);
  }

  return { kind: 'exhausted', reason: 'retry budget exhausted' };
};

const makeSleep = (options: PlaceNameLookupOptions): ((ms: number) => unknown) => {
  if (typeof options.sleep === 'function') return options.sleep;
  const utilities = options.utilities ?? requireGlobal<UtilitiesLike>('Utilities');
  return (ms) => utilities.sleep(ms);
};

const makeLogger = (options: PlaceNameLookupOptions): ((message: string) => void) => {
  const logger = options.logger ?? (readGlobal('Logger') as LoggerLike | undefined);
  return (message) => {
    if (logger && typeof logger.log === 'function') logger.log(message);
  };
};

/**
 * Fills empty Name cells for rows that have a PlaceId.
 *
 * Successful writes happen immediately and are never rolled back. A row whose
 * retries are exhausted stops the run so no further quota is spent.
 */
export const runPlaceNameLookup = (options: PlaceNameLookupOptions): PlaceNameLookupResult => {
  if (!options.apiKey) {
    throw new Error('Missing Google Maps Platform API key.');
  }

  const urlFetchApp = options.urlFetchApp ?? requireGlobal<UrlFetchAppLike>('UrlFetchApp');
  const sleep = makeSleep(options);
  const log = makeLogger(options);

  const sheet =
    options.sheet ??
    (options.spreadsheetApp ?? requireGlobal<SpreadsheetAppLike>('SpreadsheetApp'))
      .getActiveSpreadsheet()
      .getActiveSheet();
  const values = sheet.getDataRange().getValues();

  const delayMs = options.delayMs ?? DEFAULT_DELAY_MS;
  const maxAttempts = Math.max(1, options.maxAttempts || DEFAULT_MAX_ATTEMPTS);
  const baseDelayMs = options.retryBaseDelayMs || DEFAULT_RETRY_BASE_DELAY_MS;
  const maxDelayMs = options.maxRetryDelayMs || DEFAULT_MAX_RETRY_DELAY_MS;

  const result: PlaceNameLookupResult = {
    updatedCount: 0,
    notFoundCount: 0,
    requestsMade: 0,
    aborted: false,
    abortedRow: null,
  };

  // Start at index 1 (row 2) to skip the header row.
  for (let i = 1; i < values.length; i += 1) {
    const row = values[i] ?? [];
    const nameValue = row[NAME_COL];
    const placeId = row[PLACE_ID_COL];

    // Only request a name when Name (B) is empty AND PlaceId (H) exists.
    if (!isBlank(nameValue) || isBlank(placeId)) continue;

    const rowNumber = i + 1;
    const requestUrl =
      `${BASE_URL}?place_id=${encodeURIComponent(String(placeId))}&fields=name&key=${options.apiKey}`;

    const outcome = requestPlaceName({
      urlFetchApp,
      url: requestUrl,
      rowNumber,
      maxAttempts,
      baseDelayMs,
      maxDelayMs,
      sleep,
      log,
      onRequest: () => {
        result.requestsMade += 1;
      },
    });

    if (outcome.kind === 'ok') {
      // Write immediately: names already filled in survive any later abort.
      sheet.getRange(rowNumber, NAME_COL + 1).setValue(outcome.name);
      result.updatedCount += 1;
      log(`Updated row ${rowNumber}: ${outcome.name}`);
    } else if (outcome.kind === 'miss') {
      result.notFoundCount += 1;
      log(`No name for row ${rowNumber} (PlaceId ${placeId}): ${outcome.status}`);
    } else {
      result.aborted = true;
      result.abortedRow = rowNumber;
      log(`Stopped at row ${rowNumber} (PlaceId ${placeId}): ${outcome.reason}`);
      break;
    }

    sleep(delayMs);
  }

  return result;
};

/** Builds the single end-of-run summary shown to the user. */
export const buildSummary = (result: PlaceNameLookupResult): string => {
  const lines = ['Place name lookup finished.'];
  lines.push(`Filled in: ${result.updatedCount} place name${result.updatedCount === 1 ? '' : 's'}.`);
  lines.push(
    `Not found: ${result.notFoundCount} ` +
      '(no usable name: NOT_FOUND, ZERO_RESULTS, or a deterministic API error; these rows still ' +
      'have an empty Name).',
  );
  lines.push(`API requests made: ${result.requestsMade}.`);

  if (result.aborted && result.abortedRow !== null) {
    lines.push(
      `Stopped at row ${result.abortedRow} after repeated rate-limit or network errors. ` +
        'Names filled in before that point were kept (no rollback).',
    );
    lines.push(
      `Unprocessed rows start at row ${result.abortedRow}: that row was not filled in and later ` +
        'rows were not requested.',
    );
    lines.push(
      'Run the lookup again to continue from the first unprocessed row ' +
        `(row ${result.abortedRow}); rows that already have a name are skipped.`,
    );
  } else if (result.notFoundCount > 0) {
    lines.push(
      'All rows with a PlaceId were processed. Run the lookup again later to try the rows that are ' +
        'still empty.',
    );
  } else {
    lines.push('Every row with a PlaceId now has a name.');
  }

  return lines.join('\n');
};

/**
 * Apps Script entry point used by the sheet menu: shows a start alert, runs the
 * lookup on the active sheet, then shows one summary alert.
 */
export const runFromActiveSheet = (options: PlaceNameLookupOptions = {}): PlaceNameLookupResult => {
  const spreadsheetApp = options.spreadsheetApp ?? requireGlobal<SpreadsheetAppLike>('SpreadsheetApp');
  const ui = spreadsheetApp.getUi();

  ui.alert('Starting Place Name lookup. This may take a moment...');
  const result = runPlaceNameLookup({ ...options, spreadsheetApp });
  ui.alert(buildSummary(result));
  return result;
};
