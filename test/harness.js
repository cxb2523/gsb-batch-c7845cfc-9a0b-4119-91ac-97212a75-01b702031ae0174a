// Tiny dependency-free test harness. Works on any Node version the project
// supports (including Node 14, which predates `node:test`).
const tests = [];

function test(name, fn) {
  tests.push({ name, fn });
}

async function run() {
  let failed = 0;

  for (const { name, fn } of tests) {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (error) {
      failed++;
      console.error(`not ok - ${name}`);
      console.error(`  ${error && error.stack ? error.stack : error}`);
    }
  }

  const passed = tests.length - failed;
  console.log(`\n${passed}/${tests.length} tests passed`);
  if (failed > 0) process.exitCode = 1;
}

module.exports = { test, run };
