import type { NetworkTables } from '@ntcore-ts/client';

import { decodeInventoryValue } from './decode.js';
import {
  DEFAULT_SUBSCRIBE_DURATION_S,
  MAX_SAMPLES_PER_TOPIC,
  MAX_SUBSCRIBE_DURATION_S,
  MAX_SUBSCRIBE_TOPICS,
  toJsonSafe,
  type JsonValue,
} from './response.js';

export interface SamplePoint {
  t: number;
  value: JsonValue;
}

export interface TopicSummary {
  topic: string;
  type: string;
  /** Sample points kept for this topic (never exceeds MAX_SAMPLES_PER_TOPIC). */
  count: number;
  first: JsonValue;
  last: JsonValue;
  min?: number;
  max?: number;
  mean?: number;
  rateHz?: number;
  /**
   * Updates delivered for this topic with a usable value, before change/interval/cap filtering.
   * Always equals `count + droppedDuplicate + droppedInterval + droppedLimit`; undecodable
   * payloads are counted in `samplingErrors` instead.
   */
  received: number;
  /** Updates skipped by changeOnly de-duplication. */
  droppedDuplicate: number;
  /** Updates skipped by sampleIntervalMs thinning. */
  droppedInterval: number;
  /** Updates dropped because this topic already held MAX_SAMPLES_PER_TOPIC points. */
  droppedLimit: number;
  /** True when this topic hit MAX_SAMPLES_PER_TOPIC and later updates were dropped. */
  truncated: boolean;
}

/** What the link did during the sampling window. */
export interface SampleConnectionReport {
  /** Whether this client could report link state at all; false only for minimal stubs. */
  observable: boolean;
  /** Link state when the window opened; null when it could not be observed. */
  connectedAtStart: boolean | null;
  /** Link state when the window closed; null when it could not be observed. */
  connectedAtEnd: boolean | null;
  /** No disconnect was observed and the link was up at both ends; null when unobservable. */
  stayedConnected: boolean | null;
  /** Observed connected -> disconnected transitions during the window. */
  disconnects: number;
  /** Observed disconnected -> connected transitions during the window. */
  reconnects: number;
  /** Observed time offline during the window (milliseconds, approximate). */
  disconnectedMs: number;
  /** Offset from window start of the first observed disconnect, or null when none. */
  firstDisconnectAtMs: number | null;
}

export interface PrefixSubscriptionError {
  prefix: string;
  error: string;
}

export interface SubscribeOptions {
  prefixes: string[];
  durationSeconds?: number;
  sampleIntervalMs?: number;
  changeOnly?: boolean;
  changeEpsilon?: number;
  format?: 'summary' | 'samples';
  types?: Record<string, string>;
}

export interface SubscribeResult {
  durationSeconds: number;
  topicCount: number;
  format: 'summary' | 'samples';
  summaries?: TopicSummary[];
  samples?: Record<string, SamplePoint[]>;
  /** True when the topic cap or a per-topic sample cap dropped data. */
  truncated: boolean;
  /** Connection settlement for the window. */
  connection: SampleConnectionReport;
  /** Distinct topics skipped because MAX_SUBSCRIBE_TOPICS was already reached. */
  omittedTopics: number;
  /**
   * True when the window is clean evidence: the link stayed up for the whole window, every prefix
   * subscribed, no topic was omitted by the topic cap, no topic was truncated by the sample cap,
   * and at least one topic produced a sample. Read `warnings` for why it is false.
   *
   * Undecodable values are reported in `samplingErrors` but do not by themselves clear `complete`;
   * the capture worked, those particular values just have no JSON representation.
   */
  complete: boolean;
  /** Human/agent-readable reasons the window is not clean evidence (empty when clean). */
  warnings: string[];
  /** Prefixes whose subscription could not be created; their topics are absent from this result. */
  prefixErrors: PrefixSubscriptionError[];
  /** Updates that could not be turned into a sample (undecodable payload or sampler error). */
  samplingErrors: number;
}

interface TopicBucket {
  type: string;
  points: SamplePoint[];
  lastRaw: unknown;
  received: number;
  droppedDuplicate: number;
  droppedInterval: number;
  droppedLimit: number;
}

