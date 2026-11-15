let _browserLastUrl = '';
let _browserCurrentUrl = '';

// Outcome of a scrape run is always written to this single VFS file. A new run
// (including a failed one) replaces the previous outcome entirely.
const BROWSER_SCRAPE_PATH = '/workspace/scrape_result.txt';
const BROWSER_SCRAPE_MAX_CHARS = 5000;
const BROWSER_SCRAPE_FETCH_TIMEOUT_MS = 3000;

// Synchronous lock: a second click while a scrape is still running must be
// rejected instead of racing the first run's VFS write.
let _browserScrapeInFlight = false;
let _browserScrapeRequestSeq = 0;

function _browserLog(msg, type = 'log') {
    const out = document.getElementById('browser-console-out');
    if (!out) return;
    const el = document.createElement('div');
    el.style.cssText = `padding:3px 6px;border-radius:4px;word-break:break-all;background:${type === 'error' ? 'rgba(255,74,74,0.1)' : type === 'info' ? 'rgba(0,179,255,0.08)' : 'rgba(255,255,255,0.03)'};color:${type === 'error' ? 'var(--err)' : type === 'info' ? 'var(--ac2)' : 'var(--txt)'};font-size:12px;font-family:var(--font-mono);border-left:2px solid ${type === 'error' ? 'var(--err)' : type === 'info' ? 'var(--ac2)' : 'rgba(255,255,255,0.15)'};`;
    el.textContent = msg;
    out.appendChild(el);
    out.scrollTop = out.scrollHeight;
}

function _browserHidePlaceholder() {
    const ph = document.getElementById('browser-frame-placeholder');
    if (ph) ph.style.display = 'none';
}

