"use strict";
importScripts("collector.js");
const defaults = {subreddits: "", keywords: "", minScore: 0, maxPosts: 500, maxComments: 2000,
  includeComments: true, loginConfirmed: false, appUrl: "http://127.0.0.1:8000", bridgeToken: ""};
const empty = () => ({schema_version: 1, posts: [], comments: []});
let queue = Promise.resolve();
const serialize = operation => {
  const result = queue.then(operation);
  queue = result.catch(() => {});
  return result;
};
function appUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname) || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash) throw new Error("Use a local app address such as http://127.0.0.1:8000.");
  return url.origin;
}
function normalizedSettings(input) {
  return {...defaults, subreddits: String(input.subreddits || "").slice(0, 1000), keywords: String(input.keywords || "").slice(0, 1000),
    minScore: Math.max(0, Math.min(1e9, Number(input.minScore) || 0)),
    maxPosts: Math.max(1, Math.min(10000, Number(input.maxPosts) || 500)),
    maxComments: Math.max(0, Math.min(20000, Number(input.maxComments) || 0)),
    includeComments: Boolean(input.includeComments), loginConfirmed: Boolean(input.loginConfirmed),
    appUrl: appUrl(input.appUrl || defaults.appUrl), bridgeToken: String(input.bridgeToken || "").trim().slice(0, 256)};
}
async function stored() {
  const result = await chrome.storage.local.get(["settings", "data", "lastSync"]);
  return {settings: {...defaults, ...result.settings}, data: result.data || empty(), lastSync: result.lastSync || null};
}
async function session() { return (await chrome.storage.session.get("capture")).capture || null; }
// Ends a running capture, keeping the counters and the reason in the same session record so the
// popup, content script and later reports all observe the same state.
async function halt(capture, reason) {
  if (!capture) { await chrome.storage.session.set({capture: null}); return null; }
  capture.running = false;
  capture.message = reason;
  await chrome.storage.session.set({capture});
  if (capture.tabId) await chrome.tabs.sendMessage(capture.tabId, {type: "STOP"}).catch(() => {});
  return capture;
}
async function stop(reason = "Capture stopped. Your collection is saved locally.") {
  return halt(await session(), reason);
}
function loginAllowed(report, settings) {
  if (report.error) throw new Error(report.error);
  if (report.login === "blocked") throw new Error("Reddit is showing an access challenge. Capture stopped; resolve it in the browser.");
  if (report.login === "logged_out") throw new Error("Log in to Reddit in this browser, then try again.");
  if (report.login !== "logged_in" && !settings.loginConfirmed) {
    throw new Error("This page does not expose a login indicator. Confirm you are signed in, or open old.reddit.com while signed in.");
  }
}
// A dimension is full once this session wrote as many distinct records as the user allowed.
// Comment capacity 0 (with comments included) means "store no comments", not "stop immediately",
// so it only disables the comment dimension instead of ending the session on the first page.
function filledDimensions(capture) {
  const rules = capture.rules;
  return {
    posts: capture.addedPosts >= rules.maxPosts,
    comments: Boolean(rules.includeComments) && rules.maxComments > 0 && capture.addedComments >= rules.maxComments
  };
}
function limitNotice(capture, filled) {
  if (filled.posts && filled.comments) {
    return `Post and comment limits reached (${capture.addedPosts} new posts, ${capture.addedComments} new comments). Capture stopped; your collection is saved.`;
  }
  if (filled.posts) {
    return `Post limit reached (${capture.addedPosts} of ${capture.rules.maxPosts} new posts). Capture stopped; your collection is saved.`;
  }
  return `Comment limit reached (${capture.addedComments} of ${capture.rules.maxComments} new comments). Capture stopped; your collection is saved.`;
}
async function begin(input, once = false) {
  const settings = normalizedSettings(input);
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !BrowserCollector.allowedPage(tab.url)) throw new Error("Open a Reddit feed, public profile, or post in the active tab.");
  let report;
  try { report = await chrome.tabs.sendMessage(tab.id, {type: "PAGE_INFO"}); }
  catch { throw new Error("Reload the Reddit tab after installing the extension, then try again."); }
  loginAllowed(report, settings);
  await stop();
  await chrome.storage.local.set({settings});
  const {bridgeToken, appUrl: localAddress, ...captureRules} = settings;
  await chrome.storage.session.set({capture: {tabId: tab.id, running: true, rules: captureRules,
    addedPosts: 0, addedComments: 0, pageUrl: tab.url, message: "Collecting loaded content as you browse."}});
  await chrome.tabs.sendMessage(tab.id, {type: once ? "ONCE" : "START"});
  // A one-shot page might have hit a limit while reporting; keep that reason instead of overwriting it.
  if (once && (await session())?.running) await stop("Current page captured.");
  return state();
}
async function acceptReport(report, sender) {
  const capture = await session();
  if (!capture?.running || capture.tabId !== sender.tab?.id) return {ok: true, running: false};
  if (!BrowserCollector.allowedPage(sender.url || sender.tab.url) || !BrowserCollector.allowedPage(report.page_url)) {
    await stop("Open a supported Reddit page to continue.");
    return {ok: true, running: false};
  }
  try { loginAllowed(report, capture.rules); }
  catch (error) { await stop(error.message); return {ok: true, running: false}; }
  const {data} = await stored();
  const posts = new Map(data.posts.map(post => [post.id, post]));
  const comments = new Map(data.comments.map(comment => [comment.comment_id, comment]));
  // A record already in the collection is refreshed in place: it never consumes this session's
  // quota, so the quota always equals the set of records the session actually wrote.
  for (const record of report.posts.slice(0, 500)) {
    if (!BrowserCollector.matches(record, capture.rules)) continue;
    if (!posts.has(record.id)) {
      if (capture.addedPosts >= capture.rules.maxPosts || posts.size >= 10000) continue;
      capture.addedPosts++;
    }
    posts.set(record.id, record);
  }
  if (capture.rules.includeComments) {
    for (const record of report.comments.slice(0, 1000)) {
      if (!BrowserCollector.matches(record, capture.rules)) continue;
      if (!comments.has(record.comment_id)) {
        if (capture.addedComments >= capture.rules.maxComments || comments.size >= 20000) continue;
        capture.addedComments++;
      }
      comments.set(record.comment_id, record);
    }
  }
  const updated = {schema_version: 1, exported_at: new Date().toISOString(), posts: [...posts.values()], comments: [...comments.values()]};
  if (new TextEncoder().encode(JSON.stringify(updated)).length > 8 * 1024 * 1024) {
    await stop("Local storage is nearly full. Export or send your collection, then clear it.");
    return {ok: true, running: false};
  }
  await chrome.storage.local.set({data: updated});
  capture.pageUrl = report.page_url;
  capture.message = `Captured ${capture.addedPosts} new posts and ${capture.addedComments} new comments this session.`;
  const filled = filledDimensions(capture);
  if (filled.posts || filled.comments) {
    // Persist the settled counters together with the stop reason, then stop accepting reports.
    await halt(capture, limitNotice(capture, filled));
    return {ok: true, running: false, message: capture.message};
  }
  await chrome.storage.session.set({capture});
  return {ok: true, running: true, message: capture.message};
}
async function state() {
  const {settings, data, lastSync} = await stored();
  const capture = await session();
  return {ok: true, settings, posts: data.posts.length, comments: data.comments.length, capture, lastSync,
    preview: data.posts.slice(-4).reverse().map(post => ({title: post.title, subreddit: post.subreddit}))};
}
async function forgetPairing() {
  const {settings} = await stored();
  await chrome.storage.local.set({settings: {...settings, bridgeToken: ""}});
  const capture = await session();
  if (capture?.rules) {
    const {bridgeToken, appUrl: localAddress, ...rules} = capture.rules;
    await chrome.storage.session.set({capture: {...capture, rules}});
  }
  return {ok: true};
}
async function bridgeRequest(path, input, bundle) {
  const settings = normalizedSettings(input);
  if (settings.bridgeToken.length < 24) throw new Error("Paste the app's pairing token first (python main.py --bridge-token).");
  const origin = appUrl(settings.appUrl);
  const permitted = await chrome.permissions.contains({origins: [`http://${new URL(origin).hostname}/*`]});
  if (!permitted) throw new Error("Grant local app access using the Connect button.");
  await chrome.storage.local.set({settings});
  let response;
  try {
    response = await fetch(origin + path, {method: bundle ? "POST" : "GET", credentials: "omit", redirect: "error",
      headers: {Authorization: `Bearer ${settings.bridgeToken}`, "Content-Type": "application/json"},
      ...(bundle ? {body: JSON.stringify(bundle)} : {}), signal: AbortSignal.timeout(15000)});
  } catch { throw new Error("Cannot reach the local app. Start python main.py --api and check the address."); }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `App returned HTTP ${response.status}.`);
  return payload;
}
async function sync(input) {
  const {data} = await stored();
  if (!data.posts.length && !data.comments.length) throw new Error("Collect some posts or comments first.");
  const records = [...data.posts.map(record => ({post: record})), ...data.comments.map(record => ({comment: record}))];
  const totals = {posts_added: 0, comments_added: 0, duplicates: 0};
  for (let offset = 0; offset < records.length;) {
    const batch = empty();
    let bytes = 0;
    while (offset < records.length && batch.posts.length + batch.comments.length < 100) {
      const record = records[offset];
      const size = new TextEncoder().encode(JSON.stringify(record)).length;
      if (bytes + size > 1500000 && bytes) break;
      (record.post ? batch.posts : batch.comments).push(record.post || record.comment);
      bytes += size;
      offset++;
    }
    const result = await bridgeRequest("/bridge/import", input, batch);
    for (const key of Object.keys(totals)) totals[key] += result[key] || 0;
  }
  await chrome.storage.local.set({lastSync: {at: new Date().toISOString(), ...totals}});
  return {ok: true, ...totals};
}
async function download(format) {
  const {data} = await stored();
  let content, type, suffix;
  if (format === "json") { content = JSON.stringify({...data, exported_at: new Date().toISOString()}, null, 2); type = "application/json"; suffix = "json"; }
  else if (format === "posts") {
    content = BrowserCollector.csv(data.posts, ["id", "subreddit", "title", "author", "created_utc", "permalink", "url", "score", "num_comments", "selftext", "post_type", "media_urls"]);
    type = "text/csv"; suffix = "posts.csv";
  } else if (format === "comments") {
    content = BrowserCollector.csv(data.comments, ["comment_id", "post_id", "subreddit", "parent_id", "author", "body", "score", "created_utc", "depth", "post_permalink"]);
    type = "text/csv"; suffix = "comments.csv";
  } else throw new Error("Choose JSON, posts CSV, or comments CSV.");
  await chrome.downloads.download({url: `data:${type};charset=utf-8,${encodeURIComponent(content)}`,
    filename: `browser-collector-${new Date().toISOString().replace(/[:.]/g, "-")}.${suffix}`, saveAs: true});
  return {ok: true};
}
chrome.runtime.onInstalled.addListener(async () => {
  await chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
});
chrome.tabs.onRemoved.addListener(tabId => serialize(async () => {
  if ((await session())?.tabId === tabId) await stop("Reddit tab closed.");
}));
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  const trusted = sender.id === chrome.runtime.id && sender.url?.startsWith(chrome.runtime.getURL(""));
  if (message.type === "CAPTURE_RULES" && sender.tab) {
    session().then(capture => reply({active: capture?.running && capture.tabId === sender.tab.id}));
    return true;
  }
  if (message.type === "REPORT" && sender.tab && sender.id === chrome.runtime.id) {
    serialize(() => acceptReport(message.report, sender)).then(reply).catch(error => reply({ok: false, error: error.message}));
    return true;
  }
  if (!trusted) { reply({ok: false, error: "Unsupported extension message."}); return; }
  // Capture waits for a content report, so its start cannot sit in the report-write queue.
  const operation = message.type === "START" ? begin(message.settings) :
    message.type === "ONCE" ? begin(message.settings, true) :
    message.type === "STOP" ? stop().then(state) :
    message.type === "STATE" ? state() :
    message.type === "SAVE_SETTINGS" ? chrome.storage.local.set({settings: normalizedSettings(message.settings)}).then(() => ({ok: true})) :
    message.type === "FORGET_PAIRING" ? forgetPairing() :
    message.type === "CONNECT" ? bridgeRequest("/bridge/status", message.settings).then(result => ({ok: true, ...result})) :
    message.type === "SYNC" ? sync(message.settings) :
    message.type === "EXPORT" ? download(message.format) :
    message.type === "CLEAR" ? stop("Collection cleared.").then(() => serialize(() => chrome.storage.local.set({data: empty(), lastSync: null}))).then(state) :
    Promise.reject(new Error("Unknown extension action."));
  operation.then(reply).catch(error => reply({ok: false, error: error.message}));
  return true;
});
