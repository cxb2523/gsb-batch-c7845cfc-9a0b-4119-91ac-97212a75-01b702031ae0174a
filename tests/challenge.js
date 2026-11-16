import hooman from '../hooman.js';
import assert from 'node:assert';
import http from 'node:http';

// A minimal legacy-style js-challenge page that lib/core.js can parse and
// solve: a script computing jschl_answer plus the challenge form
const challengePage = () => `<!DOCTYPE html>
<html>
<head><title>Just a moment...</title></head>
<body>
<form id="challenge-form" action="/cdn-cgi/l/chk_jschl" method="post" enctype="application/x-www-form-urlencoded">
  <input type="hidden" name="r" value="rvalue" />
  <input type="hidden" name="jschl_vc" value="vcvalue" />
  <input type="hidden" name="pass" value="passvalue" />
  <input type="hidden" name="jschl_answer" id="jschl-answer" value="" />
</form>
<script type="text/javascript">
//<![CDATA[
var s,t,o,p,b,r,e,a,k,i,n,g,f, FAKE={"x":+((+!+[]+[])+(+!+[]+!![]+[]))};
FAKE.x += 5;
a = document.getElementById('jschl-answer');
a.value = (FAKE.x + 10).toFixed(10);
f = document.getElementById('challenge-form');
f.submit();
//]]>
</script>
</body>
</html>`;

// Looks like a challenge (contains jschl-answer and var s) but cannot be
// solved: there is no challenge form, so parsing fails
const brokenChallengePage = () => `<!DOCTYPE html>
<html>
<body>
<input type="hidden" name="jschl_answer" id="jschl-answer" value="" />
<script type="text/javascript">
var s = {};
</script>
</body>
</html>`;

// Local fake cloudflare server: serves the 503 challenge page until the
// challenge form is submitted to /cdn-cgi/l/chk_jschl, then serves 200
const startChallengeServer = (page) =>
  new Promise((resolve) => {
    const state = { solved: false, challengeHits: 0, solveHits: 0 };
    const server = http.createServer((req, res) => {
      if (req.url.startsWith('/cdn-cgi/l/chk_jschl')) {
        state.solveHits++;
        req.resume();
        req.on('end', () => {
          state.solved = true;
          res.writeHead(200, { 'content-type': 'text/html' });
          res.end('<html><body>challenge passed</body></html>');
        });
        return;
      }
      if (state.solved) {
        res.writeHead(200, { 'content-type': 'text/html' });
        res.end('<html><body>challenge passed</body></html>');
        return;
      }
      state.challengeHits++;
      res.writeHead(503, { server: 'cloudflare', 'content-type': 'text/html' });
      res.end(page());
    });
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, state, url: `http://127.0.0.1:${port}/` });
    });
  });

const closeServer = (server) => new Promise((resolve) => server.close(resolve));

describe('- cloudflare js-challenge handling (local fake servers)', () => {
  it('should solve once for concurrent same-host requests and reuse the result', async () => {
    const { server, state, url } = await startChallengeServer(challengePage);
    try {
      const responses = await Promise.all([hooman(url), hooman(url), hooman(url)]);
      for (const response of responses) {
        assert.equal(response.statusCode, 200);
        assert(response.body.includes('challenge passed'));
      }
      // Only one of the concurrent requests may actually run the solve
      assert.equal(state.solveHits, 1);
      assert(state.challengeHits >= 1);
    } finally {
      await closeServer(server);
    }
  }).timeout(1000 * 20);

  it('should wake waiting requests with the original 503 when solving fails', async () => {
    const { server, url } = await startChallengeServer(brokenChallengePage);
    try {
      const startedAt = Date.now();
      const responses = await Promise.all([
        hooman(url, { throwHttpErrors: false }),
        hooman(url, { throwHttpErrors: false }),
      ]);
      const elapsed = Date.now() - startedAt;
      for (const response of responses) {
        // Original 503 response is returned, no unhandled exception
        assert.equal(response.statusCode, 503);
        assert(response.body.includes('jschl-answer'));
      }
      // Waiters must be woken by the failure, not left polling forever
      assert(elapsed < 1000 * 5, `took too long (${elapsed}ms), waiting requests were not woken`);
    } finally {
      await closeServer(server);
    }
  }).timeout(1000 * 15);

  it('should return the original 503 when cloudflareRetry is exhausted', async () => {
    const { server, state, url } = await startChallengeServer(challengePage);
    try {
      const response = await hooman(url, {
        throwHttpErrors: false,
        context: { cloudflareRetry: 0 },
      });
      assert.equal(response.statusCode, 503);
      assert(response.body.includes('jschl-answer'));
      assert.equal(state.solveHits, 0);
    } finally {
      await closeServer(server);
    }
  }).timeout(1000 * 10);

  it('should solve challenges for different hosts in parallel', async () => {
    const first = await startChallengeServer(challengePage);
    const second = await startChallengeServer(challengePage);
    try {
      const startedAt = Date.now();
      const [firstResponse, secondResponse] = await Promise.all([hooman(first.url), hooman(second.url)]);
      const elapsed = Date.now() - startedAt;
      assert.equal(firstResponse.statusCode, 200);
      assert.equal(secondResponse.statusCode, 200);
      assert.equal(first.state.solveHits, 1);
      assert.equal(second.state.solveHits, 1);
      // Each solve has a built-in 4 second delay; if the hosts blocked each
      // other this would take 8+ seconds
      assert(elapsed < 1000 * 7, `took too long (${elapsed}ms), different hosts blocked each other`);
    } finally {
      await closeServer(first.server);
      await closeServer(second.server);
    }
  }).timeout(1000 * 20);
});
