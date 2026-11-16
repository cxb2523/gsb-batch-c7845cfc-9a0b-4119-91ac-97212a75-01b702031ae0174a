import { describe, expect, it } from 'vitest';

import type { NetworkTables } from '@ntcore-ts/client';

import { collectSamples } from './sampler.js';

type Callback = (value: unknown, params: { name: string; type: string; id: number; properties: null }) => void;

class StubPrefixTopic {
  private nextId = 1;
  private readonly handlers = new Map<number, Callback>();
  private readonly values = new Map<string, unknown>();

  constructor(readonly prefix: string) {}

  subscribe(cb: Callback, options?: { immediateNotify?: boolean }) {
    const id = this.nextId++;
    this.handlers.set(id, cb);
    if (options?.immediateNotify) {
      // Mirror the real client: replay the last known value of each matching subtopic.
      for (const [name, value] of this.values) {
        cb(value, { name, type: 'double', id: 0, properties: null });
      }
    }
    return id;
  }

  unsubscribe(id: number) {
    this.handlers.delete(id);
  }

  deliver(name: string, type: string, value: unknown) {
    this.values.set(name, value);
    // Map iterators tolerate deleting entries mid-iteration, so a snapshot is unnecessary.
    for (const cb of this.handlers.values()) {
      cb(value, { name, type, id: 0, properties: null });
    }
  }

  get handlerCount() {
    return this.handlers.size;
  }
}

interface StubOptions {
  /** Omit addRobotConnectionListener to exercise the isRobotConnected() polling fallback. */
  withConnectionListener?: boolean;
  /** Prefixes that throw from getPrefixTopic, simulating a subscription that cannot be created. */
  failingPrefixes?: string[];
}

class StubNt {
  connected = true;
  readonly listeners = new Set<(connected: boolean) => void>();
  readonly prefixes = new Map<string, StubPrefixTopic>();
  private readonly options: StubOptions;

  constructor(options: StubOptions = {}) {
    this.options = options;
  }

  getPrefixTopic(prefix: string) {
    if (this.options.failingPrefixes?.includes(prefix)) throw new Error(`cannot subscribe ${prefix}`);
    let topic = this.prefixes.get(prefix);
    if (!topic) {
      topic = new StubPrefixTopic(prefix);
      this.prefixes.set(prefix, topic);
    }
    return topic;
  }

  isRobotConnected() {
    return this.connected;
  }

  addRobotConnectionListener(cb: (connected: boolean) => void) {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  }

  setConnected(connected: boolean) {
    this.connected = connected;
    for (const listener of this.listeners) listener(connected);
  }

  emit(name: string, type: string, value: unknown) {
    for (const topic of this.prefixes.values()) {
      if (name.startsWith(topic.prefix) || topic.prefix === '/') topic.deliver(name, type, value);
    }
  }

  emitSeries(name: string, type: string, series: unknown[]) {
    for (const value of series) this.emit(name, type, value);
  }

  get activeHandlers() {
    let total = 0;
    for (const topic of this.prefixes.values()) total += topic.handlerCount;
    return total;
  }

  asNt(): NetworkTables {
    const withListener = this.options.withConnectionListener ?? true;
    const api: Record<string, unknown> = {
      getPrefixTopic: (prefix: string) => this.getPrefixTopic(prefix),
      isRobotConnected: () => this.isRobotConnected(),
    };
    if (withListener) {
      api.addRobotConnectionListener = (cb: (connected: boolean) => void) => this.addRobotConnectionListener(cb);
    }
    return api as unknown as NetworkTables;
  }
}

/** Stub with no connection surface at all (neither listener nor isRobotConnected). */
function bareStub(): NetworkTables {
  const prefixes = new Map<string, StubPrefixTopic>();
  return {
    getPrefixTopic: (prefix: string) => {
      let topic = prefixes.get(prefix);
      if (!topic) {
        topic = new StubPrefixTopic(prefix);
        prefixes.set(prefix, topic);
      }
      return topic;
    },
  } as unknown as NetworkTables;
}

