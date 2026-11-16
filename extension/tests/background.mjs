import assert from "node:assert/strict";
import fs from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import {fileURLToPath} from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = await fs.readFile(path.join(root, "background.js"), "utf8");
const parser = await fs.readFile(path.join(root, "collector.js"), "utf8");
let listener, report;
const local = {}, session = {};
const requests = [], downloads = [];
// Set while exercising the real one-shot flow, where the content script reports as it scans.
let autoReport = false;
const popupSender = {id: "extension-test", url: "chrome-extension://extension-test/popup.html"};
const contentSender = {id: "extension-test", tab: {id: 10, url: "https://www.reddit.com/r/python/"}, url: "https://www.reddit.com/r/python/"};
const post = {id: "abc123", title: "Automation", subreddit: "python", permalink: "/r/python/comments/abc123/title/", score: 20, media_urls: []};
const comment = id => ({comment_id: id, post_id: "abc123", subreddit: "python", body: "Automation note", score: 3});
const rules = {subreddits: "python", keywords: "automation", maxPosts: 2, maxComments: 0,
  includeComments: false, appUrl: "http://127.0.0.1:8000", bridgeToken: "test-token-at-least-24-characters", loginConfirmed: false};
const storage = data => ({get: async keys => Object.fromEntries((typeof keys === "string" ? [keys] : keys).map(key => [key, structuredClone(data[key])])),
  set: async input => Object.assign(data, structuredClone(input)), setAccessLevel: async () => {}});
const chrome = {
  storage: {local: storage(local), session: storage(session)},
  runtime: {id: "extension-test", getURL: suffix => `chrome-extension://extension-test/${suffix}`,
    onInstalled: {addListener() {}}, onMessage: {addListener(fn) { listener = fn; }}},
  permissions: {contains: async () => true}, downloads: {download: async options => { downloads.push(options); return 1; }},
  tabs: {query: async () => [{id: 10, url: contentSender.url}], onRemoved: {addListener() {}},
    sendMessage: async (id, message) => {
      if (message.type === "PAGE_INFO") return report;
      if (autoReport && ["START", "ONCE"].includes(message.type)) return messageTo({type: "REPORT", report}, contentSender);
      return {ok: true};
    }}
};
const context = vm.createContext({console, chrome, URL, TextEncoder, AbortSignal, setTimeout, clearTimeout,
  fetch: async (url, options) => {
    requests.push({url, options});
    return {ok: true, json: async () => url.endsWith("/status") ? {status: "ready"} : {posts_added: 1, comments_added: 0, duplicates: 0}};
  }});
context.importScripts = () => vm.runInContext(parser, context);
vm.runInContext(source, context);
function messageTo(message, sender = popupSender) {
  return new Promise(resolve => { listener(message, sender, resolve); });
}
let count = 0;
async function test(name, fn) { await fn(); count++; console.log(`PASS ${name}`); }
const resetData = () => { local.data = {schema_version: 1, posts: [], comments: []}; };
const start = (overrides = {}, once = false) => messageTo({type: once ? "ONCE" : "START", settings: {...rules, ...overrides}});
const sendReport = (posts, comments = [], extra = {}) => messageTo({type: "REPORT",
  report: {login: "logged_in", page_url: contentSender.url, posts, comments, ...extra}}, contentSender);
