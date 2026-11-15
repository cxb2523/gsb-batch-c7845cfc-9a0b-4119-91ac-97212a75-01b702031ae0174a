// Waits that end early when the run is aborted; without a signal, a plain timer.
function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
// One scheduler per run. Requests to the same host (hostname plus port) run
// one at a time, with at least the host's gap between consecutive starts;
// different hosts never wait on each other. The gap is the run's politeness
// floor raised by the site's robots.txt Crawl-delay (capped at 10 s, 0 when
// undeclared), and a Retry-After answer pushes the host's next request out to
// the time the server asked for. Aborting the run releases every wait at
// once, and a failed request never jams its host's queue.
export function createHostScheduler({ signal, politenessMs = 0 } = {}) {
  const hosts = new Map();
  const entry = (host) => {
    let e = hosts.get(host);
    if (!e) {
      e = {
        tail: Promise.resolve(),
        lastStart: -Infinity,
        nextAt: 0,
        gapMs: 0,
      };
      hosts.set(host, e);
    }
    return e;
  };
  return {
    // The host's robots.txt Crawl-delay in seconds, capped at 10 s.
    setCrawlDelay(host, seconds) {
      entry(host).gapMs = Math.min(Math.max(seconds ?? 0, 0) * 1000, 10000);
    },
    // The host's next request waits at least delayMs, e.g. after a 429/503
    // with Retry-After. Only this host's queue is affected.
    defer(host, delayMs) {
      const e = entry(host);
      e.nextAt = Math.max(e.nextAt, Date.now() + Math.max(0, delayMs));
    },
    // Runs task once the host's previous task has finished and its wait
    // window has passed. taskSignal aborts this task's wait alone.
    schedule(host, task, taskSignal) {
      const e = entry(host);
      const waitSignal =
        signal && taskSignal
          ? AbortSignal.any([signal, taskSignal])
          : (signal ?? taskSignal);
      const result = e.tail.then(async () => {
        if (waitSignal?.aborted) throw waitSignal.reason;
        const waitMs = Math.max(
          e.nextAt - Date.now(),
          e.lastStart + Math.max(e.gapMs, politenessMs) - Date.now(),
        );
        if (waitMs > 0) await wait(waitMs, waitSignal);
        e.lastStart = Date.now();
        return task();
      });
      // A failed or aborted task releases the queue for the next one.
      e.tail = result.then(
        () => {},
        () => {},
      );
      return result;
    },
  };
}
