/* eslint-disable max-len */
import axios from 'axios';
import fs from 'fs';
import Path from 'path';
import * as cheerio from 'cheerio';
import { Parser } from 'xml2js';

import { LOG_FILE_NAME } from './constants.js';
import Log from './logger.js';
import Outputter from './outputter.js';
import { forEachAsync } from './utils.js';

/** @typedef {import('axios').AxiosStatic} Axios */
/** @typedef {typeof import('cheerio')} Cheerio */

const log = new Log(LOG_FILE_NAME);

/**
 * @description The possible outcomes for an attempted page crawl
 * @constant {object} CRAWL_STATUSES
 * @property {string} success - the page was requested and its links were parsed
 * @property {string} requestFailed - the page could not be requested
 * @property {string} linkParseFailed - the page was requested but its links could not be parsed
 */
export const CRAWL_STATUSES = {
  success: 'success',
  requestFailed: 'request-failed',
  linkParseFailed: 'link-parse-failed',
};

/**
 * @typedef {object} CrawlResult
 * @property {string} url - the url of the attempted page
 * @property {string} status - one of CRAWL_STATUSES
 * @property {number} linkCount - how many in-site links were collected from the page
 * @property {string} [reason] - a summary of why the page failed (absent on success)
 */

/**
 * @typedef {object} SiteCrawlerConfig
 * @property {string} startPage - The starting URL for crawling or sitemap retrieval
 * @property {string} linkSelector - CSS selector used to find links on pages
 * @property {boolean} shouldCrawl - Whether to crawl pages or use sitemap
 * @property {boolean} useExportedSitemap - Whether to use existing exported sitemap file if available
 */
const DEFAULT_CONFIG = {
  startPage: 'https://frankmtaylor.com',
  linkSelector: 'a[href]',
  shouldCrawl: false,
  useExportedSitemap: true,
};

/**
 * @typedef {object} SiteCrawlerLibraries
 * @property {object} ajax - an ajax library (usually axios)
 * @property {object} dom - an HTML parser
 * @property {object} Parser - an XML parser
 */
const DEFAULT_LIBRARIES = {
  ajax: axios,
  dom: cheerio,
  Parser,
};

/**
 * @class SiteCrawler
 * @classdesc Produces a sitemap either from a sitemap.xml or by crawling
 */
export default class SiteCrawler {
  /**
   * Creates an instance of SiteCrawler
   * @param  {SiteCrawlerConfig} config - the configuration for the crawler
   * @param  {SiteCrawlerLibraries} libraries - the libraries to use
   */
  constructor(config, libraries) {
    /**
     * @public
     * @type {SiteCrawlerConfig} - The config for the instance
     */
    this.config = { ...SiteCrawler.defaultConfig, ...config };
    /**
     * @public
     * @type {SiteCrawlerLibraries} - The Libraries the crawler uses
     */
    this.libraries = { ...SiteCrawler.defaultLibraries, ...libraries };
    /**
     * @public
     * @type {Set<string>} - The set of unique links for the site
     */
    this.linkSet = new Set();
    /**
     * @public
     * @type {CrawlResult[]} - The per-page outcome of the most recent crawl
     */
    this.crawlResults = [];
    /**
     * @public
     * @type {Outputter} - The outputter for the crawler; used to create the sitemap.json file
     */
    this.outputter = new Outputter('sitemap.json', log);
  }

  /**
   * The possible outcomes of an attempted page crawl
   * @static
   * @readonly
   * @returns {object} the crawl statuses
   */
  static get statuses() {
    return CRAWL_STATUSES;
  }

  /**
   * The default configuration for the SiteCrawler
   * @static
   * @returns {SiteCrawlerConfig} the default configuration
   */
  static get defaultConfig() {
    return DEFAULT_CONFIG;
  }

  /**
   * The default libraries for the SiteCrawler
   * @static
   * @readonly
   * @returns {SiteCrawlerLibraries} the default libraries
   */
  static get defaultLibraries() {
    return DEFAULT_LIBRARIES;
  }

  /**
   * The origin part of the URL
   * @readonly
   * @returns {string} the origin component of the url
   */
  get origin() {
    const url = new URL(this.config.startPage);

    return url.origin;
  }

  /**
   * The host part of the URL
   * @readonly
   * @returns {string} the host part of the url object
   */
  get host() {
    const url = new URL(this.config.startPage);

    return url.host;
  }

