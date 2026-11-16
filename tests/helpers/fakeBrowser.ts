import type { Page } from "playwright-core";
import type { IBrowserService } from "../../src/types/index.js";

export interface FakeElement {
  tag: string;
  text?: string;
  href?: string;
  src?: string;
  alt?: string;
}

export interface FakePageConfig {
  title: string;
  /** href values rendered as <a href="..."> on the page */
  links?: string[];
  /** elements returned for structured-content extraction */
  elements?: FakeElement[];
  /** when set, page.goto() rejects with this error (e.g. timeout) */
  gotoError?: Error;
  /** when set, structured-content extraction rejects with this error */
  extractError?: Error;
}

interface SyntheticElement {
  tagName: string;
  textContent: string;
  getAttribute(name: string): string | null;
}

function toSynthetic(el: FakeElement): SyntheticElement {
  return {
    tagName: el.tag.toUpperCase(),
    textContent: el.text ?? "",
    getAttribute(name: string) {
      if (name === "href") return el.href ?? null;
      if (name === "src") return el.src ?? null;
      if (name === "alt") return el.alt ?? null;
      return null;
    },
  };
}

/**
 * Minimal in-memory Playwright Page stand-in. It implements only the
 * surface touched by Scraper.processPage / ContentExtractor and runs the
 * same callbacks the real browser would receive.
 */
export class FakePage {
  public closed = false;
  private currentPath = "";
  private currentUrl = "";

  constructor(
    private readonly site: Record<string, FakePageConfig>,
    private readonly visitCounts: Map<string, number>,
  ) {}

  async goto(fullUrl: string): Promise<void> {
    const pathname = new URL(fullUrl).pathname;
    this.currentPath = pathname;
    this.currentUrl = fullUrl;
    this.visitCounts.set(pathname, (this.visitCounts.get(pathname) ?? 0) + 1);

    const config = this.site[pathname];
    if (!config) throw new Error(`net::ERR_ABORTED; no fake page for ${pathname}`);
    if (config.gotoError) throw config.gotoError;
  }

  async title(): Promise<string> {
    return this.site[this.currentPath]?.title ?? "";
  }

  url(): string {
    return this.currentUrl;
  }

  // Playwright's $eval throws when no element matches; the production code
  // relies on that rejection (each call is wrapped in .catch).
  async $eval(): Promise<never> {
    throw new Error("Error: failed to find element matching selector");
  }

  async $$eval<T>(
    selector: string,
    // The callback is browser-side code in production, but it is a plain
    // function, so it can be exercised directly with synthetic elements.
    callback: (elements: SyntheticElement[], arg?: string) => T,
    arg?: string,
  ): Promise<T> {
    const config = this.site[this.currentPath];
    if (!config) throw new Error(`no fake page for ${this.currentPath}`);

    if (selector === "a[href]") {
      const anchors: SyntheticElement[] = (config.links ?? []).map((href) =>
        toSynthetic({ tag: "a", text: `link-${href}`, href }),
      );
      return callback(anchors, arg);
    }

    if (selector.startsWith("meta")) {
      return callback([], arg);
    }

    if (config.extractError) throw config.extractError;
    return callback((config.elements ?? []).map(toSynthetic), arg);
  }
}

export interface FakeBrowserOptions {
  /** closePage() rejects, simulating a torn-down context */
  closeShouldThrow?: boolean;
  /** getPage() rejects, simulating browser/context launch failure */
  getPageShouldThrow?: boolean;
}

/**
 * Controllable IBrowserService: no real browser, no network. Every issued
 * page is tracked so tests can assert that all pages/contexts are closed.
 */
export class FakeBrowserService implements IBrowserService {
  public openedPages = 0;
  public closedPages = 0;
  public readonly pages: FakePage[] = [];
  public readonly visitCounts = new Map<string, number>();

  constructor(
    private readonly site: Record<string, FakePageConfig>,
    private readonly options: FakeBrowserOptions = {},
  ) {}

  async getPage(): Promise<Page> {
    if (this.options.getPageShouldThrow) {
      throw new Error("Failed to launch new browser context");
    }
    this.openedPages += 1;
    const page = new FakePage(this.site, this.visitCounts);
    this.pages.push(page);
    return page as unknown as Page;
  }

  async closePage(page: Page): Promise<void> {
    this.closedPages += 1;
    if (this.options.closeShouldThrow) {
      throw new Error("Target page, context or browser has been closed");
    }
    (page as unknown as FakePage).closed = true;
  }

  async closeAll(): Promise<void> {
    for (const page of this.pages) page.closed = true;
  }
}
