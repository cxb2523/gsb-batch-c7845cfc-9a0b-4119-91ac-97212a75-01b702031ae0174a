#!/usr/bin/env node
// Sync public GitHub activity into .github/content/build-in-public.jsonl so the
// tweet pipeline gets fresh first-party build-in-public material for free.
// Runs under bun or node; no Bun-only APIs.
//
// Every event in the fetched batch is settled on its own:
//   - written to the content file AND recorded in the sync state, or
//   - skipped with a machine-readable reason (unsupported-type,
//     unsupported-action, stale-event, excluded-repo, missing-fields,
//     invalid-event, duplicate-event, run-limit-reached, ...).
// The content file is appended and the state file is persisted after every
// successful event, so an earlier good event survives a later failure. Re-runs
// dedupe against both the state file and the content file.
//
// If the API response is a rate-limit/error or not an array, nothing is
// written and the process exits non-zero so the workflow sees the failure.
// If only individual events are unusable, the rest are processed and the run
// exits zero.
//
// Offline/local runs (no GitHub credentials needed):
//   node .github/scripts/sync-github-events.mjs --events-file events.json
//   GITHUB_EVENTS_FIXTURE_FILE=events.json node .github/scripts/sync-github-events.mjs
//   GITHUB_EVENTS_FIXTURE_JSON='[...]' node .github/scripts/sync-github-events.mjs
//   GITHUB_EVENTS_FIXTURE_STATUS=403 ...       # simulate a rate-limited response
//   GITHUB_EVENTS_NOW=2026-11-16T00:00:00Z ... # freeze "now" for age checks
// Content/state paths come from TWEET_BUILD_IN_PUBLIC_FILE and
// GITHUB_EVENTS_SYNC_FILE, so tests can point them at temp files.
// Self-test: node .github/scripts/sync-github-events.mjs --self-test

import { spawnSync } from "node:child_process";
import { appendFile, mkdir, mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const DAY_MS = 24 * 60 * 60 * 1000;
const DEFAULT_LIMIT = 8;
const DEFAULT_MAX_AGE_DAYS = 4;
const DEFAULT_MAX_STATE_IDS = 200;
const SUPPORTED_EVENT_TYPES = new Set([
  "ReleaseEvent",
  "WatchEvent",
  "IssuesEvent",
  "PullRequestEvent",
  "PushEvent",
]);

export class SyncError extends Error {
  constructor(message) {
    super(message);
    this.name = "SyncError";
  }
}

const env = (source, name, fallback = "") => String(source[name] || "").trim() || fallback;

function numberEnv(source, name, fallback, { min = 0, integer = false } = {}) {
  const raw = env(source, name);
  if (!raw) return fallback;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < min) return fallback;
  return integer ? Math.floor(parsed) : parsed;
}

function readCliOption(name, argv) {
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === `--${name}`) return argv[index + 1] || "";
    if (arg.startsWith(`--${name}=`)) return arg.slice(name.length + 3);
  }
  return "";
}

function parseNow(value) {
  if (!value) return Date.now();
  const parsed = Date.parse(value);
  if (Number.isFinite(parsed)) return parsed;
  console.warn(`Ignoring unparsable GITHUB_EVENTS_NOW="${value}"; using the current time.`);
  return Date.now();
}

