#!/usr/bin/env node
import {createRequire} from 'node:module';
import process from 'node:process';
import {parseArgs} from 'node:util';
import {getTitlesAtUrl} from './index.ts';

const HELP = `
  Get the title of the web page at one or more URLs.

  Usage
    $ get-title-at-url <url...> [--timeout <ms>] [--concurrency <n>] [--json]
    $ ... | get-title-at-url [options]   (URLs, one per line, on stdin)

  Options
    --timeout <ms>      Give up on a page after this many milliseconds (default 10000)
    --concurrency <n>   Fetch at most this many pages at once (default 8)
    --json              Print the whole result as JSON, even on failure (an array when there is more than one URL)
    --help, -h          Show this help
    --version, -v       Show the version

  Exit codes
    0  every title was printed
    1  at least one page could not be fetched or has no title
    2  bad usage

  Example
    $ get-title-at-url https://example.com/
    Example Domain
`;

async function main(argv: string[]): Promise<number> {
  let parsed;
  try {
    parsed = parseArgs({
      args: argv,
      allowPositionals: true,
      options: {
        timeout: {type: 'string'},
        concurrency: {type: 'string'},
        json: {type: 'boolean'},
        help: {type: 'boolean', short: 'h'},
        version: {type: 'boolean', short: 'v'},
      },
    });
  } catch (error) {
    return usageError((error as Error).message);
  }

  const {values, positionals} = parsed;
  if (values.help) {
    console.log(HELP);
    return 0;
  }

  if (values.version) {
    // A relative require inside the package reaches package.json without going through the exports map.
    const {version} = createRequire(import.meta.url)('../package.json') as {version: string};
    console.log(version);
    return 0;
  }

  const urls = positionals.length > 0 ? positionals : await readStdinUrls();
  if (urls.length === 0) {
    return usageError('a URL is required');
  }

  let timeout: number | undefined;
  if (values.timeout !== undefined) {
    timeout = Number(values.timeout);
    if (!Number.isFinite(timeout) || timeout <= 0) {
      return usageError(`--timeout takes a positive number of milliseconds, not ${JSON.stringify(values.timeout)}`);
    }
  }

  let concurrency: number | undefined;
  if (values.concurrency !== undefined) {
    concurrency = Number(values.concurrency);
    if (!Number.isSafeInteger(concurrency) || concurrency <= 0) {
      return usageError(`--concurrency takes a positive whole number, not ${JSON.stringify(values.concurrency)}`);
    }
  }

  const results = await getTitlesAtUrl(urls, {timeout, concurrency});
  const isBatch = urls.length > 1;
  if (values.json) {
    console.log(JSON.stringify(isBatch ? results : results[0], undefined, 2));
  } else {
    for (const [index, result] of results.entries()) {
      if (result.error) {
        console.error(isBatch ? `error: ${urls[index]}: ${result.error.message}` : `error: ${result.error.message}`);
      } else {
        console.log(result.title);
      }
    }
  }

  return results.some(result => result.error !== undefined) ? 1 : 0;
}

/**
The URLs piped in, one per line, when no URL was given as an argument. Empty on a terminal, so a bare call still shows the usage.
*/
async function readStdinUrls(): Promise<string[]> {
  if (process.stdin.isTTY) {
    return [];
  }

  let text = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) {
    text += chunk as string;
  }

  return text.split(/\r?\n/u).map(line => line.trim()).filter(line => line !== '');
}

function usageError(message: string): number {
  console.error(`error: ${message}\n${HELP}`);
  return 2;
}

process.exitCode = await main(process.argv.slice(2));