function browserGo() {
    const input = document.getElementById('browser-url');
    let val = (input ? input.value.trim() : '') || _browserLastUrl;
    if (!val) return;

    const frame = document.getElementById('browser-frame');
    if (!frame) return;

    if (val.startsWith('<') || val.startsWith('<!')) {
        const blob = new Blob([val], { type: 'text/html' });
        const blobUrl = URL.createObjectURL(blob);
        frame.src = blobUrl;
        _browserCurrentUrl = blobUrl;
        _browserHidePlaceholder();
        _browserLog(`[info] Loaded inline HTML (${val.length} chars)`, 'info');
    } else {
        if (!/^https?:\/\//i.test(val)) val = 'https://' + val;
        _browserLastUrl = val;
        _browserCurrentUrl = val;
        frame.src = val;
        _browserHidePlaceholder();
        _browserLog(`[info] Navigating to: ${val}`, 'info');
        if (input) input.value = val;
    }
}

function browserClear() {
    const frame = document.getElementById('browser-frame');
    if (frame) { frame.src = 'about:blank'; }
    const out = document.getElementById('browser-console-out');
    if (out) out.innerHTML = '';
    const ph = document.getElementById('browser-frame-placeholder');
    if (ph) ph.style.display = 'flex';
    _browserLastUrl = '';
    _browserCurrentUrl = '';
    const input = document.getElementById('browser-url');
    if (input) input.value = '';
    _browserLog('[info] Browser cleared.', 'info');
}

function browserRunJs() {
    const inp = document.getElementById('browser-js-in');
    const code = inp ? inp.value.trim() : '';
    if (!code) return;
    const frame = document.getElementById('browser-frame');
    if (!frame || !frame.contentWindow) { _browserLog('[error] No page loaded in iframe.', 'error'); return; }
    try {
        const result = frame.contentWindow.eval(code);
        const out = result !== undefined ? JSON.stringify(result) : '(undefined)';
        _browserLog(`> ${code}`, 'log');
        _browserLog(`← ${out}`, 'info');
        if (typeof lg === 'function') lg('SYS', `Browser JS exec: ${code} => ${out}`);
    } catch (e) {
        _browserLog(`> ${code}`, 'log');
        _browserLog(`[error] ${e.message}`, 'error');
        if (typeof lg === 'function') lg('ERR', `Browser JS exec error: ${e.message}`);
    }
}

function _browserScrapeErr(e) {
    if (!e) return 'unknown error';
    return e.name && e.message ? `${e.name}: ${e.message}` : String(e.message || e);
}

function _browserScrapeResolveUrl(frame) {
    let url = '';
    try { url = (frame.contentWindow && frame.contentWindow.location.href) || ''; } catch (e) { /* cross-origin: unreadable */ }
    if (!url || url === 'about:blank') {
        url = frame.getAttribute('src') || frame.src || _browserCurrentUrl || _browserLastUrl || '';
    }
    return url === 'about:blank' ? '' : url;
}

function _browserScrapeNormalize(text) {
    return String(text || '')
        .replace(/\r/g, '')
        .replace(/[ \t\f\v]+/g, ' ')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

function _browserScrapeClip(text) {
    const full = _browserScrapeNormalize(text);
    return {
        text: full.substring(0, BROWSER_SCRAPE_MAX_CHARS),
        total: full.length,
        truncated: full.length > BROWSER_SCRAPE_MAX_CHARS
    };
}

function _browserScrapeExtract(doc, fromParsedHtml) {
    if (!doc) throw new Error('iframe document is not available');
    const title = String(doc.title || '').trim();
    let text = '';
    if (doc.body) {
        if (fromParsedHtml) {
            // DOMParser never lays the document out, so innerText is empty and
            // textContent would include <script>/<style> payloads. Strip first.
            const clone = doc.body.cloneNode(true);
            Array.from(clone.querySelectorAll('script,style,noscript,template,svg')).forEach(n => n.remove());
            text = clone.textContent || '';
        } else {
            text = doc.body.innerText || doc.body.textContent || '';
        }
    }
    return { title, ..._browserScrapeClip(text) };
}

// Degraded path #1: execute a snippet *inside* the iframe and have it hand back
// title + visible text. Works when the frame is scriptable but a direct
// document read is unavailable; a cross-origin frame rejects this as well.
function _browserScrapeViaFrameScript(frame) {
    const win = frame.contentWindow;
    if (!win) throw new Error('iframe window is not available');
    const snippet = `(function () {
        var doc = document;
        var body = doc.body;
        var text = body ? (body.innerText || body.textContent || '') : '';
        return { title: String(doc.title || ''), text: String(text) };
    })()`;
    // eval() on the frame's window is the only way this code runs inside the
    // page. Never fall back to the parent realm: that would scrape KREASYS
    // itself and silently report the wrong page.
    const res = win.eval(snippet);
    if (!res || typeof res !== 'object') throw new Error('in-frame script returned no result');
    return {
        title: String(res.title || '').trim(),
        ..._browserScrapeClip(res.text)
    };
}

// Degraded path #2: fetch the page from this document and parse it locally.
// Only succeeds when the remote origin allows CORS. The wording recorded with
// the result calls out that this is the network copy, not the rendered frame,
// so the captured text is never mistaken for what the panel displays.
async function _browserScrapeViaFetch(url) {
    if (typeof fetch !== 'function') throw new Error('fetch is unavailable');
    if (!/^https?:\/\//i.test(url)) throw new Error('source is not an http(s) URL');
    if (typeof DOMParser !== 'function') throw new Error('DOMParser is unavailable');

    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), BROWSER_SCRAPE_FETCH_TIMEOUT_MS) : null;
    try {
        const res = await fetch(url, {
            mode: 'cors',
            credentials: 'omit',
            cache: 'no-store',
            signal: controller ? controller.signal : undefined
        });
        if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`.trim());
        const html = await res.text();
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const got = _browserScrapeExtract(doc, true);
        if (!got.title && !got.text) throw new Error('fetched document contains no readable content');
        return got;
    } catch (e) {
        if (e && e.name === 'AbortError') throw new Error(`request timed out after ${BROWSER_SCRAPE_FETCH_TIMEOUT_MS}ms`);
        throw e;
    } finally {
        if (timer) clearTimeout(timer);
    }
}

function _browserScrapeFormat(outcome, fields) {
    const lines = [
        '[SCRAPE RESULT]',
        `Outcome: ${outcome}`,
        `Source URL: ${fields.url || '(unknown)'}`,
        `Title: ${fields.title || '(unavailable)'}`,
        `Content Length: ${fields.total || 0}`,
        `Scraped At: ${fields.at}`
    ];
    if (fields.truncated) lines.push(`Stored: first ${BROWSER_SCRAPE_MAX_CHARS} of ${fields.total} characters`);
    if (fields.method) lines.push(`Method: ${fields.method}`);
    if (fields.note) lines.push(`Note: ${fields.note}`);
    if (outcome === 'failed') lines.push(`Failure Reason: ${fields.reason || 'unknown'}`);
    lines.push('');
    lines.push(fields.text || '(no page content could be captured)');
    return lines.join('\n');
}

function _browserScrapeWrite(content) {
    if (typeof st === 'undefined' || !st.vfs) return false;
    st.vfs[BROWSER_SCRAPE_PATH] = content;
    if (typeof svGlb === 'function') svGlb();
    if (typeof rVfs === 'function') rVfs();
    return true;
}

function _browserScrapeSetBusy(busy) {
    const btn = document.getElementById('browser-scrape-btn');
    if (!btn) return;
    btn.disabled = busy;
    btn.style.opacity = busy ? '0.55' : '';
    btn.style.cursor = busy ? 'wait' : '';
}

async function browserScrape() {
    if (_browserScrapeInFlight) {
        _browserLog('[scrape] rejected — a scrape is already in progress', 'error');
        if (typeof lg === 'function') lg('ERR', 'Browser: Scrape rejected — previous scrape still in progress.');
        if (typeof showToast === 'function') showToast('Scrape In Progress', 'Wait for the current scrape to finish.');
        return;
    }

    _browserScrapeInFlight = true;
    _browserScrapeRequestSeq++;
    const seq = _browserScrapeRequestSeq;
    const at = new Date().toISOString();
    _browserScrapeSetBusy(true);

    try {
        const frame = document.getElementById('browser-frame');
        if (!frame) {
            const url = _browserCurrentUrl || _browserLastUrl;
            const content = _browserScrapeFormat('failed', {
                url, title: '', text: '', at,
                reason: 'Browser iframe element (#browser-frame) is not present.'
            });
            _browserScrapeWrite(content);
            _browserLog('[scrape] failed — #browser-frame not found', 'error');
            if (typeof lg === 'function') lg('ERR', `Browser: Scrape failed — #browser-frame not found. Outcome written to ${BROWSER_SCRAPE_PATH}.`);
            return;
        }

        const url = _browserScrapeResolveUrl(frame);
        const isInlineHtml = /^blob:/i.test(url);
        let title = '';
        let text = '';
        let total = 0;
        let truncated = false;
        let outcome = 'success';
        let method = '';
        let reason = '';
        const notes = [];
        if (isInlineHtml) notes.push('Source is inline HTML pasted into the URL bar; its blob: address is not a network location.');

        // 1. Straightforward same-origin read.
        try {
            const doc = frame.contentDocument || frame.contentWindow.document;
            if (!doc) throw new Error('iframe has no document yet (page may still be loading)');
            const got = _browserScrapeExtract(doc, false);
            title = got.title;
            text = got.text;
            total = got.total;
            truncated = got.truncated;
            method = 'direct DOM read';
        } catch (directErr) {
            const directReason = _browserScrapeErr(directErr);

            // 2. In-frame script path — the intended cross-origin fallback.
            let inFrameReason = '';
            try {
                const got = _browserScrapeViaFrameScript(frame);
                title = got.title;
                text = got.text;
                total = got.total;
                truncated = got.truncated;
                outcome = 'degraded';
                method = 'in-frame script';
                notes.push(`Direct DOM read blocked (${directReason}); recovered by running a script inside the iframe.`);
            } catch (inFrameErr) {
                inFrameReason = _browserScrapeErr(inFrameErr);
            }

            // 3. Last resort: fetch + local parse (needs CORS on the target).
            if (outcome !== 'degraded') {
                try {
                    const got = await _browserScrapeViaFetch(url);
                    title = got.title;
                    text = got.text;
                    total = got.total;
                    truncated = got.truncated;
                    outcome = 'degraded';
                    method = 'CORS fetch + DOMParser';
                    notes.push(`Direct DOM read blocked (${directReason}); in-iframe script blocked (${inFrameReason}); recovered from the network copy of the page, not the rendered frame. Scripts and styles are excluded.`);
                } catch (fetchErr) {
                    outcome = 'failed';
                    reason = `Direct DOM read blocked (${directReason}); in-iframe script blocked (${inFrameReason}); network fetch fallback failed (${_browserScrapeErr(fetchErr)}).`;
                }
            }
        }

        // A read that yields nothing at all is not a success: report it as a
        // failure with the URL kept, so the file never claims a fresh scrape of
        // an empty frame.
        if (outcome !== 'failed' && !title && !text) {
            outcome = 'failed';
            reason = url
                ? 'The loaded page exposed no readable title or text (it may still be loading, or the frame is empty).'
                : 'No page is loaded in the iframe (nothing to scrape).';
        }

        const scraped = _browserScrapeFormat(outcome, { url, title, text, total, truncated, at, method, note: notes.join(' '), reason });
        const written = _browserScrapeWrite(scraped);
        if (!written) {
            _browserLog('[scrape] failed — VFS is unavailable, result not saved', 'error');
            if (typeof lg === 'function') lg('ERR', 'Browser: Scrape failed — VFS unavailable, result not saved.');
            return;
        }

        const dest = BROWSER_SCRAPE_PATH;
        const titleForLog = (title || '(no title)').replace(/\s+/g, ' ').substring(0, 80);
        if (outcome === 'success') {
            _browserLog(`[scrape] success — ${url || '(unknown)'} — "${titleForLog}" — ${total} chars — ${at}`, 'info');
            if (typeof lg === 'function') lg('SYS', `Browser: Scrape success — "${titleForLog}" (${total} chars) from ${url || '(unknown)'} → ${dest}`);
            if (typeof showToast === 'function') showToast('Page Scraped', dest);
        } else if (outcome === 'degraded') {
            _browserLog(`[scrape] degraded — ${url || '(unknown)'} — "${titleForLog}" — ${total} chars via ${method} — ${at}`, 'info');
            if (typeof lg === 'function') lg('SYS', `Browser: Scrape degraded — "${titleForLog}" (${total} chars) from ${url || '(unknown)'} via ${method} → ${dest}`);
            if (typeof showToast === 'function') showToast('Page Scraped (Degraded)', dest);
        } else {
            _browserLog(`[scrape] failed — ${url || '(unknown)'} — ${reason} — ${at}`, 'error');
            if (typeof lg === 'function') lg('ERR', `Browser: Scrape failed — ${url || '(unknown)'}: ${reason} Result written to ${dest}.`);
            if (typeof showToast === 'function') showToast('Scrape Failed', `${dest} — see console`);
        }

        if (typeof memAppend === 'function') {
            const tag = outcome === 'success' ? 'BROWSER SCRAPE' : outcome === 'degraded' ? 'BROWSER SCRAPE (DEGRADED)' : 'BROWSER SCRAPE FAILED';
            memAppend('SYS', `[${tag} #${seq}]\n${scraped.substring(0, 1000)}`);
        }
    } catch (e) {
        // Defensive: never let a scrape disappear without a terminal record.
        try {
            const content = _browserScrapeFormat('failed', {
                url: _browserCurrentUrl || _browserLastUrl, title: '', text: '', at,
                reason: `Unexpected scrape error: ${_browserScrapeErr(e)}`
            });
            _browserScrapeWrite(content);
            _browserLog(`[scrape] failed — unexpected error: ${_browserScrapeErr(e)} — ${at}`, 'error');
            if (typeof lg === 'function') lg('ERR', `Browser: Scrape failed — ${_browserScrapeErr(e)}. Result written to ${BROWSER_SCRAPE_PATH}.`);
        } catch (nested) {
            _browserLog(`[scrape] failed — ${_browserScrapeErr(e)} (result could not be written: ${_browserScrapeErr(nested)})`, 'error');
            if (typeof lg === 'function') lg('ERR', `Browser: Scrape failed — ${_browserScrapeErr(e)}; result could not be written either.`);
        }
    } finally {
        _browserScrapeInFlight = false;
        _browserScrapeSetBusy(false);
    }
}