  /**
   * The name of the file the sitecrawler exports to
   * @readonly
   * @returns {string} the filename to use (the url with http and slashes removed)
   */
  get exportFileName() {
    return this.origin.replace(/https?:\/\//gi, '');
  }

  /**
   * @typedef {object} SiteUrl
   * @property {string} loc - a url to a page on a site
   */

  /**
   * formated collection of links to look like jsonified sitemap
   * @property {SiteUrl[]} urlset - an array of SiteUrls
   * @returns {SiteUrl[]} a collection of urls
   */
  get urlset() {
    const linkArray = [...this.linkSet]
      .map((link) => {
        const url = link.indexOf(this.host) === -1
          ? `${this.origin}${link}`
          : link;
        return {
          loc: url,
        };
      });
    return linkArray;
  }

  /**
   * provides a fully qualified path to the sitemap json file
   * @readonly
   * @property {string} pathToExportedFile - the path where the exported file will be placed
   * @returns {string} the fully qualified path for the export file
   */
  get pathToExportedFile() {
    return Path.join(process.cwd(), `${this.exportFileName}.${this.outputter.defaultOutputFile}`);
  }

  /**
   * Determines if the links have already been exported to a file
   * @type {boolean}
   */
  get hasExportedLinks() {
    return fs.existsSync(this.pathToExportedFile);
  }

  /**
   * A canonical key for a link so that a relative and a fully qualified version
   * of the same page are recognized as the same page
   * @param  {string} link - an href value (relative or fully qualified)
   * @returns {string} a canonical key for the link
   */
  getLinkKey(link) {
    try {
      return new URL(link, this.origin).href;
    } catch (linkKeyError) {
      return link;
    }
  }

  /**
   * The results of the most recent crawl, one entry per attempted page
   * @readonly
   * @returns {CrawlResult[]} the per-page crawl results
   */
  get crawlReport() {
    return this.crawlResults;
  }

  /**
   * Counts of the outcomes of the most recent crawl
   * @readonly
   * @returns {{total: number, succeeded: number, failed: number,
   *   failures: CrawlResult[]}} a summary of the crawl
   */
  get crawlSummary() {
    const failures = this.crawlResults.filter(
      (result) => result.status !== CRAWL_STATUSES.success,
    );

    return {
      total: this.crawlResults.length,
      succeeded: this.crawlResults.length - failures.length,
      failed: failures.length,
      failures,
    };
  }

  /**
   * adds multiple items to the linkSet property
   * @param  {string[]|object[]} linkArray an array of href values, or objects with a loc property
   */
  addLinks(linkArray) {
    const cleanArray = linkArray.map((link) => {
      if (typeof link === 'string') {
        return link;
      }
      if (typeof link === 'object' && link.loc) {
        return link.loc;
      }
      return '';
    });
    // A relative and a fully qualified version of the same page are the same page
    const linkKeys = new Set([...this.linkSet].map((link) => this.getLinkKey(link)));
    const newLinks = cleanArray.filter((link) => {
      if (!link) return false;
      const key = this.getLinkKey(link);
      if (linkKeys.has(key)) return false;
      linkKeys.add(key);
      return true;
    });

    this.linkSet = new Set([...this.linkSet, ...newLinks]);
  }

  /**
   * Gets an HTML page
   * @static
   * @async
   * @param  {string|URL} url - fully qualified url to page
   * @param  {Axios} [ajax=this.defaultLibraries.ajax] - ajax library
   * @returns {string} HTML markup as a string
   */
  static async getPageAsync(url, ajax = this.defaultLibraries.ajax) {
    let result = null;

    if (!url) throw new Error('A url was not provided');
    try {
      result = await SiteCrawler.fetchPageAsync(url, ajax);
    } catch (getFileError) {
      await log.errorToFileAsync(getFileError);
    }
    return result;
  }

  /**
   * Gets an HTML page, throwing when the request fails so that callers can
   * distinguish a failed request from a page with no links
   * @static
   * @async
   * @param  {string|URL} url - fully qualified url to page
   * @param  {Axios} [ajax=this.defaultLibraries.ajax] - ajax library
   * @returns {string} HTML markup as a string
   */
  static async fetchPageAsync(url, ajax = this.defaultLibraries.ajax) {
    if (!url) throw new Error('A url was not provided');

    const { data } = await ajax(url);

    return data;
  }

  /**
   * makes an ajax request for a url
   * @param  {string} url - the url from which to request a file
   * @returns {object} Result of the request
   */
  async getFileAsync(url) {
    let result = null;
    try {
      const { data } = await this.libraries.ajax(url);
      result = data;
    } catch (getFileError) {
      await log.errorToFileAsync(getFileError);
    }
    return result;
  }

  /**
   * Gets an XML Sitemap
   * @param  {string} [sitemapUrl=this.config.startPage] fully qualified url
   * @returns {object} parsed xml
   */
  async getSitemapAsync(sitemapUrl = this.config.startPage) {
    let parsedXml = null;
    try {
      const data = await this.getFileAsync(sitemapUrl);
      const parser = new this.libraries.Parser();
      parsedXml = await parser.parseStringPromise(data);
    } catch (getSitemapError) {
      await log.errorToFileAsync(getSitemapError);
      await log.errorToConsoleAsync(
        `Couldn't get the sitemap:\n ${getSitemapError}`,
      );
    }
    return parsedXml;
  }

  /**
   * gets links to pages from a sitemap
   * @param  {object} sitemapJson - A json object that represents the sitemap
   * @returns {string[]} an array of href values to sitemaps
   */
  static getLinksFromSitemap(sitemapJson) {
    if (!sitemapJson) throw new Error('Sitemap JSON was not provided');
    if (!sitemapJson.urlset) return [];
    const pageLinks = sitemapJson
      .urlset
      .url // note: each url node in the xml becomes object in an array called url
      .map((urlObject) => urlObject.loc[0]);

    return pageLinks;
  }

  /**
   * gets links to sitemaps from a sitemap
   * @param  {object} sitemapJson sitemapJson - A json object that represents the sitemap
   * @returns {string[]} an array of href values to sitemaps
   */
  static getSitemapsFromSitemap(sitemapJson) {
    if (!sitemapJson) throw new Error('Sitemap JSON was not provided');
    if (!sitemapJson.sitemapindex) return [];
    const sitemapLinks = sitemapJson
      .sitemapindex
      .sitemap
      .map((urlObject) => urlObject.loc[0]);

    return sitemapLinks;
  }

  /**
   * Gets only links from a string containing markup
   * @static
   * @param  {string} pageMarkup string containing markup
   * @param  {string} [linkSelector=this.defaultConfig.linkSelector] selector to find links
   * @param  {Cheerio} [dom=this.defaultLibraries.dom] Dom querying library
   * @returns {string[]} array of href values
   */
  static getLinksFromMarkup(
    pageMarkup,
    linkSelector = this.defaultConfig.linkSelector,
    dom = this.defaultLibraries.dom,
  ) {
    if (!pageMarkup) throw new Error('Markup was not provided');
    let pageLinks = [];

    try {
      pageLinks = SiteCrawler.extractLinksFromMarkup(pageMarkup, linkSelector, dom);
    } catch (getLinksFromMarkupError) {
      log.toConsole(getLinksFromMarkupError);
    }

    return pageLinks;
  }

  /**
   * Gets only links from a string containing markup, throwing if the markup cannot be parsed
   * @static
   * @param  {string} pageMarkup string containing markup
   * @param  {string} [linkSelector=this.defaultConfig.linkSelector] selector to find links
   * @param  {Cheerio} [dom=this.defaultLibraries.dom] Dom querying library
   * @returns {string[]} array of href values
   */
  static extractLinksFromMarkup(
    pageMarkup,
    linkSelector = this.defaultConfig.linkSelector,
    dom = this.defaultLibraries.dom,
  ) {
    if (!pageMarkup) throw new Error('Markup was not provided');

    const $ = dom.load(pageMarkup);
    const nodes = $(linkSelector);

    return [...nodes].map((node) => node.attribs.href);
  }

  /**
   * Summarizes an error (or thrown value) into a short, single-line reason
   * @static
   * @param {Error|string} error - an error or any thrown value
   * @returns {string} a short summary of the error
   */
  static summarizeError(error) {
    if (!error) return 'Unknown error';

    const message = typeof error === 'object' && error.message
      ? error.message
      : String(error);

    return message.replace(/\s+/g, ' ').trim().slice(0, 200);
  }

  /**
   * Filters an array of links (removes duplicates, external urls, and anchor links)
   * @param  {string[]} pageLinks links to pages
   * @param  {string} siteOrigin the origin of the website
   * @returns {string[]} an array of href values
   */
  static filterPageLinks(pageLinks, siteOrigin) {
    if (!Array.isArray(pageLinks)) throw new Error('pageLinks is not an array');
    if (!siteOrigin) throw new Error('No site origin is provided');

    const filteredLinks = pageLinks.filter((pageLink) => {
      if (typeof pageLink !== 'string') return false;
      const isFullyQualifiedSiteLink = pageLink.indexOf(siteOrigin) === 0;
      const isRelativeLink = pageLink.indexOf('/') === 0;
      const isAnchorLink = pageLink.indexOf('#') === 0;
      return !isAnchorLink && (isFullyQualifiedSiteLink || isRelativeLink);
    });
    // A relative and a fully qualified version of the same page are the same page
    const seenLinks = new Set();
    const uniqueLinks = filteredLinks.filter((pageLink) => {
      let canonicalLink = pageLink;
      try {
        canonicalLink = new URL(pageLink, siteOrigin).href;
      } catch (canonicalLinkError) {
        canonicalLink = pageLink;
      }
      if (seenLinks.has(canonicalLink)) return false;
      seenLinks.add(canonicalLink);
      return true;
    });

    return uniqueLinks;
  }

  /**
   * Gets array of href values from a page
   * @async
   * @param  {string} url fully qualified url to page
   * @returns {string[]} an array of href values
   */
  async getLinksFromPageAsync(url) {
    let links = [];

    try {
      const { linkSelector } = this.config;
      const { ajax, dom } = this.libraries;
      const pageMarkup = await SiteCrawler.getPageAsync(url, ajax);
      const pageLinks = SiteCrawler.getLinksFromMarkup(pageMarkup, linkSelector, dom);
      links = SiteCrawler.filterPageLinks(pageLinks, this.origin);
    } catch (getLinksError) {
      await log.errorToFileAsync(getLinksError);
    }
    return links;
  }

  /**
   * Attempts to crawl a single page, reporting what happened without throwing
   * @private
   * @async
   * @param  {string} url fully qualified url to page
   * @returns {Promise<CrawlResult>} the outcome for this page
   */
  async attemptPageCrawlAsync(url) {
    const { linkSelector } = this.config;
    const { ajax, dom } = this.libraries;
    let pageMarkup;

    try {
      pageMarkup = await SiteCrawler.fetchPageAsync(url, ajax);
    } catch (requestError) {
      await log.errorToFileAsync(requestError);

      return {
        url,
        status: CRAWL_STATUSES.requestFailed,
        linkCount: 0,
        reason: SiteCrawler.summarizeError(requestError),
      };
    }

    try {
      const pageLinks = SiteCrawler.extractLinksFromMarkup(pageMarkup, linkSelector, dom);
      const siteLinks = SiteCrawler.filterPageLinks(pageLinks, this.origin);
      this.addLinks(siteLinks);

      return {
        url,
        status: CRAWL_STATUSES.success,
        linkCount: siteLinks.length,
      };
    } catch (linkParseError) {
      await log.errorToFileAsync(linkParseError);

      return {
        url,
        status: CRAWL_STATUSES.linkParseFailed,
        linkCount: 0,
        reason: SiteCrawler.summarizeError(linkParseError),
      };
    }
  }

  /**
   * Crawls a page, records its outcome, and adds links to the linkSet when successful
   * @async
   * @param  {string} url fully qualified usrl to page
   * @returns {Promise<CrawlResult>} the outcome for this page
   */
  async crawlPageAsync(url) {
    let result;
    try {
      result = await this.attemptPageCrawlAsync(url);
    } catch (crawlPageError) {
      await log.errorToFileAsync(crawlPageError);
      result = {
        url,
        status: CRAWL_STATUSES.linkParseFailed,
        linkCount: 0,
        reason: SiteCrawler.summarizeError(crawlPageError),
      };
    }

    this.crawlResults.push(result);

    return result;
  }

  /**
   * Removes links that failed to crawl from the searchable link pool
   * @private
   * @returns {void}
   */
  removeFailedLinks() {
    this.crawlResults.forEach((result) => {
      if (result.status === CRAWL_STATUSES.success) return;

      const key = this.getLinkKey(result.url);
      [...this.linkSet].forEach((link) => {
        if (this.getLinkKey(link) === key) {
          this.linkSet.delete(link);
        }
      });
    });
  }

  /**
   * Crawls entire site looking for links
   * @async
   * @param  {string} url=this.config.startPage - the page where the crawler begins its crawl
   * @returns {Promise<CrawlResult[]>} the per-page outcomes for this crawl
   */
  async crawlSiteAsync(url = this.config.startPage) {
    this.crawlResults = [];
    const attemptedUrls = new Set([this.getLinkKey(url)]);
    await this.crawlPageAsync(url);

    const pagesToCrawl = this.urlset;
    await forEachAsync(pagesToCrawl, async (urlObject) => {
      const pageKey = this.getLinkKey(urlObject.loc);
      if (attemptedUrls.has(pageKey)) return;
      attemptedUrls.add(pageKey);
      await this.crawlPageAsync(urlObject.loc);
    });

    this.removeFailedLinks();

    return this.crawlReport;
  }

  /**
   * Exports all of the collected site links to a file
   * @async
   * @param {string} [fileName=this.exportFileName] Name of file to be concatenated to sitemap.json
   * @returns {Promise<void>}
   */
  async exportSiteLinks(fileName = this.exportFileName) {
    try {
      await this.outputter.writeDataAsync(this.urlset, fileName);
    } catch (exportSiteLinksError) {
      await log.errorToFileAsync(exportSiteLinksError);
    }
  }

  /**
   * Crawls. Wrapper for crawl, in case other functionality should be added
   * @param  {string} startPage=this.config.startPage - the page where the crawler begins its crawl
   * @returns {Promise<CrawlResult[]>} the per-page outcomes for this crawl
   */
  async crawl(startPage = this.config.startPage) {
    try {
      return await this.crawlSiteAsync(startPage);
    } catch (crawlError) {
      await log.errorToFileAsync(crawlError);
      return this.crawlReport;
    }
  }

  /**
   * Fetches a sitemap and returns the links from it
   * @param  {string} [sitemapUrl=this.config.startPage] - the page where the crawler begins its crawl OR the sitemap
   * @returns {string[]} an array of href values
   */
  async getSitemapLinks(sitemapUrl = this.config.startPage) {
    let sitemapUrls = [];
    let nestedSitemaps = [];
    try {
      const sitemapJson = await this.getSitemapAsync(sitemapUrl);
      sitemapUrls = SiteCrawler.getLinksFromSitemap(sitemapJson);
      nestedSitemaps = SiteCrawler.getSitemapsFromSitemap(sitemapJson);

      if (nestedSitemaps.length > 0) {
        await forEachAsync(nestedSitemaps, async (nestedSitemap) => {
          const nestedSitemapLinks = await this.getSitemapLinks(nestedSitemap);
          sitemapUrls = [...sitemapUrls, ...nestedSitemapLinks];
        });
      }
    } catch (setSitemapError) {
      await log.errorToFileAsync(setSitemapError);
    }

    return sitemapUrls;
  }

  /**
   * Fetches a sitemap and adds links to linkset
   * @async
   * @param  {string} [sitemapUrl=this.config.startPage] - the page where the crawler gets a sitemap
   */
  async setSitemap(sitemapUrl = this.config.startPage) {
    this.config.startPage = sitemapUrl;

    try {
      const sitemapUrls = await this.getSitemapLinks(sitemapUrl);
      this.addLinks(sitemapUrls);
    } catch (setSitemapError) {
      await this.errorToFileAsync(setSitemapError);
    }
  }

  /**
   * sets links from an existing json file
   * @async
   * @param  {string} fileName - the filename from which the crawler will read JSON and set links
   */
  async setLinksFromJsonFile(fileName) {
    if (!fileName) return;
    try {
      const existingJson = await fs.promises.readFile(fileName, 'utf-8');
      const existingSiteLinks = JSON.parse(existingJson);
      this.addLinks(existingSiteLinks);
    } catch (setLinksError) {
      await this.errorToFileAsync(setLinksError);
    }
  }

  /**
   * wrapper for crawl and setSitemap that also produces export file
   * @async
   * @param  {boolean} [shouldCrawl=this.config.shouldCrawl] - whether it should crawl
   * @param {boolean} [useExportedSitemap=this.config.useExportedSitemap] - use existing file if already exists
   * @returns {Promise<CrawlResult[]>} the per-page outcomes when crawling (empty otherwise)
   */
  async produceSiteLinks(
    shouldCrawl = this.config.shouldCrawl,
    useExportedSitemap = this.config.useExportedSitemap,
  ) {
    const shouldNotProduceLinks = useExportedSitemap && this.hasExportedLinks;
    if (shouldNotProduceLinks) {
      const alreadyExistsMessage = `📂  file ${this.pathToExportedFile} already exists and recrawling was not forced.`;
      await log.infoToFileAsync(alreadyExistsMessage);
      log.toConsole(alreadyExistsMessage);
      await this.setLinksFromJsonFile(`${this.exportFileName}.${this.outputter.defaultOutputFile}`);
      return this.crawlReport;
    }

    let crawlResults = [];
    if (shouldCrawl) {
      crawlResults = await this.crawl();
    } else {
      await this.setSitemap(this.config.startPage);
    }
    await this.exportSiteLinks(this.exportFileName);

    return crawlResults;
  }
}