describe('collectSamples', () => {
  it('summarizes numeric series and settles a clean window', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.15,
      format: 'summary',
    });
    stub.emitSeries('/MyTable/Gyro', 'double', [1, 2, 3, 4]);
    const result = await pending;

    expect(result.topicCount).toBe(1);
    const summary = result.summaries?.[0];
    expect(summary?.topic).toBe('/MyTable/Gyro');
    expect(summary?.count).toBe(4);
    expect(summary?.received).toBe(4);
    expect(summary?.min).toBe(1);
    expect(summary?.max).toBe(4);
    expect(summary?.mean).toBe(2.5);
    expect(summary?.last).toBe(4);
    expect(summary?.droppedDuplicate).toBe(0);
    expect(summary?.droppedInterval).toBe(0);
    expect(summary?.droppedLimit).toBe(0);
    expect(summary?.truncated).toBe(false);

    expect(result.connection).toMatchObject({
      observable: true,
      connectedAtStart: true,
      connectedAtEnd: true,
      stayedConnected: true,
      disconnects: 0,
      reconnects: 0,
      disconnectedMs: 0,
      firstDisconnectAtMs: null,
    });
    expect(result.complete).toBe(true);
    expect(result.warnings).toEqual([]);
    expect(result.truncated).toBe(false);
    expect(result.prefixErrors).toEqual([]);
    expect(result.omittedTopics).toBe(0);
    expect(result.samplingErrors).toBe(0);
  });

  it('respects change_only and counts de-duplicated updates separately', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.15,
      changeOnly: true,
      format: 'summary',
    });
    stub.emitSeries('/MyTable/Gyro', 'double', [1, 1, 1, 2]);
    const result = await pending;

    const summary = result.summaries?.[0];
    expect(summary?.count).toBe(2);
    expect(summary?.received).toBe(4);
    expect(summary?.droppedDuplicate).toBe(2);
    expect(summary?.droppedInterval).toBe(0);
    expect(summary?.droppedLimit).toBe(0);
  });

  it('reports a whole-window no-change topic as stable evidence, not as silence', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.15,
      changeOnly: true,
      format: 'summary',
    });
    stub.emitSeries(
      '/MyTable/Gyro',
      'double',
      Array.from({ length: 20 }, () => 1.5)
    );
    const result = await pending;

    const summary = result.summaries?.[0];
    expect(summary?.count).toBe(1);
    expect(summary?.received).toBe(20);
    expect(summary?.droppedDuplicate).toBe(19);
    expect(summary?.first).toBe(1.5);
    expect(summary?.last).toBe(1.5);
    expect(result.connection.stayedConnected).toBe(true);
    expect(result.complete).toBe(true);
    expect(result.warnings).toEqual([]);
  });

  it('distinguishes interval thinning from truncation', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.15,
      sampleIntervalMs: 10_000,
      format: 'summary',
    });
    stub.emitSeries('/MyTable/Gyro', 'double', [1, 2, 3, 4, 5]);
    const result = await pending;

    const summary = result.summaries?.[0];
    expect(summary?.count).toBe(1);
    expect(summary?.received).toBe(5);
    expect(summary?.droppedInterval).toBe(4);
    expect(summary?.droppedLimit).toBe(0);
    expect(summary?.truncated).toBe(false);
    expect(result.truncated).toBe(false);
    expect(result.complete).toBe(true);
  });

  it('accounts for every delivered update exactly once across dedupe and cap', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.15,
      changeOnly: true,
      format: 'summary',
    });
    // 260 updates: 10 identical leading values, then 250 distinct ones that cross the 200-point cap.
    stub.emitSeries(
      '/MyTable/Gyro',
      'double',
      Array.from({ length: 260 }, (_, i) => (i < 10 ? 1 : i))
    );
    const result = await pending;

    const summary = result.summaries?.[0];
    expect(summary).toBeDefined();
    const s = summary!;
    expect(s.received).toBe(260);
    expect(s.count + s.droppedDuplicate + s.droppedInterval + s.droppedLimit).toBe(s.received);
    expect(s.droppedDuplicate).toBe(9);
    expect(s.droppedInterval).toBe(0);
    expect(s.droppedLimit).toBeGreaterThan(0);
    expect(s.count).toBe(200);
    expect(s.truncated).toBe(true);
  });

  it('marks truncation when a topic exceeds MAX_SAMPLES_PER_TOPIC', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.15,
      format: 'summary',
    });
    stub.emitSeries(
      '/MyTable/Gyro',
      'double',
      Array.from({ length: 205 }, (_, i) => i)
    );
    const result = await pending;

    const summary = result.summaries?.[0];
    expect(summary?.count).toBe(200);
    expect(summary?.received).toBe(205);
    expect(summary?.droppedLimit).toBe(5);
    expect(summary?.droppedDuplicate).toBe(0);
    expect(summary?.droppedInterval).toBe(0);
    expect(summary?.truncated).toBe(true);
    expect(result.truncated).toBe(true);
    expect(result.complete).toBe(false);
    expect(result.warnings.join(' ')).toMatch(/sample cap/i);
  });

  it('keeps samples across a mid-window disconnect and resumes the same bucket', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.3,
      format: 'summary',
    });

    stub.emit('/MyTable/Gyro', 'double', 0.1);
    stub.emit('/MyTable/Gyro', 'double', 0.2);

    setTimeout(() => stub.setConnected(false), 50);
    setTimeout(() => {
      stub.setConnected(true);
      stub.emit('/MyTable/Gyro', 'double', 0.3);
      stub.emit('/MyTable/Gyro', 'double', 0.4);
    }, 150);

    const result = await pending;

    const summary = result.summaries?.find((s) => s.topic === '/MyTable/Gyro');
    expect(summary?.count).toBe(4);
    expect(summary?.received).toBe(4);
    expect(summary?.first).toBe(0.1);
    expect(summary?.last).toBe(0.4);

    expect(result.connection).toMatchObject({
      observable: true,
      connectedAtStart: true,
      connectedAtEnd: true,
      stayedConnected: false,
      disconnects: 1,
      reconnects: 1,
    });
    expect(result.connection.disconnectedMs).toBeGreaterThanOrEqual(50);
    expect(result.connection.firstDisconnectAtMs).not.toBeNull();
    expect(result.complete).toBe(false);
    expect(result.warnings.join(' ')).toMatch(/dropped 1 time/i);
    expect(result.warnings.join(' ')).toMatch(/kept/i);
  });

  it('detects a disconnect by polling isRobotConnected when no listener exists', async () => {
    const stub = new StubNt({ withConnectionListener: false });
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.6,
      format: 'summary',
    });

    stub.emit('/MyTable/Gyro', 'double', 1);
    setTimeout(() => stub.setConnected(false), 100);
    setTimeout(() => stub.setConnected(true), 400);

    const result = await pending;

    expect(result.connection.disconnects).toBe(1);
    expect(result.connection.reconnects).toBe(1);
    expect(result.connection.stayedConnected).toBe(false);
    expect(result.connection.disconnectedMs).toBeGreaterThan(0);
    expect(result.summaries?.[0]?.count).toBe(1);
  });

  it('reports an unobservable connection instead of claiming stability', async () => {
    const result = await collectSamples(bareStub(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.1,
      format: 'summary',
    });

    expect(result.connection.observable).toBe(false);
    expect(result.connection.connectedAtStart).toBeNull();
    expect(result.connection.connectedAtEnd).toBeNull();
    expect(result.connection.stayedConnected).toBeNull();
    expect(result.complete).toBe(false);
    expect(result.warnings.join(' ')).toMatch(/could not be observed/i);
  });

  it('flags a window that received nothing so silence is not read as stability', async () => {
    const stub = new StubNt();
    const result = await collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.1,
      format: 'summary',
    });

    expect(result.topicCount).toBe(0);
    expect(result.summaries).toEqual([]);
    expect(result.connection.stayedConnected).toBe(true);
    expect(result.complete).toBe(false);
    expect(result.warnings.join(' ')).toMatch(/no topic values were received/i);
  });

  it('keeps summary and samples return shapes and unsubscribes when the window closes', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.1,
      format: 'samples',
    });
    stub.emitSeries('/MyTable/Gyro', 'double', [1, 2]);
    const result = await pending;

    expect(Object.keys(result.samples ?? {})).toEqual(['/MyTable/Gyro']);
    expect(result.samples?.['/MyTable/Gyro']).toHaveLength(2);
    expect(result.samples?.['/MyTable/Gyro']?.[0]?.value).toBe(1);
    expect(result.summaries?.[0]?.topic).toBe('/MyTable/Gyro');
    expect(stub.activeHandlers).toBe(0);
  });

  it('counts undecodable struct payloads separately without failing the window', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/'],
      durationSeconds: 0.1,
      format: 'summary',
    });
    // No built-in descriptor exists for this type, so the bytes cannot be sampled.
    stub.emit('/MyTable/Unknown', 'struct:NotABuiltIn', new Uint8Array([1, 2, 3]));
    stub.emit('/MyTable/Gyro', 'double', 1);
    const result = await pending;

    expect(result.samplingErrors).toBe(1);
    expect(result.summaries?.map((s) => s.topic)).toEqual(['/MyTable/Gyro']);
    expect(result.complete).toBe(true);
    expect(result.warnings).toEqual([]);
  });

  it('records a failed prefix subscription without failing the whole window', async () => {
    const stub = new StubNt({ failingPrefixes: ['/Bad/'] });
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/MyTable/', '/Bad/'],
      durationSeconds: 0.1,
      format: 'summary',
    });
    stub.emit('/MyTable/Gyro', 'double', 1);
    const result = await pending;

    expect(result.prefixErrors).toHaveLength(1);
    expect(result.prefixErrors[0]?.prefix).toBe('/Bad/');
    expect(result.prefixErrors[0]?.error).toMatch(/cannot subscribe/);
    expect(result.summaries?.[0]?.topic).toBe('/MyTable/Gyro');
    expect(result.complete).toBe(false);
    expect(result.warnings.join(' ')).toMatch(/prefix subscription/i);
    expect(result.topicCount).toBe(1);
  });

  it('omits topics beyond MAX_SUBSCRIBE_TOPICS and reports the omission', async () => {
    const stub = new StubNt();
    const pending = collectSamples(stub.asNt(), {
      prefixes: ['/'],
      durationSeconds: 0.1,
      format: 'summary',
    });
    for (let i = 0; i < 105; i++) stub.emit(`/T${i}`, 'double', i);
    const result = await pending;

    expect(result.topicCount).toBe(100);
    expect(result.omittedTopics).toBe(5);
    expect(result.truncated).toBe(true);
    expect(result.complete).toBe(false);
    expect(result.warnings.join(' ')).toMatch(/topic cap/i);
  });
});
