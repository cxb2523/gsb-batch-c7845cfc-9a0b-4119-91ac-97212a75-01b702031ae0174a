// import

import type {Browser, Page} from 'playwright';
import fs from 'fs/promises';


// types

/**
 * Every independently-settled collection stage. Stages run in this order, but a
 * throw is recorded and settled instead of aborting the whole batch, so a later
 * stage still gets its turn.
 */
export type StageName =
  | 'workspace'
  | 'navigate'
  | 'form'
  | 'screenshot'
  | 'pdf'
  | 'video'
  | 'footerLinks';

export interface FooterLink {
  text: string;
  link: string;
}

/** A raw anchor candidate, before it has been validated. */
export interface FooterLinkCandidate {
  text?: string | null | undefined;
  link?: string | null | undefined;
}

/** A single bad footer entry that was skipped while the rest were kept. */
export interface FooterLinkProblem {
  error: string;
  text: string | null;
  href: string | null;
}

export interface OutputPaths {
  imageFile: string;
  pdfFile: string;
  videoFile: string;
}

export type OutputFileKey = keyof OutputPaths;

/**
 * One settled failure. It names the stage, carries the error text, and records
 * what had already been collected when the failure happened, so a caller can
 * tell exactly where the batch broke and what is still usable.
 */
export interface StageFailure {
  /** `stage` for a whole stage; `footerLink` for one bad anchor. */
  scope: 'stage' | 'footerLink';
  stage: StageName;
  error: string;
  /** Names of the stages that had already completed when this was recorded. */
  completed: StageName[];
  /** File outputs already produced when this was recorded (empty if none yet). */
  outputs: OutputPaths;
  /** Populated for `footerLink` failures; null for stage-level failures. */
  text: string | null;
  href: string | null;
}

export interface StageStatus {
  stage: StageName;
  status: 'completed' | 'failed';
  error: string | null;
}

export interface AutomationResult extends OutputPaths {
  footerLinks: FooterLink[];
  failures: StageFailure[];
  /**
   * Output fields whose file was not produced (they are `''` in the result).
   * Callers can check this instead of stat-ing every path.
   */
  missingFiles: OutputFileKey[];
  /** Names of the stages that completed successfully, in execution order. */
  completed: StageName[];
  /** Per-stage settlement record, in execution order. */
  stageResults: StageStatus[];
}

export interface AutomationUrls {
  home: string;
  blog: string;
}

export interface StageContext {
  browser: Browser;
  /**
   * The page shared by the stages. It is created by the first stage that needs
   * it (or supplied through {@link AutomateOptions.page}), so a navigation
   * failure never leaves later stages without a page.
   */
  page: Page | null;
  outputDir: string;
  urls: AutomationUrls;
}

export interface StageOutput {
  imageFile?: string | undefined;
  pdfFile?: string | undefined;
  videoFile?: string | undefined;
  footerLinks?: FooterLink[] | undefined;
  footerFailures?: FooterLinkProblem[] | undefined;
}

export type StageHandler = (context: StageContext) => Promise<StageOutput | void>;

export interface AutomateOptions {
  /** Defaults to `./.output/${browserType}`. */
  outputDir?: string | undefined;
  /** Override either entry point (fixtures, tests, a different site). */
  urls?: Partial<AutomationUrls> | undefined;
  /**
   * Run the stages against an existing page instead of creating one. Handy for
   * tests that serve fixtures through `page.route()` or pre-seed browser state.
   */
  page?: Page | undefined;
  /**
   * Replace individual stages. This is how a caller can retarget or fault-inject
   * a single step without rewriting the whole pipeline.
   */
  stages?: Partial<Record<StageName, StageHandler>> | undefined;
}


// vars

const defaultUrls: AutomationUrls = {
  home: 'https://www.browsercat.com/',
  blog: 'https://www.browsercat.com/blog',
};

const stageOrder: readonly StageName[] = [
  'workspace',
  'navigate',
  'form',
  'screenshot',
  'pdf',
  'video',
  'footerLinks',
];

const fileKeys: readonly OutputFileKey[] = ['imageFile', 'pdfFile', 'videoFile'];

/** Budget for the page's main document, which is the part every stage reads. */
const documentTimeoutMs = 60_000;

/**
 * How long to keep waiting for the `load` event after the DOM is ready. Third
 * party analytics regularly stall it, and nothing in this pipeline needs the
 * load event once the page's own markup exists.
 */
const settleTimeoutMs = 15_000;

/**
 * Navigate to a URL and wait for its DOM, not for every third-party subresource.
 * A stalled analytics request should not burn a whole stage's time budget.
 */
