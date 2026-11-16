/* jshint -W097 */// jshint strict:false
/*jslint node: true */

'use strict';

// RSS-Kanaele
const RSS_OPTIONS = {
    jetzt: {
        feedname: 'Jetzt',
        url: 'http://www.tvspielfilm.de/tv-programm/rss/jetzt.xml',
        state: 'json.jetzt',
        stateRaw: 'json.raw.jetzt',
        stateStatus: 'status.jetzt',
        cssclass: 'tv_jetzt'
    },
    heute2015uhr: {
        feedname: 'heute 20:15 Uhr',
        url: 'http://www.tvspielfilm.de/tv-programm/rss/heute2015.xml',
        state: 'json.heute2015',
        stateRaw: 'json.raw.heute2015',
        stateStatus: 'status.heute2015',
        cssclass: 'tv_heute2015'
    },
    heute2200uhr: {
        feedname: 'heute 22:00 Uhr',
        url: 'http://www.tvspielfilm.de/tv-programm/rss/heute2200.xml',
        state: 'json.heute2200',
        stateRaw: 'json.raw.heute2200',
        stateStatus: 'status.heute2200',
        cssclass: 'tv_heute2200'
    },
    filme: {
        feedname: 'Spielfilm-Highlights des Tages',
        url: 'http://www.tvspielfilm.de/tv-programm/rss/filme.xml',
        state: 'json.filme',
        stateRaw: 'json.raw.filme',
        stateStatus: 'status.filme',
        cssclass: 'tv_filme'
    }
};

const FEED_KEYS = Object.keys(RSS_OPTIONS);

// Abrechnungsgruende je Feed
const REASON = {
    OK: 'ok',                       // erfolgreich eingelesen und geschrieben
    REQUEST_ERROR: 'request_error', // Anfrage selbst fehlgeschlagen (Netzwerk/Timeout/DNS)
    HTTP_ERROR: 'http_error',       // Antwort mit Statuscode ungleich 200
    PARSE_ERROR: 'parse_error',     // XML konnte nicht geparst werden
    STRUCTURE_ERROR: 'structure_error' // Feed-/Eintragsstruktur unbrauchbar
};

// CSS-Style fuer Tabellenzeile bei Suchtreffer
const STRING_FOUND_CSS = ' style="border: 2px solid yellow; background-color: rgba(150,0,0,0.9); background-color: darkred;"';

function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Wildcardpruefung Sender (thx to stackoverflow.com/a/32402438)
function checkWildcard(station, wildcard) {
    return new RegExp('^' + wildcard.split('*').map(escapeRegExp).join('.*') + '$').test(station);
}

// Sendezeit, Name und Sender aus Titel extrahieren: "14:00 | RTL | Formel 1"
function getShowDetails(title) {
    const parts = String(title).split(' | ');
    return {
        time: (parts[0] || '').trim(),
        station: (parts[1] || '').trim(),
        show: (parts[2] || '').trim()
    };
}

// CSV-Suchbegriffe in sauberes Array ueberfuehren (leere Eintraege fallen raus)
function parseSearchTerms(value) {
    if (value === undefined || value === null || typeof value !== 'string') {
        return [];
    }
    return value
        .split(',')
        .map(term => term.trim())
        .filter(Boolean);
}

// Suchmuster bauen; ohne Suchbegriffe gibt es kein Muster (= keine Treffer)
function buildSearchRegExp(terms) {
    if (!Array.isArray(terms) || terms.length === 0) {
        return null;
    }
    return new RegExp(terms.map(escapeRegExp).join('|'), 'gi');
}

function isNonEmptyString(value) {
    return typeof value === 'string' && value.trim().length > 0;
}

class TvProgramService {
    /**
     * @param {object} deps
     * @param {object} deps.adapter  ioBroker-Adapter (log/getState/setState)
     * @param {Function} deps.request request-kompatible Funktion (uri, callback)
     * @param {Function} deps.parseString xml2js parseString (data, options, callback)
     */
    constructor(deps) {
        if (!deps || !deps.adapter) {
            throw new Error('TvProgramService benoetigt einen Adapter');
        }
        this.adapter = deps.adapter;
        this.request = deps.request;
        this.parseString = deps.parseString;

        this.config = { blacklist: [], whitelist: [] };
        this.searchTerms = [];
        this.searchPattern = null;

        this.matches = 0;
        this.lastResults = {};
    }