export function readConfig(source = process.env, argv = process.argv.slice(2)) {
  const fixtureStatusRaw = readCliOption("fixture-status", argv) || env(source, "GITHUB_EVENTS_FIXTURE_STATUS");
  const fixtureStatus = fixtureStatusRaw ? Number(fixtureStatusRaw) : 0;
  if (fixtureStatusRaw && !Number.isFinite(fixtureStatus)) {
    throw new SyncError(
      `GITHUB_EVENTS_FIXTURE_STATUS must be a number, got "${fixtureStatusRaw}". No files were changed.`,
    );
  }

  return {
    owner: env(source, "GITHUB_EVENTS_OWNER", env(source, "GITHUB_REPOSITORY_OWNER", "")),
    token: env(source, "GITHUB_TOKEN") || env(source, "GH_TOKEN"),
    contentFile: env(source, "TWEET_BUILD_IN_PUBLIC_FILE", ".github/content/build-in-public.jsonl"),
    stateFile: env(source, "GITHUB_EVENTS_SYNC_FILE", ".github/runtime/github-events-sync.json"),
    maxAgeDays: numberEnv(source, "GITHUB_EVENTS_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS),
    limit: numberEnv(source, "GITHUB_EVENTS_LIMIT", DEFAULT_LIMIT, { integer: true }),
    maxStateIds: numberEnv(source, "GITHUB_EVENTS_SYNC_MAX_IDS", DEFAULT_MAX_STATE_IDS, {
      min: 1,
      integer: true,
    }),
    excludeRepos: new Set(
      env(source, "GITHUB_EVENTS_EXCLUDE_REPOS", "x_bot")
        .split(/[,\s]+/)
        .map((repo) => repo.toLowerCase())
        .filter(Boolean),
    ),
    fixtureFile: readCliOption("events-file", argv) || env(source, "GITHUB_EVENTS_FIXTURE_FILE"),
    fixtureJson: readCliOption("events-json", argv) || env(source, "GITHUB_EVENTS_FIXTURE_JSON"),
    fixtureStatus,
    now: parseNow(env(source, "GITHUB_EVENTS_NOW")),
  };
}

function clean(value, max = 300) {
  return String(value ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
}

// Field reader that rejects wrong shapes instead of stringifying objects into
// "[object Object]" and writing unusable notes.
function textField(value, max = 300) {
  if (typeof value !== "string" && typeof value !== "number") return "";
  return clean(value, max);
}

function hasNumber(value) {
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string") return value.trim() !== "" && Number.isFinite(Number(value));
  return false;
}

function eventIdOf(event) {
  if (!event || typeof event !== "object" || Array.isArray(event)) return null;
  if (typeof event.id === "number" && Number.isFinite(event.id)) return String(event.id);
  if (typeof event.id === "string" && event.id.trim()) return event.id.trim();
  return null;
}

function skipOutcome(event, reason, detail = "") {
  const eventId = eventIdOf(event);
  return {
    status: "skipped",
    eventId: eventId ? `github:${eventId}` : null,
    reason,
    detail,
  };
}

// Convert one event into a note, or explain why it cannot be converted.
export function evaluateEvent(event, {
  now = Date.now(),
  maxAgeDays = DEFAULT_MAX_AGE_DAYS,
  excludeRepos = new Set(),
} = {}) {
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    return skipOutcome(event, "invalid-event", "event is not an object");
  }

  const eventId = eventIdOf(event);
  if (!eventId) return skipOutcome(event, "missing-fields", "id");

  const eventType = typeof event.type === "string" ? event.type.trim() : "";
  if (!eventType) return skipOutcome(event, "missing-fields", "type");

  const repoFullName = typeof event.repo?.name === "string" ? event.repo.name.trim() : "";
  if (!repoFullName) return skipOutcome(event, "missing-fields", "repo.name");
  const repo = repoFullName.split("/").filter(Boolean).pop() || repoFullName;
  if (excludeRepos.has(repo.toLowerCase()) || excludeRepos.has(repoFullName.toLowerCase())) {
    return skipOutcome(event, "excluded-repo", repo);
  }

  if (!SUPPORTED_EVENT_TYPES.has(eventType)) {
    return skipOutcome(event, "unsupported-type", eventType);
  }

  const createdAt = typeof event.created_at === "string" ? event.created_at.trim() : "";
  if (!createdAt) return skipOutcome(event, "missing-fields", "created_at");
  const createdAtMs = Date.parse(createdAt);
  if (!Number.isFinite(createdAtMs)) {
    return skipOutcome(event, "invalid-created-at", createdAt);
  }
  const nowMs = typeof now === "number" ? now : now instanceof Date ? now.getTime() : Date.now();
  const ageDays = (nowMs - createdAtMs) / DAY_MS;
  if (ageDays > maxAgeDays) {
    return skipOutcome(event, "stale-event", `age ${ageDays.toFixed(1)}d > max ${maxAgeDays}d`);
  }

  const payload = event.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return skipOutcome(event, "missing-fields", "payload");
  }

  const base = {
    id: `github:${eventId}`,
    product: repo,
    date: createdAt,
    tags: ["github", "build-in-public"],
    public: true,
  };
  const candidate = (note) => ({ status: "candidate", eventId: note.id, note });

  if (eventType === "ReleaseEvent") {
    const release = payload.release;
    if (!release || typeof release !== "object" || Array.isArray(release)) {
      return skipOutcome(event, "missing-fields", "payload.release");
    }
    const tag = textField(release.tag_name, 40);
    if (!tag) return skipOutcome(event, "missing-fields", "payload.release.tag_name");
    return candidate({
      ...base,
      kind: "milestone",
      summary: `released ${tag} on ${repo}`,
      detail: textField(release.name, 700) || textField(release.body, 700),
      evidence: `release: ${tag}`,
    });
  }

  if (eventType === "WatchEvent") {
    if (payload.action !== "started") {
      return skipOutcome(
        event,
        "unsupported-action",
        `WatchEvent action=${clean(payload.action, 40) || "missing"}`,
      );
    }
    return candidate({
      ...base,
      kind: "milestone",
      summary: `${repo} got a new GitHub star`,
      detail: `Someone starred ${repo}; total stars are climbing.`,
      evidence: "new star",
    });
  }

  if (eventType === "IssuesEvent") {
    if (payload.action !== "closed") {
      return skipOutcome(
        event,
        "unsupported-action",
        `IssuesEvent action=${clean(payload.action, 40) || "missing"}`,
      );
    }
    const issue = payload.issue;
    if (!issue || typeof issue !== "object" || Array.isArray(issue)) {
      return skipOutcome(event, "missing-fields", "payload.issue");
    }
    const title = textField(issue.title, 120);
    if (!title) return skipOutcome(event, "missing-fields", "payload.issue.title");
    if (!hasNumber(issue.number)) return skipOutcome(event, "missing-fields", "payload.issue.number");
    return candidate({
      ...base,
      kind: "progress",
      summary: `closed issue in ${repo}: ${title}`,
      detail: textField(issue.body, 700),
      evidence: `issue #${textField(issue.number, 40)} closed`,
    });
  }

  if (eventType === "PullRequestEvent") {
    if (payload.action !== "closed") {
      return skipOutcome(
        event,
        "unsupported-action",
        `PullRequestEvent action=${clean(payload.action, 40) || "missing"}`,
      );
    }
    const pullRequest = payload.pull_request;
    if (!pullRequest || typeof pullRequest !== "object" || Array.isArray(pullRequest)) {
      return skipOutcome(event, "missing-fields", "payload.pull_request");
    }
    if (pullRequest.merged !== true) {
      return skipOutcome(event, "unsupported-action", "PullRequestEvent not merged");
    }
    const title = textField(pullRequest.title, 120);
    if (!title) return skipOutcome(event, "missing-fields", "payload.pull_request.title");
    if (!hasNumber(pullRequest.number)) {
      return skipOutcome(event, "missing-fields", "payload.pull_request.number");
    }
    return candidate({
      ...base,
      kind: "progress",
      summary: `merged PR in ${repo}: ${title}`,
      detail: textField(pullRequest.body, 700),
      evidence: `PR #${textField(pullRequest.number, 40)} merged`,
    });
  }

  // PushEvent
  const commits = payload.commits;
  if (!Array.isArray(commits) || commits.length === 0) {
    return skipOutcome(event, "missing-fields", "payload.commits");
  }
  for (let commitIndex = 0; commitIndex < commits.length; commitIndex += 1) {
    const commit = commits[commitIndex];
    if (
      !commit ||
      typeof commit !== "object" ||
      Array.isArray(commit) ||
      !textField(commit.message, 160)
    ) {
      return skipOutcome(event, "missing-fields", `payload.commits[${commitIndex}].message`);
    }
  }
  const branch = typeof payload.ref === "string" ? clean(payload.ref.replace("refs/heads/", ""), 60) : "";
  return candidate({
    ...base,
    kind: "progress",
    summary: `shipped ${commits.length} commit(s) to ${repo}`,
    detail: commits
      .slice(0, 3)
      .map((commit) => `- ${textField(commit.message, 160)}`)
      .join(" "),
    evidence: `branch: ${branch || "default"}`,
  });
}

