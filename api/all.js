const { errorResult } = require("./helpers");
const tribunnews = require("./tribunnews");
const cnnindonesia = require("./cnnindonesia");
const tempo = require("./tempo");
const kompas = require("./kompas");
const liputan6 = require("./liputan6");

/**
 * Aggregated list endpoint for all five Indonesian news sites.
 *
 * Design notes:
 * - The aggregator only REUSES each site's existing getData; no parser logic
 *   is duplicated here. The default category per site matches the one already
 *   used by the site's own "all" route in index.js (Tribunnews defaults to
 *   "news", the other four to "").
 * - All five requests are started together with Promise.all. Every per-site
 *   promise settles inside collectSite(), so a rejection can never abort the
 *   other four and Promise.all itself never rejects.
 * - No extra timeout/race layer is added on top. Per-site latency stays
 *   governed by the existing data layer: fetchHtml()'s 20s timeout with 2
 *   retries (the four HTML sites) and Tempo's fetchApi() axios 20s timeout.
 *   Because the calls run concurrently, the aggregate wall-clock is bounded
 *   by the slowest single site rather than the sum of all five. The tradeoff
 *   is that a fully hanging site can still make the whole request wait up to
 *   its own timeout/retry budget; accepting that keeps the retry/backoff
 *   policy in exactly one place instead of inventing a second one here.
 */

// `path` mirrors the single-site route so error responses stay locatable.
const SITES = [
    { name: "Tribunnews", path: "/tribunnews", category: "news", getData: tribunnews.getData },
    { name: "CNN Indonesia", path: "/cnnindonesia", category: "", getData: cnnindonesia.getData },
    { name: "Tempo", path: "/tempo", category: "", getData: tempo.getData },
    { name: "Kompas", path: "/kompas", category: "", getData: kompas.getData },
    { name: "Liputan 6", path: "/liputan6", category: "", getData: liputan6.getData },
];

/**
 * Run one site's getData and normalize every possible outcome into a
 * uniform per-site object. This function never throws.
 */
async function collectSite(site) {
    const base = { name: site.name, path: site.path };

    try {
        const data = await site.getData(site.category);

        // getData is not supposed to return undefined/null, but guard anyway:
        // one site returning nothing must not poison the aggregate response.
        if (data === undefined || data === null) {
            return {
                ...base,
                status: "error",
                error: {
                    message: `${site.name} getData returned ${String(data)} instead of an article list`,
                    status: null,
                    code: null,
                },
            };
        }

        // Existing parsers signal failure with { error: { message, ... } }.
        // Reuse the same message/status/code fields as helpers.errorResult.
        if (!Array.isArray(data)) {
            if (data.error && typeof data.error === "object") {
                return {
                    ...base,
                    status: "error",
                    error: {
                        message: data.error.message || "Unknown error",
                        status: data.error.status != null ? data.error.status : null,
                        code: data.error.code != null ? data.error.code : null,
                    },
                };
            }
            return {
                ...base,
                status: "error",
                error: errorResult(
                    new Error(`${site.name} getData returned an unsupported response shape`)
                ).error,
            };
        }

        // An empty list is a distinct, explicitly marked outcome rather than
        // an error: the site responded but had no parseable articles.
        if (data.length === 0) {
            return {
                ...base,
                status: "empty",
                message: `${site.name} responded successfully but returned no articles`,
                count: 0,
                data: [],
            };
        }

        return {
            ...base,
            status: "success",
            count: data.length,
            data: data,
        };
    } catch (error) {
        // A site that throws (network failure, timeout after retries, ...) is
        // recorded with the same error shape as the single-site endpoints.
        return {
            ...base,
            status: "error",
            error: errorResult(error).error,
        };
    }
}

/**
 * Fetch all five site lists concurrently and group the results by site.
 *
 * @param {Array} [sites] optional injected site list, used by the tests so
 *                        no real network request is needed.
 * @returns {Promise<{message: string, summary: object, results: Array}>}
 */
async function getAll(sites = SITES) {
    // The map starts every getData immediately; collectSite swallows each
    // site's own failure, so Promise.all waits for all five without aborting.
    const results = await Promise.all(sites.map((site) => collectSite(site)));

    const summary = {
        total: results.length,
        success: results.filter((r) => r.status === "success").length,
        empty: results.filter((r) => r.status === "empty").length,
        failed: results.filter((r) => r.status === "error").length,
    };
    // Only true when every single site failed; the route maps this to HTTP 500.
    summary.all_failed = summary.total > 0 && summary.failed === summary.total;

    return {
        message: "Agregasi daftar berita dari lima situs berita Indonesia",
        summary: summary,
        results: results,
    };
}

module.exports = {
    SITES: SITES,
    getAll: getAll,
};