    // Blacklist/Whitelist uebernehmen und gegen undefiniert absichern
    setConfig(config) {
        config = config || {};
        this.config = {
            blacklist: Array.isArray(config.blacklist) ? config.blacklist : [],
            whitelist: Array.isArray(config.whitelist) ? config.whitelist : []
        };
    }

    // Suchbegriffe aus dem Datenpunkt search.list uebernehmen
    setSearchTerms(value) {
        this.searchTerms = parseSearchTerms(value);
        this.searchPattern = buildSearchRegExp(this.searchTerms);
        return this.searchTerms;
    }

    // Steht der Sender in Whitelist/Blacklist? Titel: "16:50 | Sky Cinema | Kill the Boss 2"
    showStation(title) {
        const station = getShowDetails(title).station;
        let display = true;

        if (this.config.whitelist.length === 0) {
            display = this.config.blacklist.indexOf(station, 0) === -1;
        } else {
            for (const wl in this.config.whitelist) {
                display = checkWildcard(station, this.config.whitelist[wl]);
                if (display) break;
            }
            if (!display) {
                display = this.config.whitelist.indexOf(station, 0) !== -1;
            }
            this.adapter.log.debug(station + ' in Whitelist ?  ' + display);
        }
        return display;
    }

    // Regex ohne stateful lastIndex pruefen (jeder Test bekommt einen frischen RegExp)
    matchText(text) {
        if (!this.searchPattern || !isNonEmptyString(text)) {
            return false;
        }
        return new RegExp(this.searchPattern.source, 'i').test(text);
    }

    // Treffer im Text mit <mark> umschliessen
    markText(text) {
        if (!this.searchPattern || !isNonEmptyString(text)) {
            return text;
        }
        return String(text).replace(new RegExp(this.searchPattern.source, 'gi'), '<mark>$&</mark>');
    }

    setStateAsync(id, state) {
        return new Promise(resolve => {
            this.adapter.setState(id, state, () => resolve());
        });
    }

    getStateAsync(id) {
        return new Promise(resolve => {
            this.adapter.getState(id, (err, state) => resolve(err ? null : state));
        });
    }

    // Einen Feed per HTTP anfordern. Fehler werden niemals geworfen, sondern
    // als abgerechnetes Ergebnis zurueckgegeben.
    requestFeed(url) {
        return new Promise(resolve => {
            this.request(url, (error, response, body) => {
                if (error) {
                    resolve({
                        ok: false,
                        reason: REASON.REQUEST_ERROR,
                        httpStatus: null,
                        error: error.message || String(error)
                    });
                    return;
                }
                if (!response || response.statusCode !== 200) {
                    const statusCode = response ? response.statusCode : null;
                    resolve({
                        ok: false,
                        reason: REASON.HTTP_ERROR,
                        httpStatus: statusCode,
                        error: 'Unerwarteter HTTP-Statuscode: ' + (statusCode === null ? 'keine Antwort' : statusCode)
                    });
                    return;
                }
                resolve({ ok: true, httpStatus: 200, body: body });
            });
        });
    }

    parseXmlAsync(body) {
        return new Promise((resolve, reject) => {
            this.parseString(body, {
                explicitArray: false,
                mergeAttrs: true
            }, (err, result) => {
                if (err) {
                    reject(err);
                } else {
                    resolve(result);
                }
            });
        });
    }
    createBaseStatus(key) {
        const opts = RSS_OPTIONS[key];
        return {
            feed: key,
            feedname: opts.feedname,
            url: opts.url,
            success: false,
            reason: null,
            httpStatus: null,
            items: 0,
            itemsProcessed: 0,
            itemsSkipped: 0,
            itemsFiltered: 0,
            anomalies: [],
            error: null,
            timestamp: new Date().toISOString()
        };
    }