function describePayload(data) {
  if (Array.isArray(data)) return "array";
  if (data === null) return "null";
  return typeof data;
}

function apiErrorMessage(status, data) {
  const detail = clean(data?.message, 300);
  const kind =
    status === 403 || status === 429
      ? "GitHub API rate limit or access error"
      : "GitHub events API request failed";
  return `${kind} (HTTP ${status})${detail ? `: ${detail}` : ""}. Existing content and state files were left unchanged.`;
}

function unexpectedPayloadMessage(status, data) {
  return `GitHub events API returned an unexpected payload (HTTP ${status}): expected a JSON array, got ${describePayload(
    data,
  )}. Existing content and state files were left unchanged.`;
}

async function fetchEvents({ owner, token }) {
  let response;
  try {
    response = await fetch(`https://api.github.com/users/${owner}/events/public?per_page=100`, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "x-bot-github-events-sync",
      },
    });
  } catch (error) {
    throw new SyncError(
      `GitHub events request failed: ${
        error instanceof Error ? error.message : String(error)
      }. Existing content and state files were left unchanged.`,
    );
  }

  const data = await response.json().catch(() => null);
  if (!response.ok) throw new SyncError(apiErrorMessage(response.status, data));
  if (!Array.isArray(data)) throw new SyncError(unexpectedPayloadMessage(response.status, data));
  return data;
}

async function loadFixture({ fixtureFile, fixtureJson, fixtureStatus }) {
  let data;
  if (fixtureJson) {
    try {
      data = JSON.parse(fixtureJson);
    } catch (error) {
      throw new SyncError(
        `Cannot parse the inline GitHub events fixture: ${
          error instanceof Error ? error.message : String(error)
        }. No files were changed.`,
      );
    }
  } else {
    let raw;
    try {
      raw = await readFile(fixtureFile, "utf8");
    } catch (error) {
      throw new SyncError(
        `Cannot read the GitHub events fixture ${fixtureFile}: ${
          error instanceof Error ? error.message : String(error)
        }. No files were changed.`,
      );
    }
    try {
      data = JSON.parse(raw);
    } catch (error) {
      throw new SyncError(
        `The GitHub events fixture ${fixtureFile} is not valid JSON: ${
          error instanceof Error ? error.message : String(error)
        }. No files were changed.`,
      );
    }
  }

  const status = fixtureStatus || 200;
  if (status < 200 || status >= 300) throw new SyncError(apiErrorMessage(status, data));
  if (!Array.isArray(data)) throw new SyncError(unexpectedPayloadMessage(status, data));
  return data;
}