report = {login: "logged_in", page_url: contentSender.url, posts: [post], comments: []};
await test("start captures and persists data without worker deadlock", async () => {
  resetData();
  autoReport = true;
  const result = await messageTo({type: "START", settings: rules});
  autoReport = false;
  assert.equal(result.ok, true); assert.equal(result.posts, 1); assert.equal(result.capture.running, true);
  assert.ok(!JSON.stringify(session).includes(rules.bridgeToken), "Pairing token must not enter capture session rules");
});
await test("duplicate reports refresh content, keep one record and do not consume the quota", async () => {
  const result = await sendReport([{...post, title: "Automation updated"}]);
  assert.equal(local.data.posts.length, 1);
  assert.equal(local.data.posts[0].title, "Automation updated");
  assert.equal(session.capture.addedPosts, 1);
  assert.equal(result.running, true);
  await sendReport([post, post, post]);
  assert.equal(session.capture.addedPosts, 1, "Repeated copies of a stored post must not add up");
  assert.equal(session.capture.running, true);
});
await test("quota follows the written set, so a mixed report stops exactly at the limit", async () => {
  resetData();
  await start({maxPosts: 3});
  await sendReport([post, post, {...post, id: "two"}]); // 2 distinct written
  assert.equal(session.capture.addedPosts, 2);
  assert.equal(session.capture.running, true);
  const result = await sendReport([{...post, id: "two"}, {...post, id: "three"}]); // update + 1 new = 3
  assert.equal(session.capture.addedPosts, 3);
  assert.equal(result.running, false);
  assert.match(session.capture.message, /Post limit reached/);
});
await test("wrong tab cannot write data", async () => {
  const before = local.data.posts.length;
  await messageTo({type: "REPORT", report: {...report, posts: [{...post, id: "other"}]}}, {...contentSender, tab: {id: 99}});
  assert.equal(local.data.posts.length, before);
});
await test("content scripts cannot read pairing settings", async () => {
  await start({maxPosts: 10}); // Keep a running session so the content-script probe is meaningful.
  const result = await messageTo({type: "STATE"}, contentSender);
  assert.equal(result.ok, false); assert.ok(!JSON.stringify(result).includes(rules.bridgeToken));
  const active = await messageTo({type: "CAPTURE_RULES"}, contentSender);
  assert.equal(active.active, true); assert.ok(!JSON.stringify(active).includes(rules.bridgeToken));
  assert.ok(!JSON.stringify(session).includes(rules.bridgeToken), "Capture rules must never store the pairing token");
});
await test("new-post limit stops capture and names the post dimension", async () => {
  resetData();
  await start({maxPosts: 2, includeComments: false});
  await sendReport([post]);
  const result = await sendReport([{...post, id: "two"}]);
  assert.equal(local.data.posts.length, 2);
  assert.equal(session.capture.running, false);
  assert.match(result.message, /Post limit reached \(2 of 2 new posts\)/);
  assert.match(session.capture.message, /Post limit reached/);
  assert.equal(session.capture.addedPosts, 2);
});
await test("reports after a dimension is full no longer write", async () => {
  const before = JSON.stringify(local.data);
  const result = await sendReport([{...post, id: "later"}]);
  assert.equal(result.running, false);
  assert.equal(JSON.stringify(local.data), before);
});
await test("duplicate-only reports never trigger the post limit", async () => {
  resetData();
  await start({maxPosts: 1, includeComments: false});
  const result = await sendReport([post, post]);
  assert.equal(session.capture.addedPosts, 1);
  assert.equal(result.running, false);
  // One distinct record filled a capacity of one; repeating it afterwards must not add more.
  await sendReport([post]);
  assert.equal(session.capture.addedPosts, 1);
  assert.equal(local.data.posts.length, 1);
});
await test("comment limit stops capture even while posts are below their limit", async () => {
  resetData();
  await start({maxPosts: 10, maxComments: 2, includeComments: true});
  let result = await sendReport([post], [comment("c1")]);
  assert.equal(result.running, true);
  assert.equal(session.capture.addedComments, 1);
  result = await sendReport([{...post, id: "two"}], [comment("c2")]);
  assert.equal(result.running, false);
  assert.match(result.message, /Comment limit reached \(2 of 2 new comments\)/);
  assert.equal(local.data.comments.length, 2);
  assert.equal(local.data.posts.length, 2, "Posts below their own limit are still written in the stop report");
});
await test("post limit keeps comment capacity open until its own limit fills", async () => {
  resetData();
  await start({maxPosts: 1, maxComments: 2, includeComments: true});
  const result = await sendReport([post], [comment("c1")]);
  assert.equal(result.running, false);
  assert.match(result.message, /Post limit reached \(1 of 1 new posts\)/);
  assert.doesNotMatch(result.message, /Comment limit/);
});
await test("both dimensions reporting full names both and stops", async () => {
  resetData();
  await start({maxPosts: 1, maxComments: 1, includeComments: true});
  const result = await sendReport([post], [comment("c1")]);
  assert.equal(result.running, false);
  assert.match(result.message, /Post and comment limits reached/);
});
await test("comment capacity zero stores no comments and does not stop on the first page", async () => {
  resetData();
  await start({maxPosts: 5, maxComments: 0, includeComments: true});
  const result = await sendReport([post], [comment("c1")]);
  assert.equal(result.running, true);
  assert.equal(local.data.comments.length, 0);
  assert.equal(session.capture.addedComments, 0);
});
await test("one-shot capture keeps a limit notice instead of reporting a plain page capture", async () => {
  resetData();
  autoReport = true;
  const result = await start({maxPosts: 1, includeComments: false}, true);
  autoReport = false;
  assert.equal(result.capture.running, false);
  assert.match(result.capture.message, /Post limit reached/);
  assert.doesNotMatch(result.capture.message, /Current page captured/);
});
await test("starting a new session resets the settled counters", async () => {
  resetData();
  autoReport = true;
  await start({maxPosts: 5, includeComments: false});
  autoReport = false;
  await sendReport([{...post, id: "two"}]); // two writes in the previous session
  assert.equal(session.capture.addedPosts, 2);
  const result = await start({maxPosts: 2, includeComments: false});
  assert.equal(result.capture.addedPosts, 0);
  assert.equal(result.capture.addedComments, 0);
  assert.equal(result.capture.running, true);
});
await test("logged-out pages cannot start capture", async () => {
  report.login = "logged_out";
  const result = await messageTo({type: "START", settings: {...rules, loginConfirmed: true}});
  assert.equal(result.ok, false); assert.match(result.error, /Log in/);
  report.login = "logged_in";
});
await test("challenge pauses capture", async () => {
  await start({maxPosts: 10});
  await messageTo({type: "REPORT", report: {...report, login: "blocked"}}, contentSender);
  assert.equal(session.capture.running, false); assert.match(session.capture.message, /challenge/);
});
await test("pairing rejects a remote app destination", async () => {
  const result = await messageTo({type: "CONNECT", settings: {...rules, appUrl: "https://evil.test"}});
  assert.equal(result.ok, false); assert.equal(requests.length, 0);
});
await test("bridge request uses token, omits browser credentials and disallows redirects", async () => {
  const result = await messageTo({type: "CONNECT", settings: rules});
  assert.equal(result.ok, true);
  assert.equal(requests[0].options.credentials, "omit"); assert.equal(requests[0].options.redirect, "error");
  assert.equal(requests[0].options.headers.Authorization, `Bearer ${rules.bridgeToken}`);
});
await test("sync sends portable batches and leaves local data intact", async () => {
  const before = local.data.posts.length;
  const result = await messageTo({type: "SYNC", settings: rules});
  assert.equal(result.ok, true); assert.equal(local.data.posts.length, before);
  const payload = JSON.parse(requests.at(-1).options.body);
  assert.equal(payload.schema_version, 1); assert.equal(payload.posts.length, before);
  assert.ok(!JSON.stringify(payload).includes(rules.bridgeToken));
});
await test("standalone exports do not require app requests", async () => {
  const before = requests.length;
  const result = await messageTo({type: "EXPORT", format: "json"});
  assert.equal(result.ok, true); assert.equal(requests.length, before);
  assert.ok(downloads[0].url.startsWith("data:application/json"));
});
await test("clear stops capture and empties only local collection", async () => {
  const result = await messageTo({type: "CLEAR"});
  assert.equal(result.posts, 0); assert.equal(result.comments, 0);
  assert.equal(local.settings.bridgeToken, rules.bridgeToken);
});
await test("forget pairing removes saved and legacy session credentials", async () => {
  session.capture.rules.bridgeToken = rules.bridgeToken;
  const result = await messageTo({type: "FORGET_PAIRING"});
  assert.equal(result.ok, true);
  assert.equal(local.settings.bridgeToken, "");
  assert.ok(!JSON.stringify(session).includes(rules.bridgeToken));
});
console.log(`${count} background checks passed.`);