async function browserScreenshot(outPath, tgChatId) {
    const frame = document.getElementById('browser-frame');
    if (!frame) { _browserLog('[error] iframe not found', 'error'); return; }
    if (typeof html2canvas === 'undefined') { _browserLog('[error] html2canvas not loaded', 'error'); return; }

    _browserLog('[info] Capturing screenshot…', 'info');
    if (typeof lg === 'function') lg('SYS', 'Browser: Taking screenshot of iframe…');

    try {
        const doc = frame.contentDocument || frame.contentWindow.document;
        const canvas = await html2canvas(doc.body, { useCORS: true, logging: false, scale: 2 });
        const dataUrl = canvas.toDataURL('image/jpeg', 0.9);
        const savePath = outPath || '/workspace/browser_screenshot.jpg';

        if (typeof st !== 'undefined') {
            st.vfs[savePath] = dataUrl;
            if (typeof svGlb === 'function') svGlb();
            if (typeof rVfs === 'function') rVfs();
            if (typeof showToast === 'function') showToast('Screenshot Saved', savePath);
        }
        _browserLog(`[info] Screenshot saved → ${savePath}`, 'info');
        if (typeof lg === 'function') lg('SYS', `Browser: Screenshot saved to ${savePath}`);

        if (tgChatId && typeof tgSendDoc === 'function') {
            await tgSendDoc(tgChatId, savePath);
            _browserLog(`[info] Screenshot dispatched to Telegram user ${tgChatId}`, 'info');
        }
    } catch (e) {
        _browserLog(`[error] Screenshot failed: ${e.message}`, 'error');
        if (typeof lg === 'function') lg('ERR', `Browser screenshot error: ${e.message}`);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const inp = document.getElementById('browser-url');
    if (inp) {
        inp.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); browserGo(); }
        });
    }
    const jsIn = document.getElementById('browser-js-in');
    if (jsIn) {
        jsIn.addEventListener('keydown', e => {
            if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); browserRunJs(); }
        });
    }
});

function browserOpenUrl(url) {
    const input = document.getElementById('browser-url');
    if (input) input.value = url;
    const tab = document.querySelector('.nav-tab[data-target="p-browser"]');
    if (tab) tab.click();
    setTimeout(() => browserGo(), 150);
}
