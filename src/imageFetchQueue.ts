// Image fetch queue: pulls source entries (local paths or http(s) URLs) one by
// one, decodes them into images for the downstream pipeline, and settles every
// entry independently as success / failed / skipped / unprocessed.

export interface SourceEntry {
  /** Unique id of the source entry. Duplicate ids reject the whole batch. */
  id: string;
  /** Local file path, file:// URL, or http(s) URL. */
  url: string;
}

export type ImageFormat = 'png' | 'jpeg' | 'gif' | 'bmp' | 'webp';

export interface DecodedImage {
  format: ImageFormat;
  width: number;
  height: number;
  /** Raw encoded bytes, handed to the downstream processing chain as-is. */
  bytes: Uint8Array;
}

export type ItemStatus = 'success' | 'failed' | 'skipped' | 'unprocessed';

export type SkipReason =
  /** Same address already claimed by an earlier entry; shares its output slot. */
  | 'duplicate-url'
  /** Entry already succeeded in a previous run of this queue. */
  | 'already-fetched';

export interface QueueItemResult {
  id: string;
  url: string;
  status: ItemStatus;
  /** Failure reason when status is 'failed'. */
  error?: string;
  skipReason?: SkipReason;
  /** For 'duplicate-url' skips: id of the entry owning the shared output slot. */
  sourceId?: string;
}

export interface QueueSummary {
  total: number;
  success: number;
  failed: number;
  skipped: number;
  unprocessed: number;
}

export interface RunReport {
  /** One result per input entry, in input order. */
  results: QueueItemResult[];
  /**
   * Deliverable decoded images keyed by entry id. Contains one slot per
   * unique successful source — duplicate addresses never get a second slot.
   * Includes entries that succeeded in earlier runs (reported as skipped).
   */
  images: Map<string, DecodedImage>;
  summary: QueueSummary;
}

export class DuplicateIdError extends Error {
  readonly duplicateIds: string[];
  constructor(ids: string[]) {
    super(`duplicate source id(s): ${ids.join(', ')}`);
    this.name = 'DuplicateIdError';
    this.duplicateIds = ids;
  }
}

/** Pulls the raw bytes for one source. Must reject on fetch failure. */
export type Fetcher = (url: string, signal: AbortSignal) => Promise<Uint8Array>;
/** Decodes raw bytes into an image. Must throw on undecodable content. */
export type Decoder = (bytes: Uint8Array) => DecodedImage;

export interface ImageFetchQueueOptions {
  fetcher?: Fetcher;
  decoder?: Decoder;
}

// ---------------------------------------------------------------------------
// Decoder
// ---------------------------------------------------------------------------

function u16be(b: Uint8Array, o: number): number {
  return (b[o] << 8) | b[o + 1];
}
function u16le(b: Uint8Array, o: number): number {
  return b[o] | (b[o + 1] << 8);
}
function u32be(b: Uint8Array, o: number): number {
  return ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0;
}
function u32le(b: Uint8Array, o: number): number {
  return (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
}
function ascii(b: Uint8Array, o: number, len: number): string {
  let s = '';
  for (let i = 0; i < len; i++) s += String.fromCharCode(b[o + i]);
  return s;
}

function decodePng(b: Uint8Array): DecodedImage | null {
  const sig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (b.length < 24 || !sig.every((v, i) => b[i] === v)) return null;
  if (ascii(b, 12, 4) !== 'IHDR') throw new Error('PNG missing IHDR chunk');
  return { format: 'png', width: u32be(b, 16), height: u32be(b, 20), bytes: b };
}

function decodeGif(b: Uint8Array): DecodedImage | null {
  if (b.length < 10) return null;
  const head = ascii(b, 0, 6);
  if (head !== 'GIF87a' && head !== 'GIF89a') return null;
  return { format: 'gif', width: u16le(b, 6), height: u16le(b, 8), bytes: b };
}

function decodeBmp(b: Uint8Array): DecodedImage | null {
  if (b.length < 26 || ascii(b, 0, 2) !== 'BM') return null;
  const width = u32le(b, 18);
  const height = u32le(b, 22);
  return { format: 'bmp', width, height: height & 0x80000000 ? (~height + 1) >>> 0 : height, bytes: b };
}

function decodeJpeg(b: Uint8Array): DecodedImage | null {
  if (b.length < 4 || b[0] !== 0xff || b[1] !== 0xd8) return null;
  let o = 2;
  while (o + 1 < b.length) {
    if (b[o] !== 0xff) {
      o++;
      continue;
    }
    const marker = b[o + 1];
    // Start-of-frame markers carry the dimensions.
    if (
      (marker >= 0xc0 && marker <= 0xc3) ||
      (marker >= 0xc5 && marker <= 0xc7) ||
      (marker >= 0xc9 && marker <= 0xcb) ||
      (marker >= 0xcd && marker <= 0xcf)
    ) {
      if (o + 8 >= b.length) throw new Error('truncated JPEG frame header');
      return {
        format: 'jpeg',
        width: u16be(b, o + 7),
        height: u16be(b, o + 5),
        bytes: b,
      };
    }
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      o += 2; // standalone markers without a length field
      continue;
    }
    if (o + 3 >= b.length) break;
    o += 2 + u16be(b, o + 2);
  }
  throw new Error('JPEG frame header not found');
}

