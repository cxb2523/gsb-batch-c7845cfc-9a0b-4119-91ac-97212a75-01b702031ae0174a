import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@ntcore-ts/client', () => {
  class FakeTopic {
    value: unknown = null;
    lastChangedTime: number | null = null;
    private listeners: ((v: unknown) => void)[] = [];
    constructor(
      public name: string,
      public typeInfo: [number, string] = [2, 'double']
    ) {}
    getValue() {
      return this.value;
    }
    subscribe(cb: (v: unknown) => void) {
      this.listeners.push(cb);
      if (this.value != null) cb(this.value);
      return this.listeners.length;
    }
    unsubscribe() {
      return undefined;
    }
    publish() {
      return Promise.resolve(undefined);
    }
    setValue(v: unknown) {
      this.value = v;
      for (const l of this.listeners) l(v);
    }
  }

  class FakePrefix {
    private nextId = 1;
    private readonly listeners = new Map<
      number,
      (value: unknown, params: { name: string; type: string; id: number; properties: null }) => void
    >();

    constructor(public prefix: string) {
      prefixRegistry.push(this);
    }

    subscribe(
      cb: (value: unknown, params: { name: string; type: string; id: number; properties: null }) => void,
      options?: { immediateNotify?: boolean }
    ) {
      const id = this.nextId++;
      this.listeners.set(id, cb);
      if (options?.immediateNotify) {
        // announce a few topics immediately
        cb(1.5, { name: '/MyTable/Gyro', type: 'double', id: 1, properties: null });
        cb(
          { translation: { x: 1, y: 2 }, rotation: { value: 0 } },
          { name: '/MyTable/PoseStruct', type: 'struct:Pose2d', id: 2, properties: null }
        );
      }
      return id;
    }
    unsubscribe(id: number) {
      this.listeners.delete(id);
      return undefined;
    }
    deliver(name: string, type: string, value: unknown) {
      for (const cb of this.listeners.values()) cb(value, { name, type, id: 0, properties: null });
    }
    get listenerCount() {
      return this.listeners.size;
    }
  }

  const prefixRegistry: FakePrefix[] = [];
  const connectionListeners = new Set<(connected: boolean) => void>();
  let connectedState = true;

  const emitToPrefixes = (name: string, type: string, value: unknown) => {
    for (const prefix of prefixRegistry) {
      if (name.startsWith(prefix.prefix) || prefix.prefix === '/') prefix.deliver(name, type, value);
    }
  };

  const topics = new Map<string, FakeTopic>();

  const nt = {
    isRobotConnected: () => connectedState,
    isRobotConnecting: () => false,
    getRttMs: () => 12,
    getURI: () => 'localhost',
    getPort: () => 5810,
    addRobotConnectionListener: (cb: (c: boolean) => void, immediate?: boolean) => {
      connectionListeners.add(cb);
      if (immediate) cb(connectedState);
      return () => connectionListeners.delete(cb);
    },
    setConnected: (connected: boolean) => {
      connectedState = connected;
      for (const listener of connectionListeners) listener(connected);
    },
    emit: emitToPrefixes,
    close: () => undefined,
    getPrefixTopic: (prefix: string) => new FakePrefix(prefix),
    reset: () => {
      topics.clear();
      prefixRegistry.length = 0;
      connectionListeners.clear();
      connectedState = true;
    },
    getDoubleTopic: (name: string) => {
      let t = topics.get(name);
      if (!t) {
        t = new FakeTopic(name, [2, 'double']);
        t.value = 1.5;
        topics.set(name, t);
      }
      return t;
    },
    getStructTopic: (name: string) => {
      let t = topics.get(name);
      if (!t) {
        t = new FakeTopic(name, [5, 'struct:Pose2d']);
        t.value = { translation: { x: 1, y: 2 }, rotation: { value: 0 } };
        topics.set(name, t);
      }
      return t;
    },
    getBooleanTopic: (name: string) => new FakeTopic(name, [0, 'boolean']),
    getIntegerTopic: (name: string) => new FakeTopic(name, [3, 'int']),
    getFloatTopic: (name: string) => new FakeTopic(name, [4, 'float']),
    getStringTopic: (name: string) => new FakeTopic(name, [1, 'string']),
    getBooleanArrayTopic: (name: string) => new FakeTopic(name, [16, 'boolean[]']),
    getDoubleArrayTopic: (name: string) => new FakeTopic(name, [18, 'double[]']),
    getIntegerArrayTopic: (name: string) => new FakeTopic(name, [19, 'int[]']),
    getFloatArrayTopic: (name: string) => new FakeTopic(name, [20, 'float[]']),
    getStringArrayTopic: (name: string) => new FakeTopic(name, [17, 'string[]']),
    getJsonTopic: (name: string) => new FakeTopic(name, [21, 'json']),
    getRawTopic: (name: string) => new FakeTopic(name, [5, 'raw']),
    getProtobufTopic: (name: string) => new FakeTopic(name, [5, 'protobuf']),
  };

  return {
    NetworkTables: {
      getInstanceByURI: () => nt,
      getInstanceByTeam: () => nt,
    },
    Pose2d: { typeName: 'Pose2d', schema: {} },
    Pose3d: { typeName: 'Pose3d', schema: {} },
    Quaternion: { typeName: 'Quaternion', schema: {} },
    Rotation2d: { typeName: 'Rotation2d', schema: {} },
    Rotation3d: { typeName: 'Rotation3d', schema: {} },
    Transform2d: { typeName: 'Transform2d', schema: {} },
    Transform3d: { typeName: 'Transform3d', schema: {} },
    Translation2d: { typeName: 'Translation2d', schema: {} },
    Translation3d: { typeName: 'Translation3d', schema: {} },
    Twist2d: { typeName: 'Twist2d', schema: {} },
    Twist3d: { typeName: 'Twist3d', schema: {} },
    getBuiltInDescriptor: () => null,
    unpack: () => ({}),
    pack: () => new Uint8Array(),
    BUILT_IN_STRUCT_TYPE_NAMES: ['Pose2d', 'Translation2d'],
  };
});

