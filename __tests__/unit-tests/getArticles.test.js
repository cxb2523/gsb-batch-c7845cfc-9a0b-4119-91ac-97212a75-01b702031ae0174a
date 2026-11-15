const axios = require('axios');

jest.mock('axios');

// three fake sources: one good, one returning a page with no matching
// articles, one whose request blows up
jest.mock('../../src/data/sources.json', () => [
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
      <title>Article</title>
    </head>
    <body></body>
  </html>
`;

const EMPTY_PAGE = `
  <!DOCTYPE html>
  <html>
    <head><title>Empty Source</title></head>
    <body>
      <a href="/weather">Sunny spells expected this weekend</a>
    </body>
  </html>
`;

function mockRound({ allFail = false } = {}) {
  axios.get.mockImplementation((url) => {
    if (allFail) {
      return Promise.reject(new Error('network is down'));
    }
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

const { getArticles } = require('../../src/utils/getArticles');
const controller = require('../../src/controller/api/newsAPIController');

describe('getArticles collection pipeline', () => {
  beforeEach(() => mockRound());

  it('waits for all sources and returns aggregated articles keyed by url hash', async () => {
    const { articles } = await getArticles();

    const values = Object.values(articles);
    expect(values).toHaveLength(1);
    expect(values[0]).toEqual({
      title: 'Energy prices fall as renewable energy output rises',
      url: 'https://ok.example.com/article-1',
      source: 'Ok Source',
      region: 'Europe',
      image: 'https://ok.example.com/img/article-1.png',
    });
    // keys are the short url hashes
    expect(Object.keys(articles)[0]).toMatch(/^[A-Za-z0-9_-]{10}$/);
  });

  it('reports per-source outcome: ok with count, empty, error with reason', async () => {
    const { sources } = await getArticles();

    expect(sources['Ok Source']).toEqual({ status: 'ok', count: 1 });
    expect(sources['Empty Source']).toEqual({ status: 'empty', count: 0 });
    expect(sources['Broken Source'].status).toBe('error');
    expect(sources['Broken Source'].reason).toContain('ECONNREFUSED');
  });

  it('keeps other sources when one source throws', async () => {
    const { articles } = await getArticles();

    // the broken source must not take the ok source down with it
    expect(Object.values(articles).some((a) => a.source === 'Ok Source')).toBe(true);
    expect(Object.values(articles).some((a) => a.source === 'Broken Source')).toBe(false);
  });
});

describe('newsAPIController refresh behaviour', () => {
  function getNewsBody() {
    let body;
    controller.getNews({ query: {} }, { json: (b) => (body = b) });
    return body;
  }

  function getStatusBody() {
    let body;
    controller.getCollectionStatus({}, { json: (b) => (body = b) });
    return body;
  }

  beforeEach(async () => {
    mockRound();
    await controller.fetchArticles();
  });

  it('serves aggregated articles and per-source status after a round', () => {
    const articles = getNewsBody();
    expect(Object.values(articles).some((a) => a.source === 'Ok Source')).toBe(true);

    const status = getStatusBody();
    expect(status.lastRun).toBeTruthy();
    expect(status.sources['Ok Source']).toEqual({ status: 'ok', count: 1 });
    expect(status.sources['Empty Source']).toEqual({ status: 'empty', count: 0 });
    expect(status.sources['Broken Source'].status).toBe('error');
    expect(status.sources['Broken Source'].reason).toBeTruthy();
  });

  it('keeps the previous catalogue when a refresh round fails entirely', async () => {
    const before = getNewsBody();
    expect(Object.keys(before).length).toBeGreaterThan(0);

    // every request now fails
    mockRound({ allFail: true });
    await controller.fetchArticles();

    const after = getNewsBody();
    expect(after).toEqual(before);

    // ...but the status endpoint honestly reports the failed round
    const status = getStatusBody();
    expect(status.sources['Ok Source'].status).toBe('error');
    expect(status.sources['Empty Source'].status).toBe('error');
    expect(status.sources['Broken Source'].status).toBe('error');
  });
});
