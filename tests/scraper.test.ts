import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import Scraper from "../src/Services/Scraper.js";
import type { BrowserService } from "../src/Services/BrowserService.js";
import {
  FakeBrowserService,
  type FakePageConfig,
} from "./helpers/fakeBrowser.js";

const ORIGIN = "https://example.test";

/**
 * Site map:
 *   /        -> home, links to /a /b /c /e plus a self-link and an
 *               off-origin link (both must be filtered/ignored)
 *   /a       -> successful child, links to /d and (again) /b
 *   /b       -> navigation timeout (fails), linked from /, /a and /c so we
 *               can prove a failed path is processed once and never re-queued
 *   /c       -> successful child, also links back to the failing /b
 *   /d       -> successful grandchild
 *   /e       -> fails later, during structured-content extraction
 */
function buildSite(): Record<string, FakePageConfig> {
  return {
    "/": {
      title: "Home",
      links: ["/a", "/b", "/c", "/e", "/", "https://other.example.org/x"],
      elements: [{ tag: "h1", text: "Home heading" }],
    },
    "/a": {
      title: "Page A",
      links: ["/b", "/d"],
      elements: [{ tag: "p", text: "A content" }],
    },
    "/b": {
      title: "Page B",
      gotoError: new Error("page.goto: Timeout 60000ms exceeded"),
      elements: [{ tag: "p", text: "never reached" }],
    },
    "/c": {
      title: "Page C",
      links: ["/b"],
      elements: [{ tag: "p", text: "C content" }],
    },
    "/d": {
      title: "Page D",
      links: [],
      elements: [{ tag: "p", text: "D content" }],
    },
    "/e": {
      title: "Page E",
      links: [],
      extractError: new Error("Content extraction exploded"),
      elements: [{ tag: "p", text: "never reached" }],
    },
  };
}

let outputRoot: string;

function makeScraper(
  site: Record<string, FakePageConfig>,
  options: {
    closeShouldThrow?: boolean;
    getPageShouldThrow?: boolean;
    format?: "md" | "json" | "csv";
  } = {},
): { scraper: Scraper; browser: FakeBrowserService; outputDir: string } {
  const outputDir = fs.mkdtempSync(
    path.join(os.tmpdir(), "crawler-test-"),
  );
  const browser = new FakeBrowserService(site, {
    closeShouldThrow: options.closeShouldThrow,
    getPageShouldThrow: options.getPageShouldThrow,
  });
  const scraper = new Scraper(ORIGIN + "/", {
    outputPath: outputDir,
    format: options.format ?? "md",
    delay: 0,
  });
  // Inject the offline fake browser without changing the constructor.
  (scraper as unknown as { browserService: BrowserService }).browserService =
    browser as unknown as BrowserService;
  return { scraper, browser, outputDir };
}

beforeEach(() => {
  outputRoot = fs.mkdtempSync(path.join(os.tmpdir(), "crawler-root-"));
});

afterEach(() => {
  fs.rmSync(outputRoot, { recursive: true, force: true });
});