import { NetworkTables } from '@ntcore-ts/client';

import { NtMcpService } from './service.js';

interface FakeNtControls {
  emit: (name: string, type: string, value: unknown) => void;
  setConnected: (connected: boolean) => void;
  reset: () => void;
}

/** The module mock returns the same fake client instance for every connect. */
const nt = NetworkTables.getInstanceByURI('localhost') as unknown as FakeNtControls;

describe('NtMcpService', () => {
  let service: NtMcpService;

  beforeEach(() => {
    nt.reset();
    service = new NtMcpService();
  });
  it('nt_guide includes workflow and write gate', () => {
    const r = service.guide();
    expect(r.structuredContent.callFirst).toBe('nt_guide');
    expect(r.structuredContent.writeGate).toMatchObject({ enabled: false });
  });

  it('connects and lists topics', async () => {
    const conn = await service.connect({ host: 'localhost', port: 5810 });
    expect(conn.isError).toBeUndefined();
    expect(conn.structuredContent.connected).toBe(true);

    const list = await service.listTopics('/MyTable/');
    expect(list.structuredContent.count).toBeGreaterThanOrEqual(2);
    const names = (list.structuredContent.topics as { name: string }[]).map((t) => t.name);
    expect(names).toContain('/MyTable/Gyro');
  });

  it('gets scalar and decoded struct', async () => {
    await service.connect({ host: 'localhost' });
    await service.listTopics('/MyTable/');

    const gyro = await service.get('/MyTable/Gyro');
    expect(gyro.structuredContent.value).toBe(1.5);

    const pose = await service.getDecoded('/MyTable/PoseStruct');
    expect(pose.structuredContent.value).toMatchObject({
      translation: { x: 1, y: 2 },
    });
  });

  it('refuses set when write gate off', async () => {
    await service.connect({ host: 'localhost' });
    await service.listTopics('/MyTable/');
    const r = await service.set('/MyTable/Gyro', 9, 'double');
    expect(r.isError).toBe(true);
    expect(String(r.structuredContent.error)).toMatch(/disabled/i);
  });

  it('allows set when write gate enabled', async () => {
    await service.connect({ host: 'localhost' });
    await service.listTopics('/MyTable/');
    service.setWriteMode(true, true);
    const r = await service.set('/MyTable/Gyro', 9, 'double');
    expect(r.isError).toBeUndefined();
    expect(r.structuredContent.published).toBe(true);
  });

  it('lists builtin struct types', () => {
    const r = service.listStructTypes();
    expect(r.structuredContent.builtin).toContain('Pose2d');
  });

  describe('nt_subscribe settlement', () => {
    it('returns a complete settlement for a clean window and keeps the old shape', async () => {
      await service.connect({ host: 'localhost' });
      await service.listTopics('/MyTable/');

      const pending = service.subscribe({
        prefixes: ['/MyTable/'],
        durationSeconds: 0.2,
        format: 'summary',
      });
      nt.emit('/MyTable/Gyro', 'double', 2.5);
      nt.emit('/MyTable/Gyro', 'double', 3.5);
      const r = await pending;

      expect(r.isError).toBeUndefined();
      const sc = r.structuredContent;
      expect(sc.connected).toBe(true);
      expect(sc.durationSeconds).toBeCloseTo(0.2);
      expect(sc.format).toBe('summary');
      expect(Array.isArray(sc.summaries)).toBe(true);
      expect(sc.samples).toBeUndefined();

      const summaries = sc.summaries as {
        topic: string;
        count: number;
        received: number;
        truncated: boolean;
        droppedDuplicate: number;
      }[];
      const gyro = summaries.find((s) => s.topic === '/MyTable/Gyro');
      expect(gyro).toBeDefined();
      expect(gyro!.count).toBeGreaterThanOrEqual(2);
      expect(gyro!.received).toBeGreaterThanOrEqual(2);
      expect(gyro!.truncated).toBe(false);
      expect(gyro!.droppedDuplicate).toBe(0);

      expect(sc.connection).toMatchObject({
        observable: true,
        stayedConnected: true,
        disconnects: 0,
        reconnects: 0,
      });
      expect(sc.complete).toBe(true);
      expect(sc.warnings).toEqual([]);
      expect(sc.truncated).toBe(false);
    });

    it('keeps pre-disconnect samples and reports the mid-window disconnect', async () => {
      await service.connect({ host: 'localhost' });
      await service.listTopics('/MyTable/');

      const pending = service.subscribe({
        prefixes: ['/MyTable/'],
        durationSeconds: 0.4,
        format: 'samples',
      });
      nt.emit('/MyTable/Gyro', 'double', 1);
      setTimeout(() => nt.setConnected(false), 50);
      setTimeout(() => {
        nt.setConnected(true);
        nt.emit('/MyTable/Gyro', 'double', 2);
      }, 200);

      const r = await pending;
      expect(r.isError).toBeUndefined();
      const sc = r.structuredContent;

      const samples = sc.samples as Record<string, { t: number; value: unknown }[]>;
      const gyro = samples['/MyTable/Gyro'];
      expect(gyro).toBeDefined();
      expect(gyro!.map((p) => p.value)).toContain(1);
      expect(gyro!.map((p) => p.value)).toContain(2);
      expect(gyro!.length).toBeGreaterThanOrEqual(2);

      expect(sc.connection).toMatchObject({ disconnects: 1, reconnects: 1, stayedConnected: false });
      expect((sc.connection as { disconnectedMs: number }).disconnectedMs).toBeGreaterThan(0);
      expect(sc.complete).toBe(false);
      expect((sc.warnings as string[]).join(' ')).toMatch(/dropped 1 time/i);
    });

    it('reports a no-change window as stable with duplicate counts, not as missing data', async () => {
      await service.connect({ host: 'localhost' });
      await service.listTopics('/MyTable/');

      const pending = service.subscribe({
        prefixes: ['/MyTable/'],
        durationSeconds: 0.2,
        changeOnly: true,
        format: 'summary',
      });
      for (let i = 0; i < 10; i++) nt.emit('/MyTable/Gyro', 'double', 1.5);
      const r = await pending;

      const summaries = r.structuredContent.summaries as {
        topic: string;
        count: number;
        received: number;
        droppedDuplicate: number;
        first: unknown;
        last: unknown;
      }[];
      const gyro = summaries.find((s) => s.topic === '/MyTable/Gyro');
      expect(gyro).toBeDefined();
      expect(gyro!.count).toBe(1);
      expect(gyro!.received).toBeGreaterThanOrEqual(10);
      expect(gyro!.droppedDuplicate).toBeGreaterThanOrEqual(9);
      expect(gyro!.first).toBe(1.5);
      expect(gyro!.last).toBe(1.5);
      expect(r.structuredContent.connection).toMatchObject({ stayedConnected: true, disconnects: 0 });
      expect(r.structuredContent.complete).toBe(true);
    });
  });
});
