module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  // Only *.test.ts files under src are Jest tests. The cluster-fixtures
  // directory contains standalone cluster entry points spawned as separate
  // processes by cluster-scheduler.test.ts, not Jest suites.
  testMatch: [
    '<rootDir>/src/**/*.test.ts',
  ],
};
