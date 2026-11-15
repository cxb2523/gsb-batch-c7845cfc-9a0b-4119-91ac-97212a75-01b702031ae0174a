const normalize = path => {
  const parts = [];
  for (const part of path.replaceAll('\\', '/').split('/')) {
    if (!part || part === '.') continue;
    if (part === '..' && parts.length && parts.at(-1) !== '..') parts.pop();
    else parts.push(part);
  }
  return parts.join('/');
};
const labelKey = label => label.trim().replace(/\s+/g, ' ').toLowerCase();

function visibleMarkdown(text) {
  let fence;
  return text.split('\n').map(line => {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = undefined;
      return '';
    }
    return fence ? '' : line.replace(/(`+).*?\1/g, '');
  });
}

// Inline code spans, reported as [start, end) character ranges on a single line.
function codeSpans(line) {
  const spans = [];
  line.replace(/(`+).*?\1/g, (match, ...args) => {
    const index = args.at(-2);
    spans.push([index, index + match.length]);
    return match;
  });
  return spans;
}

// Bounded Markdown subset, identical to the original link extractor: inline
// links/images (balanced one-level parentheses), reference links/images and
// shortcut references backed by a definition.
const linkPattern = /!?\[([^\]]*)\](?:\(\s*(?:<([^>\n]+)>|((?:\\.|[^\s()\\]|\((?:\\.|[^()])*\))+))(?:\s+["'][^\n]*?["'])?\s*\)|\[([^\]]*)\])?/g;
const definitionPattern = /^ {0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))/;
const definitionLinePattern = /^ {0,3}\[[^\]]+\]:/;
const fencePattern = /^ {0,3}(`{3,}|~{3,})/;

export const LINK_STATUS = Object.freeze({
  EXISTS: 'exists',
  MISSING: 'missing',
  SKIPPED: 'skipped'
});

export const SKIP_REASONS = Object.freeze({
  EXTERNAL_URL: 'external-url',
  ANCHOR: 'anchor',
  ROOT_PATH: 'root-path',
  QUERY: 'query',
  EMPTY_LOCAL: 'empty-local',
  DECODE_FAILED: 'decode-failed',
  CODE_EXAMPLE: 'code-example'
});

export const MANIFEST_FORMAT = 'reporelief-link-manifest';
export const MANIFEST_VERSION = 1;

export class LinkManifestError extends Error {
  constructor(message, line) {
    super(line === undefined ? message : `${message} (line ${line})`);
    this.name = 'LinkManifestError';
    this.code = 'LINK_MANIFEST_CORRUPT';
    this.line = line;
  }
}

// Stable identity of a candidate link: where it was found plus the raw target.
// The normalized target is deliberately excluded so a target mismatch on rerun
// is detected instead of silently trusted.
export function linkRecordKey(record) {
  return `${record.source}\u0000${record.line}\u0000${record.destination}`;
}

function hash32(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

// Scope fingerprint: any change to the audited Markdown contents or the path
// inventory invalidates a saved manifest so results match a full rerun.
export function buildLinkAuditScope(entries, availablePaths) {
  const markdownFiles = entries
    .filter(([path]) => /\.(md|markdown)$/i.test(normalize(path)))
    .map(([path, content]) => ({ path: normalize(path), hash: hash32(content) }))
    .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  const inventory = [...new Set(availablePaths.map(normalize))].sort();
  return {
    markdownFiles: markdownFiles.map(file => file.path),
    inventoryCount: inventory.length,
    hash: hash32(
      `${markdownFiles.map(file => `${file.path}:${file.hash}`).join('\n')}\u0000${inventory.join('\n')}`
    )
  };
}

function settle(destination, source, known) {
  const prefix = destination.match(/^(?:([a-z][a-z\d+.-]*:)|(\/)|(#)|(\?))/i);
  if (prefix) {
    const reason = prefix[1]
      ? SKIP_REASONS.EXTERNAL_URL
      : prefix[2]
        ? SKIP_REASONS.ROOT_PATH
        : prefix[3]
          ? SKIP_REASONS.ANCHOR
          : SKIP_REASONS.QUERY;
    return { target: null, status: LINK_STATUS.SKIPPED, reason };
  }
  const local = destination.split(/[?#]/)[0];
  if (!local) {
    return { target: null, status: LINK_STATUS.SKIPPED, reason: SKIP_REASONS.EMPTY_LOCAL };
  }
  let decoded;
  try {
    decoded = decodeURIComponent(local).replace(/\\([\\()[\] ])/g, '$1');
  } catch {
    return { target: null, status: LINK_STATUS.SKIPPED, reason: SKIP_REASONS.DECODE_FAILED };
  }
  const directory = source.split('/').slice(0, -1).join('/');
  const target = normalize(`${directory}/${decoded}`);
  const exists = known.has(target) || [...known].some(path => path.startsWith(`${target}/`));
  return { target, status: exists ? LINK_STATUS.EXISTS : LINK_STATUS.MISSING };
}

// Enumerate every candidate Markdown link with its settlement. Skipped links
// carry the rule that excluded them so the manifest is fully auditable.
export function collectMarkdownLinks(entries, availablePaths) {
  const known = new Set(availablePaths.map(normalize));
  const records = [];

  for (const [rawSource, text] of entries.filter(([path]) => /\.(md|markdown)$/i.test(path))) {
    const source = normalize(rawSource);
    const normalizedText = text.replaceAll('\r\n', '\n').replaceAll('\r', '\n');
    const rawLines = normalizedText.split('\n');
    const lines = visibleMarkdown(normalizedText);
    const definitions = new Map();
    for (const line of lines) {
      const match = line.match(definitionPattern);
      if (match && !definitions.has(labelKey(match[1]))) {
        definitions.set(labelKey(match[1]), match[2] ?? match[3]);
      }
    }

    let fence;
    lines.forEach((line, index) => {
      const raw = rawLines[index];
      const marker = raw.match(fencePattern);
      let fenceLine = false;
      if (marker) {
        if (!fence) fence = marker[1];
        else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = undefined;
        fenceLine = true;
      }
      const insideFence = Boolean(fence) && !fenceLine;

      if (insideFence) {
        // Every link-looking token inside a fenced block is documented as a
        // code example rather than validated.
        for (const match of raw.matchAll(linkPattern)) {
          const destination = match[2] ?? match[3] ?? definitions.get(labelKey(match[4] || match[1]));
          if (destination === undefined) continue;
          records.push({
            source,
            line: index + 1,
            destination,
            target: null,
            status: LINK_STATUS.SKIPPED,
            reason: SKIP_REASONS.CODE_EXAMPLE
          });
        }
        return;
      }
      if (fenceLine || definitionLinePattern.test(line)) return;

      const spans = codeSpans(raw);
      for (const match of raw.matchAll(linkPattern)) {
        if (match.index > 0 && raw[match.index - 1] === '\\') continue;
        const destination = match[2] ?? match[3] ?? definitions.get(labelKey(match[4] || match[1]));
        if (destination === undefined) continue;
        if (spans.some(([start, end]) => match.index >= start && match.index < end)) {
          records.push({
            source,
            line: index + 1,
            destination,
            target: null,
            status: LINK_STATUS.SKIPPED,
            reason: SKIP_REASONS.CODE_EXAMPLE
          });
          continue;
        }
        records.push({
          source,
          line: index + 1,
          destination,
          ...settle(destination, source, known)
        });
      }
    });
  }

  return records;
}

export function missingMarkdownLinks(entries, availablePaths) {
  return collectMarkdownLinks(entries, availablePaths)
    .filter(record => record.status === LINK_STATUS.MISSING)
    .map(({ source, line, destination, target }) => ({ source, line, destination, target }));
}

export function createLinkManifest(records, scope, options = {}) {
  return {
    format: MANIFEST_FORMAT,
    version: MANIFEST_VERSION,
    generatedAt: options.generatedAt ?? new Date().toISOString(),
    scope,
    records
  };
}

// One JSON object per line: a human-readable header followed by one settled
// link record per line, so partial files stay parseable and reviewable.
export function serializeLinkManifest(manifest) {
  const header = JSON.stringify({
    format: manifest.format,
    version: manifest.version,
    generatedAt: manifest.generatedAt,
    scope: manifest.scope
  });
  return [header, ...manifest.records.map(record => JSON.stringify(record))].join('\n') + '\n';
}

function isObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validateRecord(value, line) {
  if (!isObject(value)) throw new LinkManifestError('manifest line is not a JSON object', line);
  if (typeof value.source !== 'string' || !value.source) {
    throw new LinkManifestError('manifest record is missing a string "source"', line);
  }
  if (!Number.isInteger(value.line) || value.line < 1) {
    throw new LinkManifestError('manifest record has an invalid "line"', line);
  }
  if (typeof value.destination !== 'string') {
    throw new LinkManifestError('manifest record is missing a string "destination"', line);
  }
  if (value.target !== null && typeof value.target !== 'string') {
    throw new LinkManifestError('manifest record has an invalid "target"', line);
  }
  if (!Object.values(LINK_STATUS).includes(value.status)) {
    throw new LinkManifestError(`unknown settlement status "${value.status}"`, line);
  }
  if (value.status === LINK_STATUS.SKIPPED) {
    if (!Object.values(SKIP_REASONS).includes(value.reason)) {
      throw new LinkManifestError(`skipped record has an unknown reason "${value.reason}"`, line);
    }
  } else if (value.reason !== undefined || typeof value.target !== 'string') {
    throw new LinkManifestError('settled record must carry a normalized target and no skip reason', line);
  }}

export function parseLinkManifest(text) {
  if (typeof text !== 'string' || !text.trim()) {
    throw new LinkManifestError('link manifest is empty', 1);
  }
  const rawLines = text.split('\n');
  // A missing final newline means the process was interrupted while writing
  // the last record. Drop that partial line rather than discard the whole run.
  const truncated = rawLines.at(-1) !== '';
  const lines = rawLines.slice(0, -1);
  if (!lines.length) throw new LinkManifestError('link manifest has no header', 1);

  let header;
  try {
    header = JSON.parse(lines[0]);
  } catch {
    throw new LinkManifestError('manifest header is not valid JSON', 1);
  }
  if (!isObject(header) || header.format !== MANIFEST_FORMAT) {
    throw new LinkManifestError(`not a ${MANIFEST_FORMAT} file`, 1);
  }
  if (header.version !== MANIFEST_VERSION) {
    throw new LinkManifestError(`unsupported manifest version ${header.version}`, 1);
  }
  if (!isObject(header.scope) || typeof header.scope.hash !== 'string') {
    throw new LinkManifestError('manifest header has an invalid scope', 1);
  }
  if (typeof header.generatedAt !== 'string') {
    throw new LinkManifestError('manifest header is missing "generatedAt"', 1);
  }

  const records = [];
  const seen = new Set();
  for (let i = 1; i < lines.length; i++) {
    if (!lines[i].trim()) throw new LinkManifestError('blank line inside manifest', i + 1);
    let record;
    try {
      record = JSON.parse(lines[i]);
    } catch {
      throw new LinkManifestError('manifest record is not valid JSON', i + 1);
    }
    validateRecord(record, i + 1);
    const key = linkRecordKey(record);
    if (seen.has(key)) throw new LinkManifestError('duplicate link record in manifest', i + 1);
    seen.add(key);
    records.push(record);
  }

  return {
    format: header.format,
    version: header.version,
    generatedAt: header.generatedAt,
    scope: header.scope,
    records,
    truncated
  };
}

// Resumable link audit. Already settled records from a valid manifest are
// reused by identity; only remaining candidates are settled again. A scope
// mismatch means the files changed, so the audit restarts from scratch.
export async function runLinkAudit(entries, availablePaths, options = {}) {
  const scope = options.scope ?? buildLinkAuditScope(entries, availablePaths);
  const candidates = collectMarkdownLinks(entries, availablePaths);
  const prior = options.manifest ?? null;
  const scopeMatches = prior?.scope?.hash === scope.hash;
  const resumed = Boolean(prior && scopeMatches);
  const restarted = Boolean(prior && !scopeMatches);

  const settledByIdentity = new Map();
  if (resumed) {
    for (const record of prior.records) settledByIdentity.set(linkRecordKey(record), record);
  }

  const records = [];
  let reused = 0;
  let processed = 0;
  for (const candidate of candidates) {
    const key = linkRecordKey(candidate);
    const previous = settledByIdentity.get(key);
    if (previous && previous.target === candidate.target) {
      records.push(previous);
      reused += 1;
      continue;
    }
    records.push(candidate);
    processed += 1;
    if (typeof options.onSettled === 'function') {
      await options.onSettled(candidate);
    }
  }

  const count = status => records.filter(record => record.status === status).length;
  const stats = {
    total: records.length,
    exists: count(LINK_STATUS.EXISTS),
    missing: count(LINK_STATUS.MISSING),
    skipped: count(LINK_STATUS.SKIPPED),
    reused,
    processed
  };

  return {
    records,
    missing: records.filter(record => record.status === LINK_STATUS.MISSING),
    manifest: createLinkManifest(records, scope, { generatedAt: options.generatedAt }),
    scope,
    stats,
    resumed,
    restarted
  };
}

export { normalize as normalizePath };
