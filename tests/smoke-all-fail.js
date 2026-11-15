// Standalone acceptance check: all five getData implementations failing
// (a mix of throws, undefined returns and errorResult-style objects).
const aggregate = require('../api/all');

const failingSites = aggregate.SITES.map((site, i) => ({
    ...site,
    getData: async () => {
        if (i === 0) throw new Error('ETIMEDOUT after 20000ms');
        if (i === 1) return undefined;
        if (i === 2) return { error: { message: 'API Tempo error', status: 503, code: 'SERVICE_UNAVAILABLE' } };
        if (i === 3) throw new Error('socket hang up');
        return { error: { message: 'parse failed' } };
    },
}));

aggregate.getAll(failingSites).then((body) => {
    const ok =
        body.summary.failed === 5 &&
        body.summary.all_failed === true &&
        body.results.every(
            (r) => r.status === 'error' && r.name && r.path && r.error && typeof r.error.message === 'string'
        );
    console.log(JSON.stringify(body.summary));
    for (const r of body.results) {
        console.log(r.name.padEnd(14), r.status, JSON.stringify(r.error));
    }
    console.log(ok ? 'ALL-FAIL CHECK: PASS' : 'ALL-FAIL CHECK: FAIL');
    process.exit(ok ? 0 : 1);
});
