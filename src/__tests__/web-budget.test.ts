import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from "vitest";
import { promises as fsp } from "fs";
import fs from "fs";
import os from "os";
import path from "path";
import https from "https";
import axios from "axios";

const dns = vi.hoisted(() => ({ lookup: vi.fn() }));
vi.mock("dns/promises", () => ({ lookup: dns.lookup }));

import { webSearch, webFetch } from "../tools/web.js";

/** Minimal typed view of the built-in SQLite API (Node 22.13+ / 24). */
type SqliteModule = {
  DatabaseSync: new (filename: string, options?: { readOnly?: boolean }) => {
    exec(sql: string): void;
    prepare(sql: string): { get(...values: Array<string | number>): unknown; run(...values: Array<string | number>): unknown };
    close(): void;
  };
};

function sqlite(): SqliteModule {
  return require("node:sqlite") as SqliteModule;
}

const PUBLIC_IP = "93.184.216.34";
const BASE = new Date("2026-05-10T10:00:00.000Z");
const TODAY = "2026-05-10";
const YESTERDAY = "2026-05-09";

let stateDir: string;
let savedEnv: Record<string, string | undefined>;

function envSnapshot(): Record<string, string | undefined> {
  return {
    WEB_STATE_DIR: process.env.WEB_STATE_DIR,
    WEB_DAILY_LIMIT: process.env.WEB_DAILY_LIMIT,
    DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY,
  };
}

function restoreEnv(snapshot: Record<string, string | undefined>): void {
  for (const [key, value] of Object.entries(snapshot)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
}

function shelf(...parts: string[]): string {
  return path.join(stateDir, ...parts);
}

const QUOTA_DDL = "CREATE TABLE IF NOT EXISTS quota (id INTEGER PRIMARY KEY CHECK(id=1), utc_date TEXT NOT NULL, used INTEGER NOT NULL CHECK(used>=0), next_request_at INTEGER NOT NULL)";

function seedQuota(state: { utc_date: string; used: number; next_request_at: number }): void {
  const { DatabaseSync } = sqlite();
  const filename = shelf("quota.sqlite");
  const db = new DatabaseSync(filename);
  try {
    db.exec(QUOTA_DDL);
    db.prepare("INSERT INTO quota(id,utc_date,used,next_request_at) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET utc_date=excluded.utc_date,used=excluded.used,next_request_at=excluded.next_request_at")
      .run(state.utc_date, state.used, state.next_request_at);
  } finally {
    db.close();
  }
  // Tests also run on POSIX runners, where the real quota contract is 0600/0700.
  fs.chmodSync(filename, 0o600);
}

function storedQuota(): { utc_date: string; used: number; next_request_at: number } {
  const { DatabaseSync } = sqlite();
  const db = new DatabaseSync(shelf("quota.sqlite"), { readOnly: true });
  try {
    return db.prepare("SELECT utc_date, used, next_request_at FROM quota WHERE id=1").get() as { utc_date: string; used: number; next_request_at: number };
  } finally {
    db.close();
  }
}

function axiosError(status?: number): Error {
  return Object.assign(new Error(status ? `Request failed with status code ${status}` : "timeout of 30000ms exceeded"), {
    isAxiosError: true,
    ...(status ? { response: { status } } : {}),
  });
}

interface FakePage {
  status: number;
  headers: Record<string, string>;
  body?: string;
}

/** Replaces https.request so web_fetch can be exercised without a network. */
function mockHttps(handler: (url: URL, options: Record<string, any>) => FakePage) {
  return vi.spyOn(https, "request").mockImplementation(((url: URL, options: Record<string, any>, callback: (res: any) => void) => {
    const response = handler(url, options);
    const responseListeners: Record<string, Array<(...args: any[]) => void>> = {};
    const requestListeners: Record<string, Array<(...args: any[]) => void>> = {};
    const emitResponse = (event: string, ...args: any[]) => (responseListeners[event] ?? []).forEach(fn => fn(...args));
    const emitRequest = (event: string, ...args: any[]) => (requestListeners[event] ?? []).forEach(fn => fn(...args));
    let settled = false;

    const incoming: any = {
      statusCode: response.status,
      headers: response.headers,
      on(event: string, fn: (...args: any[]) => void) { (responseListeners[event] ??= []).push(fn); return incoming; },
      destroy(error?: Error) {
        if (settled) return;
        settled = true;
        setImmediate(() => {
          if (error) emitResponse("error", error);
          emitRequest("close");
        });
      },
    };

    const request: any = {
      on(event: string, fn: (...args: any[]) => void) { (requestListeners[event] ??= []).push(fn); return request; },
      destroy(error?: Error) {
        if (settled) return;
        settled = true;
        setImmediate(() => {
          if (error) emitRequest("error", error);
          emitRequest("close");
        });
      },
      end() {
        setImmediate(() => {
          if (settled) return;
          callback(incoming);
          setImmediate(() => {
            if (settled) return;
            if (response.body !== undefined) emitResponse("data", Buffer.from(response.body));
            // A data handler may destroy the stream (size limit); do not end it then.
            if (settled) return;
            settled = true;
            emitResponse("end");
            emitRequest("close");
          });
        });
      },
    };
    return request;
  }) as any);
}

beforeAll(() => {
  process.env.DEEPSEEK_API_KEY = "test-deepseek-key";
});

beforeEach(() => {
  savedEnv = envSnapshot();
  stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "homelab-web-quota-"));
  if (process.platform !== "win32") fs.chmodSync(stateDir, 0o700);
  process.env.WEB_STATE_DIR = stateDir;
  process.env.WEB_DAILY_LIMIT = "3";
  process.env.DEEPSEEK_API_KEY = "test-deepseek-key";
  dns.lookup.mockReset();
  dns.lookup.mockImplementation(async () => [{ address: PUBLIC_IP, family: 4 }]);
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(BASE);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  restoreEnv(savedEnv);
  fs.rmSync(stateDir, { recursive: true, force: true });
});