describe("Scraper fault isolation", () => {
  it("BFS: one failing child does not stop the crawl and records both lists", async () => {
    const { scraper, browser, outputDir } = makeScraper(buildSite());

    const result = await scraper.bfs("/");
    await scraper.close();

    // BFS order: home, then /a /b /c /e in link order, then /d from /a.
    assert.deepEqual(result.success, ["/", "/a", "/c", "/d"]);

    // /b fails on navigation; /e fails later during extraction.
    assert.equal(result.failed.length, 2);
    assert.deepEqual(
      result.failed.map((f) => f.path),
      ["/b", "/e"],
    );
    for (const failure of result.failed) {
      assert.equal(typeof failure.error, "string");
      assert.ok(failure.error.length > 0);
    }
    const bFailure = result.failed.find((f) => f.path === "/b")!;
    assert.match(bFailure.error, /Timeout/);
    const eFailure = result.failed.find((f) => f.path === "/e")!;
    assert.match(eFailure.error, /Content extraction exploded/);

    // Failed paths are visited exactly once despite being linked three times
    // (/b is linked from /, /a and /c); /e once.
    assert.equal(browser.visitCounts.get("/b"), 1);
    assert.equal(browser.visitCounts.get("/e"), 1);
    assert.equal(browser.visitCounts.get("/"), 1);

    // Every opened page (one per visited path: 6) is closed exactly once.
    assert.equal(browser.openedPages, 6);
    assert.equal(browser.closedPages, 6);
    assert.equal(browser.pages.length, 6);
    assert.ok(browser.pages.every((p) => p.closed));

    // Output files exist only for successful paths. Naming follows the
    // existing FileService convention ("/a" -> "_a.md", "/" -> "home.md").
    const folder = path.join(outputDir, "example_test");
    const written = fs.readdirSync(folder).sort();
    assert.deepEqual(written, ["_a.md", "_c.md", "_d.md", "home.md"]);
    assert.ok(!fs.existsSync(path.join(folder, "_b.md")));
    assert.ok(!fs.existsSync(path.join(folder, "_e.md")));
  });

  it("DFS: behaves consistently with BFS, same entries in DFS visit order", async () => {
    const { scraper, browser } = makeScraper(buildSite());

    const result = await scraper.dfs("/");
    await scraper.close();

    // Iterative DFS pushes reversed links so the first link is popped first:
    // / -> /a -> /b(fail) -> /d, back to /c -> /b already visited, then /e.
    assert.deepEqual(result.success, ["/", "/a", "/d", "/c"]);
    assert.deepEqual(
      result.failed.map((f) => f.path),
      ["/b", "/e"],
    );
    for (const failure of result.failed) {
      assert.equal(typeof failure.path, "string");
      assert.equal(typeof failure.error, "string");
    }

    assert.equal(browser.visitCounts.get("/b"), 1);
    assert.equal(browser.openedPages, 6);
    assert.equal(browser.closedPages, 6);
    assert.ok(browser.pages.every((p) => p.closed));
  });

  it("root page failure: returns empty success set, one failure, no unhandled throw", async () => {
    const site = buildSite();
    site["/"] = {
      title: "Home",
      gotoError: new Error("page.goto: net::ERR_CONNECTION_REFUSED"),
      links: ["/a"],
    };
    const { scraper, browser } = makeScraper(site);

    const result = await scraper.bfs("/");
    await scraper.close();

    assert.deepEqual(result.success, []);
    assert.equal(result.failed.length, 1);
    assert.equal(result.failed[0]!.path, "/");
    assert.match(result.failed[0]!.error, /ERR_CONNECTION_REFUSED/);

    // Nothing was enqueued from the failed root.
    assert.equal(browser.openedPages, 1);
    assert.equal(browser.closedPages, 1);
    assert.ok(browser.pages[0]!.closed);
  });

  it("getPage() failure is isolated per page as well", async () => {
    const site = buildSite();
    const { scraper, browser } = makeScraper(site, {
      getPageShouldThrow: true,
    });

    const result = await scraper.dfs("/");
    await scraper.close();

    assert.deepEqual(result.success, []);
    assert.equal(result.failed.length, 1);
    assert.equal(result.failed[0]!.path, "/");
    assert.match(result.failed[0]!.error, /Failed to launch new browser context/);
    assert.equal(browser.closedPages, 0);
  });

  it("still closes every page/context when closePage itself throws", async () => {
    const { scraper, browser } = makeScraper(buildSite(), {
      closeShouldThrow: true,
    });

    const result = await scraper.bfs("/");
    await scraper.close();

    assert.deepEqual(result.success, ["/", "/a", "/c", "/d"]);
    assert.equal(result.failed.length, 2);

    // closePage was attempted for every visited page; errors there must not
    // surface or interrupt the crawl.
    assert.equal(browser.openedPages, 6);
    assert.equal(browser.closedPages, 6);
  });

  it("json format keeps writing outputs for successful pages after a failure", async () => {
    const { scraper, outputDir } = makeScraper(buildSite(), {
      format: "json",
    });

    const result = await scraper.bfs("/");
    await scraper.close();

    assert.deepEqual(result.success, ["/", "/a", "/c", "/d"]);
    const folder = path.join(outputDir, "example_test");
    const written = fs.readdirSync(folder).sort();
    assert.deepEqual(written, ["_a.json", "_c.json", "_d.json", "home.json"]);
    const home = JSON.parse(
      fs.readFileSync(path.join(folder, "home.json"), "utf-8"),
    );
    assert.equal(home.metadata.title, "Home");
  });
});
