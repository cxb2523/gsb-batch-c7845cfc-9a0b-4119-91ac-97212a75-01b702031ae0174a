// Minimal Node.js module shims so `tsc` can type-check the default fetcher
// without requiring @types/node. Tests are excluded from the build.
declare module 'node:fs/promises' {
  export function readFile(path: string): Promise<Uint8Array>;
}
