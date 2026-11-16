/* eslint-disable no-undef */

import { readFile, unlink } from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';

import SiteCrawler from '../src/site-crawler.js';

const FIXTURE_DIRECTORY = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  'fixtures',
  'site-crawl-report',
);

const ORIGIN = 'https://local.test';
const EXPORTED_FILE = 'local.test.sitemap.json';

const readFixture = (fileName) => readFile(path.join(FIXTURE_DIRECTORY, fileName), 'utf-8');

/**
 * A tiny fake site: three working pages, one page whose request throws,
 * and one page whose markup cannot be parsed into links
 * @param {string} url - the requested url
 * @returns {Promise<object>} an axios-like response
 */
async function fixtureAjax(url) {
  const { pathname } = new URL(url);

  switch (pathname) {
    case '/':
      return { data: await readFixture('index.html') };
    case '/about.html':
      return { data: await readFixture('about.html') };
    case '/broken.html':
      throw new Error('Request failed with status code 500');
    case '/empty.html':
      return { data: await readFixture('empty.html') };
    default:
      return { data: await readFixture('index.html') };
  }
}

const buildCrawler = () => {
  const siteCrawler = new SiteCrawler({ startPage: `${ORIGIN}/` }, { ajax: fixtureAjax });

  return siteCrawler;
};

afterAll(async () => {
  try {
    await unlink(EXPORTED_FILE);
  } catch (unlinkError) {
    // the file may not exist if a test failed before exporting
  }
});

