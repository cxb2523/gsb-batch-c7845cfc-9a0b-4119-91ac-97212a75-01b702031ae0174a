import { HttpReader } from '@/readers/http-reader';

describe('HttpReader', () => {
  it('should fetch and yield records from an API', async () => {
    const mockData = [{ id: 1, name: 'Alice' }];
    
    // Mock global fetch
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => mockData
    });

    const reader = new HttpReader('https://api.test.com/data');
    const results = [];
    for await (const record of reader.read()) {
      results.push(record);
    }

    expect(results).toEqual(mockData);
    expect(global.fetch).toHaveBeenCalledWith('https://api.test.com/data', expect.any(Object));
  });

  it('should handle nested results using resultsPath', async () => {
    const mockRes = { data: { items: [{ id: 1 }] } };
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => mockRes
    });

    const reader = new HttpReader('https://api.test.com', {
      resultsPath: (res) => res.data.items
    });

    const results = [];
    for await (const r of reader.read()) {
      results.push(r);
    }

    expect(results).toHaveLength(1);
  });

  it('should handle pagination using nextPageUrl', async () => {
    global.fetch = jest.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [{ id: 1 }], next: 'page2' })
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [{ id: 2 }], next: null })
      });

    const reader = new HttpReader('page1', {
      resultsPath: (res) => res.items,
      nextPageUrl: (res) => res.next
    });

    const results = [];
    for await (const r of reader.read()) {
      results.push(r);
    }

    expect(results).toHaveLength(2);
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it('should throw error on non-ok HTTP response', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      text: async () => 'Not Found'
    });

    const reader = new HttpReader('url');
    const iterator = reader.read();
    await expect(iterator.next()).rejects.toThrow('failed with 404');
  });

  it('should throw error if records are not an array', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ data: 'not an array' })
    });

    const reader = new HttpReader('url', { resultsPath: (res) => res.data });
    const iterator = reader.read();
    await expect(iterator.next()).rejects.toThrow('records are not in an array format');
  });

  describe('pagination observability and failure handling', () => {
    afterEach(() => {
      jest.restoreAllMocks();
    });

    function stubTimers(): number[] {
      const delays: number[] = [];
      jest.spyOn(global, 'setTimeout').mockImplementation(((
        fn: () => void,
        ms?: number
      ) => {
        delays.push(ms ?? 0);
        fn();
        return 0 as unknown as ReturnType<typeof setTimeout>;
      }) as any);
      return delays;
    }

    it('reports page number, page size and running total through onPage', async () => {
      global.fetch = jest.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ items: [{ id: 1 }, { id: 2 }], next: 'page2' })
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ items: [{ id: 3 }], next: null })
        });

      const onPage = jest.fn();
      const reader = new HttpReader('page1', {
        resultsPath: (res) => res.items,
        nextPageUrl: (res) => res.next,
        onPage
      });

      const results = [];
      for await (const r of reader.read()) {
        results.push(r);
      }

      expect(results).toHaveLength(3);
      expect(onPage).toHaveBeenNthCalledWith(1, 1, 2, 2);
      expect(onPage).toHaveBeenNthCalledWith(2, 2, 1, 3);
    });

    it('keeps throwing for a failed page when onPageError is not provided', async () => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 500,
        text: async () => 'Server Error'
      });

      const reader = new HttpReader('url');
      const iterator = reader.read();
      await expect(iterator.next()).rejects.toThrow('failed with 500');
    });

    it('skips a bad page when onPageError returns continue', async () => {
      global.fetch = jest.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ items: 'not an array', next: 'page2' })
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ items: [{ id: 2 }], next: null })
        });

      const onPageError = jest.fn().mockReturnValue('continue');
      const onPage = jest.fn();
      const reader = new HttpReader('page1', {
        resultsPath: (res) => res.items,
        nextPageUrl: (res) => res.next,
        onPageError,
        onPage
      });

      const results = [];
      for await (const r of reader.read()) {
        results.push(r);
      }

      expect(results).toEqual([{ id: 2 }]);
      expect(global.fetch).toHaveBeenCalledTimes(2);
      expect(onPageError).toHaveBeenCalledTimes(1);
      expect(onPageError.mock.calls[0][0]).toBe('page1');
      expect(onPageError.mock.calls[0][1]).toBe(1);
      expect(onPageError.mock.calls[0][2]).toBeInstanceOf(Error);
      expect(onPage).toHaveBeenCalledTimes(1);
      expect(onPage).toHaveBeenCalledWith(2, 1, 1);
    });

    it('stops collection when onPageError returns stop, keeping yielded records consumable', async () => {
      global.fetch = jest.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ items: [{ id: 1 }], next: 'page2' })
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 503,
          headers: { 'Retry-After': '0' },
          text: async () => 'Unavailable'
        });

      const onPageError = jest.fn().mockReturnValue('stop');
      const reader = new HttpReader('page1', {
        resultsPath: (res) => res.items,
        nextPageUrl: (res) => res.next,
        onPageError
      });

      const results = [];
      for await (const r of reader.read()) {
        results.push(r);
      }

      expect(results).toEqual([{ id: 1 }]);
      expect(onPageError).toHaveBeenCalledTimes(1);
      expect(onPageError.mock.calls[0][1]).toBe(2);
      expect(global.fetch).toHaveBeenCalledTimes(2);
    });

    it('retries 429 after waiting for Retry-After, then hands the failure to onPageError', async () => {
      global.fetch = jest.fn()
        .mockResolvedValueOnce({
          ok: false,
          status: 429,
          headers: { 'Retry-After': '2' },
          text: async () => 'Too Many Requests'
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 429,
          headers: { 'Retry-After': '2' },
          text: async () => 'Too Many Requests'
        });

      const delays = stubTimers();
      const onPageError = jest.fn().mockReturnValue('stop');

      const reader = new HttpReader('url', { maxRetries: 1, onPageError });
      const iterator = reader.read();

      await expect(iterator.next()).resolves.toEqual({ value: undefined, done: true });

      expect(global.fetch).toHaveBeenCalledTimes(2);
      expect(delays).toEqual([2000]);
      expect(onPageError).toHaveBeenCalledTimes(1);
      expect(onPageError.mock.calls[0][1]).toBe(1);
      expect((onPageError.mock.calls[0][2] as Error).message).toContain('failed with 429');
    });

    it('uses exponential backoff when no Retry-After header is present', async () => {
      global.fetch = jest.fn()
        .mockResolvedValueOnce({ ok: false, status: 503, text: async () => 'down' })
        .mockResolvedValueOnce({ ok: false, status: 503, text: async () => 'down' })
        .mockResolvedValueOnce({ ok: false, status: 503, text: async () => 'down' });

      const delays = stubTimers();
      const reader = new HttpReader('url', {
        maxRetries: 2,
        onPageError: () => 'continue'
      });

      const iterator = reader.read();
      await expect(iterator.next()).resolves.toEqual({ value: undefined, done: true });

      expect(global.fetch).toHaveBeenCalledTimes(3);
      expect(delays).toEqual([1000, 2000]);
    });

    it('does not retry non-retryable statuses even when maxRetries is set', async () => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 404,
        text: async () => 'Not Found'
      });

      const delays = stubTimers();
      const onPageError = jest.fn().mockReturnValue('stop');
      const reader = new HttpReader('url', { maxRetries: 3, onPageError });

      const iterator = reader.read();
      await expect(iterator.next()).resolves.toEqual({ value: undefined, done: true });

      expect(global.fetch).toHaveBeenCalledTimes(1);
      expect(delays).toEqual([]);
      expect(onPageError).toHaveBeenCalledTimes(1);
    });

    it('waits delayMs between page requests but not before the first one', async () => {
      global.fetch = jest.fn()
        .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [{ id: 1 }], next: 'p2' }) })
        .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [{ id: 2 }], next: 'p3' }) })
        .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [{ id: 3 }], next: null }) });

      const delays = stubTimers();
      const reader = new HttpReader('p1', {
        resultsPath: (res) => res.items,
        nextPageUrl: (res) => res.next,
        delayMs: 25
      });

      const results = [];
      for await (const r of reader.read()) {
        results.push(r);
      }

      expect(results).toHaveLength(3);
      expect(delays).toEqual([25, 25]);
    });

    it('recovers when a retry succeeds', async () => {
      global.fetch = jest.fn()
        .mockResolvedValueOnce({
          ok: false,
          status: 503,
          headers: { 'Retry-After': '1' },
          text: async () => 'Unavailable'
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ items: [{ id: 1 }], next: null })
        });

      const delays = stubTimers();
      const onPageError = jest.fn();
      const onPage = jest.fn();
      const reader = new HttpReader('url', {
        resultsPath: (res) => res.items,
        nextPageUrl: (res) => res.next,
        maxRetries: 1,
        onPageError,
        onPage
      });

      const results = [];
      for await (const r of reader.read()) {
        results.push(r);
      }

      expect(results).toEqual([{ id: 1 }]);
      expect(global.fetch).toHaveBeenCalledTimes(2);
      expect(delays).toEqual([1000]);
      expect(onPageError).not.toHaveBeenCalled();
      expect(onPage).toHaveBeenCalledWith(1, 1, 1);
    });

    it('throws after retries are exhausted when onPageError is not provided', async () => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 429,
        headers: { 'Retry-After': '0' },
        text: async () => 'Too Many Requests'
      });

      stubTimers();
      const reader = new HttpReader('url', { maxRetries: 2 });
      const iterator = reader.read();

      await expect(iterator.next()).rejects.toThrow('failed with 429');
      expect(global.fetch).toHaveBeenCalledTimes(3);
    });

    it('ends collection when a skipped page has no payload to paginate from', async () => {
      global.fetch = jest.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ items: [{ id: 1 }], next: 'page2' })
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 500,
          text: async () => 'Server Error'
        });

      const onPageError = jest.fn().mockReturnValue('continue');
      const reader = new HttpReader('page1', {
        resultsPath: (res) => res.items,
        nextPageUrl: (res) => res.next,
        onPageError
      });

      const results = [];
      for await (const r of reader.read()) {
        results.push(r);
      }

      // The plain-text error body cannot tell `nextPageUrl` where to go, so
      // collection ends gracefully instead of failing or looping; the record
      // produced before the failure stays consumable.
      expect(results).toEqual([{ id: 1 }]);
      expect(global.fetch).toHaveBeenCalledTimes(2);
      expect(onPageError).toHaveBeenCalledTimes(1);
    });

    it('propagates an onPage callback error instead of swallowing it', async () => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => [{ id: 1 }]
      });

      const reader = new HttpReader('url', {
        onPage: () => {
          throw new Error('observer failed');
        }
      });

      const iterator = reader.read();
      await expect(iterator.next()).rejects.toThrow('observer failed');
    });

    it('propagates an onPageError callback error instead of swallowing it', async () => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 500,
        text: async () => 'Server Error'
      });

      const reader = new HttpReader('url', {
        onPageError: () => {
          throw new Error('decider failed');
        }
      });

      const iterator = reader.read();
      await expect(iterator.next()).rejects.toThrow('decider failed');
    });
  });
});
