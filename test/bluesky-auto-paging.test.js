// Auto-pagination of the four cursor-paged Bluesky listings (Get
// Notifications, Search Posts, Search Accounts, List Drafts). The fake context
// answers each HTTP request from a scripted queue so the tests can assert every
// page request, the merged output, the auto_paging progress block and both
// failure modes.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { UploadPost } = require(path.join(__dirname, '..', 'dist/nodes/UploadPost/UploadPost.node.js'));

const API = 'https://api.upload-post.com/api';
const DEFAULT_MAX_PAGES = 10;

// params: node parameters; responder: (options, callIndex) => response body
const run = async (params, responder) => {
	const calls = [];
	const ctx = {
		getInputData: () => [{ json: {} }],
		getNodeParameter: (name, _itemIndex, fallback) => {
			if (Object.prototype.hasOwnProperty.call(params, name)) return params[name];
			if (fallback !== undefined) return fallback;
			throw new Error(`missing parameter ${name}`);
		},
		getNode: () => ({ id: 'node-1', name: 'Upload Post', type: 'uploadPost', typeVersion: 1 }),
		getExecutionId: () => 'exec-1',
		helpers: {
			httpRequestWithAuthentication: async (_credential, options) => {
				calls.push(options);
				return responder(options, calls.length - 1);
			},
			httpRequest: async (options) => {
				calls.push(options);
				return responder(options, calls.length - 1);
			},
		},
	};
	const [items] = await new UploadPost().execute.call(ctx);
	assert.equal(items.length, 1);
	return { calls, output: items[0].json };
};

const runRejecting = async (params, responder) => {
	const calls = [];
	const ctx = {
		getInputData: () => [{ json: {} }],
		getNodeParameter: (name, _itemIndex, fallback) => {
			if (Object.prototype.hasOwnProperty.call(params, name)) return params[name];
			if (fallback !== undefined) return fallback;
			throw new Error(`missing parameter ${name}`);
		},
		getNode: () => ({ id: 'node-1', name: 'Upload Post', type: 'uploadPost', typeVersion: 1 }),
		getExecutionId: () => 'exec-1',
		helpers: {
			httpRequestWithAuthentication: async (_credential, options) => {
				calls.push(options);
				return responder(options, calls.length - 1);
			},
			httpRequest: async (options) => {
				calls.push(options);
				return responder(options, calls.length - 1);
			},
		},
	};
	await assert.rejects(new UploadPost().execute.call(ctx));
	return calls;
};

const baseParams = (overrides) => ({
	resource: 'bluesky',
	operation: 'getBlueskyNotifications',
	networkProfile: 'acme',
	blueskyAutoPaginate: true,
	blueskyMaxPages: DEFAULT_MAX_PAGES,
	...overrides,
});

// AT-Protocol style pages: root-level cursor/has_more.
const atProtoPages = (key, cursors) => (options, index) => ({
	[key]: [{ id: `${key}-${index + 1}` }],
	cursor: cursors[index] ?? '',
	has_more: Boolean(cursors[index]),
});

test('auto paginate walks every page until the API stops returning a cursor', async () => {
	const { calls, output } = await run(
		baseParams({ blueskyPageSize: 25, blueskyCursor: 'start-cursor' }),
		atProtoPages('notifications', ['cursor-1', 'cursor-2', '']),
	);

	assert.equal(calls.length, 3);
	assert.equal(calls[0].url, `${API}/uploadposts/bluesky/notifications`);
	// The user's explicit cursor and page size are kept on the first request.
	assert.deepEqual(calls[0].qs, { user: 'acme', limit: 25, cursor: 'start-cursor' });
	assert.equal(calls[1].qs.cursor, 'cursor-1');
	assert.equal(calls[2].qs.cursor, 'cursor-2');
	// Page size and other filters stay untouched on later pages.
	assert.equal(calls[2].qs.limit, 25);

	assert.deepEqual(output.notifications, [
		{ id: 'notifications-1' },
		{ id: 'notifications-2' },
		{ id: 'notifications-3' },
	]);
	assert.deepEqual(output.auto_paging, {
		pages_fetched: 3,
		has_next_page: false,
		next_cursor: '',
		incomplete: false,
	});
});

test('auto paginate stops at Max Pages and hands back the continuation cursor', async () => {
	const { calls, output } = await run(
		baseParams({ blueskyMaxPages: 2 }),
		atProtoPages('notifications', ['c1', 'c2', 'c3']),
	);
	assert.equal(calls.length, 2);
	assert.deepEqual(output.notifications, [{ id: 'notifications-1' }, { id: 'notifications-2' }]);
	assert.deepEqual(output.auto_paging, {
		pages_fetched: 2,
		has_next_page: true,
		next_cursor: 'c2',
		incomplete: false,
	});
});