function decodeWebp(b: Uint8Array): DecodedImage | null {
  if (b.length < 30 || ascii(b, 0, 4) !== 'RIFF' || ascii(b, 8, 4) !== 'WEBP') return null;
  const chunk = ascii(b, 12, 4);
  if (chunk === 'VP8X') {
    return {
      format: 'webp',
      width: 1 + (b[24] | (b[25] << 8) | (b[26] << 16)),
      height: 1 + (b[27] | (b[28] << 8) | (b[29] << 16)),
      bytes: b,
    };
  }
  if (chunk === 'VP8 ' && b.length >= 30) {
    return {
      format: 'webp',
      width: u16le(b, 26) & 0x3fff,
      height: u16le(b, 28) & 0x3fff,
      bytes: b,
    };
  }
  if (chunk === 'VP8L' && b.length >= 25) {
    const bits = u32le(b, 21);
    return {
      format: 'webp',
      width: (bits & 0x3fff) + 1,
      height: ((bits >> 14) & 0x3fff) + 1,
      bytes: b,
    };
  }
  throw new Error('unsupported WebP chunk');
}

/** Decodes the image header. Throws on empty or undecodable content. */
export function decodeImage(bytes: Uint8Array): DecodedImage {
  if (!bytes || bytes.length === 0) throw new Error('empty content');
  const image =
    decodePng(bytes) ??
    decodeGif(bytes) ??
    decodeBmp(bytes) ??
    decodeJpeg(bytes) ??
    decodeWebp(bytes);
  if (!image) throw new Error('unrecognized image format');
  if (!(image.width > 0) || !(image.height > 0)) {
    throw new Error('invalid image dimensions');
  }
  return image;
}

// ---------------------------------------------------------------------------
// Default fetcher (http(s) everywhere, local files under Node.js)
// ---------------------------------------------------------------------------

