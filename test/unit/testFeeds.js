'use strict';

const { expect } = require('chai');
const parseString = require('xml2js').parseString;
const {
    TvProgramService,
    RSS_OPTIONS,
    REASON,
    FEED_KEYS
} = require('../../lib/tvspielfilm');
const {
    createFakeAdapter,
    createStubRequest,
    buildItem,
    buildRss,
    routesForAll,
    successfulResponse,
    urlByFeedKey
} = require('./helpers');

function createService(routes, initialStates) {
    const adapter = createFakeAdapter(initialStates);
    const request = createStubRequest(routes);
    const service = new TvProgramService({ adapter, request, parseString });
    service.setConfig(adapter.config);
    service.setSearchTerms('');
    return { adapter, request, service };
}

const OLD_TABLE = JSON.stringify([{ text: 'ALTE-TABELLE' }]);
const OLD_RAW = JSON.stringify([{ title: 'ALTE-ROHDATEN' }]);

function oldStatesFor(key) {
    const states = {};
    states[RSS_OPTIONS[key].state] = { val: OLD_TABLE, ack: true };
    states[RSS_OPTIONS[key].stateRaw] = { val: OLD_RAW, ack: true };
    return states;
}

describe('tvspielfilm Feed-Sammlung (offline)', () => {
    describe('erfolgreicher Feed', () => {
        it('schreibt json.* und json.raw.* sowie einen Erfolgs-Status', async () => {
            const items = [
                buildItem({ time: '20:15', station: 'RTL', show: 'Tatort' }),
                buildItem({ time: '22:00', station: 'ZDF', show: 'Nachrichten' })
            ];
            const { adapter, service } = createService(
                routesForAll(successfulResponse(items))
            );

            const { results, alert, matches } = await service.processAllFeeds();

            expect(matches).to.equal(0);
            expect(alert).to.equal(false);
            for (const key of FEED_KEYS) {
                const status = results[key];
                expect(status.success).to.equal(true);
                expect(status.reason).to.equal(REASON.OK);
                expect(status.items).to.equal(2);
                expect(status.itemsProcessed).to.equal(2);
                expect(status.itemsSkipped).to.equal(0);

                const table = JSON.parse(adapter.states.get(RSS_OPTIONS[key].state).val);
                expect(table).to.have.length(2);
                expect(table[0].text).to.contain('Tatort');

                const raw = JSON.parse(adapter.states.get(RSS_OPTIONS[key].stateRaw).val);
                expect(raw[0]).to.include({ title: 'Tatort', station: 'RTL', time: '20:15' });
                expect(raw[0]).to.have.property('description').that.is.a('string');
                expect(raw[0].imgUrl).to.match(/^https?:\/\//);

                const writtenStatus = JSON.parse(adapter.states.get(RSS_OPTIONS[key].stateStatus).val);
                expect(writtenStatus.success).to.equal(true);
                expect(writtenStatus.reason).to.equal('ok');
            }
        });

        it('aktualisiert erfolgreich eingelesene Feeds und ueberschreibt alte Werte', async () => {
            const key = 'jetzt';
            const routes = {};
            for (const k of FEED_KEYS) {
                routes[RSS_OPTIONS[k].url] = successfulResponse([buildItem({ show: 'NEUE-SENDUNG-' + k })]);
            }
            const { adapter, service } = createService(routes, oldStatesFor(key));

            await service.processAllFeeds();

            const table = JSON.parse(adapter.states.get(RSS_OPTIONS[key].state).val);
            expect(table[0].text).to.contain('NEUE-SENDUNG-jetzt');
            expect(adapter.states.get(RSS_OPTIONS[key].state).val).to.not.equal(OLD_TABLE);
            expect(adapter.states.get(RSS_OPTIONS[key].stateRaw).val).to.not.equal(OLD_RAW);
        });

        it('verarbeitet auch einen Feed mit genau einem Eintrag (kein Array im XML)', async () => {
            const { adapter, service } = createService(
                routesForAll(successfulResponse([buildItem({ show: 'Einzelstueck' })]))
            );

            const { results } = await service.processAllFeeds();

            for (const key of FEED_KEYS) {
                expect(results[key].success).to.equal(true);
                expect(results[key].itemsProcessed).to.equal(1);
                const raw = JSON.parse(adapter.states.get(RSS_OPTIONS[key].stateRaw).val);
                expect(raw[0].title).to.equal('Einzelstueck');
            }
        });
    });

    describe('Fehlergrund: Anfragefehler', () => {
        it('behaelt alte Datenpunkte bei und schreibt einen request_error-Status', async () => {
            const key = 'jetzt';
            const routes = routesForAll(successfulResponse([buildItem({ show: 'Gesund' })]));
            routes[urlByFeedKey(key)] = { error: new Error('ECONNREFUSED') };

            const { adapter, request, service } = createService(routes, oldStatesFor(key));
            const { results } = await service.processAllFeeds();

            expect(results[key].success).to.equal(false);
            expect(results[key].reason).to.equal(REASON.REQUEST_ERROR);
            expect(results[key].error).to.contain('ECONNREFUSED');
            expect(adapter.states.get(RSS_OPTIONS[key].state).val).to.equal(OLD_TABLE);
            expect(adapter.states.get(RSS_OPTIONS[key].stateRaw).val).to.equal(OLD_RAW);

            const writtenStatus = JSON.parse(adapter.states.get(RSS_OPTIONS[key].stateStatus).val);
            expect(writtenStatus.reason).to.equal('request_error');

            // alle vier Feeds wurden trotzdem angefragt
            expect(request.calls).to.have.length(FEED_KEYS.length);
        });
    });

    describe('Fehlergrund: Nicht-200-Statuscode', () => {
        it('behaelt alte Datenpunkte bei HTTP 500 und meldet http_error', async () => {
            const key = 'filme';
            const routes = routesForAll(successfulResponse([buildItem({ show: 'Gesund' })]));
            routes[urlByFeedKey(key)] = { statusCode: 500, body: 'Serverfehler' };

            const { adapter, service } = createService(routes, oldStatesFor(key));
            const { results } = await service.processAllFeeds();

            expect(results[key].success).to.equal(false);
            expect(results[key].reason).to.equal(REASON.HTTP_ERROR);
            expect(results[key].httpStatus).to.equal(500);
            expect(adapter.states.get(RSS_OPTIONS[key].state).val).to.equal(OLD_TABLE);
            expect(adapter.states.get(RSS_OPTIONS[key].stateRaw).val).to.equal(OLD_RAW);

            const writtenStatus = JSON.parse(adapter.states.get(RSS_OPTIONS[key].stateStatus).val);
            expect(writtenStatus.reason).to.equal('http_error');
            expect(writtenStatus.httpStatus).to.equal(500);
        });

        it('akzeptiert nur 200 als Erfolg (404 scheitert ebenfalls)', async () => {
            const routes = routesForAll({ statusCode: 404, body: 'nicht gefunden' });
            const { service } = createService(routes);
            const { results } = await service.processAllFeeds();

            for (const key of FEED_KEYS) {
                expect(results[key].reason).to.equal(REASON.HTTP_ERROR);
                expect(results[key].success).to.equal(false);
            }
        });
    });

    describe('Fehlergrund: XML-Parsing', () => {
        it('behaelt alte Datenpunkte bei ungueltigem XML und meldet parse_error', async () => {
            const key = 'heute2015uhr';
            const routes = routesForAll(successfulResponse([buildItem({ show: 'Gesund' })]));
            routes[urlByFeedKey(key)] = { statusCode: 200, body: '<<<kaputtes xml>>>' };

            const { adapter, service } = createService(routes, oldStatesFor(key));
            const { results } = await service.processAllFeeds();

            expect(results[key].success).to.equal(false);
            expect(results[key].reason).to.equal(REASON.PARSE_ERROR);
            expect(results[key].error).to.be.a('string').that.is.not.empty;
            expect(adapter.states.get(RSS_OPTIONS[key].state).val).to.equal(OLD_TABLE);
            expect(adapter.states.get(RSS_OPTIONS[key].stateRaw).val).to.equal(OLD_RAW);

            const writtenStatus = JSON.parse(adapter.states.get(RSS_OPTIONS[key].stateStatus).val);
            expect(writtenStatus.reason).to.equal('parse_error');
        });
    });

    describe('Fehlergrund: Struktur-Anomalien', () => {
        it('meldet structure_error wenn keine Eintraege vorhanden sind', async () => {
            const key = 'heute2200uhr';
            const routes = routesForAll(successfulResponse([buildItem({ show: 'Gesund' })]));
            routes[urlByFeedKey(key)] = { statusCode: 200, body: buildRss([]) };

            const { adapter, service } = createService(routes, oldStatesFor(key));
            const { results } = await service.processAllFeeds();

            expect(results[key].reason).to.equal(REASON.STRUCTURE_ERROR);
            expect(results[key].success).to.equal(false);
            expect(adapter.states.get(RSS_OPTIONS[key].state).val).to.equal(OLD_TABLE);
            expect(adapter.states.get(RSS_OPTIONS[key].stateRaw).val).to.equal(OLD_RAW);
        });

        it('ueberspringt Eintraege ohne title oder description und liefert den Rest normal aus', async () => {
            const good = buildItem({ show: 'Brauchbar' });
            const noTitle = buildItem({ title: null });
            const noDescription = buildItem({ show: 'OhneBeschreibung', description: null });
            const whitespaceTitle = buildItem({ title: '   ' });

            const { adapter, service } = createService(
                routesForAll(successfulResponse([noTitle, good, noDescription, whitespaceTitle]))
            );
            const { results } = await service.processAllFeeds();

            for (const key of FEED_KEYS) {
                const status = results[key];
                expect(status.success).to.equal(true);
                expect(status.reason).to.equal(REASON.OK);
                expect(status.items).to.equal(4);
                expect(status.itemsProcessed).to.equal(1);
                expect(status.itemsSkipped).to.equal(3);
                expect(status.anomalies.map(a => a.reason)).to.include('missing_title');
                expect(status.anomalies.map(a => a.reason)).to.include('missing_description');

                const table = JSON.parse(adapter.states.get(RSS_OPTIONS[key].state).val);
                expect(table).to.have.length(1);
                expect(table[0].text).to.contain('Brauchbar');
            }
        });

        it('ueberspringt Eintraege mit unvollstaendigem Titelaufbau', async () => {
            const malformed = buildItem({ title: '20:15 | NurSenderOhneSendung' });
            const good = buildItem({ show: 'TrotzdemGut' });

            const { service } = createService(
                routesForAll(successfulResponse([malformed, good]))
            );
            const { results } = await service.processAllFeeds();

            for (const key of FEED_KEYS) {
                expect(results[key].itemsSkipped).to.equal(1);
                expect(results[key].anomalies[0].reason).to.equal('malformed_title');
                expect(results[key].success).to.equal(true);
            }
        });

        it('behaelt alte Datenpunkte wenn saemtliche Eintraege unbrauchbar sind', async () => {
            const key = 'jetzt';
            const routes = routesForAll(successfulResponse([buildItem({ show: 'Gesund' })]));
            routes[urlByFeedKey(key)] = {
                statusCode: 200,
                body: buildRss([
                    buildItem({ title: null }),
                    buildItem({ description: null })
                ])
            };

            const { adapter, service } = createService(routes, oldStatesFor(key));
            const { results } = await service.processAllFeeds();

            expect(results[key].success).to.equal(false);
            expect(results[key].reason).to.equal(REASON.STRUCTURE_ERROR);
            expect(results[key].itemsSkipped).to.equal(2);
            expect(adapter.states.get(RSS_OPTIONS[key].state).val).to.equal(OLD_TABLE);
            expect(adapter.states.get(RSS_OPTIONS[key].stateRaw).val).to.equal(OLD_RAW);
        });
    });

    describe('gemischter Lauf: Erfolg und Fehler nebeneinander', () => {
        it('verarbeitet die Feeds nacheinander und rechnet jeden einzeln ab', async () => {
            const routes = {};
            for (const k of FEED_KEYS) {
                routes[RSS_OPTIONS[k].url] = successfulResponse([buildItem({ show: 'SERVICE-' + k })]);
            }
            routes[urlByFeedKey('jetzt')] = { error: new Error('ETIMEDOUT') };
            routes[urlByFeedKey('filme')] = { statusCode: 503, body: 'wartung' };

            const initialStates = Object.assign(
                oldStatesFor('jetzt'),
                oldStatesFor('filme')
            );
            const { adapter, request, service } = createService(routes, initialStates);
            const { results } = await service.processAllFeeds();

            // Reihfolge entspricht der Deklaration in RSS_OPTIONS (seriell, nicht parallel)
            expect(request.calls).to.deep.equal(FEED_KEYS.map(k => RSS_OPTIONS[k].url));

            expect(results.jetzt.reason).to.equal(REASON.REQUEST_ERROR);
            expect(results.filme.reason).to.equal(REASON.HTTP_ERROR);
            expect(results.filme.httpStatus).to.equal(503);
            expect(results.heute2015uhr.reason).to.equal(REASON.OK);
            expect(results.heute2200uhr.reason).to.equal(REASON.OK);

            // gescheiterte Feeds: alte Werte
            expect(adapter.states.get(RSS_OPTIONS.jetzt.state).val).to.equal(OLD_TABLE);
            expect(adapter.states.get(RSS_OPTIONS.jetzt.stateRaw).val).to.equal(OLD_RAW);
            expect(adapter.states.get(RSS_OPTIONS.filme.state).val).to.equal(OLD_TABLE);
            expect(adapter.states.get(RSS_OPTIONS.filme.stateRaw).val).to.equal(OLD_RAW);

            // erfolgreiche Feeds: neue Werte
            const table2015 = JSON.parse(adapter.states.get(RSS_OPTIONS.heute2015uhr.state).val);
            expect(table2015[0].text).to.contain('SERVICE-heute2015uhr');
            const table2200 = JSON.parse(adapter.states.get(RSS_OPTIONS.heute2200uhr.state).val);
            expect(table2200[0].text).to.contain('SERVICE-heute2200uhr');

            // jeder Feed hat einen Statusdatenpunkt erhalten
            for (const key of FEED_KEYS) {
                const written = JSON.parse(adapter.states.get(RSS_OPTIONS[key].stateStatus).val);
                expect(written.feed).to.equal(key);
                expect(written).to.have.property('timestamp');
            }
        });

        it('steckt auch einen geworfenen Fehler pro Feed weg und macht mit den weiteren weiter', async () => {
            const routes = {};
            for (const k of FEED_KEYS) {
                routes[RSS_OPTIONS[k].url] = successfulResponse([buildItem({ show: 'SERVICE-' + k })]);
            }
            const { service } = createService(routes);
            const explodingKey = 'jetzt';
            const original = service.readFeed.bind(service);
            service.readFeed = async function (key) {
                if (key === explodingKey) {
                    throw new Error('boeese Ueberraschung');
                }
                return original(key);
            };

            const { results } = await service.processAllFeeds();

            expect(results.jetzt.success).to.equal(false);
            expect(results.jetzt.reason).to.equal(REASON.STRUCTURE_ERROR);
            expect(results.heute2015uhr.success).to.equal(true);
            expect(results.heute2200uhr.success).to.equal(true);
            expect(results.filme.success).to.equal(true);
        });
    });

    describe('search.alert', () => {
        it('ist false ohne Suchbegriffe, auch wenn alle Feeds erfolgreich sind', async () => {
            const { adapter, service } = createService(
                routesForAll(successfulResponse([buildItem({ show: 'Tatort' })]))
            );
            service.setSearchTerms('');

            const summary = await service.processAllFeeds();

            expect(summary.alert).to.equal(false);
            expect(summary.matches).to.equal(0);
            expect(adapter.states.get('search.alert').val).to.equal(false);
        });

        it('ist false bei leerer CSV-Eingabe (nur Kommas/Leerzeichen)', async () => {
            const { service } = createService(
                routesForAll(successfulResponse([buildItem({ show: 'Tatort' })]))
            );
            service.setSearchTerms('  , , ,  ');

            const summary = await service.processAllFeeds();

            expect(service.searchPattern).to.equal(null);
            expect(summary.alert).to.equal(false);
        });

        it('ist true und zaehlt Treffer wenn ein Suchbegriff vorkommt', async () => {
            const { adapter, service } = createService(
                routesForAll(successfulResponse([
                    buildItem({ show: 'Tatort: Blutiger Pfad' }),
                    buildItem({ show: 'Komodie', description: 'Krimi mit Witz' })
                ]))
            );
            service.setSearchTerms('Tatort, Krimi');

            const summary = await service.processAllFeeds();

            expect(summary.matches).to.be.greaterThan(0);
            expect(summary.alert).to.equal(true);
            expect(adapter.states.get('search.alert').val).to.equal(true);

            // Treffer wurden in der HTML-Tabelle markiert
            const table = JSON.parse(adapter.states.get(RSS_OPTIONS.jetzt.state).val);
            expect(table[0].text).to.contain('<mark>');
        });

        it('behandelt Regex-Sonderzeichen in Suchbegriffen wortlich', async () => {
            const { service } = createService(
                routesForAll(successfulResponse([buildItem({ show: 'SOKO (Munchen)' })]))
            );
            service.setSearchTerms('SOKO (Munchen)');

            const summary = await service.processAllFeeds();
            expect(summary.alert).to.equal(true);
        });

        it('erzeugt wiederholte Laeufe zuverlaessig dieselben Treffer (kein lastIndex-Problem)', async () => {
            const { service } = createService(
                routesForAll(successfulResponse([buildItem({ show: 'Tatort' })]))
            );
            service.setSearchTerms('Tatort');

            const first = await service.processAllFeeds();
            const second = await service.processAllFeeds();

            expect(first.matches).to.equal(second.matches);
            expect(first.matches).to.be.greaterThan(0);
        });

        it('ist false wenn alle Feeds scheitern, und loescht keine alten Datenpunkte', async () => {
            const initialStates = oldStatesFor('jetzt');
            const routes = routesForAll({ statusCode: 500, body: 'fehler' });
            const { adapter, service } = createService(routes, initialStates);
            service.setSearchTerms('Tatort');

            const summary = await service.processAllFeeds();

            expect(summary.alert).to.equal(false);
            expect(adapter.states.get(RSS_OPTIONS.jetzt.state).val).to.equal(OLD_TABLE);
        });
    });
});
