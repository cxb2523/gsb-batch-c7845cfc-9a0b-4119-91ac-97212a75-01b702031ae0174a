import { type Page } from "playwright-core";
import { BrowserService } from "./BrowserService.js";
import ContentExtractor from "./ContentExtractor.js";
import FileService from "./FileService.js";

export interface ScraperOptions {
  depth?: number;
  format?: "md" | "json" | "csv";
  delay?: number;
  outputPath?: string;
  selectors?: string[];
  tags?: string;
}

export interface FailedPage {
  path: string;
  error: string;
}

export interface CrawlResult {
  success: string[];
  failed: FailedPage[];
}

class Scraper {
  private website: string;
  private baseSegments: number;
  private depth: number;
  private format: "md" | "json" | "csv";
  private delay: number;
  private selectors: string[];
  private tags: string | undefined;
  private fileService: FileService;
  private browserService: BrowserService;

  constructor(website: string, options?: ScraperOptions) {
    this.website = website;
    const url = new URL(website);
    const basePath = url.pathname === "/" ? "/" : url.pathname.replace(/\/+$/, "");
    this.baseSegments = basePath === "/" ? 0 : basePath.split("/").filter(Boolean).length;
    this.depth = options?.depth ?? -1;
    this.format = options?.format ?? "md";
    this.delay = options?.delay ?? 0;
    this.selectors = options?.selectors ?? [];
    this.tags = options?.tags || undefined;
    this.fileService = new FileService(options?.outputPath);
    this.browserService = new BrowserService();
  }

  private relativeDepth(path: string): number {
    const normalized = path === "/" ? "/" : path.replace(/\/+$/, "");
    if (normalized === "/") return 0;
    const segments = normalized.split("/").filter(Boolean).length;
    return segments - this.baseSegments;
  }

  private toErrorMessage(error: unknown): string {
    if (error instanceof Error && error.message) return error.message;
    try {
      const message = String(error);
      return message === "[object Object]" ? "Unknown crawl error" : message;
    } catch {
      return "Unknown crawl error";
    }
  }

  private async processPage(page: Page, endpoint: string): Promise<string[]> {
    const originUrl = new URL(this.website).origin;
    const fullUrl = `${originUrl}${endpoint}`;

    await page.goto(fullUrl, { waitUntil: "networkidle", timeout: 60000 });

    const extractor = new ContentExtractor(page);
    const basicDetails = await extractor.getBasicDetails();
    const structuredContent = await extractor.getStructuredContent(this.tags);

    if (this.selectors.length > 0) {
      const selectorContent = await extractor.getContentBySelectors(this.selectors);
      this.fileService.saveCsv(this.website, endpoint, extractor.toCsv(selectorContent));
    }

    if (this.format === "json") {
      this.fileService.saveJson(this.website, endpoint, {
        metadata: basicDetails,
        content: structuredContent,
      });
    } else if (this.format === "csv") {
      this.fileService.saveCsv(this.website, endpoint, extractor.toCsv(structuredContent));
    } else {
      this.fileService.saveMarkdown(
        this.website,
        endpoint,
        extractor.toMarkdown(basicDetails, structuredContent),
      );
    }

    const links = await page.$$eval("a[href]", (elements, website) => {
      const baseUrl = new URL(website);
      const uniquePaths = new Set<string>();
      for (const el of elements) {
        try {
          const href = el.getAttribute("href");
          if (!href) continue;
          const linkUrl = new URL(href, website);
          if (linkUrl.href.includes(baseUrl.href)) {
            let path = linkUrl.pathname;
            if (path !== "/" && path.endsWith("/")) path = path.slice(0, -1);
            uniquePaths.add(path);
          }
        } catch {}
      }
      return Array.from(uniquePaths);
    }, this.website);

    return links;
  }

  /**
   * Shared traversal used by both BFS and DFS. A failure on a single page
   * (navigation timeout, extraction error, ...) is recorded and the crawl
   * continues with the remaining links. Failed paths are marked as visited
   * before processing, so they can never be enqueued again. Every page and
   * its context are closed regardless of outcome.
   */
  private async crawl(
    start: string,
    mode: "bfs" | "dfs",
  ): Promise<CrawlResult> {
    const visited: Record<string, boolean> = {};
    const success: string[] = [];
    const failed: FailedPage[] = [];
    const pending: string[] = [start];

    while (pending.length > 0) {
      const current = mode === "bfs" ? pending.shift() : pending.pop();
      if (!current || visited[current]) continue;
      if (this.depth !== -1 && this.relativeDepth(current) > this.depth) continue;

      // Count the attempt up-front: failed pages stay visited and are
      // never re-enqueued by links discovered on other pages.
      visited[current] = true;

      let page: Page | null = null;
      try {
        page = await this.browserService.getPage();
        const links = await this.processPage(page, current);
        success.push(current);

        const freshLinks = links.filter((link) => !visited[link]);
        if (mode === "dfs") freshLinks.reverse();
        pending.push(...freshLinks);
      } catch (error) {
        failed.push({ path: current, error: this.toErrorMessage(error) });
      } finally {
        if (page) {
          try {
            await this.browserService.closePage(page);
          } catch (closeError) {
            // Closing must never abort the crawl; the page/context may
            // already have been torn down by the browser itself.
            console.warn(
              `Failed to close page for ${current}: ${this.toErrorMessage(closeError)}`,
            );
          }
        }
      }

      if (this.delay > 0) await new Promise((r) => setTimeout(r, this.delay));
    }

    return { success, failed };
  }

  async bfs(endpoint: string = "/"): Promise<CrawlResult> {
    return this.crawl(endpoint, "bfs");
  }

  async dfs(endpoint: string = "/"): Promise<CrawlResult> {
    return this.crawl(endpoint, "dfs");
  }

  async close(): Promise<void> {
    await this.browserService.closeAll();
  }
}

export default Scraper;
