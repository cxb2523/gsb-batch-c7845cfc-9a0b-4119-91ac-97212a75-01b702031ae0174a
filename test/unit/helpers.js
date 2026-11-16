'use strict';

// Offline-Hilfsfunktionen fuer die Komponententests des tvspielfilm-Adapters.
// Es findet keinerlei Netzwerkzugriff statt: HTTP wird durch einen Stub ersetzt.

const { RSS_OPTIONS } = require('../../lib/tvspielfilm');

// Einfacher Adapter-Stub mit In-Memory-Datenpunkten und stillen Loggern
function createFakeAdapter(initialStates) {
    const states = new Map(Object.entries(initialStates || {}));
    const setStateCalls = [];

    return {
        namespace: 'tvspielfilm.0',
        config: { blacklist: [], whitelist: [] },
        log: {
            debug() {},
            info() {},
            warn() {},
            error() {},
            silly() {}
        },
        states,
        setStateCalls,
        getState(id, callback) {
            const value = states.has(id) ? states.get(id) : null;
            setImmediate(() => callback(null, value));
        },
        setState(id, state, callback) {
            states.set(id, state);
            setStateCalls.push({ id, state });
            if (typeof callback === 'function') {
                setImmediate(callback);
            }
        }
    };
}

// request-Stub: je nach URL wird eine konfigurierte Antwort geliefert.
// Antworten koennen {error}, {statusCode, body} sein.
// Die Callback-Signatur folgt request: callback(error, response, body).
function createStubRequest(routes) {
    const calls = [];
    const stubRequest = function (url, callback) {
        calls.push(url);
        const route = routes[url];
        setImmediate(() => {
            if (!route) {
                callback(new Error('keine Stub-Antwort konfiguriert fuer ' + url));
                return;
            }
            if (route.error) {
                callback(route.error instanceof Error ? route.error : new Error(String(route.error)));
                return;
            }
            callback(null, { statusCode: route.statusCode, body: route.body }, route.body);
        });
    };
    stubRequest.calls = calls;
    return stubRequest;
}

function urlByFeedKey(key) {
    return RSS_OPTIONS[key].url;
}

// Einen RSS-Eintrag bauen; mit Sonderfaellen fuer fehlende Felder
function buildItem(options) {
    options = options || {};
    const parts = [];
    if (options.time !== null) parts.push(options.time || '20:15');
    if (options.station !== null) parts.push(options.station || 'RTL');
    if (options.show !== null) parts.push(options.show === undefined ? 'Tatort' : options.show);

    const item = {};
    if (options.title !== null && options.title !== undefined) {
        item.title = options.title;
    } else if (options.title !== null) {
        item.title = parts.join(' | ');
    }
    if ('description' in options) {
        if (options.description !== null) item.description = options.description;
    } else {
        item.description = 'Beschreibung zu ' + (options.show || 'Tatort');
    }
    if (options.link !== null) {
        item.link = options.link || 'http://www.tvspielfilm.de/sendung/1';
    }
    if (options.enclosure !== undefined) {
        item.enclosure = options.enclosure;
    } else {
        item.enclosure = { url: 'http://www.tvspielfilm.de/bild/1.jpg' };
    }
    return item;
}

function escapeXml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// Komplettes RSS-Dokument aus Eintragsobjekten erzeugen
function buildRss(items) {
    const itemXml = (items || []).map(item => {
        const lines = ['    <item>'];
        if ('title' in item) lines.push('      <title>' + escapeXml(item.title) + '</title>');
        if ('description' in item) lines.push('      <description>' + escapeXml(item.description) + '</description>');
        if ('link' in item) lines.push('      <link>' + escapeXml(item.link) + '</link>');
        if (item.enclosure) {
            lines.push('      <enclosure url="' + escapeXml(item.enclosure.url) + '" type="image/jpeg"/>');
        }
        lines.push('    </item>');
        return lines.join('\n');
    }).join('\n');

    return '<?xml version="1.0" encoding="UTF-8"?>\n' +
        '<rss version="2.0">\n' +
        '  <channel>\n' +
        '    <title>TV Spielfilm</title>\n' +
        itemXml + '\n' +
        '  </channel>\n' +
        '</rss>';
}

// Allen vier Feeds dieselbe Antwort geben
function routesForAll(response) {
    const routes = {};
    for (const key of Object.keys(RSS_OPTIONS)) {
        routes[RSS_OPTIONS[key].url] = response;
    }
    return routes;
}

function successfulResponse(items) {
    return { statusCode: 200, body: buildRss(items) };
}

module.exports = {
    createFakeAdapter,
    createStubRequest,
    urlByFeedKey,
    buildItem,
    buildRss,
    escapeXml,
    routesForAll,
    successfulResponse
};
