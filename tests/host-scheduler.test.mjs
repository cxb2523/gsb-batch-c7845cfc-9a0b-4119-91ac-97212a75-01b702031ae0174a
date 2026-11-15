import test from "node:test";
import assert from "node:assert/strict";
import dns from "node:dns/promises";
import http from "node:http";
import { syncBuiltinESMExports } from "node:module";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { fetchPublicResource } from "../server/crawler.mjs";
import { createFetchSession } from "../server/session.mjs";
import { createHostScheduler } from "../server/host-scheduler.mjs";

const PAGE = "<html><head><title>t</title></head><body>page</body></html>";
const ALLOW_ALL = "User-agent: *\nAllow: /";

// A local fake web: DNS resolves everything to a public address and each
// route (keyed "host/path") answers with a fixed or computed response. Every
// request's start time is recorded so tests can measure real gaps.
function mockWeb(t, routes, starts) {
  t.mock.method(dns, "lookup", async () => [
    { address: "93.184.216.34", family: 4 },
  ]);
  t.mock.method(http, "request", (url, _options, callback) => {
    const u = new URL(url);
    starts.push({ url: u.href, host: u.host, time: Date.now() });
    const request = new EventEmitter();
    request.end = () =>
      queueMicrotask(() => {
        let route = routes[u.host + u.pathname];
        if (typeof route === "function") route = route();
        route ??= { status: 404 };
        const response = new PassThrough();
        response.complete = true;
        response.statusCode = route.status;
        response.headers = route.headers ?? { "content-type": "text/html" };
        callback(response);
        response.end(
          response.statusCode >= 200 && response.statusCode < 300
            ? (route.body ?? PAGE)
            : "",
        );
      });
    return request;
  });
  syncBuiltinESMExports();
}

// Builds a run-like fetch stack: one scheduler shared by one session, with no
// politeness floor so only the behaviour under test adds delay.
function sessionFor(signal, scheduler, options = {}) {
  return createFetchSession({
    fetchPage: fetchPublicResource,
    signal,
    scheduler,
    politenessMs: 0,
    retryBaseMs: 50,
    ...options,
  });
}