function searchResult(): { data: unknown } {
  return { data: { results: [{ title: "Result", url: "https://example.com/page", snippet: "snippet" }] } };
}

describe("web_search budget settlement", () => {
  it("reports used, limit and remaining on success", async () => {
    vi.spyOn(axios, "post").mockResolvedValue(searchResult() as any);
    const payload = JSON.parse(await webSearch({ query: "public research topic", count: 5 }));
    expect(payload.budget).toEqual({ counted: true, used: 1, limit: 3, remaining: 2, utc_date: TODAY });
    expect(payload.results).toHaveLength(1);
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it("counts an upstream failure once, without retrying, and says so", async () => {
    const post = vi.spyOn(axios, "post").mockRejectedValue(axiosError(503) as any);
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toContain("DeepSeek search failed (HTTP 503)");
    expect(error.message).toContain("No automatic retry was made");
    expect(error.message).toContain("counts toward the shared daily public-web budget: 1 of 3 used, 2 remaining");
    expect(post).toHaveBeenCalledTimes(1);
    expect(storedQuota().used).toBe(1);
  });

  it("counts a timeout the same way", async () => {
    vi.spyOn(axios, "post").mockRejectedValue(axiosError() as any);
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("DeepSeek search failed or timed out");
    expect(error.message).toContain("1 of 3 used, 2 remaining");
    expect(storedQuota().used).toBe(1);
  });

  it("returns remaining quota with success after the upstream error", async () => {
    vi.spyOn(axios, "post").mockRejectedValueOnce(axiosError(500) as any).mockResolvedValueOnce(searchResult() as any);
    await webSearch({ query: "first topic", count: 5 }).catch(() => undefined);
    vi.setSystemTime(new Date(BASE.getTime() + 1_500));
    const payload = JSON.parse(await webSearch({ query: "second topic", count: 5 }));
    expect(payload.budget).toEqual({ counted: true, used: 2, limit: 3, remaining: 1, utc_date: TODAY });
  });
});

describe("pre-network rejections never consume quota", () => {
  it("rejects credential-like input before any network call, without counting", async () => {
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "lookup password=hunter2", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("Credential-like input is blocked");
    expect(error.message).toContain("does not count toward the shared daily public-web budget: 0 of 3 used, 3 remaining");
    expect(post).not.toHaveBeenCalled();
    expect(fs.existsSync(shelf("quota.sqlite"))).toBe(false);
  });

  it("rejects a private reference before any network call, without counting", async () => {
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "status of 10.0.0.5", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("Private or reserved IP addresses must not be sent");
    expect(error.message).toContain("does not count");
    expect(post).not.toHaveBeenCalled();
    expect(storedQuotaOrNull()).toBeNull();
  });

  it("rejects a query URL that resolves privately before the upstream call", async () => {
    dns.lookup.mockResolvedValue([{ address: "192.168.1.10", family: 4 }]);
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "summarize https://internal.example.com/status", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("URL resolves to a private or reserved network address");
    expect(error.message).toContain("0 of 3 used, 3 remaining");
    expect(post).not.toHaveBeenCalled();
    expect(storedQuotaOrNull()).toBeNull();
  });

  it("rejects a missing search credential without counting", async () => {
    delete process.env.DEEPSEEK_API_KEY;
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("dedicated DeepSeek search credential is not configured");
    expect(error.message).toContain("does not count");
    expect(post).not.toHaveBeenCalled();
    expect(storedQuotaOrNull()).toBeNull();
  });

  it("rejects an exhausted daily budget before the upstream call", async () => {
    seedQuota({ utc_date: TODAY, used: 3, next_request_at: 0 });
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("Daily public-web request budget exhausted");
    expect(error.message).toContain("does not count toward the shared daily public-web budget: 3 of 3 used, 0 remaining");
    expect(post).not.toHaveBeenCalled();
    expect(storedQuota().used).toBe(3);
  });

  it("rejects a repeat inside one second before the upstream call", async () => {
    seedQuota({ utc_date: TODAY, used: 1, next_request_at: BASE.getTime() + 900 });
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("rate limit reached. Wait at least one second");
    expect(error.message).toContain("1 of 3 used, 2 remaining");
    expect(post).not.toHaveBeenCalled();
    expect(storedQuota().used).toBe(1);
  });

  it("allows the next request once the one-second interval has passed", async () => {
    seedQuota({ utc_date: TODAY, used: 1, next_request_at: BASE.getTime() + 900 });
    vi.spyOn(axios, "post").mockResolvedValue(searchResult() as any);
    vi.setSystemTime(new Date(BASE.getTime() + 1_000));
    const payload = JSON.parse(await webSearch({ query: "public research topic", count: 5 }));
    expect(payload.budget).toMatchObject({ counted: true, used: 2, remaining: 1 });
  });

  it("rejects an overly permissive state directory before the upstream call", async () => {
    seedQuota({ utc_date: TODAY, used: 1, next_request_at: 0 });
    const realLstat = fsp.lstat.bind(fsp);
    vi.spyOn(fsp, "lstat").mockImplementation((async (target: fs.PathLike, options?: any) => {
      if (path.resolve(String(target)) === path.resolve(stateDir)) {
        return { isDirectory: () => true, isFile: () => false, isSymbolicLink: () => false, mode: 0o755, uid: 0 } as any;
      }
      return realLstat(target, options);
    }) as any);
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("Web quota state directory must be private");
    expect(error.message).toContain("does not count");
    expect(post).not.toHaveBeenCalled();
    expect(storedQuota().used).toBe(1);
  });

  it("rejects an overly permissive database file before the upstream call", async () => {
    seedQuota({ utc_date: TODAY, used: 1, next_request_at: 0 });
    const realLstat = fsp.lstat.bind(fsp);
    vi.spyOn(fsp, "lstat").mockImplementation((async (target: fs.PathLike, options?: any) => {
      if (String(target).endsWith("quota.sqlite")) {
        return { isDirectory: () => false, isFile: () => true, isSymbolicLink: () => false, mode: 0o644, uid: 0 } as any;
      }
      return realLstat(target, options);
    }) as any);
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("Invalid web quota state");
    expect(error.message).toContain("does not count");
    expect(post).not.toHaveBeenCalled();
    expect(storedQuota().used).toBe(1);
  });

  it("rejects a symlinked state directory before the upstream call", async () => {
    const realLstat = fsp.lstat.bind(fsp);
    vi.spyOn(fsp, "lstat").mockImplementation((async (target: fs.PathLike, options?: any) => {
      if (path.resolve(String(target)) === path.resolve(stateDir)) {
        return { isDirectory: () => false, isFile: () => false, isSymbolicLink: () => true, mode: 0o700, uid: 0 } as any;
      }
      return realLstat(target, options);
    }) as any);
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("must be private");
    expect(post).not.toHaveBeenCalled();
  });

  it("rejects a symlinked quota database before the upstream call", async () => {
    seedQuota({ utc_date: TODAY, used: 1, next_request_at: 0 });
    const realLstat = fsp.lstat.bind(fsp);
    vi.spyOn(fsp, "lstat").mockImplementation((async (target: fs.PathLike, options?: any) => {
      if (String(target).endsWith("quota.sqlite")) {
        return { isDirectory: () => false, isFile: () => false, isSymbolicLink: () => true, mode: 0o600, uid: 0 } as any;
      }
      return realLstat(target, options);
    }) as any);
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("Invalid web quota state");
    expect(error.message).toContain("does not count");
    expect(post).not.toHaveBeenCalled();
  });

  it("rejects a missing quota state directory before the upstream call", async () => {
    process.env.WEB_STATE_DIR = path.join(stateDir, "absent");
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("Web quota state directory does not exist");
    expect(error.message).toContain("does not count");
    expect(post).not.toHaveBeenCalled();
  });

  it("rejects an invalid WEB_DAILY_LIMIT before the upstream call", async () => {
    process.env.WEB_DAILY_LIMIT = "0";
    const post = vi.spyOn(axios, "post");
    const error: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(error.message).toContain("WEB_DAILY_LIMIT must be an integer between 1 and 1000");
    expect(error.message).toContain("does not count");
    expect(post).not.toHaveBeenCalled();
  });
});

describe("first request into an empty quota directory", () => {
  it("creates the private database and reports the first counted attempt", async () => {
    vi.spyOn(axios, "post").mockResolvedValue(searchResult() as any);
    expect(fs.existsSync(shelf("quota.sqlite"))).toBe(false);
    const payload = JSON.parse(await webSearch({ query: "public research topic", count: 5 }));
    expect(payload.budget).toEqual({ counted: true, used: 1, limit: 3, remaining: 2, utc_date: TODAY });
    expect(storedQuota()).toMatchObject({ utc_date: TODAY, used: 1 });
    if (process.platform !== "win32") {
      expect(fs.statSync(shelf("quota.sqlite")).mode & 0o077).toBe(0);
    }
  });
});

describe("UTC day rollover", () => {
  it("resets the daily allowance but keeps the last-request throttle", async () => {
    seedQuota({ utc_date: YESTERDAY, used: 3, next_request_at: BASE.getTime() + 800 });
    const post = vi.spyOn(axios, "post");
    const throttled: any = await webSearch({ query: "public research topic", count: 5 }).catch((e: unknown) => e);
    expect(throttled.message).toContain("rate limit reached");
    expect(throttled.message).toContain("0 of 3 used, 3 remaining");
    expect(post).not.toHaveBeenCalled();

    vi.setSystemTime(new Date(BASE.getTime() + 1_200));
    vi.spyOn(axios, "post").mockResolvedValue(searchResult() as any);
    const payload = JSON.parse(await webSearch({ query: "public research topic", count: 5 }));
    expect(payload.budget).toEqual({ counted: true, used: 1, limit: 3, remaining: 2, utc_date: TODAY });
    expect(storedQuota()).toMatchObject({ utc_date: TODAY, used: 1 });
  });

  it("does not carry yesterday's consumption into today", async () => {
    seedQuota({ utc_date: YESTERDAY, used: 3, next_request_at: 0 });
    vi.spyOn(axios, "post").mockResolvedValue(searchResult() as any);
    const payload = JSON.parse(await webSearch({ query: "public research topic", count: 5 }));
    expect(payload.budget).toMatchObject({ used: 1, remaining: 2, utc_date: TODAY });
  });
});

describe("web_fetch budget settlement and guards", () => {
  it("returns the settlement on success and keeps DNS pinning", async () => {
    const seen: Array<Record<string, any>> = [];
    mockHttps((url, options) => {
      seen.push(options);
      return { status: 200, headers: { "content-type": "text/html; charset=utf-8" }, body: "<html><head><title>T</title></head><body>Hello</body></html>" };
    });
    const payload = JSON.parse(await webFetch({ url: "https://example.com/page" }));
    expect(payload.budget).toEqual({ counted: true, used: 1, limit: 3, remaining: 2, utc_date: TODAY });
    expect(payload.content).toContain("Hello");
    expect(seen).toHaveLength(1);
    const lookup = seen[0].lookup as (hostname: string, options: Record<string, unknown>, cb: (...args: any[]) => void) => void;
    const pinned = await new Promise<any>(resolve => lookup("example.com", {}, (...args: any[]) => resolve(args)));
    expect([pinned[1], pinned[2]]).toEqual([PUBLIC_IP, 4]);
    expect(https.request).toHaveBeenCalledTimes(1);
  });

  it("rejects a private DNS answer before reserving quota", async () => {
    dns.lookup.mockResolvedValue([{ address: "10.0.0.5", family: 4 }]);
    const request = vi.spyOn(https, "request");
    const error: any = await webFetch({ url: "https://internal.example.com/" }).catch((e: unknown) => e);
    expect(error.message).toContain("URL resolves to a private or reserved network address");
    expect(error.message).toContain("does not count toward the shared daily public-web budget: 0 of 3 used, 3 remaining");
    expect(request).not.toHaveBeenCalled();
    expect(storedQuotaOrNull()).toBeNull();
  });

  it("rejects credential-bearing URLs without counting", async () => {
    const request = vi.spyOn(https, "request");
    const error: any = await webFetch({ url: "https://user:secret@example.com/" }).catch((e: unknown) => e);
    expect(error.message).toContain("Only public HTTPS pages on port 443 are allowed");
    expect(error.message).toContain("does not count");
    expect(request).not.toHaveBeenCalled();
  });

  it("checks each redirect hop and rejects a private redirect target", async () => {
    mockHttps((url): FakePage => (url.hostname === "example.com"
      ? { status: 302, headers: { location: "https://10.0.0.9/secret" } }
      : { status: 200, headers: { "content-type": "text/plain" }, body: "should not load" }));
    const error: any = await webFetch({ url: "https://example.com/start" }).catch((e: unknown) => e);
    expect(error.message).toContain("Only public HTTPS pages on port 443 are allowed");
    expect(error.message).toContain("counts toward the shared daily public-web budget: 1 of 3 used, 2 remaining");
    expect(https.request).toHaveBeenCalledTimes(1);
  });

  it("rejects a redirect chain longer than four hops", async () => {
    mockHttps(url => ({ status: 302, headers: { location: `https://example.com/hop${url.pathname.length}` } }));
    const error: any = await webFetch({ url: "https://example.com/start" }).catch((e: unknown) => e);
    expect(error.message).toContain("invalid redirect or exceeds four redirects");
    expect(error.message).toContain("counts toward");
    expect(https.request).toHaveBeenCalledTimes(5);
    // One pre-reservation lookup for the initial URL; redirect hops are resolved as they occur.
    expect(dns.lookup).toHaveBeenCalledTimes(5);
  });

  it("still rejects disallowed content types and counts the attempt", async () => {
    mockHttps(() => ({ status: 200, headers: { "content-type": "image/png" }, body: "binary" }));
    const error: any = await webFetch({ url: "https://example.com/image" }).catch((e: unknown) => e);
    expect(error.message).toContain("must return HTTP 200 and uncompressed HTML, plain text, Markdown or JSON");
    expect(error.message).toContain("counts toward the shared daily public-web budget: 1 of 3 used, 2 remaining");
  });

  it("still enforces the one-megabyte response limit and counts the attempt", async () => {
    mockHttps(() => ({ status: 200, headers: { "content-type": "text/plain" }, body: "x".repeat(1_000_001) }));
    const error: any = await webFetch({ url: "https://example.com/big" }).catch((e: unknown) => e);
    expect(error.message).toContain("exceeds the one-megabyte response limit");
    expect(error.message).toContain("counts toward");
    expect(storedQuota().used).toBe(1);
  });

  it("rejects a call inside one second before any DNS or HTTP work", async () => {
    seedQuota({ utc_date: TODAY, used: 1, next_request_at: BASE.getTime() + 900 });
    const request = vi.spyOn(https, "request");
    const error: any = await webFetch({ url: "https://example.com/page" }).catch((e: unknown) => e);
    expect(error.message).toContain("rate limit reached");
    expect(error.message).toContain("1 of 3 used, 2 remaining");
    expect(dns.lookup).not.toHaveBeenCalled();
    expect(request).not.toHaveBeenCalled();
    expect(storedQuota().used).toBe(1);
  });

  it("shares the daily budget with web_search", async () => {
    vi.spyOn(axios, "post").mockResolvedValue(searchResult() as any);
    mockHttps(() => ({ status: 200, headers: { "content-type": "text/plain" }, body: "page" }));
    const first = JSON.parse(await webSearch({ query: "public research topic", count: 5 }));
    expect(first.budget).toMatchObject({ used: 1, remaining: 2 });
    vi.setSystemTime(new Date(BASE.getTime() + 1_100));
    const second = JSON.parse(await webFetch({ url: "https://example.com/page" }));
    expect(second.budget).toEqual({ counted: true, used: 2, limit: 3, remaining: 1, utc_date: TODAY });
  });

  it("rejects an exhausted budget before DNS resolution", async () => {
    seedQuota({ utc_date: TODAY, used: 3, next_request_at: 0 });
    const error: any = await webFetch({ url: "https://example.com/page" }).catch((e: unknown) => e);
    expect(error.message).toContain("budget exhausted");
    expect(error.message).toContain("0 remaining");
    expect(dns.lookup).not.toHaveBeenCalled();
  });
});

function storedQuotaOrNull(): unknown {
  try { return storedQuota(); } catch { return null; }
}