test('a single page without cursor reports no next page', async () => {
	const { calls, output } = await run(
		baseParams(),
		(_options, index) => ({ notifications: [{ id: `n-${index}` }] }),
	);
	assert.equal(calls.length, 1);
	assert.deepEqual(output.auto_paging, {
		pages_fetched: 1,
		has_next_page: false,
		next_cursor: '',
		incomplete: false,
	});
});

test('search posts, search accounts and drafts merge their own item arrays', async () => {
	const cases = [
		{
			params: baseParams({ operation: 'searchBlueskyPosts', blueskyQuery: 'n8n' }),
			url: `${API}/uploadposts/bluesky/search/posts`,
			key: 'posts',
			cursors: ['p1', ''],
		},
		{
			params: baseParams({ operation: 'searchBlueskyActors', blueskyQuery: 'bob' }),
			url: `${API}/uploadposts/bluesky/search/actors`,
			key: 'actors',
			cursors: ['a1', ''],
		},
		{
			params: baseParams({ operation: 'listBlueskyDrafts' }),
			url: `${API}/uploadposts/bluesky/drafts`,
			key: 'drafts',
			cursors: ['d1', ''],
		},
	];
	for (const item of cases) {
		const { calls, output } = await run(item.params, atProtoPages(item.key, item.cursors));
		assert.equal(calls[0].url, item.url);
		assert.equal(calls.length, 2);
		assert.equal(output[item.key].length, 2);
		assert.equal(output.auto_paging.pages_fetched, 2);
		assert.equal(output.auto_paging.has_next_page, false);
	}
});

test('first-page failure throws and produces no result item', async () => {
	const calls = await runRejecting(baseParams(), () => {
		throw new Error('401 - unauthorized');
	});
	assert.equal(calls.length, 1);
});

test('failure after at least one page keeps the fetched pages and flags incomplete', async () => {
	const { calls, output } = await run(
		baseParams({ blueskyMaxPages: 5 }),
		(_options, index) => {
			if (index === 2) throw new Error('500 - server error');
			return atProtoPages('notifications', ['c1', 'c2', 'c3'])(_options, index);
		},
	);
	assert.equal(calls.length, 3);
	assert.deepEqual(output.notifications, [{ id: 'notifications-1' }, { id: 'notifications-2' }]);
	assert.equal(output.auto_paging.incomplete, true);
	assert.equal(output.auto_paging.pages_fetched, 2);
	assert.equal(output.auto_paging.failed_page, 3);
	assert.match(output.auto_paging.error, /500 - server error/);
	assert.equal(output.auto_paging.has_next_page, true);
	assert.equal(output.auto_paging.next_cursor, 'c2');
});

test('a cursor identical to the request cursor is treated as a non-advancing failure', async () => {
	// Page 2 echoes the cursor page 1 returned: the listing cannot move forward.
	const responder = (_options, index) => {
		if (index === 0) return { notifications: [{ id: 'n-1' }], cursor: 'stuck', has_more: true };
		return { notifications: [{ id: 'n-2' }], cursor: 'stuck', has_more: true };
	};
	const { calls, output } = await run(baseParams(), responder);
	assert.equal(calls.length, 2);
	assert.deepEqual(output.notifications, [{ id: 'n-1' }]);
	assert.equal(output.auto_paging.incomplete, true);
	assert.equal(output.auto_paging.failed_page, 2);
	assert.match(output.auto_paging.error, /same cursor/);
});

test('a stall on the first page throws instead of returning an empty success', async () => {
	const calls = await runRejecting(
		baseParams({ blueskyCursor: 'same' }),
		() => ({ notifications: [], cursor: 'same', has_more: true }),
	);
	assert.equal(calls.length, 1);
});

test('non-positive Max Pages is refused before any request', async () => {
	for (const bad of [0, -3, 1.5]) {
		const calls = await runRejecting(baseParams({ blueskyMaxPages: bad }), () => {
			throw new Error('should never be called');
		});
		assert.equal(calls.length, 0, `value ${bad} must be rejected up front`);
	}
});

test('empty Max Pages falls back to the default cap instead of looping forever', async () => {
	const cursors = Array.from({ length: DEFAULT_MAX_PAGES + 2 }, (_, i) => `c${i}`);
	const params = baseParams();
	delete params.blueskyMaxPages;
	const { calls, output } = await run(params, atProtoPages('notifications', cursors));
	assert.equal(calls.length, DEFAULT_MAX_PAGES);
	assert.equal(output.auto_paging.pages_fetched, DEFAULT_MAX_PAGES);
	assert.equal(output.auto_paging.has_next_page, true);
	assert.equal(output.auto_paging.next_cursor, `c${DEFAULT_MAX_PAGES - 1}`);
});

