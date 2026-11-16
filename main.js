/* jshint -W097 */// jshint strict:false
/*jslint node: true */

'use strict';
const utils = require('@iobroker/adapter-core'); // Get common adapter utils
const parseString = require('xml2js').parseString;
const request = require('request');
const {
    TvProgramService,
    RSS_OPTIONS
} = require('./lib/tvspielfilm');

let terminating_timer;

const adapter = utils.Adapter({
    name: 'tvspielfilm',
    systemConfig: true,
    useFormatDate: true
});

let service = null;

// Settings (Blacklist/Whitelist) protokollieren und an den Service uebergeben
function readSettings() {
    const config = adapter.config || {};
    if (config.blacklist === undefined || config.blacklist.length === 0) {
        adapter.log.debug('Keine Stationen zur Blacklist hinzugefuegt');
    } else {
        adapter.log.debug('Zahl Stationen in Blacklist: ' + config.blacklist.length);
    }
    for (const bl in config.blacklist) {
        adapter.log.debug('Blacklist (#' + (parseInt(bl, 10) + 1) + '): ' + config.blacklist[bl]);
    }
    if (config.whitelist === undefined || config.whitelist.length === 0) {
        adapter.log.debug('Keine Stationen zur Whitelist hinzugefuegt');
    } else {
        adapter.log.debug('Zahl Stationen in Whitelist: ' + config.whitelist.length);
    }
    for (const wl in config.whitelist) {
        adapter.log.debug('Whitelist (#' + (parseInt(wl, 10) + 1) + '): ' + config.whitelist[wl]);
    }

    service.setConfig({
        blacklist: config.blacklist,
        whitelist: config.whitelist
    });
}

// Suchbegriffe aus dem Datenpunkt search.list einlesen und Suchmuster erstellen
function searchStringCheck() {
    return new Promise(resolve => {
        adapter.getState('search.list', function (err, obj) {
            if (err) {
                adapter.log.warn('Fehler beim Einlesen der Suchbegriffe');
                resolve();
                return;
            }
            if (!obj) {
                adapter.log.debug('keine Suchbegriffe gefunden, Datenpunkt leer oder nicht angelegt');
                service.setSearchTerms('');
                resolve();
                return;
            }

            const terms = service.setSearchTerms(obj.val); // leere Eintraege werden entfernt
            adapter.log.debug('Anzahl der Suchbegriffe: ' + terms.length);
            for (let s = 0; s < terms.length; s++) {
                adapter.log.debug('Suchbegriff (#' + (s + 1) + '): ' + terms[s]);
            }

            // aufbereitete Liste zurueck in den Datenpunkt schreiben
            adapter.setState('search.list', { val: terms.toString(), ack: true });
            resolve();
        });
    });
}

// Alle Feeds nacheinander einlesen, danach wie gewohnt den Adapter beenden
async function iterateAllFeeds() {
    try {
        const summary = await service.processAllFeeds();

        for (const key of Object.keys(summary.results)) {
            const status = summary.results[key];
            const line = 'Feed "' + RSS_OPTIONS[key].feedname + '": ' +
                (status.success ? 'ERFOLG' : 'FEHLER (' + status.reason + ')') +
                ' | Eintraege: ' + status.items +
                ', verarbeitet: ' + status.itemsProcessed +
                ', uebersprungen: ' + status.itemsSkipped +
                ', gefiltert: ' + status.itemsFiltered;
            if (status.success) {
                adapter.log.info(line);
            } else {
                adapter.log.warn(line + (status.error ? ' | ' + status.error : ''));
            }
        }

        adapter.setState('search.alert', { val: summary.alert, ack: true }, () => {
            adapter.log.info('objects written');
            setTimeout(stopAdapter, 10000);
        });
    } catch (err) {
        // auch bei einem unverhofften Fehler den Adapter kontrolliert beenden
        adapter.log.error('Schwerwiegender Fehler beim Einlesen der Feeds: ' + (err && err.message ? err.message : err));
        setTimeout(stopAdapter, 10000);
    }
}

async function main() {
    try {
        readSettings(); // Einstellungen lesen und pruefen
        await searchStringCheck(); // Suchbegriffe aus Datenpunkt einlesen und Suchmuster erstellen
        setTimeout(iterateAllFeeds, 2000); // Alle Feeds nacheinander durchgehen
    } catch (err) {
        adapter.log.error('Fehler bei der Initialisierung: ' + (err && err.message ? err.message : err));
        setTimeout(stopAdapter, 10000);
    }
}

function stopAdapter(isTimeout) {
    clearTimeout(terminating_timer);
    if (isTimeout) {
        adapter.log.info('force terminating after 1 minute');
    }
    adapter.stop();
}

// TRIGGER: Aenderungen in den Datenpunkten
adapter.on('stateChange', (id, state) => {
    if (state && !state.ack) {
        if (id === adapter.namespace + '.search.list') { // derzeit nur Suchbegriffe-Datenpunkt
            adapter.log.info('Suchbegriffe geaendert');
            main();
        }
    }
});

adapter.on('ready', function () {
    terminating_timer = setTimeout(() => stopAdapter(true), 60000);
    adapter.log.debug('adapter tvspielfilm initializing objects');

    service = new TvProgramService({
        adapter: adapter,
        request: request,
        parseString: parseString
    });

    main();
});
