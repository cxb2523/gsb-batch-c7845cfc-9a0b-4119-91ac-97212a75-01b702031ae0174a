import test from 'node:test';
import assert from 'node:assert/strict';
import { connect, ChallengeTimeoutError } from '../dist/index.js';

// A renderer stuck in an infinite loop cannot run the polling evaluate, so the
// challenge wait times out with reason 'page-unresponsive'.
const stick = page => { void page.evaluate(() => { while (true) {} }).catch(() => {}); };

for (const driver of ['puppeteer', 'playwright']) {
  test(`${driver}: autoRecover replaces an unresponsive page only when opted in`, { timeout: 45000 }, async t => {
    const session = await connect({ driver, headless: true, timeout: 5000 });
    t.after(() => session.close());
    const stuck = session.page;
    stick(stuck);
    // Default behavior is unchanged: the timeout still throws and the page is kept.
    await assert.rejects(session.waitForChallenge({ timeout: 150 }), error => {
      assert.ok(error instanceof ChallengeTimeoutError);
      assert.equal(error.diagnostics.reason, 'page-unresponsive');
      return true;
    });
    assert.equal(session.page, stuck);
    assert.equal(stuck.isClosed(), false);
    // Opted in: the stuck page is closed and session.page is a working replacement.
    const result = await session.waitForChallenge({ timeout: 150, autoRecover: true });
    assert.equal(result.status, 'recovered');
    assert.equal(result.recovered, true);
    assert.equal(stuck.isClosed(), true);
    assert.notEqual(session.page, stuck);
    assert.equal(result.page, session.page);
    assert.equal(await session.page.evaluate(() => 42), 42);
  });

  test(`${driver}: autoRecover of a non-default page leaves session.page untouched`, { timeout: 45000 }, async t => {
    const session = await connect({ driver, headless: true, timeout: 5000 });
    t.after(() => session.close());
    const main = session.page;
    const extra = await session.newPage();
    stick(extra);
    const result = await session.waitForChallenge({ timeout: 150, autoRecover: true }, extra);
    assert.equal(result.status, 'recovered');
    assert.equal(result.recovered, true);
    assert.equal(session.page, main);
    assert.equal(extra.isClosed(), true);
    assert.ok(result.page);
    assert.notEqual(result.page, extra);
    assert.notEqual(result.page, main);
    assert.equal(await result.page.evaluate(() => 7), 7);
    assert.equal(await main.evaluate(() => 1), 1);
  });

  test(`${driver}: autoRecover does not trigger for readiness timeouts or aborted signals`, { timeout: 45000 }, async t => {
    const session = await connect({ driver, headless: true, timeout: 5000 });
    t.after(() => session.close());
    // A readiness timeout is not a stuck page: it still throws even with autoRecover.
    await assert.rejects(session.waitForChallenge({ timeout: 150, readySelector: '#missing', autoRecover: true }), error => {
      assert.ok(error instanceof ChallengeTimeoutError);
      assert.equal(error.diagnostics.reason, 'readiness');
      return true;
    });
    // An aborted signal prevents recovery of an unresponsive page.
    const stuck = session.page;
    stick(stuck);
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 100);
    await assert.rejects(session.waitForChallenge({ timeout: 10_000, autoRecover: true, signal: controller.signal }), /abort/i);
    assert.equal(session.page, stuck);
    assert.equal(stuck.isClosed(), false);
    await session.recoverPage(); // Discard the stuck page so shutdown stays fast.
  });
}