function isNumeric(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function valuesEqual(a: unknown, b: unknown, epsilon: number): boolean {
  if (isNumeric(a) && isNumeric(b)) return Math.abs(a - b) <= epsilon;
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Tracks link up/down transitions during a sampling window.
 *
 * Uses the client's connection listener when available and also polls isRobotConnected() as a
 * safety net, so a stub (or a missed event) cannot hide a disconnect. Transitions are de-duplicated
 * against the last observed state, so both sources can be active without double counting.
 */
class ConnectionTracker {
  private online: boolean;
  private readonly startedOnline: boolean;
  private disconnects = 0;
  private reconnects = 0;
  private disconnectedMs = 0;
  private downSince: number | null;
  private firstDisconnectAtMs: number | null = null;
  private removeListener: (() => void) | undefined;
  private readonly observable: boolean;

  constructor(
    private readonly nt: NetworkTables,
    private readonly startedAt: number
  ) {
    this.observable = typeof nt.isRobotConnected === 'function';
    this.startedOnline = this.read();
    this.online = this.startedOnline;
    this.downSince = this.startedOnline ? null : startedAt;
    if (typeof nt.addRobotConnectionListener === 'function') {
      try {
        this.removeListener = nt.addRobotConnectionListener((connected) => this.update(connected));
      } catch {
        this.removeListener = undefined;
      }
    }
  }

  private read(): boolean {
    if (!this.observable) return false;
    try {
      return this.nt.isRobotConnected();
    } catch {
      return false;
    }
  }

  /** Re-read the current state; safe to call as often as needed. */
  sync(): void {
    this.update(this.read());
  }

  private update(connected: boolean): void {
    if (!this.observable) return;
    if (connected === this.online) return;
    const now = Date.now();
    if (connected) {
      if (this.downSince != null) this.disconnectedMs += now - this.downSince;
      this.downSince = null;
      this.reconnects += 1;
    } else {
      this.disconnects += 1;
      this.downSince = now;
      if (this.firstDisconnectAtMs == null) this.firstDisconnectAtMs = now - this.startedAt;
    }
    this.online = connected;
  }

  /** Close the window: capture the final state, settle offline time, and detach. */
  finish(): SampleConnectionReport {
    this.sync();
    if (!this.online && this.downSince != null) {
      this.disconnectedMs += Math.max(0, Date.now() - this.downSince);
      this.downSince = null;
    }
    try {
      this.removeListener?.();
    } catch {
      // ignore listener teardown races
    }
    this.removeListener = undefined;
    return {
      observable: this.observable,
      connectedAtStart: this.observable ? this.startedOnline : null,
      connectedAtEnd: this.observable ? this.online : null,
      stayedConnected: this.observable ? this.startedOnline && this.disconnects === 0 && this.online : null,
      disconnects: this.disconnects,
      reconnects: this.reconnects,
      disconnectedMs: Math.max(0, Math.round(this.disconnectedMs)),
      firstDisconnectAtMs: this.firstDisconnectAtMs,
    };
  }
}

/**
 * Collect samples via short-lived prefix subscriptions only.
 * (Typed topic subscriptions do not receive values after a prefix sub has been used
 * on the same NetworkTables instance — see inventory snapshot design.)
 *
 * A disconnect mid-window never aborts or clears the window: buckets are kept, the window keeps
 * running, and updates arriving after reconnect accumulate into the same per-topic bucket.
 */
export async function collectSamples(nt: NetworkTables, options: SubscribeOptions): Promise<SubscribeResult> {
  const durationSeconds = Math.min(
    Math.max(options.durationSeconds ?? DEFAULT_SUBSCRIBE_DURATION_S, 0.1),
    MAX_SUBSCRIBE_DURATION_S
  );
  const format = options.format ?? 'summary';
  const changeOnly = options.changeOnly ?? false;
  const epsilon = options.changeEpsilon ?? 1e-9;
  const sampleIntervalMs = options.sampleIntervalMs ?? 0;
  const typeMap = { ...options.types };

  const buckets = new Map<string, TopicBucket>();
  const omittedTopics = new Set<string>();
  const prefixErrors: PrefixSubscriptionError[] = [];
  let truncated = false;
  let samplingErrors = 0;

  const windowStartedAt = Date.now();
  const connection = new ConnectionTracker(nt, windowStartedAt);

  const ensureBucket = (name: string, type: string): TopicBucket | null => {
    let bucket = buckets.get(name);
    if (!bucket) {
      if (buckets.size >= MAX_SUBSCRIBE_TOPICS) {
        truncated = true;
        omittedTopics.add(name);
        return null;
      }
      bucket = {
        type,
        points: [],
        lastRaw: undefined,
        received: 0,
        droppedDuplicate: 0,
        droppedInterval: 0,
        droppedLimit: 0,
      };
      buckets.set(name, bucket);
    }
    return bucket;
  };

  const record = (name: string, type: string, value: unknown) => {
    if (value == null) return;
    let decoded: unknown = value;
    if (value instanceof Uint8Array) {
      decoded = decodeInventoryValue(type, value);
      if (decoded == null) {
        samplingErrors += 1;
        return;
      }
    }
    const bucket = ensureBucket(name, type);
    if (!bucket) return;
    bucket.received += 1;
    if (changeOnly && bucket.lastRaw !== undefined && valuesEqual(bucket.lastRaw, decoded, epsilon)) {
      bucket.droppedDuplicate += 1;
      return;
    }
    const now = Date.now();
    if (sampleIntervalMs > 0 && bucket.points.length > 0) {
      const lastT = bucket.points[bucket.points.length - 1]!.t;
      if (now - lastT < sampleIntervalMs) {
        bucket.droppedInterval += 1;
        return;
      }
    }
    if (bucket.points.length >= MAX_SAMPLES_PER_TOPIC) {
      bucket.droppedLimit += 1;
      truncated = true;
      return;
    }
    bucket.lastRaw = decoded;
    bucket.points.push({ t: now, value: toJsonSafe(decoded) });
  };

  const subscriptions: { pt: ReturnType<NetworkTables['getPrefixTopic']>; subuid: number }[] = [];
  try {
    for (const prefix of options.prefixes) {
      try {
        const pt = nt.getPrefixTopic(prefix);
        const subuid = pt.subscribe(
          (value, params) => {
            try {
              const type = typeMap[params.name] ?? params.type;
              typeMap[params.name] = type;
              record(params.name, type, value);
            } catch {
              // A bad value must never tear down the subscription for the rest of the window.
              samplingErrors += 1;
            }
          },
          { all: true, immediateNotify: true }
        );
        subscriptions.push({ pt, subuid });
      } catch (err) {
        prefixErrors.push({ prefix, error: err instanceof Error ? err.message : String(err) });
      }
    }

    const windowMs = durationSeconds * 1000;
    // Poll as a safety net for clients/stubs that do not fire connection listeners promptly.
    const pollMs = Math.max(20, Math.min(100, Math.round(windowMs / 4)));
    const pollTimer = setInterval(() => connection.sync(), pollMs);
    try {
      await new Promise((r) => setTimeout(r, windowMs));
    } finally {
      clearInterval(pollTimer);
    }
  } finally {
    for (const { pt, subuid } of subscriptions) {
      try {
        pt.unsubscribe(subuid);
      } catch {
        // ignore
      }
    }
  }

  const connectionReport = connection.finish();

  const summaries: TopicSummary[] = [];
  const samples: Record<string, SamplePoint[]> = {};

  for (const [topic, bucket] of buckets) {
    if (format === 'samples') {
      samples[topic] = bucket.points;
    }
    const nums = bucket.points.map((p) => p.value).filter(isNumeric);
    const first = bucket.points[0]?.value ?? null;
    const last = bucket.points[bucket.points.length - 1]?.value ?? null;
    const elapsedSec =
      bucket.points.length >= 2
        ? (bucket.points[bucket.points.length - 1]!.t - bucket.points[0]!.t) / 1000
        : durationSeconds;
    const summary: TopicSummary = {
      topic,
      type: bucket.type,
      count: bucket.points.length,
      first,
      last,
      rateHz: elapsedSec > 0 ? bucket.points.length / elapsedSec : undefined,
      received: bucket.received,
      droppedDuplicate: bucket.droppedDuplicate,
      droppedInterval: bucket.droppedInterval,
      droppedLimit: bucket.droppedLimit,
      truncated: bucket.droppedLimit > 0,
    };
    if (nums.length > 0) {
      summary.min = Math.min(...nums);
      summary.max = Math.max(...nums);
      summary.mean = nums.reduce((a, b) => a + b, 0) / nums.length;
    }
    summaries.push(summary);
  }

  summaries.sort((a, b) => a.topic.localeCompare(b.topic));

  const warnings: string[] = [];
  if (!connectionReport.observable) {
    warnings.push('Connection state could not be observed; the link state for this window is unknown.');
  }
  if (!connectionReport.connectedAtStart) {
    warnings.push('Connection was down when the sampling window started; early updates may be missing.');
  }
  if (connectionReport.disconnects > 0) {
    warnings.push(
      `Connection dropped ${connectionReport.disconnects} time(s) during the window (~${connectionReport.disconnectedMs}ms offline); samples from before the drop were kept and sampling resumed after reconnect.`
    );
  }
  if (!connectionReport.connectedAtEnd) {
    warnings.push('Connection was down when the sampling window ended; the last part of the window delivered no data.');
  }
  if (prefixErrors.length > 0) {
    warnings.push(
      `${prefixErrors.length} prefix subscription(s) failed; those prefixes are not represented in this result.`
    );
  }
  if (omittedTopics.size > 0) {
    warnings.push(
      `Topic cap (${MAX_SUBSCRIBE_TOPICS}) reached; ${omittedTopics.size} matching topic(s) were not sampled (truncated).`
    );
  }
  const truncatedTopics = summaries.filter((s) => s.truncated).length;
  if (truncatedTopics > 0) {
    warnings.push(
      `${truncatedTopics} topic(s) hit the per-topic sample cap (${MAX_SAMPLES_PER_TOPIC}); later updates were dropped as truncated.`
    );
  }
  if (summaries.length === 0) {
    warnings.push(
      'No topic values were received in the window; the prefixes matched no topics or the link delivered nothing. This is not evidence of a stable value.'
    );
  }

  // `samplingErrors` counts updates whose value could not be decoded (e.g. a custom struct with no
  // built-in descriptor). It is reported separately and deliberately not folded into `complete`:
  // the capture window itself was healthy; those values just have no JSON representation.
  const complete = warnings.length === 0;

  return {
    durationSeconds,
    topicCount: buckets.size,
    format,
    ...(format === 'summary' ? { summaries } : { samples, summaries }),
    truncated,
    connection: connectionReport,
    omittedTopics: omittedTopics.size,
    complete,
    warnings,
    prefixErrors,
    samplingErrors,
  };
}