    // EINEN Feed einlesen und einzeln abrechnen.
    // Bei Misserfolg werden json.*/json.raw.* NICHT angeruehrt (alte Daten bleiben).
    async readFeed(key) {
        const opts = RSS_OPTIONS[key];
        const status = this.createBaseStatus(key);
        this.adapter.log.debug('RSS Feed wird eingelesen: ' + opts.url);

        // 1) Anfrage
        const fetched = await this.requestFeed(opts.url);
        if (!fetched.ok) {
            status.reason = fetched.reason;
            status.httpStatus = fetched.httpStatus;
            status.error = fetched.error;
            this.adapter.log.warn(
                'Feed "' + opts.feedname + '" abgebrochen (' + status.reason + '): ' + status.error +
                ' - bestehende Datenpunkte bleiben unveraendert'
            );
            return status;
        }
        status.httpStatus = fetched.httpStatus;

        // 2) XML-Parsing
        let result;
        try {
            result = await this.parseXmlAsync(fetched.body);
        } catch (err) {
            status.reason = REASON.PARSE_ERROR;
            status.error = err.message || String(err);
            this.adapter.log.warn(
                'Feed "' + opts.feedname + '" XML-Parsing fehlgeschlagen: ' + status.error +
                ' - bestehende Datenpunkte bleiben unveraendert'
            );
            return status;
        }

        // 3) Struktur pruefen
        let items = result && result.rss && result.rss.channel ? result.rss.channel.item : undefined;
        if (items && !Array.isArray(items)) {
            // Bei genau einem Eintrag liefert xml2js (explicitArray:false) ein einzelnes Objekt
            items = [items];
        }
        if (!Array.isArray(items) || items.length === 0) {
            status.reason = REASON.STRUCTURE_ERROR;
            status.error = 'Keine Eintraege (rss.channel.item) im Feed vorhanden';
            this.adapter.log.warn(
                'Feed "' + opts.feedname + '" enthaelt keine verwertbaren Eintraege' +
                ' - bestehende Datenpunkte bleiben unveraendert'
            );
            return status;
        }
        status.items = items.length;

        // 4) Eintraege verarbeiten
        const table = [];
        const rawData = [];

        for (let i = 0; i < items.length; i++) {
            const item = items[i];
            try {
                if (!item || typeof item !== 'object') {
                    throw Object.assign(new Error('Eintrag #' + i + ' ist kein gueltiges Objekt'), { anomalyReason: 'invalid_entry' });
                }
                if (!isNonEmptyString(item.title)) {
                    throw Object.assign(new Error('Eintrag #' + i + ' ohne Titel'), { anomalyReason: 'missing_title' });
                }
                if (!isNonEmptyString(item.description)) {
                    throw Object.assign(new Error('Eintrag #' + i + ' ohne Beschreibung'), { anomalyReason: 'missing_description' });
                }

                const details = getShowDetails(item.title);
                if (!details.time || !details.station || !details.show) {
                    throw Object.assign(new Error('Eintrag #' + i + ' mit unvollstaendigem Titelaufbau'), { anomalyReason: 'malformed_title' });
                }

                // Blacklist/Whitelist
                if (!this.showStation(item.title)) {
                    status.itemsFiltered++;
                    continue; // von den Einstellungen gefiltert, keine Anomalie
                }

                let titel = item.title;
                let beschreibung = item.description;
                let stringFound = '';

                // Suche nur bei vorhandenen Suchbegriffen; leere Suchliste = nie Treffer
                if (this.searchPattern) {
                    if (this.matchText(details.show)) {
                        stringFound = STRING_FOUND_CSS;
                        this.matches++;
                        this.adapter.log.debug('Suchtreffer im Titel: ' + details.show);
                    }
                    if (this.matchText(beschreibung)) {
                        stringFound = STRING_FOUND_CSS;
                        this.matches++;
                        this.adapter.log.debug('Suchtreffer in der Beschreibung: ' + details.show);
                    }
                    titel = this.markText(titel);
                    beschreibung = this.markText(beschreibung);
                }

                rawData.push({
                    title: details.show,
                    description: item.description,
                    station: details.station,
                    time: details.time,
                    imgUrl: item.enclosure && item.enclosure.url ? item.enclosure.url : ''
                });

                table.push({
                    image: item.enclosure ? '<img width="100%" src="' + item.enclosure.url + '" />' : '',
                    text: '<table' + stringFound + ' class="' + opts.cssclass + '"><tr><td class="' + opts.cssclass +
                        '_text" style="text-align: left; padding-left: 5px; font-weight: bold"><a href="' +
                        item.link + '" target="_blank">' + titel +
                        '</a></td></tr><tr><td style="text-align: left; padding-left: 5px">' +
                        beschreibung + '</td></tr></table>',
                    _Bild: item.enclosure ? '<img class="' + opts.cssclass + '_bild" width="100%" src="' + item.enclosure.url + '" />' : 'no image'
                });
                status.itemsProcessed++;
            } catch (entryError) {
                // Einzelner kaputter Eintrag ueberspringen, Feed laeuft weiter
                status.itemsSkipped++;
                status.anomalies.push({
                    index: i,
                    reason: entryError.anomalyReason || REASON.STRUCTURE_ERROR,
                    message: entryError.message || String(entryError)
                });
                this.adapter.log.warn(
                    'Eintrag #' + i + ' in Feed "' + opts.feedname + '" wird uebersprungen: ' +
                    (entryError.message || entryError)
                );
            }
        }

        // 5) Abrechnung: kein einziger verwertbarer Eintrag -> alten Datenbestand behalten
        if (status.itemsProcessed === 0) {
            status.reason = REASON.STRUCTURE_ERROR;
            if (status.itemsSkipped > 0) {
                status.error = 'Alle ' + status.itemsSkipped + ' Eintraege sind strukturell ungueltig';
            } else {
                status.error = 'Alle ' + status.itemsFiltered + ' Eintraege wurden durch Blacklist/Whitelist herausgefiltert';
            }
            this.adapter.log.warn(
                'Feed "' + opts.feedname + '" lieferte keine anzeigbaren Eintraege' +
                ' - bestehende Datenpunkte bleiben unveraendert'
            );
            return status;
        }

        // 6) Erfolg: Datenpunkte aktualisieren
        await this.setStateAsync(opts.state, { val: JSON.stringify(table), ack: true });
        await this.setStateAsync(opts.stateRaw, { val: JSON.stringify(rawData), ack: true });

        status.success = true;
        status.reason = REASON.OK;
        this.adapter.log.info(
            'Feed "' + opts.feedname + '" erfolgreich: ' + status.itemsProcessed +
            ' Eintraege verarbeitet' +
            (status.itemsSkipped ? ', ' + status.itemsSkipped + ' uebersprungen' : '') +
            (status.itemsFiltered ? ', ' + status.itemsFiltered + ' gefiltert' : '')
        );
        return status;
    }

