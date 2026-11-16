'use strict';

const { expect } = require('chai');
const parseString = require('xml2js').parseString;
const {
    TvProgramService,
    REASON,
    checkWildcard,
    getShowDetails,
    parseSearchTerms,
    buildSearchRegExp,
    isNonEmptyString
} = require('../../lib/tvspielfilm');
const {
    createFakeAdapter,
    createStubRequest,
    buildItem,
    routesForAll,
    successfulResponse
} = require('./helpers');

function createService(routes, config) {
    const adapter = createFakeAdapter();
    adapter.config = config || { blacklist: [], whitelist: [] };
    const service = new TvProgramService({
        adapter,
        request: createStubRequest(routes || {}),
        parseString
    });
    service.setConfig(adapter.config);
    return { adapter, service };
}

describe('tvspielfilm Hilfsfunktionen (offline)', () => {
    describe('parseSearchTerms', () => {
        it('liefert ein leeres Array bei leeren, nicht-string oder Whitespace-Eingaben', () => {
            expect(parseSearchTerms('')).to.deep.equal([]);
            expect(parseSearchTerms('   ')).to.deep.equal([]);
            expect(parseSearchTerms(',,,')).to.deep.equal([]);
            expect(parseSearchTerms(undefined)).to.deep.equal([]);
            expect(parseSearchTerms(null)).to.deep.equal([]);
            expect(parseSearchTerms(42)).to.deep.equal([]);
        });

        it('trimmt Begriffe und entfernt leere CSV-Teile', () => {
            expect(parseSearchTerms(' Tatort , ,Krimi ')).to.deep.equal(['Tatort', 'Krimi']);
        });
    });

    describe('buildSearchRegExp', () => {
        it('liefert null ohne Begriffe', () => {
            expect(buildSearchRegExp([])).to.equal(null);
            expect(buildSearchRegExp(undefined)).to.equal(null);
        });

        it('escapt Regex-Sonderzeichen', () => {
            const pattern = buildSearchRegExp(['SOKO (M)', 'a+b']);
            expect(pattern.test('SOKO (M)')).to.equal(true);
            expect(pattern.test('SOKO M')).to.equal(false);
            expect(pattern.test('a+b')).to.equal(true);
            expect(pattern.test('axb')).to.equal(false);
        });
    });

    describe('getShowDetails', () => {
        it('zerlegt den Titel in Zeit, Sender und Sendung', () => {
            expect(getShowDetails('14:00 | RTL | Formel 1: GP Aserbaidschan')).to.deep.equal({
                time: '14:00',
                station: 'RTL',
                show: 'Formel 1: GP Aserbaidschan'
            });
        });

        it('liefert leere Felder statt zu werfen wenn der Titel zu kurz ist', () => {
            const details = getShowDetails('20:15 | RTL');
            expect(details.show).to.equal('');
            expect(details.station).to.equal('RTL');
        });
    });

    describe('checkWildcard', () => {
        it('nutzt * als Platzhalter und behandelt andere Sonderzeichen woertlich', () => {
            expect(checkWildcard('Sky Cinema', 'Sky*')).to.equal(true);
            expect(checkWildcard('Sky', 'Sky*')).to.equal(true);
            expect(checkWildcard('RTL', 'Sky*')).to.equal(false);
            expect(checkWildcard('RTLplus', 'RTL*')).to.equal(true);
            expect(checkWildcard('RTLplus', 'RTL.')).to.equal(false);
        });
    });

    describe('isNonEmptyString', () => {
        it('akzeptiert nur nicht-leere Strings nach Trim', () => {
            expect(isNonEmptyString('x')).to.equal(true);
            expect(isNonEmptyString(' x ')).to.equal(true);
            expect(isNonEmptyString('')).to.equal(false);
            expect(isNonEmptyString('   ')).to.equal(false);
            expect(isNonEmptyString(undefined)).to.equal(false);
            expect(isNonEmptyString(123)).to.equal(false);
        });
    });
});

describe('tvspielfilm Senderfilter (offline)', () => {
    it('filtert Sender ueber die Blacklist wenn die Whitelist leer ist', async () => {
        const items = [
            buildItem({ station: 'RTL', show: 'A' }),
            buildItem({ station: 'RTL II', show: 'B' }),
            buildItem({ station: 'ZDF', show: 'C' })
        ];
        const { adapter, service } = createService(
            routesForAll(successfulResponse(items)),
            { blacklist: ['RTL'], whitelist: [] }
        );

        const { results } = await service.processAllFeeds();

        expect(results.jetzt.success).to.equal(true);
        const raw = JSON.parse(adapter.states.get('json.raw.jetzt').val);
        const stations = raw.map(entry => entry.station);
        expect(stations).to.not.include('RTL');
        expect(stations).to.include('RTL II');
        expect(stations).to.include('ZDF');
    });

    it('nutzt bei Whitelist-Eintraegen nur noch die Whitelist (Wildcard)', async () => {
        const items = [
            buildItem({ station: 'Sky Cinema', show: 'A' }),
            buildItem({ station: 'Sky Sport 1', show: 'B' }),
            buildItem({ station: 'RTL', show: 'C' })
        ];
        const { adapter, service } = createService(
            routesForAll(successfulResponse(items)),
            { blacklist: ['RTL'], whitelist: ['Sky*'] }
        );

        await service.processAllFeeds();

        const raw = JSON.parse(adapter.states.get('json.raw.jetzt').val);
        const stations = raw.map(entry => entry.station);
        expect(stations).to.include('Sky Cinema');
        expect(stations).to.include('Sky Sport 1');
        expect(stations).to.not.include('RTL');
    });

    it('akzeptiert exakte Whitelist-Treffer ohne Wildcard', async () => {
        const items = [
            buildItem({ station: 'ARD', show: 'A' }),
            buildItem({ station: 'ZDF', show: 'B' })
        ];
        const { adapter, service } = createService(
            routesForAll(successfulResponse(items)),
            { blacklist: [], whitelist: ['ARD'] }
        );

        await service.processAllFeeds();

        const raw = JSON.parse(adapter.states.get('json.raw.jetzt').val);
        expect(raw.map(entry => entry.station)).to.deep.equal(['ARD']);
    });

    it('laeuft weiter wenn ein Feed komplett herausgefiltert wird, ohne den alten Bestand zu loeschen', async () => {
        const key = 'jetzt';
        const oldState = { val: JSON.stringify([{ text: 'ALT' }]), ack: true };
        const routes = routesForAll(successfulResponse([
            buildItem({ station: 'RTL', show: 'Raus' })
        ]));
        const { adapter, service } = createService(routes, { blacklist: ['RTL'], whitelist: [] });
        adapter.states.set('json.jetzt', oldState);
        adapter.states.set('json.raw.jetzt', oldState);

        const { results } = await service.processAllFeeds();

        // alle Eintraege gefiltert -> kein verwertbarer Eintrag -> alte Daten bleiben
        expect(results[key].success).to.equal(false);
        expect(results[key].reason).to.equal(REASON.STRUCTURE_ERROR);
        expect(results[key].itemsFiltered).to.equal(1);
        expect(adapter.states.get('json.jetzt')).to.equal(oldState);
        expect(adapter.states.get('json.raw.jetzt')).to.equal(oldState);
    });
});