async function ensureParentDirectory(filePath) {
  const directory = path.dirname(filePath);
  if (directory && directory !== ".") await mkdir(directory, { recursive: true });
}

async function readStateIds(filePath) {
  let raw;
  try {
    raw = await readFile(filePath, "utf8");
  } catch (error) {
    if (error?.code !== "ENOENT") {
      console.warn(`Cannot read sync state ${filePath}; continuing with content-file dedupe: ${error.message}`);
    }
    return [];
  }
  if (!raw.trim()) return [];
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    console.warn(`Sync state ${filePath} is not valid JSON; continuing with content-file dedupe.`);
    return [];
  }
  if (!Array.isArray(parsed?.syncedEventIds)) {
    console.warn(`Sync state ${filePath} has no syncedEventIds array; continuing with content-file dedupe.`);
    return [];
  }
  return parsed.syncedEventIds
    .filter((id) => typeof id === "string" && id.trim())
    .map((id) => id.trim());
}

async function readContent(filePath) {
  try {
    return await readFile(filePath, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return "";
    throw new SyncError(
      `Cannot read content file ${filePath}: ${
        error instanceof Error ? error.message : String(error)
      }. No files were changed.`,
    );
  }
}

function contentIds(content) {
  const ids = new Set();
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed && typeof parsed.id === "string" && parsed.id.trim()) ids.add(parsed.id.trim());
    } catch {
      // Ignore malformed historical lines; the tweet pipeline already skips them.
    }
  }
  return ids;
}

async function persistState(filePath, ids, maxStateIds, nowMs) {
  // Newest IDs are kept, so the state file stays bounded.
  const pruned = [...new Set(ids)].slice(-maxStateIds);
  const payload = `${JSON.stringify(
    { updatedAt: new Date(nowMs).toISOString(), syncedEventIds: pruned },
    null,
    2,
  )}\n`;
  await ensureParentDirectory(filePath);
  const temporaryPath = `${filePath}.tmp`;
  await writeFile(temporaryPath, payload, "utf8");
  await rename(temporaryPath, filePath);
}

// Settle a known event array against the content and state files. Returns one
// settlement per input event (in input order) plus a fatal message when the run
// had to stop early.
export async function runSync({
  events,
  now = Date.now(),
  maxAgeDays = DEFAULT_MAX_AGE_DAYS,
  excludeRepos = new Set(),
  limit = DEFAULT_LIMIT,
  maxStateIds = DEFAULT_MAX_STATE_IDS,
  contentFile = ".github/content/build-in-public.jsonl",
  stateFile = ".github/runtime/github-events-sync.json",
} = {}) {
  if (!Array.isArray(events)) {
    throw new SyncError(`Expected an array of GitHub events, got ${describePayload(events)}. No files were changed.`);
  }

  const content = await readContent(contentFile);
  const contentIdSet = contentIds(content);
  const seenIds = new Set(await readStateIds(stateFile));
  // Content IDs written by this script (github:*) are synced even if the state
  // file was lost or never saved (e.g. a previous run failed mid-way). Seed the
  // state with them so the next persist reflects reality; other build-in-public
  // seeds are left out of the event-ID ledger.
  for (const id of contentIdSet) {
    if (id.startsWith("github:")) seenIds.add(id);
  }
  const settlements = new Array(events.length);
  const candidates = [];

  events.forEach((event, index) => {
    const outcome = evaluateEvent(event, { now, maxAgeDays, excludeRepos });
    if (outcome.status === "candidate") {
      candidates.push({ index, outcome });
      settlements[index] = {
        index,
        eventId: outcome.eventId,
        status: "pending",
        reason: "pending",
      };
    } else {
      settlements[index] = { index, ...outcome };
    }
  });

  // Oldest first, matching the previous behavior, then cap at the run limit.
  candidates.sort((left, right) => {
    const delta = Date.parse(left.outcome.note.date) - Date.parse(right.outcome.note.date);
    return delta || left.index - right.index;
  });

  let writtenCount = 0;
  let fatal = "";
  let separatorPending = content.length > 0 && !content.endsWith("\n");

  for (const { index, outcome } of candidates) {
    if (fatal) break;
    const note = outcome.note;

    if (seenIds.has(note.id) || contentIdSet.has(note.id)) {
      settlements[index] = {
        index,
        eventId: note.id,
        status: "skipped",
        reason: "duplicate-event",
        detail: "already synced",
      };
      continue;
    }
    if (writtenCount >= limit) {
      settlements[index] = {
        index,
        eventId: note.id,
        status: "skipped",
        reason: "run-limit-reached",
        detail: `limit ${limit} per run`,
      };
      continue;
    }

    try {
      await ensureParentDirectory(contentFile);
      await appendFile(contentFile, `${separatorPending ? "\n" : ""}${JSON.stringify(note)}\n`, "utf8");
    } catch (error) {
      settlements[index] = {
        index,
        eventId: note.id,
        status: "failed",
        reason: "write-failed",
        detail: error instanceof Error ? error.message : String(error),
      };
      fatal = `content write failed for ${note.id}; earlier events were kept.`;
      break;
    }

    separatorPending = false;
    writtenCount += 1;
    contentIdSet.add(note.id);
    seenIds.add(note.id);
    settlements[index] = {
      index,
      eventId: note.id,
      status: "written",
      summary: note.summary,
      note,
    };

    try {
      await persistState(stateFile, seenIds, maxStateIds, now);
    } catch (error) {
      settlements[index] = {
        index,
        eventId: note.id,
        status: "failed",
        reason: "state-write-failed",
        detail: `content for ${note.id} was written, but sync state was not saved: ${
          error instanceof Error ? error.message : String(error)
        }`,
      };
      fatal = `state write failed after ${note.id}; content was kept, re-run dedupes it from the content file.`;
    }
  }

  // Any candidate left "pending" was never attempted because the run stopped
  // early; report it explicitly instead of leaving a gap in the settlement.
  for (const { index, outcome } of candidates) {
    if (settlements[index]?.status === "pending") {
      settlements[index] = {
        index,
        eventId: outcome.eventId,
        status: "skipped",
        reason: "not-processed",
        detail: "run stopped before this event",
      };
    }
  }

  const byStatus = (status) => settlements.filter((settlement) => settlement.status === status);
  return {
    received: events.length,
    settlements,
    written: byStatus("written"),
    skipped: byStatus("skipped"),
    failed: byStatus("failed"),
    fatal,
  };
}

