import { describe, it, mock } from 'node:test';
import assert from 'node:assert/strict';

import { fetchOutlines, fetchOutline, PortalError, PORTAL_API } from '../src/index.js';

const outlineFor = (host) => ({
	host,
	title: `Title ${host}`,
	description: null,
	words: 42,
	sections: [
		{ heading: 'Intro', level: 1, words: 10, paragraphs: 1, codeBlocks: 0, links: [], images: [], summary: '' },
	],
});

/** Build a minimal Response-like object the client accepts. */
function jsonResponse(status, body) {
	return {
		ok: status >= 200 && status < 300,
		status,
		json: async () => body,
	};
}

/** Routes fetches by the `url` query parameter, so one mock serves a batch. */
function routingFetch(routes, { delay = 0 } = {}) {
	return mock.fn(async (endpoint) => {
		const requested = new URL(endpoint).searchParams.get('url');
		const route = routes[requested];
		if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
		if (route instanceof Error) throw route;
		return route;
	});
}

describe('fetchOutlines', () => {
	it('returns every successful page with outline, cached and stale, plus counts', async () => {
		const fetcher = routingFetch({
			'a.com': jsonResponse(200, { outline: outlineFor('a.com'), cached: true, stale: false }),
			'b.com': jsonResponse(200, { outline: outlineFor('b.com'), cached: false, stale: true }),
		});

		const batch = await fetchOutlines(['a.com', 'b.com'], { fetch: fetcher });

		assert.equal(fetcher.mock.callCount(), 2);
		assert.deepEqual(batch.succeeded, 2);
		assert.deepEqual(batch.failed, 0);
		assert.equal(batch.results.length, 2);
		assert.deepEqual(batch.results[0], {
			url: 'a.com',
			ok: true,
			outline: outlineFor('a.com'),
			cached: true,
			stale: false,
		});
		assert.deepEqual(batch.results[1], {
			url: 'b.com',
			ok: true,
			outline: outlineFor('b.com'),
			cached: false,
			stale: true,
		});
	});

	it('keeps successful pages when another page fails, and records the failure per address', async () => {
		const fetcher = routingFetch({
			'good.com': jsonResponse(200, { outline: outlineFor('good.com') }),
			'ratelimited.com': jsonResponse(429, { error: 'rate_limited', error_description: 'Slow down.' }),
			'broken.com': jsonResponse(200, { something: 'no outline here' }),
		});

		const batch = await fetchOutlines(['good.com', 'ratelimited.com', 'broken.com'], { fetch: fetcher });

		assert.equal(fetcher.mock.callCount(), 3);
		assert.deepEqual(batch.succeeded, 1);
		assert.deepEqual(batch.failed, 2);

		const [good, limited, broken] = batch.results;
		assert.equal(good.ok, true);
		assert.equal(good.url, 'good.com');
		assert.equal(good.outline.host, 'good.com');

		assert.equal(limited.ok, false);
		assert.equal(limited.url, 'ratelimited.com');
		assert.equal(limited.code, 'rate_limited');
		assert.equal(limited.message, 'Slow down.');
		assert.equal(limited.status, 429);
		assert.equal('outline' in limited, false, 'a failure must not masquerade as an empty structure');

		assert.equal(broken.ok, false);
		assert.equal(broken.url, 'broken.com');
		assert.equal(broken.code, 'http_200', 'missing upstream code falls back to http_<status>');
		assert.equal(broken.status, 200);
	});

	it('uses the upstream machine-readable code whenever the API provides one', async () => {
		const fetcher = routingFetch({
			'no.com': jsonResponse(403, { error: 'robots_disallowed', error_description: 'Forbidden by robots.txt.' }),
			'gone.com': jsonResponse(502, { error: 'upstream_status', error_description: 'Origin failed.' }),
		});

		const batch = await fetchOutlines(['no.com', 'gone.com'], { fetch: fetcher });

		assert.deepEqual(batch.succeeded, 0);
		assert.deepEqual(batch.failed, 2);
		assert.equal(batch.results[0].code, 'robots_disallowed');
		assert.equal(batch.results[0].message, 'Forbidden by robots.txt.');
		assert.equal(batch.results[1].code, 'upstream_status');
	});

	it('falls back to http_<status> when the error body has no code', async () => {
		const fetcher = routingFetch({
			'plain-500.com': jsonResponse(500, { error_description: 'oops' }),
			'empty-404.com': jsonResponse(404, null),
		});

		const batch = await fetchOutlines(['plain-500.com', 'empty-404.com'], { fetch: fetcher });

		assert.equal(batch.results[0].code, 'http_500');
		assert.equal(batch.results[0].status, 500);
		assert.equal(batch.results[1].code, 'http_404');
		assert.equal(batch.results[1].status, 404);
	});

	it('survives fetch itself rejecting, and keeps the other pages', async () => {
		const fetcher = routingFetch({
			'dead.com': Object.assign(new TypeError('fetch failed: ECONNRESET'), { name: 'TypeError' }),
			'alive.com': jsonResponse(200, { outline: outlineFor('alive.com') }),
		});

		const batch = await fetchOutlines(['dead.com', 'alive.com'], { fetch: fetcher });

		assert.equal(batch.results[0].ok, false);
		assert.equal(batch.results[0].code, 'unreachable');
		assert.equal(batch.results[0].status, 0);
		assert.equal(batch.results[1].ok, true);
		assert.deepEqual(batch.succeeded, 1);
		assert.deepEqual(batch.failed, 1);
	});

	it('requests every address independently, concurrently, and in input order', async () => {
		const started = [];
		const fetcher = mock.fn(async (endpointUrl) => {
			const requested = new URL(endpointUrl).searchParams.get('url');
			started.push(requested);
			await new Promise((resolve) => setTimeout(resolve, requested === 'slow.com' ? 20 : 1));
			return jsonResponse(200, { outline: outlineFor(requested) });
		});

		const batch = await fetchOutlines(['slow.com', 'fast.com'], { fetch: fetcher });

		assert.deepEqual(started, ['slow.com', 'fast.com'], 'all requests start up front, one per address');
		assert.deepEqual(
			batch.results.map((entry) => entry.url),
			['slow.com', 'fast.com'],
			'results follow input order even when responses finish out of order',
		);
	});

	it('passes the same endpoint, include=outline and signal opts through to each request', async () => {
		const fetcher = mock.fn(async () => jsonResponse(200, { outline: outlineFor('x.com') }));
		const controller = new AbortController();

		await fetchOutlines(['x.com', 'y.com'], {
			endpoint: 'https://self.host/api/portal',
			fetch: fetcher,
			signal: controller.signal,
		});

		const calls = fetcher.mock.calls;
		assert.equal(calls.length, 2);
		for (const call of calls) {
			assert.ok(call.arguments[0].startsWith('https://self.host/api/portal?'));
			assert.equal(new URL(call.arguments[0]).searchParams.get('include'), 'outline');
			assert.equal(call.arguments[1].signal, controller.signal);
			assert.deepEqual(call.arguments[1].headers, { accept: 'application/json' });
		}
	});

	it('throws on a non-array argument instead of silently succeeding', async () => {
		await assert.rejects(() => fetchOutlines('a.com', {}), (err) => {
			assert.ok(err instanceof PortalError);
			assert.equal(err.code, 'bad_urls');
			return true;
		});
		await assert.rejects(() => fetchOutlines(undefined), { code: 'bad_urls' });
		await assert.rejects(() => fetchOutlines(null), { code: 'bad_urls' });
		await assert.rejects(() => fetchOutlines({ 0: 'a.com', length: 1 }), { code: 'bad_urls' });
	});

	it('throws on an empty array instead of reporting a zero-failure success', async () => {
		await assert.rejects(() => fetchOutlines([]), (err) => {
			assert.ok(err instanceof PortalError);
			assert.equal(err.code, 'bad_urls');
			return true;
		});
	});

	it('throws no_fetch when no fetch implementation is available', async () => {
		const originalFetch = globalThis.fetch;
		delete globalThis.fetch;
		try {
			await assert.rejects(() => fetchOutlines(['a.com']), { code: 'no_fetch' });
		} finally {
			globalThis.fetch = originalFetch;
		}
	});

	it('rejects before sending anything when the signal is already aborted', async () => {
		const fetcher = mock.fn(async () => jsonResponse(200, { outline: outlineFor('a.com') }));
		const controller = new AbortController();
		controller.abort(new Error('cancelled up front'));

		await assert.rejects(
			() => fetchOutlines(['a.com', 'b.com'], { fetch: fetcher, signal: controller.signal }),
			(err) => {
				assert.ok(err instanceof PortalError);
				assert.equal(err.code, 'aborted');
				assert.equal(err.message, 'cancelled up front');
				return true;
			},
		);
		assert.equal(fetcher.mock.callCount(), 0, 'no request may be sent, and no address is booked as failed');
	});

	it('an abort mid-flight only fails the in-flight requests; pages already answered survive', async () => {
		let abort;
		const controller = new AbortController();
		const fetcher = mock.fn((endpointUrl) => {
			const requested = new URL(endpointUrl).searchParams.get('url');
			if (requested === 'fast.com') {
				return Promise.resolve(jsonResponse(200, { outline: outlineFor('fast.com') }));
			}
			return new Promise((_resolve, reject) => {
				abort = () =>
					reject(Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' }));
			});
		});

		const pending = fetchOutlines(['fast.com', 'slow.com'], { fetch: fetcher, signal: controller.signal });
		await new Promise((resolve) => setTimeout(resolve, 5));
		controller.abort();
		abort();
		const batch = await pending;

		assert.deepEqual(batch.succeeded, 1);
		assert.deepEqual(batch.failed, 1);
		assert.equal(batch.results[0].ok, true);
		assert.equal(batch.results[0].url, 'fast.com');
		assert.equal(batch.results[1].ok, false);
		assert.equal(batch.results[1].code, 'aborted');
		assert.equal(batch.results[1].url, 'slow.com');
	});

	it('leaves fetchOutline unchanged: it still throws a PortalError on failure', async () => {
		const fetcher = routingFetch({
			'bad.com': jsonResponse(429, { error: 'rate_limited', error_description: 'Slow down.' }),
			'good.com': jsonResponse(200, { outline: outlineFor('good.com'), cached: false, stale: false }),
		});

		await assert.rejects(() => fetchOutline('bad.com', { fetch: fetcher }), { code: 'rate_limited' });
		const good = await fetchOutline('good.com', { fetch: fetcher });
		assert.deepEqual(good, { outline: outlineFor('good.com'), cached: false, stale: false });
		assert.equal(new URL(fetcher.mock.calls[1].arguments[0]).origin, new URL(PORTAL_API).origin);
	});
});