test("two requests to one host start at least its Crawl-delay apart", async (t) => {
  const starts = [];
  mockWeb(
    t,
    {
      "a.example/robots.txt": {
        status: 200,
        headers: { "content-type": "text/plain" },
        body: "User-agent: *\nCrawl-delay: 0.6\nAllow: /",
      },
      "a.example/one": { status: 200 },
      "a.example/two": { status: 200 },
    },
    starts,
  );
  try {
    const controller = new AbortController();
    const scheduler = createHostScheduler({ signal: controller.signal });
    const get = sessionFor(controller.signal, scheduler);
    await Promise.all([
      get("http://a.example/one"),
      get("http://a.example/two"),
    ]);
    const pages = starts
      .filter((s) => ["/one", "/two"].includes(new URL(s.url).pathname))
      .sort((a, b) => a.time - b.time);
    assert.equal(pages.length, 2);
    assert.ok(
      pages[1].time - pages[0].time >= 550,
      `gap was ${pages[1].time - pages[0].time} ms, expected >= 550`,
    );
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});

test("different hosts never wait on each other's queues", async (t) => {
  const starts = [];
  const robots = {
    status: 200,
    headers: { "content-type": "text/plain" },
    body: "User-agent: *\nCrawl-delay: 0.8\nAllow: /",
  };
  mockWeb(
    t,
    {
      "a.example/robots.txt": robots,
      "a.example/one": { status: 200 },
      "a.example/two": { status: 200 },
      "b.example/robots.txt": robots,
      "b.example/one": { status: 200 },
    },
    starts,
  );
  try {
    const controller = new AbortController();
    const scheduler = createHostScheduler({ signal: controller.signal });
    const get = sessionFor(controller.signal, scheduler);
    await Promise.all([
      get("http://a.example/one"),
      get("http://a.example/two"),
      get("http://b.example/one"),
    ]);
    const at = (host, path) =>
      starts.find((s) => s.url === `http://${host}${path}`).time;
    const a1 = at("a.example", "/one"),
      a2 = at("a.example", "/two"),
      b1 = at("b.example", "/one");
    // The same-host second request waits out the Crawl-delay...
    assert.ok(a2 - a1 >= 750, `same-host gap was ${a2 - a1} ms`);
    // ...while the other host's first request goes out immediately.
    assert.ok(
      Math.abs(b1 - a1) < 400,
      `other host waited ${Math.abs(b1 - a1)} ms`,
    );
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});

test("a 429 with Retry-After pushes back the host's next request", async (t) => {
  const starts = [];
  let oneCalls = 0;
  mockWeb(
    t,
    {
      "a.example/robots.txt": {
        status: 200,
        headers: { "content-type": "text/plain" },
        body: ALLOW_ALL,
      },
      "a.example/one": () =>
        ++oneCalls === 1
          ? { status: 429, headers: { "retry-after": "1" } }
          : { status: 200 },
      "a.example/two": { status: 200 },
    },
    starts,
  );
  try {
    const controller = new AbortController();
    const scheduler = createHostScheduler({ signal: controller.signal });
    const get = sessionFor(controller.signal, scheduler);
    await Promise.all([
      get("http://a.example/one"),
      get("http://a.example/two"),
    ]);
    // The throttled request was retried and succeeded.
    assert.equal(oneCalls, 2);
    const first429 = starts.find((s) => s.url.endsWith("/one")).time;
    // /two never failed itself; only the host-wide Retry-After wait held it.
    const two = starts.find((s) => s.url.endsWith("/two")).time;
    assert.ok(
      two - first429 >= 950,
      `next request started ${two - first429} ms after the 429, expected >= 950`,
    );
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});

test("aborting the run releases requests still waiting in the queue", async (t) => {
  const starts = [];
  let oneCalls = 0;
  mockWeb(
    t,
    {
      "a.example/robots.txt": {
        status: 200,
        headers: { "content-type": "text/plain" },
        body: ALLOW_ALL,
      },
      // The first request is throttled for 30 s, so the queued second request
      // is still waiting on the host's Retry-After window when the run aborts.
      "a.example/one": () =>
        ++oneCalls === 1
          ? { status: 429, headers: { "retry-after": "30" } }
          : { status: 200 },
      "a.example/two": { status: 200 },
    },
    starts,
  );
  try {
    const controller = new AbortController();
    const scheduler = createHostScheduler({ signal: controller.signal });
    const get = sessionFor(controller.signal, scheduler);
    const one = get("http://a.example/one");
    const two = get("http://a.example/two");
    // Wait until the first request has been throttled and the host's queue
    // is holding the second request back, then abort the run.
    while (!starts.some((s) => s.url.endsWith("/one")))
      await new Promise((r) => setTimeout(r, 5));
    await new Promise((r) => setTimeout(r, 50));
    const abortedAt = Date.now();
    controller.abort();
    const [first, second] = await Promise.allSettled([one, two]);
    const elapsed = Date.now() - abortedAt;
    assert.ok(
      elapsed < 5000,
      `queued request was released after ${elapsed} ms, expected << 30 s`,
    );
    assert.equal(first.status, "rejected");
    assert.equal(second.status, "rejected");
    assert.match(String(second.reason), /abort/i);
    // The queued request never reached the host.
    assert.ok(!starts.some((s) => s.url.endsWith("/two")));
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});

test("a redirect to another host queues on the new host", async (t) => {
  const starts = [];
  mockWeb(
    t,
    {
      "a.example/jump": {
        status: 302,
        headers: { location: "http://b.example/target" },
      },
      "b.example/target": { status: 200 },
      "b.example/other": { status: 200 },
    },
    starts,
  );
  try {
    const controller = new AbortController();
    const scheduler = createHostScheduler({ signal: controller.signal });
    scheduler.setCrawlDelay("b.example", 0.6);
    const get = sessionFor(controller.signal, scheduler, {
      respectRobots: false,
    });
    const [redirected] = await Promise.all([
      get("http://a.example/jump"),
      get("http://b.example/other"),
    ]);
    assert.equal(redirected.url, "http://b.example/target");
    const target = starts.find((s) => s.url.endsWith("/target")).time;
    const other = starts.find((s) => s.url.endsWith("/other")).time;
    assert.ok(
      Math.abs(target - other) >= 550,
      `requests on the redirect target host were ${Math.abs(target - other)} ms apart, expected >= 550`,
    );
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});