export function formatSummary(result, { contentFile, stateFile } = {}) {
  const reasonCounts = new Map();
  for (const settlement of result.skipped) {
    reasonCounts.set(settlement.reason, (reasonCounts.get(settlement.reason) || 0) + 1);
  }
  const lines = [
    `GitHub events sync: ${result.received} event(s) received, ${result.written.length} written, ${result.skipped.length} skipped, ${result.failed.length} failed.`,
  ];
  if (contentFile) lines.push(`  content file: ${contentFile}`);
  if (stateFile) lines.push(`  state file:   ${stateFile}`);
  if (reasonCounts.size) {
    lines.push(
      `  skip reasons: ${[...reasonCounts.entries()].map(([reason, count]) => `${reason}=${count}`).join(", ")}`,
    );
  }
  for (const settlement of result.settlements) {
    const id = settlement.eventId || "(no id)";
    if (settlement.status === "written") {
      lines.push(`  written  ${id}  ${settlement.summary}`);
    } else if (settlement.status === "skipped") {
      lines.push(
        `  skipped  ${id}  ${settlement.reason}${settlement.detail ? `: ${settlement.detail}` : ""}`,
      );
    } else if (settlement.status === "failed") {
      lines.push(`  failed   ${id}  ${settlement.reason}: ${settlement.detail}`);
    }
  }
  if (result.fatal) lines.push(`  stopped early: ${result.fatal}`);
  return lines.join("\n");
}

export async function main(source = process.env, argv = process.argv.slice(2)) {
  const config = readConfig(source, argv);
  const usingFixture = Boolean(config.fixtureFile || config.fixtureJson);

  if (!usingFixture && !config.token) {
    console.warn("GITHUB_TOKEN is not set; skipping GitHub events sync.");
    return { received: 0, settlements: [], written: [], skipped: [], failed: [], fatal: "", skippedRun: true };
  }
  if (!usingFixture && !config.owner) {
    console.warn("GITHUB_EVENTS_OWNER / GITHUB_REPOSITORY_OWNER is not set; skipping GitHub events sync.");
    return { received: 0, settlements: [], written: [], skipped: [], failed: [], fatal: "", skippedRun: true };
  }

  // Fetch/load before touching content or state: an API failure leaves both files
  // exactly as they were.
  const events = usingFixture ? await loadFixture(config) : await fetchEvents(config);
  const result = await runSync({ events, ...config });
  console.log(formatSummary(result, config));
  if (result.fatal) process.exitCode = 1;
  return result;
}

// ---------------------------------------------------------------------------
// Offline self-test: builds event arrays and temp files, then runs this script
// as a child process to verify exit codes and on-disk results.
// ---------------------------------------------------------------------------