    // Status eines Feeds in seinen status.*-Datenpunkt schreiben (Erfolg UND Misserfolg)
    async writeFeedStatus(key, status) {
        await this.setStateAsync(RSS_OPTIONS[key].stateStatus, {
            val: JSON.stringify(status),
            ack: true
        });
    }

    // Alle Feeds NACHEINANDER einlesen, je Feed einzeln abrechnen,
    // danach search.alert wie gewohnt setzen.
    async processAllFeeds() {
        this.matches = 0;
        const results = {};

        for (const key of FEED_KEYS) {
            let status;
            try {
                status = await this.readFeed(key);
            } catch (unexpected) {
                // absoluter Schutz: ein unverhoffter Fehler darf weder die restlichen
                // Feeds blockieren noch Datenpunkte loeschen
                status = this.createBaseStatus(key);
                status.reason = REASON.STRUCTURE_ERROR;
                status.error = unexpected.message || String(unexpected);
                this.adapter.log.error(
                    'Unbehandelter Fehler im Feed "' + RSS_OPTIONS[key].feedname + '": ' + status.error
                );
            }
            results[key] = status;
            await this.writeFeedStatus(key, status);
        }

        this.lastResults = results;
        const alert = this.matches > 0;
        await this.setStateAsync('search.alert', { val: alert, ack: true });
        this.adapter.log.debug('Endgueltige Matches im Suchlauf: ' + this.matches);

        return { results, matches: this.matches, alert };
    }
}

module.exports = {
    RSS_OPTIONS,
    FEED_KEYS,
    REASON,
    STRING_FOUND_CSS,
    TvProgramService,
    escapeRegExp,
    checkWildcard,
    getShowDetails,
    parseSearchTerms,
    buildSearchRegExp,
    isNonEmptyString
};