describe('SiteCrawler: per-page crawl outcomes', () => {
  describe('statuses', () => {
    test('it exposes the three outcome kinds', () => {
      expect(SiteCrawler.statuses).toEqual({
        success: 'success',
        requestFailed: 'request-failed',
        linkParseFailed: 'link-parse-failed',
      });
    });
  });

  describe('crawlPageAsync', () => {
    test('it reports a successful page', async () => {
      const siteCrawler = buildCrawler();

      const result = await siteCrawler.crawlPageAsync(`${ORIGIN}/about.html`);

      expect(result).toEqual({
        url: `${ORIGIN}/about.html`,
        status: SiteCrawler.statuses.success,
        linkCount: expect.any(Number),
      });
      expect(result.reason).toBeUndefined();
      expect(siteCrawler.crawlReport).toEqual([result]);
    });

    test('it reports a failed request without throwing', async () => {
      const siteCrawler = buildCrawler();

      const result = await siteCrawler.crawlPageAsync(`${ORIGIN}/broken.html`);

      expect(result).toHaveProperty('status', SiteCrawler.statuses.requestFailed);
      expect(result).toHaveProperty('url', `${ORIGIN}/broken.html`);
      expect(result.reason).toContain('500');
      expect(siteCrawler.linkSet.size).toEqual(0);
    });

    test('it reports an unparseable page as a link parse failure', async () => {
      const siteCrawler = buildCrawler();

      const result = await siteCrawler.crawlPageAsync(`${ORIGIN}/empty.html`);

      expect(result).toHaveProperty('status', SiteCrawler.statuses.linkParseFailed);
      expect(result.reason).toContain('Markup was not provided');
      expect(siteCrawler.linkSet.size).toEqual(0);
    });
  });

  describe('crawlSiteAsync', () => {
    test('one failing page does not stop the rest of the crawl', async () => {
      const siteCrawler = buildCrawler();

      const results = await siteCrawler.crawlSiteAsync(`${ORIGIN}/`);

      const statuses = results.map((result) => result.status);

      expect(statuses).toContain(SiteCrawler.statuses.success);
      expect(statuses).toContain(SiteCrawler.statuses.requestFailed);
      expect(statuses).toContain(SiteCrawler.statuses.linkParseFailed);
      expect(results.length).toEqual(4);
      expect(siteCrawler.crawlReport).toEqual(results);
    });

    test('successful links survive while failed pages are excluded from the link list', async () => {
      const siteCrawler = buildCrawler();

      await siteCrawler.crawlSiteAsync(`${ORIGIN}/`);

      const exportedUrls = siteCrawler.urlset.map((siteUrl) => siteUrl.loc);

      expect(exportedUrls).toContain(`${ORIGIN}/about.html`);
      expect(exportedUrls).toContain(`${ORIGIN}/contact.html`);
      expect(exportedUrls).not.toContain(`${ORIGIN}/broken.html`);
      expect(exportedUrls).not.toContain(`${ORIGIN}/empty.html`);
    });

    test('failed pages carry a url and a reason summary', async () => {
      const siteCrawler = buildCrawler();

      const results = await siteCrawler.crawlSiteAsync(`${ORIGIN}/`);
      const failures = results.filter((result) => result.status !== SiteCrawler.statuses.success);

      expect(failures).toHaveLength(2);
      failures.forEach((failure) => {
        expect(failure.url).toEqual(expect.any(String));
        expect(failure.reason.length).toBeGreaterThan(0);
      });
      expect(failures.map((failure) => failure.url)).toEqual(
        expect.arrayContaining([`${ORIGIN}/broken.html`, `${ORIGIN}/empty.html`]),
      );

      expect(siteCrawler.crawlSummary).toHaveProperty('total', 4);
      expect(siteCrawler.crawlSummary).toHaveProperty('succeeded', 2);
      expect(siteCrawler.crawlSummary).toHaveProperty('failed', 2);
      expect(siteCrawler.crawlSummary.failures).toEqual(failures);
    });
  });

  describe('exported sitemap', () => {
    test('the exported file keeps the urlset structure and only success links', async () => {
      const siteCrawler = buildCrawler();

      await siteCrawler.crawl();
      await siteCrawler.exportSiteLinks(siteCrawler.exportFileName);

      const exported = JSON.parse(await readFile(EXPORTED_FILE, 'utf-8'));

      expect(Array.isArray(exported)).toEqual(true);
      exported.forEach((entry) => {
        expect(Object.keys(entry)).toEqual(['loc']);
        expect(entry.loc.indexOf(ORIGIN)).toEqual(0);
      });

      const exportedLocations = exported.map((entry) => entry.loc);
      expect(exportedLocations).toContain(`${ORIGIN}/about.html`);
      expect(exportedLocations).not.toContain(`${ORIGIN}/broken.html`);
      expect(exportedLocations).not.toContain(`${ORIGIN}/empty.html`);
    });
  });

  describe('link filtering and de-duplication', () => {
    test('relative and fully qualified forms of one page count once', async () => {
      const siteCrawler = buildCrawler();

      await siteCrawler.crawlSiteAsync(`${ORIGIN}/`);

      const aboutEntries = siteCrawler.urlset
        .filter((siteUrl) => new URL(siteUrl.loc).pathname === '/about.html');

      expect(aboutEntries).toHaveLength(1);
    });

    test('external links and anchors never enter the link list', async () => {
      const siteCrawler = buildCrawler();

      await siteCrawler.crawlSiteAsync(`${ORIGIN}/`);

      siteCrawler.urlset.forEach((siteUrl) => {
        expect(siteUrl.loc.indexOf(ORIGIN)).toEqual(0);
        expect(siteUrl.loc).not.toContain('external.example.com');
        expect(siteUrl.loc).not.toContain('#');
      });
    });

    test('filterPageLinks drops duplicates across relative and absolute spellings', () => {
      const pageLinks = [
        '/about.html',
        `${ORIGIN}/about.html`,
        '/contact.html',
        'https://external.example.com/away.html',
        '#top',
      ];

      const filteredLinks = SiteCrawler.filterPageLinks(pageLinks, ORIGIN);

      expect(filteredLinks).toEqual(['/about.html', '/contact.html']);
    });
  });
});

describe('SiteCrawler: local sitemap fixture', () => {
  test('sitemap links remain usable and export as a urlset', async () => {
    const siteCrawler = new SiteCrawler(
      { startPage: `${ORIGIN}/sitemap.xml` },
      {
        ajax: async (url) => ({
          data: (url === `${ORIGIN}/sitemap.xml`)
            ? await readFixture('sitemap.xml')
            : await readFixture('index.html'),
        }),
      },
    );

    await siteCrawler.setSitemap();

    expect(siteCrawler.urlset).toEqual([
      { loc: `${ORIGIN}/` },
      { loc: `${ORIGIN}/about.html` },
      { loc: `${ORIGIN}/contact.html` },
    ]);
    expect(siteCrawler.crawlReport).toEqual([]);
  });
});
