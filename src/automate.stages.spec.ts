// import

import {test, expect} from 'playwright/test';
import type {Browser} from 'playwright';
import http from 'http';
import type {AddressInfo} from 'net';
import fs from 'fs/promises';

import {automate, parseFooterLinks} from './automate';
import type {AutomationResult, StageHandler} from './automate';

// vars

const outputDir = './.test/stages/output';
const state = {brokenFooter: false};

const footer = () => `
  <footer>
    <a href="/pricing">Pricing</a>
    <a href="https://app.browsercat.com/dashboard">Dashboard</a>
    ${state.brokenFooter ? '<a href="http://">Broken</a>' : ''}
  </footer>
`;

const pages: Record<string, () => string> = {
  '/': () => `
    <html><body>
      <header><a href="/">Home</a></header>
      <main>
        <a href="/contact">Contact</a>
      </main>
    </body></html>
  `,
  '/contact': () => `
    <html><body>
      <header><a href="/blog">Blog</a></header>
      <main>
        <form>
          <label for="name">Name</label>
          <input id="name" name="name" />
          <label for="email">Email</label>
          <input id="email" name="email" type="email" />
          <label for="message">Message</label>
          <textarea id="message" name="message"></textarea>
          <button type="button">Send Message</button>
        </form>
      </main>
      <script>
        document.querySelector('button').addEventListener('click', (event) => {
          event.currentTarget.textContent = 'Sent!';
        });
      </script>
    </body></html>
  `,
  '/blog': () => `
    <html><body>
      <header><h1>Automation Blog</h1></header>
      <main>
        <p>Blog content for the PDF and video stages.</p>
        <div style="height: 1200px"></div>
      </main>
      ${footer()}
    </body></html>
  `,
};

let server: http.Server;
let baseUrl: string;

// setup

test.beforeAll(async () => {
  server = http.createServer((req, res) => {
    const path = (req.url ?? '/').split('?')[0] ?? '/';
    const render = pages[path];
    if (!render) {
      res.statusCode = 404;
      res.end('Not found');
      return;
    }
    res.setHeader('content-type', 'text/html; charset=utf-8');
    res.end(render());
  });

  await new Promise<void>((resolve) => {
    server.listen(0, '127.0.0.1', resolve);
  });
  const {port} = server.address() as AddressInfo;
  baseUrl = `http://127.0.0.1:${port}`;
});

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
});

test.beforeEach(async () => {
  state.brokenFooter = false;
  await fs.rm(outputDir, {recursive: true, force: true});
});

// helpers

async function runFixture(browser: Browser, stages?: Record<string, StageHandler>) {
  return automate(browser, {
    outputDir,
    urls: {home: `${baseUrl}/`, blog: `${baseUrl}/blog`},
    stages,
  });
}

function stageFailure(result: AutomationResult, stage: string) {
  return result.failures.find((failure) => failure.scope === 'stage' && failure.stage === stage);
}

// test

test('settles every stage on the happy path', async ({browser}) => {
  const result = await runFixture(browser);

  // the whole batch is settled with no failures
  expect(result.failures).toEqual([]);
  expect(result.missingFiles).toEqual([]);
  expect(result.completed).toEqual([
    'workspace', 'navigate', 'form', 'screenshot', 'pdf', 'video', 'footerLinks',
  ]);

  // every produced file path points at a real file
  expect(result.imageFile).toBe(`${outputDir}/blog-header.png`);
  expect(result.pdfFile).toBe(`${outputDir}/blog-page.pdf`);
  expect(result.videoFile).toBe(`${outputDir}/blog-scroll.mp4`);
  for (const file of [result.imageFile, result.pdfFile, result.videoFile]) {
    await expect(fs.access(file)).resolves.toBeUndefined();
  }

  // the footer links keep the original structure and are all valid
  expect(result.footerLinks.length).toBe(2);
  expect(result.footerLinks).toEqual([
    {text: 'Pricing', link: '/pricing'},
    {text: 'Dashboard', link: 'https://app.browsercat.com/dashboard'},
  ]);
  for (const link of result.footerLinks) {
    expect(() => new URL(link.link, baseUrl)).not.toThrow();
  }
});

