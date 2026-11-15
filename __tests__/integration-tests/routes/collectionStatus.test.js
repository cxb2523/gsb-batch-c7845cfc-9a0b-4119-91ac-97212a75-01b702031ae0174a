const axios = require('axios');

jest.mock('axios');

jest.mock('../../../src/data/sources.json', () => [
  {
    name: 'Ok Source',
    site: 'https://ok.example.com/news',
    base: 'https://ok.example.com',
    region: 'Europe',
  },
  {
    name: 'Empty Source',
    site: 'https://empty.example.com/news',
    base: 'https://empty.example.com',
    region: 'Asia',
  },
  {
    name: 'Broken Source',
    site: 'https://broken.example.com/news',
    base: 'https://broken.example.com',
    region: 'Americas',
  },
]);

const OK_PAGE = `
  <!DOCTYPE html>
  <html>
    <head><title>Ok Source</title></head>
    <body>
      <article>
        <a href="/article-1">Energy prices fall as renewable energy output rises</a>
      </article>
    </body>
  </html>
`;

const ARTICLE_PAGE = `
  <!DOCTYPE html>
  <html>
    <head>
      <meta property="og:image" content="https://ok.example.com/img/article-1.png" />
    </head>
    <body></body>
  </html>
`;

const EMPTY_PAGE = `
  <!DOCTYPE html>
  <html>
    <head><title>Empty Source</title></head>
    <body><a href="/weather">Sunny spells expected this weekend</a></body>
  </html>
`;

function mockRound({ allFail = false } = {}) {
  axios.get.mockImplementation((url) => {
    if (allFail) return Promise.reject(new Error('network is down'));
    switch (url) {
      case 'https://ok.example.com/news':
        return Promise.resolve({ data: OK_PAGE });
      case 'https://ok.example.com/article-1':
        return Promise.resolve({ data: ARTICLE_PAGE });
      case 'https://empty.example.com/news':
        return Promise.resolve({ data: EMPTY_PAGE });
      case 'https://broken.example.com/news':
        return Promise.reject(new Error('connect ECONNREFUSED'));
      default:
        return Promise.reject(new Error(`unexpected url: ${url}`));
    }
  });
}

const createServer = require('../../../src/server');
const controller = require('../../../src/controller/api/newsAPIController');

describe('collection pipeline end-to-end (mocked sources)', () => {
  let server;
  let base;

  beforeAll(async () => {
    mockRound();
    await controller.fetchArticles();
    server = createServer().listen(0);
    await new Promise((resolve) => server.on('listening', resolve));
    base = `http://127.0.0.1:${server.address().port}`;
  });

  afterAll(() => new Promise((resolve) => server.close(resolve)));

  it('GET /api/news returns the successful source articles keyed by url hash', async () => {
    const res = await fetch(`${base}/api/news`);
    expect(res.status).toBe(200);
    const body = await res.json();

    const values = Object.values(body);
    expect(values).toHaveLength(1);
    expect(values[0]).toEqual({
      title: 'Energy prices fall as renewable energy output rises',
      url: 'https://ok.example.com/article-1',
      source: 'Ok Source',
      region: 'Europe',
      image: 'https://ok.example.com/img/article-1.png',
    });
    expect(Object.keys(body)[0]).toMatch(/^[A-Za-z0-9_-]{10}$/);
  });

  it('GET /api/news/status annotates each source truthfully', async () => {
    const res = await fetch(`${base}/api/news/status`);
    expect(res.status).toBe(200);
    const body = await res.json();

    expect(body.lastRun).toBeTruthy();
    expect(body.sources['Ok Source']).toEqual({ status: 'ok', count: 1 });
    expect(body.sources['Empty Source']).toEqual({ status: 'empty', count: 0 });
    expect(body.sources['Broken Source'].status).toBe('error');
    expect(body.sources['Broken Source'].reason).toContain('ECONNREFUSED');
  });

  it('a fully failed refresh keeps the old articles and reports the failure', async () => {
    mockRound({ allFail: true });
    await controller.fetchArticles();

    const newsRes = await fetch(`${base}/api/news`);
    const news = await newsRes.json();
    expect(Object.values(news).some((a) => a.source === 'Ok Source')).toBe(true);

    const statusRes = await fetch(`${base}/api/news/status`);
    const status = await statusRes.json();
    expect(status.sources['Ok Source'].status).toBe('error');
    expect(status.sources['Empty Source'].status).toBe('error');
    expect(status.sources['Broken Source'].status).toBe('error');
  });
});