test('an explicit has_more=false ends paging even when a cursor is present', async () => {
	const { calls, output } = await run(baseParams(), () => ({
		notifications: [{ id: 'only' }],
		cursor: 'ignored',
		has_more: false,
	}));
	assert.equal(calls.length, 1);
	assert.equal(output.auto_paging.has_next_page, false);
	assert.equal(output.auto_paging.next_cursor, '');
});

test('cursors nested in a pagination envelope are followed', async () => {
	const { calls, output } = await run(
		baseParams({ operation: 'searchBlueskyPosts', blueskyQuery: 'n8n' }),
		(_options, index) => {
			if (index === 0) {
				return { posts: [{ id: 'p1' }], pagination: { next_cursor: 'env-1', has_more: true } };
			}
			return { posts: [{ id: 'p2' }], pagination: { has_more: false } };
		},
	);
	assert.equal(calls.length, 2);
	assert.equal(calls[1].qs.cursor, 'env-1');
	assert.deepEqual(output.posts, [{ id: 'p1' }, { id: 'p2' }]);
	assert.equal(output.auto_paging.has_next_page, false);
});

test('with Auto Paginate off one page is requested and the response is untouched', async () => {
	const params = {
		resource: 'bluesky',
		operation: 'searchBlueskyPosts',
		networkProfile: 'acme',
		blueskyQuery: 'n8n',
		blueskyAutoPaginate: false,
	};
	const { calls, output } = await run(params, () => ({
		posts: [{ id: 'p1' }],
		cursor: 'untouched',
		has_more: true,
	}));
	assert.equal(calls.length, 1);
	assert.equal(calls[0].qs.cursor, undefined);
	// No auto_paging block: legacy single-page behavior, byte-for-byte response.
	assert.equal(output.auto_paging, undefined);
	assert.equal(output.cursor, 'untouched');
});

test('paging preserves the other top-level fields of the first page', async () => {
	const { output } = await run(
		baseParams(),
		(_options, index) => ({
			notifications: [{ id: `n-${index}` }],
			cursor: index === 0 ? 'c1' : '',
			has_more: index === 0,
			seen_count: 7,
			request_id: 'req-1',
		}),
	);
	assert.equal(output.seen_count, 7);
	assert.equal(output.request_id, 'req-1');
	assert.equal(output.notifications.length, 2);
});

test('the first page returning a cursor without one being sent is not a stall', async () => {
	// No starting Cursor: page 1 may legitimately return the first cursor, so it
	// must not be mistaken for a non-advancing page.
	const { calls, output } = await run(
		baseParams(),
		(_options, index) => {
			if (index === 0) return { notifications: [{ id: 'n-1' }], cursor: 'first', has_more: true };
			return { notifications: [{ id: 'n-2' }], cursor: '', has_more: false };
		},
	);
	assert.equal(calls.length, 2);
	assert.equal(calls[0].qs.cursor, undefined);
	assert.equal(calls[1].qs.cursor, 'first');
	assert.equal(output.auto_paging.incomplete, false);
	assert.equal(output.auto_paging.pages_fetched, 2);
});

test('a first-page stall with a user-supplied cursor throws and yields nothing', async () => {
	const calls = await runRejecting(
		baseParams({ blueskyCursor: 'user-cursor' }),
		() => ({ notifications: [{ id: 'n-1' }], cursor: 'user-cursor', has_more: true }),
	);
	assert.equal(calls.length, 1);
	assert.equal(calls[0].qs.cursor, 'user-cursor');
});

test('the same response cursor as the previous request fails page 3 but keeps pages 1-2', async () => {
	const responder = (_options, index) => {
		const cursors = ['c1', 'c2', 'c2', 'c4'];
		return {
			notifications: [{ id: `n-${index + 1}` }],
			cursor: cursors[index] ?? '',
			has_more: Boolean(cursors[index]),
		};
	};
	const { calls, output } = await run(baseParams(), responder);
	assert.equal(calls.length, 3);
	assert.deepEqual(output.notifications, [{ id: 'n-1' }, { id: 'n-2' }]);
	assert.equal(output.auto_paging.incomplete, true);
	assert.equal(output.auto_paging.failed_page, 3);
	assert.equal(output.auto_paging.pages_fetched, 2);
	assert.equal(output.auto_paging.next_cursor, 'c2');
});