test('skips a broken footer link instead of losing the batch', async ({browser}) => {
  state.brokenFooter = true;

  const result = await runFixture(browser);

  // the bad entry is dropped but its siblings survive
  expect(result.footerLinks).toEqual([
    {text: 'Pricing', link: '/pricing'},
    {text: 'Dashboard', link: 'https://app.browsercat.com/dashboard'},
  ]);
  expect(result.footerLinks.some((link) => link.text === 'Broken')).toBe(false);

  // and it is recorded with enough detail to locate it
  const broken = result.failures.find((failure) => failure.scope === 'footerLink');
  expect(broken).toBeDefined();
  expect(broken?.stage).toBe('footerLinks');
  expect(broken?.text).toBe('Broken');
  expect(broken?.href).toBe('http://');
  expect(broken?.error).toContain('cannot be parsed as a URL');

  // everything else still settled normally
  expect(result.missingFiles).toEqual([]);
  expect(stageFailure(result, 'footerLinks')).toBeUndefined();
});

test('keeps going after a stage throws and marks its output missing', async ({browser}) => {
  const screenshotBoom: StageHandler = async () => {
    throw new Error('screenshot boom');
  };

  const result = await runFixture(browser, {screenshot: screenshotBoom});

  // the failure is localized to the screenshot stage
  const failure = stageFailure(result, 'screenshot');
  expect(failure?.error).toContain('screenshot boom');
  expect(result.imageFile).toBe('');
  expect(result.missingFiles).toEqual(['imageFile']);

  // the failure records what had already been collected at that point
  expect(failure?.completed).toEqual(['workspace', 'navigate', 'form']);
  expect(failure?.outputs).toEqual({imageFile: '', pdfFile: '', videoFile: ''});

  // the stages after it still ran and produced their own output
  expect(result.completed).toEqual(['workspace', 'navigate', 'form', 'pdf', 'video', 'footerLinks']);
  expect(result.pdfFile).toBe(`${outputDir}/blog-page.pdf`);
  expect(result.videoFile).toBe(`${outputDir}/blog-scroll.mp4`);
  await expect(fs.access(result.pdfFile)).resolves.toBeUndefined();
  await expect(fs.access(result.videoFile)).resolves.toBeUndefined();
  expect(result.footerLinks.length).toBe(2);
});

test('later stages recover on their own when navigation fails', async ({browser}) => {
  const navigateBoom: StageHandler = async () => {
    throw new Error('navigation boom');
  };

  const result = await runFixture(browser, {navigate: navigateBoom});

  expect(stageFailure(result, 'navigate')?.error).toContain('navigation boom');
  expect(result.completed).toEqual(['workspace', 'form', 'screenshot', 'pdf', 'video', 'footerLinks']);
  expect(result.missingFiles).toEqual([]);
  await expect(fs.access(result.imageFile)).resolves.toBeUndefined();
  await expect(fs.access(result.pdfFile)).resolves.toBeUndefined();
  await expect(fs.access(result.videoFile)).resolves.toBeUndefined();
  expect(result.footerLinks.length).toBe(2);
});

test('does not expose a path for a file that was never written', async ({browser}) => {
  const phantomPdf: StageHandler = async (context) => ({
    pdfFile: `${context.outputDir}/does-not-exist.pdf`,
  });

  const result = await runFixture(browser, {pdf: phantomPdf});

  expect(stageFailure(result, 'pdf')?.error).toContain('no such file exists');
  expect(result.pdfFile).toBe('');
  expect(result.missingFiles).toEqual(['pdfFile']);
  expect(result.completed).toContain('video');
  expect(result.completed).toContain('footerLinks');
});

test('validates footer entries one by one', () => {
  const {footerLinks, failures} = parseFooterLinks(
    [
      {text: 'Docs', link: '/docs'},
      {text: '   ', link: '/blank'},
      {text: 'No href', link: null},
      {text: 'Bad href', link: 'http://'},
      {text: 'Bad spacing', link: 'https://exa mple.com'},
      {text: 'Bad escape', link: '/bad%zz'},
      {text: 'Status', link: 'https://status.browsercat.com'},
    ],
    'https://www.browsercat.com/blog',
  );

  expect(footerLinks).toEqual([
    {text: 'Docs', link: '/docs'},
    {text: 'Status', link: 'https://status.browsercat.com'},
  ]);

  // each skipped entry is named, with both the raw text and href available
  expect(failures.map((failure) => failure.href)).toEqual([
    '/blank', null, 'http://', 'https://exa mple.com', '/bad%zz',
  ]);
  expect(failures.every((failure) => failure.error.length > 0)).toBe(true);
});
