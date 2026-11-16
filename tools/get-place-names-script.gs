/**
 * Fills missing place names in the 'Name' column using the PlaceId from the
 * 'PlaceId' column of the active sheet.
 *
 * Each lookup calls the legacy Place Details API with only the 'name' field so
 * the request stays in the low-cost Place Details Essentials SKU.
 *
 * Rate limits and transient failures are retried a limited number of times with
 * exponential backoff. If a row still fails after the retries are exhausted, the
 * run stops instead of spending more quota. Names that were already written are
 * kept, and running the lookup again resumes at the first row that still has an
 * empty Name.
 *
 * The lookup logic lives in the PlaceNameLookup namespace below so the unit
 * tests can run it with fake UrlFetchApp / SpreadsheetApp objects; inside a
 * spreadsheet only the two entry points at the bottom of this file are used.
 */

// ⚠️ IMPORTANT: Replace this with your actual Google Maps Platform API Key.
// Never commit a real key to the repository.
var API_KEY = 'YOUR_API_KEY';

var PlaceNameLookup = (function () {
  'use strict';

  // Column indices (0-based) for the data.
  // Column B ('Name') is index 1.
  var NAME_COL = 1;
  // Column H ('PlaceId') is index 7.
  var PLACE_ID_COL = 7;

  // Base URL for the Legacy Place Details API, requesting only the 'name' field.
  var BASE_URL = 'https://maps.googleapis.com/maps/api/place/details/json';

  // Pause between rows so we stay well below the API rate limit.
  var DEFAULT_DELAY_MS = 100;
  // One initial request plus two retries.
  var DEFAULT_MAX_ATTEMPTS = 3;
  var DEFAULT_RETRY_BASE_DELAY_MS = 500;
  var DEFAULT_MAX_RETRY_DELAY_MS = 4000;

  var isBlank = function (value) {
    return !value || String(value).trim() === '';
  };

  // Resolve Apps Script globals with typeof guards. Bare identifiers throw a
  // ReferenceError when undeclared, so globalThis is avoided for compatibility
  // with older Apps Script runtimes.
  var lookupGlobal = function (name) {
    if (name === 'SpreadsheetApp' && typeof SpreadsheetApp !== 'undefined') return SpreadsheetApp;
    if (name === 'UrlFetchApp' && typeof UrlFetchApp !== 'undefined') return UrlFetchApp;
    if (name === 'Utilities' && typeof Utilities !== 'undefined') return Utilities;
    if (name === 'Logger' && typeof Logger !== 'undefined') return Logger;
    return null;
  };

  var requireGlobal = function (name) {
    var value = lookupGlobal(name);
    if (!value) {
      throw new Error(name + ' is not available. Run this script inside Google Sheets.');
    }
    return value;
  };

  var makeSleep = function (options) {
    if (typeof options.sleep === 'function') {
      return options.sleep;
    }
    var utilities = options.utilities || requireGlobal('Utilities');
    return function (ms) {
      utilities.sleep(ms);
    };
  };

  var makeLogger = function (options) {
    var logger = options.logger || lookupGlobal('Logger');
    return function (message) {
      if (logger && typeof logger.log === 'function') {
        logger.log(message);
      }
    };
  };

  // The legacy API reports throttling either as an HTTP 429 or as an
  // OVER_QUERY_LIMIT status in a 200 response. UNKNOWN_ERROR is also transient
  // per the API docs.
  var isRetryableStatus = function (status) {
    return status === 'OVER_QUERY_LIMIT' || status === 'UNKNOWN_ERROR';
  };

  var retryDelay = function (attempt, baseDelayMs, maxDelayMs) {
    return Math.min(baseDelayMs * Math.pow(2, attempt - 1), maxDelayMs);
  };

  /**
   * Requests the name for a single PlaceId, retrying temporary failures.
   * Returns { kind: 'ok', name } | { kind: 'miss', status } | { kind: 'exhausted', reason }.
   */
  function requestPlaceName(config) {
    var attempt = 0;

    while (attempt < config.maxAttempts) {
      attempt += 1;
      var retryReason = null;

      try {
        config.onRequest();
        var response = config.urlFetchApp.fetch(config.url, { muteHttpExceptions: true });
        var httpCode = typeof response.getResponseCode === 'function' ? response.getResponseCode() : 200;
        var body = typeof response.getContentText === 'function' ? response.getContentText() : '';

        if (httpCode === 429 || httpCode === 408 || httpCode >= 500) {
          retryReason = 'HTTP ' + httpCode;
        } else if (httpCode >= 400) {
          // Other 4xx answers (bad key, bad request) will not improve on retry.
          return { kind: 'miss', status: 'HTTP ' + httpCode };
        } else {
          var json = {};
          try {
            json = JSON.parse(body) || {};
          } catch (parseError) {
            // A truncated or proxied body is a temporary transport problem.
            retryReason = 'unparseable response body';
          }

          if (retryReason === null) {
            var status = json.status || 'NO_STATUS';
            if (status === 'OK' && json.result && json.result.name) {
              return { kind: 'ok', name: json.result.name };
            }
            if (isRetryableStatus(status)) {
              retryReason = status;
            } else {
              // NOT_FOUND, ZERO_RESULTS, REQUEST_DENIED and friends are
              // deterministic: retrying them only wastes quota.
              return {
                kind: 'miss',
                status: status === 'OK' ? 'OK_WITHOUT_NAME' : status,
              };
            }
          }
        }
      } catch (error) {
        // UrlFetchApp throws on network/DNS/timeout failures; those are temporary.
        retryReason = error && error.message ? error.message : String(error);
      }

      if (attempt >= config.maxAttempts) {
        return { kind: 'exhausted', reason: retryReason };
      }

      var backoffMs = retryDelay(attempt, config.baseDelayMs, config.maxDelayMs);
      config.log(
        'Row ' + config.rowNumber + ': ' + retryReason + '. Retrying in ' + backoffMs +
        ' ms (attempt ' + (attempt + 1) + ' of ' + config.maxAttempts + ').'
      );
      config.sleep(backoffMs);
    }

    return { kind: 'exhausted', reason: 'retry budget exhausted' };
  }

  /**
   * Fills empty Name cells for rows that have a PlaceId.
   *
   * options:
   *   apiKey            Google Maps Platform API key (required).
   *   sheet             Sheet to use (defaults to the active sheet).
   *   spreadsheetApp    Override for SpreadsheetApp (tests).
   *   urlFetchApp       Override for UrlFetchApp (tests).
   *   utilities         Override for Utilities (tests).
   *   logger            Object with a log(message) method (tests).
   *   sleep             Function receiving milliseconds (tests).
   *   delayMs           Pause between rows (default 100).
   *   maxAttempts       Total attempts per row, including the first (default 3).
   *   retryBaseDelayMs  First backoff delay (default 500).
   *   maxRetryDelayMs   Backoff cap (default 4000).
   */
  function run(options) {
    options = options || {};

    if (!options.apiKey) {
      throw new Error('Missing Google Maps Platform API key.');
    }

    var urlFetchApp = options.urlFetchApp || requireGlobal('UrlFetchApp');
    var sleep = makeSleep(options);
    var log = makeLogger(options);

    var sheet = options.sheet;
    if (!sheet) {
      var spreadsheetApp = options.spreadsheetApp || requireGlobal('SpreadsheetApp');
      sheet = spreadsheetApp.getActiveSpreadsheet().getActiveSheet();
    }
    var values = sheet.getDataRange().getValues();

    var delayMs = options.delayMs === undefined ? DEFAULT_DELAY_MS : options.delayMs;
    var maxAttempts = Math.max(1, options.maxAttempts || DEFAULT_MAX_ATTEMPTS);
    var baseDelayMs = options.retryBaseDelayMs || DEFAULT_RETRY_BASE_DELAY_MS;
    var maxDelayMs = options.maxRetryDelayMs || DEFAULT_MAX_RETRY_DELAY_MS;

    var result = {
      updatedCount: 0,
      notFoundCount: 0,
      requestsMade: 0,
      aborted: false,
      abortedRow: null,
    };

    // Start at index 1 (row 2) to skip the header row.
    for (var i = 1; i < values.length; i += 1) {
      var row = values[i] || [];

      // Only request a name when Name (B) is empty AND PlaceId (H) exists.
      if (!isBlank(row[NAME_COL]) || isBlank(row[PLACE_ID_COL])) {
        continue;
      }

      var placeId = row[PLACE_ID_COL];
      var rowNumber = i + 1;
      var requestUrl =
        BASE_URL + '?place_id=' + encodeURIComponent(placeId) + '&fields=name&key=' + options.apiKey;

      var outcome = requestPlaceName({
        urlFetchApp: urlFetchApp,
        url: requestUrl,
        maxAttempts: maxAttempts,
        baseDelayMs: baseDelayMs,
        maxDelayMs: maxDelayMs,
        sleep: sleep,
        log: log,
        rowNumber: rowNumber,
        onRequest: function () {
          result.requestsMade += 1;
        },
      });

      if (outcome.kind === 'ok') {
        // Write immediately: successful names survive any later abort.
        sheet.getRange(rowNumber, NAME_COL + 1).setValue(outcome.name);
        result.updatedCount += 1;
        log('Updated row ' + rowNumber + ': ' + outcome.name);
      } else if (outcome.kind === 'miss') {
        result.notFoundCount += 1;
        log('No name for row ' + rowNumber + ' (PlaceId ' + placeId + '): ' + outcome.status);
      } else {
        result.aborted = true;
        result.abortedRow = rowNumber;
        log('Stopped at row ' + rowNumber + ' (PlaceId ' + placeId + '): ' + outcome.reason);
        break;
      }

      sleep(delayMs);
    }

    return result;
  }

  /** Builds the single end-of-run summary shown to the user. */
  function buildSummary(result) {
    var lines = ['Place name lookup finished.'];
    lines.push(
      'Filled in: ' + result.updatedCount + ' place name' + (result.updatedCount === 1 ? '' : 's') + '.'
    );
    lines.push(
      'Not found: ' + result.notFoundCount +
      ' (no usable name: NOT_FOUND, ZERO_RESULTS, or a deterministic API error; these rows still ' +
      'have an empty Name).'
    );
    lines.push('API requests made: ' + result.requestsMade + '.');

    if (result.aborted) {
      lines.push(
        'Stopped at row ' + result.abortedRow +
        ' after repeated rate-limit or network errors. Names filled in before that point were kept ' +
        '(no rollback).'
      );
      lines.push(
        'Unprocessed rows start at row ' + result.abortedRow + ': that row was not filled in and later ' +
        'rows were not requested.'
      );
      lines.push(
        'Run the lookup again to continue from the first unprocessed row (row ' +
        result.abortedRow + '); rows that already have a name are skipped.'
      );
    } else if (result.notFoundCount > 0) {
      lines.push(
        'All rows with a PlaceId were processed. Run the lookup again later to try the rows that are ' +
        'still empty.'
      );
    } else {
      lines.push('Every row with a PlaceId now has a name.');
    }

    return lines.join('\n');
  }

  /**
   * Shows the starting alert, runs the lookup on the active sheet, then shows a
   * single summary alert describing what was filled in, what was not found, and
   * where to resume after an abort.
   */
  function runFromActiveSheet(options) {
    options = options || {};
    var spreadsheetApp = options.spreadsheetApp || requireGlobal('SpreadsheetApp');
    var ui = spreadsheetApp.getUi();

    ui.alert('Starting Place Name lookup. This may take a moment...');
    var merged = {};
    var key;
    for (key in options) {
      if (Object.prototype.hasOwnProperty.call(options, key)) {
        merged[key] = options[key];
      }
    }
    merged.spreadsheetApp = spreadsheetApp;

    var result = run(merged);
    ui.alert(buildSummary(result));
    return result;
  }

  return {
    run: run,
    buildSummary: buildSummary,
    runFromActiveSheet: runFromActiveSheet,
  };
})();

/**
 * Menu entry point. Replace API_KEY above with your key before running.
 */
function lookupMissingPlaceNames() {
  PlaceNameLookup.runFromActiveSheet({
    apiKey: API_KEY,
    spreadsheetApp: SpreadsheetApp,
    urlFetchApp: UrlFetchApp,
    utilities: Utilities,
    logger: Logger,
  });
}

/**
 * Creates a custom menu in the spreadsheet for easy access to the script.
 * This function runs automatically when the spreadsheet is opened.
 */
function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu('Lookup Place Names')
    .addItem('Run Name Lookup', 'lookupMissingPlaceNames')
    .addToUi();
}