async function gotoReady(page: Page, url: string): Promise<void> {
  await page.goto(url, {waitUntil: 'domcontentloaded', timeout: documentTimeoutMs});
  await page.waitForLoadState('load', {timeout: settleTimeoutMs}).catch(() => undefined);
}


// helpers

function toErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message || error.name : String(error);
  // Playwright decorates call logs with ANSI colors; keep settled errors readable.
  return message.replace(/\u001b\[[0-9;]*m/g, '');
}

/**
 * Hand a stage a page to work with, creating one if navigation never got that
 * far. Each stage stays usable on its own instead of cascading from the
 * navigation stage's failure.
 */
async function ensurePage(context: StageContext): Promise<Page> {
  if (!context.page) {
    context.page = await context.browser.newPage();
  }
  return context.page;
}

/** Make sure the page is showing the blog, which the media and footer stages read. */
async function ensureBlogPage(context: StageContext): Promise<Page> {
  const page = await ensurePage(context);
  if (page.url().includes('/blog')) {
    return page;
  }

  // Prefer the site's own navigation, exactly as the original flow did, and
  // fall back to the URL only if that link is unavailable (for example after an
  // earlier stage failed on a different page).
  const $blogLink = page.locator('header a', {hasText: 'Blog'});
  if ((await $blogLink.count()) > 0) {
    await $blogLink.first().click();
    await page.waitForLoadState('domcontentloaded', {timeout: settleTimeoutMs}).catch(() => undefined);
  }
  if (!page.url().includes('/blog')) {
    await gotoReady(page, context.urls.blog);
  }
  return page;
}

/** Make sure the page is showing the contact form, which the form stage fills. */
async function ensureContactPage(context: StageContext): Promise<Page> {
  const page = await ensurePage(context);
  if (!page.url().includes('/contact')) {
    await gotoReady(page, context.urls.home);
    await page.locator('a:visible', {hasText: 'Contact'}).first().click();
    await page.waitForLoadState('domcontentloaded', {timeout: settleTimeoutMs}).catch(() => undefined);
  }
  return page;
}

function snapshotOutputs(result: OutputPaths): OutputPaths {
  return {
    imageFile: result.imageFile,
    pdfFile: result.pdfFile,
    videoFile: result.videoFile,
  };
}

/**
 * The file each stage normally writes. Used to clean up a half-written artifact
 * when its stage fails, so the output directory never contains a file the
 * result does not report.
 */
const stageArtifacts: Partial<Record<StageName, string>> = {
  screenshot: 'blog-header.png',
  pdf: 'blog-page.pdf',
  video: 'blog-scroll.mp4',
};

/**
 * Check whether an href is well-formed enough to be collected. Beyond asking
 * the URL constructor, reject strings that only "parse" by being treated as a
 * relative path while containing characters a real URL cannot: raw spaces,
 * control characters, invalid percent-encoding, and broken absolute URLs such
 * as `ht!tp://host`. Those are broken data, and silently percent-encoding them
 * would hide the problem.
 */
