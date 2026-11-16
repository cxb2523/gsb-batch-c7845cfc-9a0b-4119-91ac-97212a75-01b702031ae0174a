import { describe, it, beforeEach, afterEach, mock } from 'node:test';
import assert from 'node:assert/strict';

import { createServer } from '../src/mcp.js';

const outlineFor = (host) => ({
	host,
	title: `Title ${host}`,
	description: 'A page.',
	words: 30,
	sections: [
		{ heading: 'Start', level: 1, words: 30, paragraphs: 2, codeBlocks: 0, links: ['x'], images: [], summary: 's' },
	],
});

function jsonResponse(status, body) {
	return {
		ok: status >= 200 && status < 300,
		status,
		json: async () => body,
	};
}

function installFetch(routes) {
	const originalFetch = globalThis.fetch;
	const calls = [];
	globalThis.fetch = mock.fn(async (endpointUrl) => {
		const requested = new URL(endpointUrl).searchParams.get('url');
		calls.push(requested);
		const route = routes[requested];
		if (route instanceof Error) throw route;
		return route;
	});
	return {
		calls,
		restore() {
			globalThis.fetch = originalFetch;
		},
	};
}

function siteShape() {
	return createServer()._registeredTools.site_shape;
}

describe('site_shape MCP tool', () => {
	let fixture;
	beforeEach(() => {});
	afterEach(() => fixture?.restore());

	it('keeps the single-address behaviour and response fields unchanged', async () => {
		fixture = installFetch({
			'a.com': jsonResponse(200, { outline: outlineFor('a.com'), cached: true, stale: false }),
		});
		const tool = siteShape();
		const result = await tool.handler({ url: 'a.com' });

		assert.equal(result.isError, undefined);
		const sc = result.structuredContent;
		assert.equal(sc.source, 'a.com');
		assert.equal(sc.cached, true);
		assert.equal(sc.host, 'a.com');
		assert.equal(sc.title, 'Title a.com');
		assert.deepEqual(sc.sections, [
			{
				heading: 'Start',
				level: 1,
				words: 30,
				paragraphs: 2,
				code_blocks: 0,
				links: 1,
				images: 0,
				summary: 's',
			},
		]);
		assert.ok(sc.walk_url.includes('url=a.com'));
		assert.ok(sc.glb_url.includes('format=glb'));
		assert.equal(JSON.parse(result.content[0].text).source, 'a.com');
	});

	it('returns isError for a single-page failure (the existing semantics)', async () => {
		fixture = installFetch({
			'bad.com': jsonResponse(429, { error: 'rate_limited', error_description: 'Slow down.' }),
		});
		const result = await siteShape().handler({ url: 'bad.com' });

		assert.equal(result.isError, true);
		assert.equal(result.structuredContent.error, 'rate_limited');
	});

	it('batch mode returns every page, with failures recorded per entry and isError unset', async () => {
		fixture = installFetch({
			'good.com': jsonResponse(200, { outline: outlineFor('good.com'), cached: false, stale: true }),
			'bad.com': jsonResponse(429, { error: 'rate_limited', error_description: 'Slow down.' }),
			'ugly.com': jsonResponse(500, null),
		});
		const result = await siteShape().handler({ urls: ['good.com', 'bad.com', 'ugly.com'] });

		assert.equal(result.isError, undefined, 'one bad page never makes the whole tool call an error');
		const sc = result.structuredContent;
		assert.equal(sc.mode, 'batch');
		assert.equal(sc.requested, 3);
		assert.equal(sc.succeeded, 1);
		assert.equal(sc.failed, 2);
		assert.equal(sc.results.length, 3);

		const [good, bad, ugly] = sc.results;
		assert.equal(good.source, 'good.com');
		assert.equal(good.host, 'good.com');
		assert.equal(good.cached, false);
		assert.equal(good.sections[0].heading, 'Start');
		assert.ok(good.walk_url.includes('url=good.com'));
		assert.equal('error' in good, false);

		assert.equal(bad.source, 'bad.com');
		assert.equal(bad.error, 'rate_limited');
		assert.equal(bad.error_description, 'Slow down.');
		assert.equal('sections' in bad, false, 'a failed page must not look like an empty outline');

		assert.equal(ugly.source, 'ugly.com');
		assert.equal(ugly.error, 'http_500');

		assert.deepEqual(fixture.calls, ['good.com', 'bad.com', 'ugly.com']);
	});

	it('batch mode with all failures still reports isError unset: the batch executed', async () => {
		fixture = installFetch({
			'a.com': jsonResponse(404, { error: 'not_found' }),
			'b.com': jsonResponse(500, { error: 'upstream_status' }),
		});
		const result = await siteShape().handler({ urls: ['a.com', 'b.com'] });

		assert.equal(result.isError, undefined);
		assert.equal(result.structuredContent.succeeded, 0);
		assert.equal(result.structuredContent.failed, 2);
		assert.deepEqual(
			result.structuredContent.results.map((entry) => entry.error),
			['not_found', 'upstream_status'],
		);
	});

	it('returns isError when neither url nor urls is given, or both are given', async () => {
		fixture = installFetch({});
		const tool = siteShape();

		const neither = await tool.handler({});
		assert.equal(neither.isError, true);
		assert.equal(neither.structuredContent.error, 'bad_arguments');

		const both = await tool.handler({ url: 'a.com', urls: ['b.com'] });
		assert.equal(both.isError, true);
		assert.equal(both.structuredContent.error, 'bad_arguments');
		assert.equal(fixture.calls.length, 0, 'an unrunnable call must not touch the network');
	});

	it('schema accepts either form but rejects empty url arrays and short strings', () => {
		const schema = siteShape().inputSchema;

		assert.equal(schema.safeParse({ url: 'a.com' }).success, true);
		assert.equal(schema.safeParse({ urls: ['a.com', 'b.com'] }).success, true);
		assert.equal(schema.safeParse({}).success, true, 'the handler, not the schema, enforces exactly-one');
		assert.equal(schema.safeParse({ urls: [] }).success, false);
		assert.equal(schema.safeParse({ url: 'aa' }).success, false);
		assert.equal(schema.safeParse({ urls: ['ok.com', 'x'] }).success, false);
	});

	it('still exposes the same tool name and single-address parameter', () => {
		const tools = createServer()._registeredTools;
		assert.ok(tools.walk_site);
		assert.ok(tools.site_shape);
		assert.equal(tools.site_shape.inputSchema.shape.url.isOptional(), true);
		assert.equal(tools.site_shape.inputSchema.shape.urls.isOptional(), true);
	});
});