function stripFileProtocol(url: string): string {
  if (!/^file:\/\//i.test(url)) return url;
  let p = decodeURIComponent(url.replace(/^file:\/\//i, ''));
  // file:///D:/x -> D:/x on Windows
  if (/^\/[A-Za-z]:\//.test(p)) p = p.slice(1);
  return p;
}

export const defaultFetcher: Fetcher = async (url, signal) => {
  if (/^https?:\/\//i.test(url)) {
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ''}`);
    return new Uint8Array(await res.arrayBuffer());
  }
  const proc = (globalThis as { process?: { versions?: { node?: string } } }).process;
  if (!proc?.versions?.node) {
    throw new Error('local file sources are only supported under Node.js');
  }
  const { readFile } = await import('node:fs/promises');
  const data = await readFile(stripFileProtocol(url));
  return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
};

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------

function normalizeUrl(url: string): string {
  const trimmed = url.trim();
  // Only scheme URLs get canonicalized (case-insensitive scheme/host);
  // plain local paths are compared as-is after trimming.
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)) return trimmed;
  try {
    const u = new URL(trimmed);
    u.protocol = u.protocol.toLowerCase();
    u.host = u.host.toLowerCase();
    return u.toString();
  } catch {
    return trimmed;
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

function isAbortError(err: unknown): boolean {
  return err instanceof Error && err.name === 'AbortError';
}

function validateEntries(entries: SourceEntry[]): void {
  const seen = new Set<string>();
  const dups = new Set<string>();
  for (const e of entries) {
    if (!e || typeof e.id !== 'string' || e.id.length === 0) {
      throw new TypeError('every source entry needs a non-empty string id');
    }
    if (typeof e.url !== 'string' || e.url.trim().length === 0) {
      throw new TypeError(`source entry "${e.id}" needs a non-empty url`);
    }
    if (seen.has(e.id)) dups.add(e.id);
    else seen.add(e.id);
  }
  if (dups.size > 0) throw new DuplicateIdError([...dups]);
}

type FetchOutcome =
  | { kind: 'success'; image: DecodedImage }
  | { kind: 'failed'; error: string }
  | { kind: 'aborted' };

/**
 * Sequential fetch queue. Each entry is fetched and settled independently:
 * a single failure never aborts the batch. The queue remembers successful
 * entries, so re-running the same list skips them instead of re-fetching.
 */
export class ImageFetchQueue {
  private readonly fetcher: Fetcher;
  private readonly decoder: Decoder;
  /** id -> successful result, kept across runs so reruns can skip them. */
  private readonly succeeded = new Map<string, { url: string; image: DecodedImage }>();
  /** normalized url -> owning entry id, for successful sources. */
  private readonly succeededUrls = new Map<string, string>();
  private controller: AbortController | null = null;
  private running = false;

  constructor(options: ImageFetchQueueOptions = {}) {
    this.fetcher = options.fetcher ?? defaultFetcher;
    this.decoder = options.decoder ?? decodeImage;
  }

  /** Aborts the current run: unstarted entries become 'unprocessed'. */
  abort(): void {
    this.controller?.abort();
  }

  async run(entries: SourceEntry[]): Promise<RunReport> {
    if (this.running) throw new Error('a run is already in progress on this queue');
    validateEntries(entries);
    this.running = true;
    const controller = new AbortController();
    this.controller = controller;
    try {
      const results: QueueItemResult[] = [];
      // url -> owning id. Seeded with past successes; entries claim their url
      // as they settle so later duplicates in the same batch share the slot.
      const urlOwner = new Map(this.succeededUrls);

      for (const entry of entries) {
        const url = normalizeUrl(entry.url);

        const previous = this.succeeded.get(entry.id);
        if (previous) {
          results.push({ id: entry.id, url, status: 'skipped', skipReason: 'already-fetched' });
          continue;
        }
        const owner = urlOwner.get(url);
        if (owner !== undefined) {
          results.push({
            id: entry.id,
            url,
            status: 'skipped',
            skipReason: 'duplicate-url',
            sourceId: owner,
          });
          continue;
        }
        if (controller.signal.aborted) {
          results.push({ id: entry.id, url, status: 'unprocessed' });
          continue;
        }

        const outcome = await this.fetchOne(url, controller.signal);
        if (outcome.kind === 'aborted') {
          // In-flight entry cancelled by abort(): consistent, retryable outcome.
          results.push({ id: entry.id, url, status: 'unprocessed' });
          continue;
        }
        // Terminal outcome: this entry now owns the address slot.
        urlOwner.set(url, entry.id);
        if (outcome.kind === 'success') {
          this.succeeded.set(entry.id, { url, image: outcome.image });
          this.succeededUrls.set(url, entry.id);
          results.push({ id: entry.id, url, status: 'success' });
        } else {
          results.push({ id: entry.id, url, status: 'failed', error: outcome.error });
        }
      }

      // Deliverables: this run's successes plus everything skipped as
      // already-fetched. Duplicates intentionally get no slot of their own.
      const images = new Map<string, DecodedImage>();
      for (const r of results) {
        if (r.status === 'success' || (r.status === 'skipped' && r.skipReason === 'already-fetched')) {
          const hit = this.succeeded.get(r.id);
          if (hit) images.set(r.id, hit.image);
        }
      }

      const summary: QueueSummary = { total: results.length, success: 0, failed: 0, skipped: 0, unprocessed: 0 };
      for (const r of results) summary[r.status]++;
      return { results, images, summary };
    } finally {
      this.running = false;
      if (this.controller === controller) this.controller = null;
    }
  }

  private async fetchOne(url: string, signal: AbortSignal): Promise<FetchOutcome> {
    let bytes: Uint8Array;
    try {
      bytes = await this.fetcher(url, signal);
    } catch (err) {
      if (isAbortError(err) || signal.aborted) return { kind: 'aborted' };
      return { kind: 'failed', error: `fetch failed: ${errorMessage(err)}` };
    }
    // A fetch that resolved before the abort landed is allowed to complete.
    if (!bytes || bytes.length === 0) return { kind: 'failed', error: 'empty content' };
    try {
      return { kind: 'success', image: this.decoder(bytes) };
    } catch (err) {
      return { kind: 'failed', error: `decode failed: ${errorMessage(err)}` };
    }
  }
}