function isParseableHref(href: string, baseUrl: string): boolean {
  if (/[\u0000- \u007f]/.test(href)) {
    return false;
  }
  if (/%(?![0-9A-Fa-f]{2})/.test(href)) {
    return false;
  }

  // A string that starts with a scheme, or that places `://` before any
  // path/query marker, is meant to stand on its own. If it does not, the href
  // is broken; resolving it as a relative path would silently "rescue" values
  // like `ht!tp://host`. Everything else resolves against the page, just as a
  // browser treats a relative link.
  const looksAbsolute = /^[A-Za-z][A-Za-z0-9+.-]*:/.test(href) || /^[^/?#]*:\/\//.test(href);

  try {
    if (looksAbsolute) {
      new URL(href);
    } else {
      new URL(href, baseUrl);
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * Validate footer anchors one by one. Bad entries (missing text, missing href,
 * or an unparseable href) are skipped and reported while every good entry is
 * kept: one bad link can never poison the batch.
 */
export function parseFooterLinks(
  candidates: readonly FooterLinkCandidate[],
  baseUrl: string,
): {footerLinks: FooterLink[]; failures: FooterLinkProblem[]} {
  const footerLinks: FooterLink[] = [];
  const failures: FooterLinkProblem[] = [];

  for (const candidate of candidates) {
    const text = candidate.text?.trim() ?? '';
    const href = candidate.link?.trim() ?? '';

    if (!text) {
      failures.push({
        error: 'Footer link is missing text',
        text: candidate.text ?? null,
        href: candidate.link ?? null,
      });
      continue;
    }

    if (!href) {
      failures.push({
        error: `Footer link "${text}" is missing an href`,
        text,
        href: candidate.link ?? null,
      });
      continue;
    }

    if (!isParseableHref(href, baseUrl)) {
      failures.push({
        error: `Footer link "${text}" has an href ("${href}") that cannot be parsed as a URL`,
        text,
        href,
      });
      continue;
    }

    footerLinks.push({text, link: href});
  }

  return {footerLinks, failures};
}


// stages

export const defaultStages: Record<StageName, StageHandler> = {
  workspace: async (context) => {
    // Clear stale artifacts first, then make sure the directory exists so every
    // later stage has somewhere to write.
    await fs.rm(context.outputDir, {recursive: true, force: true});
    await fs.mkdir(context.outputDir, {recursive: true});
  },

  navigate: async (context) => {
    const page = await ensurePage(context);
    await gotoReady(page, context.urls.home);
    // The homepage has several "Contact" links (including hidden ones), so
    // target the visible one instead of whichever matches first.
    await page.locator('a:visible', {hasText: 'Contact'}).first().click();
    await page.waitForLoadState('domcontentloaded', {timeout: settleTimeoutMs}).catch(() => undefined);
  },

  form: async (context) => {
    const page = await ensureContactPage(context);
    await page.getByLabel('Name').fill('BrowserCat');
    await page.getByLabel('Email').fill('user@test.browsercat.com');
    await page.getByLabel('Message').fill('This is a simple test.');

    const $sendButton = page.locator('button');
    await $sendButton.filter({hasText: 'Send'}).first().click();

    // Settle on whichever terminal state the form reaches: the "Sent!"
    // confirmation, or the inline error the site shows when a send is rejected.
    // Polling explicitly also means a rejected send settles right away instead
    // of eating the whole run's time budget.
    const deadline = Date.now() + 15_000;
    for (;;) {
      const outcome = await page.evaluate(() => {
        // Scope to the contact form so an unrelated form's state cannot settle
        // this stage by mistake. Only the validation/error slots count; the
        // submit button itself carries x-text too.
        const $form = document.querySelector('form[x-data="contactForm"]');
        const $scope = $form ?? document;
        const buttons = Array.from($scope.querySelectorAll('button'));
        if (buttons.some((button) => /Sent/.test(button.textContent ?? ''))) {
          return {status: 'sent', message: ''};
        }
        const messages = Array.from(
          $scope.querySelectorAll('[x-text*="issues"], [x-text*="error"]'),
        )
          .map((el) => (el.textContent ?? '').trim())
          .filter(Boolean);
        return {status: messages.length > 0 ? 'error' : 'pending', message: messages[0] ?? ''};
      });

      if (outcome.status === 'sent') {
        return;
      }
      if (outcome.status === 'error') {
        throw new Error(`The contact form reported an error: ${outcome.message}`);
      }
      if (Date.now() >= deadline) {
        throw new Error('The contact form did not confirm the send in time');
      }
      await page.waitForTimeout(250);
    }
  },

  screenshot: async (context) => {
    const page = await ensureBlogPage(context);
    await page.setViewportSize({width: 600, height: 600});
    await page.waitForLoadState('domcontentloaded', {timeout: settleTimeoutMs}).catch(() => undefined);

    const $blogHeader = page.locator('header:has(h1)');
    const imageFile = `${context.outputDir}/blog-header.png`;
    await $blogHeader.screenshot({path: imageFile});
    return {imageFile};
  },

  pdf: async (context) => {
    const page = await ensureBlogPage(context);
    await page.setViewportSize({width: 600, height: 600});
    await page.addStyleTag({content: `header > .fixed {position: absolute}`});

    const pdfFile = `${context.outputDir}/blog-page.pdf`;
    await page.pdf({
      path: pdfFile,
      margin: {top: 0, right: 0, bottom: 0, left: 0},
      displayHeaderFooter: false,
      preferCSSPageSize: true,
      printBackground: false,
    });
    return {pdfFile};
  },

  video: async (context) => {
    const videoContext = await context.browser.newContext({
      recordVideo: {dir: context.outputDir},
    });

    try {
      const videoPage = await videoContext.newPage();
      const video = videoPage.video();
      if (!video) {
        throw new Error('Video recording is not available for this browser context');
      }

      const videoFile = `${context.outputDir}/blog-scroll.mp4`;
      const saveProm = video.saveAs(videoFile);
      // The save only settles once the page closes. Mark it as handled now so
      // an earlier failure cannot surface later as an unhandled rejection.
      saveProm.catch(() => undefined);

      try {
        await gotoReady(videoPage, context.urls.blog);
        await videoPage.evaluate(async () => {
          for (let i = 0; i < document.body.scrollHeight; i += 100) {
            window.scrollTo(0, i);
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
        });
      } finally {
        await videoPage.close().catch(() => undefined);
      }

      await saveProm;
      return {videoFile};
    } finally {
      await videoContext.close().catch(() => undefined);
    }
  },

  footerLinks: async (context) => {
    const page = await ensureBlogPage(context);
    const $footerLinks = await page.locator('footer a').all();
    if ($footerLinks.length === 0 && (await page.locator('footer').count()) === 0) {
      // A page with no footer at all is a failed collection, not a successful
      // empty batch; otherwise a challenge or error page would masquerade as
      // good data. A footer that genuinely has no anchors is simply empty.
      throw new Error(`No footer was found on ${page.url()}`);
    }

    // Read each anchor in isolation too: a detached or unreadable element is a
    // per-link problem, not a reason to throw the whole footer away.
    const candidates: FooterLinkCandidate[] = [];
    const readFailures: FooterLinkProblem[] = [];
    for (const el of $footerLinks) {
      try {
        candidates.push({
          text: await el.textContent(),
          link: await el.getAttribute('href'),
        });
      } catch (error) {
        readFailures.push({
          error: `Footer link could not be read: ${toErrorMessage(error)}`,
          text: null,
          href: null,
        });
      }
    }

    // Relative links resolve against wherever the page ended up; if an earlier
    // stage left it somewhere non-navigable, fall back to the configured site.
    const currentUrl = page.url();
    const baseUrl = /^https?:/.test(currentUrl) ? currentUrl : context.urls.home;

    const parsed = parseFooterLinks(candidates, baseUrl);
    return {
      footerLinks: parsed.footerLinks,
      footerFailures: [...parsed.failures, ...readFailures],
    };
  },
};


// export

/**
 * Run every collection stage independently and settle them into one result.
 * A failed stage is reported in `failures` (with its name, error, the stages
 * already completed, and the files already produced) and no longer blocks the
 * stages after it. Only successfully written files are exposed as paths.
 */
export async function automate(
  browser: Browser,
  options: AutomateOptions = {},
): Promise<AutomationResult> {
  const outputDir = options.outputDir ?? `./.output/${browser.browserType().name()}`;
  const urls: AutomationUrls = {
    home: options.urls?.home ?? defaultUrls.home,
    blog: options.urls?.blog ?? defaultUrls.blog,
  };
  const context: StageContext = {browser, page: options.page ?? null, outputDir, urls};

  const result: AutomationResult = {
    footerLinks: [],
    imageFile: '',
    pdfFile: '',
    videoFile: '',
    failures: [],
    missingFiles: [...fileKeys],
    completed: [],
    stageResults: [],
  };

  for (const stage of stageOrder) {
    const handler = options.stages?.[stage] ?? defaultStages[stage];

    try {
      const output = (await handler(context)) ?? {};

      // Never expose a path that does not point at a real file. A stage that
      // claims an output it did not actually write is settled as failed.
      for (const key of fileKeys) {
        const file = output[key];
        if (file) {
          try {
            await fs.access(file);
          } catch {
            throw new Error(`The ${stage} stage reported ${key} ("${file}") but no such file exists`);
          }
        }
      }

      // Paths are only committed once the stage has completed, so a failed
      // stage can never leave behind a path to a file that was not produced.
      if (output.footerLinks) {
        result.footerLinks = output.footerLinks;
      }
      for (const key of fileKeys) {
        const file = output[key];
        if (file) {
          result[key] = file;
        }
      }
      result.missingFiles = fileKeys.filter((key) => !result[key]);

      result.completed.push(stage);
      result.stageResults.push({stage, status: 'completed', error: null});

      // Per-link footer problems are settled failures too, even though the
      // footer stage itself completed and kept every good link. Record them
      // after the stage is marked complete so `completed` stays accurate.
      for (const problem of output.footerFailures ?? []) {
        result.failures.push({
          scope: 'footerLink',
          stage,
          error: problem.error,
          completed: [...result.completed],
          outputs: snapshotOutputs(result),
          text: problem.text,
          href: problem.href,
        });
      }
    } catch (error) {
      const message = toErrorMessage(error);
      result.failures.push({
        scope: 'stage',
        stage,
        error: message,
        completed: [...result.completed],
        outputs: snapshotOutputs(result),
        text: null,
        href: null,
      });
      result.stageResults.push({stage, status: 'failed', error: message});

      // A stage can fail after it already wrote part of its file. Remove that
      // artifact so the result never hides a file that peers might read.
      const artifact = stageArtifacts[stage];
      if (artifact) {
        await fs.rm(`${outputDir}/${artifact}`, {force: true}).catch(() => undefined);
      }
    }
  }

  return result;
}