function buildEvents(nowIso) {
  const recent = new Date(Date.parse(nowIso) - 60 * 60 * 1000).toISOString();
  return {
    push: {
      id: "1001",
      type: "PushEvent",
      repo: { name: "Linus-Shyu/XGrowth" },
      created_at: recent,
      payload: { ref: "refs/heads/main", commits: [{ message: "Add per-event sync settlement" }] },
    },
    release: {
      id: "1002",
      type: "ReleaseEvent",
      repo: { name: "Linus-Shyu/XGrowth" },
      created_at: recent,
      payload: { release: { tag_name: "v1.2.3", name: "v1.2.3", body: "Settlement summary" } },
    },
  };
}

async function runSelfTest() {
  const failures = [];
  const check = (condition, message) => {
    if (!condition) failures.push(message);
  };
  const baseDir = await mkdtemp(path.join(os.tmpdir(), "github-events-sync-test-"));
  const scriptPath = fileURLToPath(import.meta.url);
  const nowIso = "2026-11-16T12:00:00.000Z";
  const fixtures = buildEvents(nowIso);

  const runScript = (envOverrides = {}, args = []) => {
    const env = {
      ...process.env,
      GITHUB_TOKEN: "",
      GH_TOKEN: "",
      GITHUB_EVENTS_OWNER: "",
      GITHUB_REPOSITORY_OWNER: "",
      GITHUB_EVENTS_NOW: nowIso,
      GITHUB_EVENTS_FIXTURE_FILE: "",
      GITHUB_EVENTS_FIXTURE_JSON: "",
      GITHUB_EVENTS_FIXTURE_STATUS: "",
      GITHUB_EVENTS_MAX_AGE_DAYS: "",
      GITHUB_EVENTS_LIMIT: "",
      GITHUB_EVENTS_SYNC_MAX_IDS: "",
      GITHUB_EVENTS_EXCLUDE_REPOS: "",
      ...envOverrides,
    };
    return spawnSync(process.execPath, [scriptPath, ...args], {
      cwd: baseDir,
      env,
      encoding: "utf8",
    });
  };
  const readText = async (filePath) => readFile(filePath, "utf8").catch(() => null);
  const lineIds = (text) =>
    String(text || "")
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => {
        try {
          return JSON.parse(line).id;
        } catch {
          return null;
        }
      })
      .filter(Boolean);

  try {
    // 1. Normal write, then idempotent re-run.
    const caseDir = path.join(baseDir, "normal");
    const contentFile = path.join(caseDir, "content", "build-in-public.jsonl");
    const stateFile = path.join(caseDir, "runtime", "sync-state.json");
    const normalEnv = { TWEET_BUILD_IN_PUBLIC_FILE: contentFile, GITHUB_EVENTS_SYNC_FILE: stateFile };
    const eventsFile = path.join(baseDir, "events.json");
    await writeFile(eventsFile, JSON.stringify([fixtures.push, fixtures.release]), "utf8");

    let result = runScript({ ...normalEnv, GITHUB_EVENTS_FIXTURE_FILE: eventsFile });
    check(result.status === 0, `normal run exited ${result.status}: ${result.stderr}`);
    let content = await readText(contentFile);
    check(lineIds(content).length === 2, `normal run should write 2 notes, wrote ${lineIds(content).length}`);
    check(lineIds(content)[0] === "github:1001", "normal run should keep oldest event first");
    const firstContent = content;
    let state = JSON.parse((await readText(stateFile)) || "{}");
    check(
      Array.isArray(state.syncedEventIds) && state.syncedEventIds.length === 2,
      "normal run should record 2 synced ids in state",
    );

    result = runScript({ ...normalEnv, GITHUB_EVENTS_FIXTURE_FILE: eventsFile });
    check(result.status === 0, `dedupe re-run exited ${result.status}: ${result.stderr}`);
    content = await readText(contentFile);
    check(content === firstContent, "dedupe re-run must not append duplicate notes");
    check(/duplicate-event=2/.test(result.stdout), `dedupe re-run summary missing duplicate reasons:\n${result.stdout}`);

    // 2. Mixed bad events are skipped individually; good ones still land.
    const mixedDir = path.join(baseDir, "mixed");
    const mixedContent = path.join(mixedDir, "content.jsonl");
    const mixedState = path.join(mixedDir, "state.json");
    const staleEvent = { ...fixtures.push, id: "2001", created_at: "2026-10-01T00:00:00.000Z" };
    const excludedEvent = { ...fixtures.push, id: "2002", repo: { name: "Linus-Shyu/x_bot" } };
    const unsupportedEvent = { id: "2003", type: "CreateEvent", repo: { name: "Linus-Shyu/XGrowth" }, created_at: fixtures.push.created_at, payload: {} };
    const missingType = { id: "2004", repo: { name: "Linus-Shyu/XGrowth" }, created_at: fixtures.push.created_at, payload: {} };
    const badAction = {
      id: "2005",
      type: "WatchEvent",
      repo: { name: "Linus-Shyu/XGrowth" },
      created_at: fixtures.push.created_at,
      payload: { action: "stopped" },
    };
    const missingCommitMessage = {
      id: "2006",
      type: "PushEvent",
      repo: { name: "Linus-Shyu/XGrowth" },
      created_at: fixtures.push.created_at,
      payload: { ref: "refs/heads/main", commits: [{}] },
    };
    await writeFile(
      path.join(baseDir, "mixed.json"),
      JSON.stringify([
        fixtures.release,
        "not-an-object",
        staleEvent,
        excludedEvent,
        unsupportedEvent,
        missingType,
        badAction,
        missingCommitMessage,
        fixtures.push,
      ]),
      "utf8",
    );
    result = runScript({
      TWEET_BUILD_IN_PUBLIC_FILE: mixedContent,
      GITHUB_EVENTS_SYNC_FILE: mixedState,
      GITHUB_EVENTS_FIXTURE_FILE: path.join(baseDir, "mixed.json"),
    });
    check(result.status === 0, `mixed run should exit 0 even with bad events (exited ${result.status})`);
    const mixedIds = lineIds(await readText(mixedContent)).sort();
    check(
      mixedIds.join(",") === "github:1001,github:1002",
      `mixed run should write only the 2 good events, got ${mixedIds.join(",")}`,
    );
    for (const reason of [
      "invalid-event",
      "stale-event",
      "excluded-repo",
      "unsupported-type",
      "missing-fields",
      "unsupported-action",
    ]) {
      check(result.stdout.includes(reason), `mixed run summary missing skip reason ${reason}:\n${result.stdout}`);
    }

    // 3. Rate limit / unexpected payload must leave existing files untouched and exit non-zero.
    const guardDir = path.join(baseDir, "guard");
    const guardContent = path.join(guardDir, "content.jsonl");
    const guardState = path.join(guardDir, "state.json");
    await mkdir(guardDir, { recursive: true });
    await writeFile(guardContent, `${JSON.stringify({ id: "existing-note", summary: "keep me" })}\n`, "utf8");
    await writeFile(guardState, `${JSON.stringify({ syncedEventIds: ["existing-note"] }, null, 2)}\n`, "utf8");
    const guardEnv = { TWEET_BUILD_IN_PUBLIC_FILE: guardContent, GITHUB_EVENTS_SYNC_FILE: guardState };
    const originalContent = await readText(guardContent);
    const originalState = await readText(guardState);

    const rateLimited = runScript({
      ...guardEnv,
      GITHUB_EVENTS_FIXTURE_FILE: eventsFile,
      GITHUB_EVENTS_FIXTURE_STATUS: "403",
    });
    check(rateLimited.status !== 0, "rate-limited run must exit non-zero");
    check(/left unchanged/.test(rateLimited.stderr), `rate-limited run should explain the guard:\n${rateLimited.stderr}`);
    check((await readText(guardContent)) === originalContent, "rate-limited run must not modify the content file");
    check((await readText(guardState)) === originalState, "rate-limited run must not modify the state file");

    await writeFile(path.join(baseDir, "not-array.json"), JSON.stringify({ message: "Bad credentials" }), "utf8");
    const badShape = runScript({ ...guardEnv, GITHUB_EVENTS_FIXTURE_FILE: path.join(baseDir, "not-array.json") });
    check(badShape.status !== 0, "non-array payload must exit non-zero");
    check(/unexpected payload/.test(badShape.stderr), `non-array payload should be explained:\n${badShape.stderr}`);
    check((await readText(guardContent)) === originalContent, "non-array payload must not modify the content file");
    check((await readText(guardState)) === originalState, "non-array payload must not modify the state file");

    // 4. A failure after a successful write keeps the written event, and a re-run
    //    does not append it again (content-file dedupe).
    const partialDir = path.join(baseDir, "partial");
    const partialContent = path.join(partialDir, "content.jsonl");
    const blockedState = path.join(partialDir, "state-blocker"); // a file, so mkdir of its child fails
    await mkdir(partialDir, { recursive: true });
    await writeFile(blockedState, "not a directory", "utf8");
    const partialEvents = JSON.stringify([fixtures.push, fixtures.release]);
    result = runScript({
      TWEET_BUILD_IN_PUBLIC_FILE: partialContent,
      GITHUB_EVENTS_SYNC_FILE: path.join(blockedState, "state.json"),
      GITHUB_EVENTS_FIXTURE_JSON: partialEvents,
    });
    check(result.status !== 0, "state-write failure must exit non-zero");
    check(lineIds(await readText(partialContent)).includes("github:1001"), "written event must survive a later state failure");

    const recoveredState = path.join(partialDir, "state.json");
    result = runScript({
      TWEET_BUILD_IN_PUBLIC_FILE: partialContent,
      GITHUB_EVENTS_SYNC_FILE: recoveredState,
      GITHUB_EVENTS_FIXTURE_JSON: partialEvents,
    });
    check(result.status === 0, `recovery run exited ${result.status}: ${result.stderr}`);
    check(
      lineIds(await readText(partialContent)).join(",") === "github:1001,github:1002",
      `recovery run must not duplicate the already-written event, got ${lineIds(await readText(partialContent)).join(",")}`,
    );

    // 5. State file stays bounded by GITHUB_EVENTS_SYNC_MAX_IDS.
    const capDir = path.join(baseDir, "cap");
    const capContent = path.join(capDir, "content.jsonl");
    const capState = path.join(capDir, "state.json");
    const capEvents = [1, 2, 3, 4].map((offset) => ({
      ...fixtures.push,
      id: String(3000 + offset),
      created_at: new Date(Date.parse(nowIso) - offset * 60 * 1000).toISOString(),
    }));
    result = runScript({
      TWEET_BUILD_IN_PUBLIC_FILE: capContent,
      GITHUB_EVENTS_SYNC_FILE: capState,
      GITHUB_EVENTS_FIXTURE_JSON: JSON.stringify(capEvents),
      GITHUB_EVENTS_SYNC_MAX_IDS: "2",
      GITHUB_EVENTS_LIMIT: "10",
    });
    check(result.status === 0, `cap run exited ${result.status}: ${result.stderr}`);
    state = JSON.parse((await readText(capState)) || "{}");
    check(
      Array.isArray(state.syncedEventIds) && state.syncedEventIds.length === 2,
      `state file must stay capped at 2 ids, got ${JSON.stringify(state.syncedEventIds)}`,
    );
    check(
      state.syncedEventIds.slice().sort().join(",") === "github:3001,github:3002",
      `state must keep the newest ids, got ${state.syncedEventIds.join(",")}`,
    );

    // 6. Run limit: extra candidates are reported as skipped, not written.
    const limitDir = path.join(baseDir, "limit");
    const limitContent = path.join(limitDir, "content.jsonl");
    const limitState = path.join(limitDir, "state.json");
    result = runScript({
      TWEET_BUILD_IN_PUBLIC_FILE: limitContent,
      GITHUB_EVENTS_SYNC_FILE: limitState,
      GITHUB_EVENTS_FIXTURE_JSON: JSON.stringify([fixtures.push, fixtures.release]),
      GITHUB_EVENTS_LIMIT: "1",
    });
    check(result.status === 0, `limit run exited ${result.status}: ${result.stderr}`);
    check(lineIds(await readText(limitContent)).length === 1, "limit run must write only one note");
    check(/run-limit-reached=1/.test(result.stdout), `limit run summary missing run-limit-reached:\n${result.stdout}`);

    // 7. A lost state file is rebuilt from the content file: already-written
    //    github: notes are not appended again, and the state is repopulated.
    const lostDir = path.join(baseDir, "lost-state");
    const lostContent = path.join(lostDir, "content.jsonl");
    const lostState = path.join(lostDir, "missing-state.json");
    result = runScript({
      TWEET_BUILD_IN_PUBLIC_FILE: lostContent,
      GITHUB_EVENTS_SYNC_FILE: path.join(lostDir, "first-state.json"),
      GITHUB_EVENTS_FIXTURE_JSON: JSON.stringify([fixtures.push]),
    });
    check(result.status === 0, `lost-state seed run exited ${result.status}: ${result.stderr}`);
    result = runScript({
      TWEET_BUILD_IN_PUBLIC_FILE: lostContent,
      GITHUB_EVENTS_SYNC_FILE: lostState,
      GITHUB_EVENTS_FIXTURE_JSON: JSON.stringify([fixtures.push, fixtures.release]),
    });
    check(result.status === 0, `lost-state run exited ${result.status}: ${result.stderr}`);
    check(
      lineIds(await readText(lostContent)).join(",") === "github:1001,github:1002",
      `lost-state run must not duplicate the note already in the content file, got ${lineIds(
        await readText(lostContent),
      ).join(",")}`,
    );
    state = JSON.parse((await readText(lostState)) || "{}");
    check(
      state.syncedEventIds?.includes("github:1001") && state.syncedEventIds?.includes("github:1002"),
      `lost-state run should rebuild the ledger from content, got ${JSON.stringify(state.syncedEventIds)}`,
    );
  } finally {
    await rm(baseDir, { recursive: true, force: true });
  }

  if (failures.length) {
    for (const failure of failures) console.error(`Self-test failed: ${failure}`);
    throw new SyncError(`${failures.length} self-test check(s) failed.`);
  }
  console.log("GitHub events sync self-test passed.");
}

const invokedDirectly =
  import.meta.main === true ||
  (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url);

if (invokedDirectly) {
  const argv = process.argv.slice(2);
  const entry = argv.includes("--self-test") ? runSelfTest() : main(process.env, argv);
  entry.catch((error) => {
    console.error(`GitHub events sync failed: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  });
}
