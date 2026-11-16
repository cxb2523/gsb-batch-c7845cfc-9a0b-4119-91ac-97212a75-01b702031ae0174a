import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest'
import {
  mkdtempSync,
  rmSync,
  existsSync,
  readFileSync,
  readdirSync,
  mkdirSync,
  statSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { Config } from '../../src/utils/config.js'
import { CheckpointManager } from '../../src/scraper/checkpoint-manager.js'
import { WorkerPool, type ProcessingSummary } from '../../src/scraper/worker-pool.js'
import { createMockBrowser } from '../helpers/mock-factories.js'
import type { ExtractedConversation } from '../../src/scraper/conversation-extractor.js'
import { err, ok } from 'super-result'

/**
 * Shared, mutable control block for the hoisted ConversationExtractor mock.
 * Tests assign `extractImpl` before each run; no network or real Perplexity
 * session is ever involved.
 */
const extractorState = vi.hoisted(() => ({
  extractImpl: (_url: string, _id?: string) => {
    throw new Error('extractImpl not configured')
  },
}))

vi.mock('../../src/scraper/conversation-extractor.js', () => {
  class MockConversationExtractor {
    extract = vi.fn((url: string, id?: string) => extractorState.extractImpl(url, id))
    recoverTimeout = vi.fn()
    reduceTimeout = vi.fn()
  }
  class MockNoDataError extends Error {
    constructor(message: string) {
      super(message)
      this.name = 'NoDataError'
    }
  }
  class MockExtractionError extends Error {
    constructor(message: string) {
      super(message)
      this.name = 'ExtractionError'
    }
  }
  return {
    ConversationExtractor: MockConversationExtractor,
    NoDataError: MockNoDataError,
    ExtractionError: MockExtractionError,
    __esModule: true,
  }
})

const CONVERSATION_ID = '11111111-2222-3333-4444-555555555555'
const CONVERSATION_URL = 'https://www.perplexity.ai/search/abc123'
const HASH_OLD = 'a'.repeat(64)
const HASH_NEW = 'b'.repeat(64)

function buildConversation(contentHash: string): ExtractedConversation {
  return {
    id: CONVERSATION_ID,
    contentHash,
    title: 'Mocked Thread',
    spaceName: 'General Space',
    timestamp: new Date('2026-01-01T00:00:00.000Z'),
    content: 'Body of the conversation',
    messages: [{ role: 'user', content: 'hello' }],
    safeFilename: `${CONVERSATION_ID}-mocked-thread`,
  }
}

describe('WorkerPool <-> CheckpointManager resume semantics (integration, no network)', () => {
  let tempRoot: string
  let exportDir: string
  let checkpointPath: string
  let config: Config

  beforeAll(() => {
    tempRoot = mkdtempSync(join(tmpdir(), 'wp-checkpoint-'))
  })

  afterAll(() => {
    rmSync(tempRoot, { recursive: true, force: true })
  })

  beforeEach(() => {
    exportDir = join(tempRoot, `exports-${Math.random().toString(36).slice(2)}`)
    checkpointPath = join(tempRoot, `checkpoint-${Math.random().toString(36).slice(2)}.json`)
    mkdirSync(exportDir, { recursive: true })
    config = {
      authStoragePath: join(tempRoot, 'auth.json'),
      waitMode: 'static',
      rateLimitMs: 10,
      parallelWorkers: 1,
      extractionConcurrency: 1,
      checkpointSaveInterval: 10,
      exportDir,
      checkpointPath,
      vectorIndexPath: join(tempRoot, 'vector-index'),
      ollamaUrl: 'http://localhost:11434',
      ollamaModel: 'llama3.1',
      ollamaEmbedModel: 'nomic-embed-text',
      aiProvider: 'ollama',
      aiEmbedProvider: 'ollama',
      enableVectorSearch: false,
      headless: true,
      debug: false,
      hydeMode: 'supplement',
      hydeThresholdScore: 0.7,
      hydeThresholdCount: 5,
      exportStrategies: ['markdown'],
    } as Config
  })

  async function seedCheckpoint(
    contentHash: string | undefined,
    processed: boolean
  ): Promise<CheckpointManager> {
    const manager = new CheckpointManager(config)
    const setResult = await manager.setDiscoveredConversations([
      { id: CONVERSATION_ID, url: CONVERSATION_URL, contentHash },
    ])
    expect(setResult.ok).toBe(true)
    if (processed) {
      const markResult = await manager.markAsProcessed(CONVERSATION_ID, contentHash)
      expect(markResult.ok).toBe(true)
    }
    return manager
  }

  async function runPool(manager: CheckpointManager) {
    const pool = new WorkerPool(config, manager, createMockBrowser())
    const initResult = await pool.initialize()
    expect(initResult.ok).toBe(true)
    const result = await pool.processConversations(manager.getPendingConversations())
    expect(result.ok).toBe(true)
    await pool.close()
    return result.value as ProcessingSummary
  }

  function expectedMarkdownPath(): string {
    return join(exportDir, 'general-space', `${CONVERSATION_ID}-mocked-thread.md`)
  }

  /**
   * Loads a fresh manager from the persisted checkpoint on disk. Used to
   * simulate a new program invocation resuming after an interruption.
   */
  function reloadManager(): CheckpointManager {
    return new CheckpointManager(config)
  }

  it('hash hit: skips disk write, counts as processed and keeps the stored hash', async () => {
    // Simulate a previous run that wrote the file and saved the checkpoint,
    // then re-discovery queues the known conversation again.
    const seeded = await seedCheckpoint(HASH_OLD, false)
    expect(seeded.getPendingConversations()).toHaveLength(1)

    // New invocation: the checkpoint on disk already records the content hash.
    const manager = reloadManager()
    extractorState.extractImpl = () => Promise.resolve(ok(buildConversation(HASH_OLD)))

    const summary = await runPool(manager)

    expect(existsSync(expectedMarkdownPath())).toBe(false)
    expect(readdirSync(exportDir)).toEqual([])
    expect(summary.upToDate).toBe(1)
    expect(summary.processed).toBe(1)
    expect(summary.written).toBe(0)
    expect(summary.failed).toBe(0)

    // The conversation is processed (no longer pending) and its hash is untouched.
    const reloaded = reloadManager()
    expect(reloaded.getPendingConversations()).toEqual([])
    expect(reloaded.getContentHash(CONVERSATION_ID)).toBe(HASH_OLD)
  })

  it('hash change: writes markdown and persists the new hash', async () => {
    const manager = await seedCheckpoint(HASH_OLD, false)
    extractorState.extractImpl = () => Promise.resolve(ok(buildConversation(HASH_NEW)))

    const summary = await runPool(manager)

    const markdownPath = expectedMarkdownPath()
    expect(existsSync(markdownPath)).toBe(true)
    const written = readFileSync(markdownPath, 'utf-8')
    expect(written).toContain(`# Mocked Thread`)
    expect(written).toContain(`**ID:** ${CONVERSATION_ID}`)
    expect(written).toContain('Body of the conversation')

    expect(summary.written).toBe(1)
    expect(summary.processed).toBe(1)
    expect(summary.upToDate).toBe(0)
    expect(summary.failed).toBe(0)

    // A fresh manager proves the new hash was persisted to disk, not just in memory.
    const reloaded = new CheckpointManager(config)
    expect(reloaded.getPendingConversations()).toEqual([])
    expect(reloaded.getContentHash(CONVERSATION_ID)).toBe(HASH_NEW)
  })

  it('missing hash (first successful run): writes markdown and stores the hash', async () => {
    const manager = await seedCheckpoint(undefined, false)
    extractorState.extractImpl = () => Promise.resolve(ok(buildConversation(HASH_NEW)))

    const summary = await runPool(manager)

    expect(existsSync(expectedMarkdownPath())).toBe(true)
    expect(summary.written).toBe(1)
    expect(summary.processed).toBe(1)
    expect(summary.failed).toBe(0)

    const reloaded = new CheckpointManager(config)
    expect(reloaded.getContentHash(CONVERSATION_ID)).toBe(HASH_NEW)
    expect(reloaded.getPendingConversations()).toEqual([])
  })

  it('retries exhausted: conversation stays pending and appears on the failure list with ID and URL', async () => {
    const manager = await seedCheckpoint(undefined, false)
    const boom = new Error('simulated extraction outage')
    let attempts = 0
    extractorState.extractImpl = () => {
      attempts++
      return Promise.resolve(err(boom))
    }

    const summary = await runPool(manager)

    // 1 initial attempt + MAX_RETRIES (2) retries.
    expect(attempts).toBe(3)
    expect(summary.processed).toBe(0)
    expect(summary.failed).toBe(1)
    expect(summary.failedConversations).toHaveLength(1)
    const failure = summary.failedConversations[0]!
    expect(failure.conversationId).toBe(CONVERSATION_ID)
    expect(failure.url).toBe(CONVERSATION_URL)
    expect(failure.attempts).toBe(3)

    // Not marked processed: still in the pending list for the next run.
    const pending = manager.getPendingConversations()
    expect(pending).toHaveLength(1)
    expect(pending[0]!.id).toBe(CONVERSATION_ID)

    // And still pending after reloading the checkpoint from disk.
    const reloaded = new CheckpointManager(config)
    expect(reloaded.getPendingConversations().map((c) => c.id)).toEqual([CONVERSATION_ID])
    expect(existsSync(expectedMarkdownPath())).toBe(false)
  })

  it('checkpoint save failure is not swallowed: file may exist but the run reports it as not persisted', async () => {
    // Pointing the checkpoint path at an existing directory makes the
    // tmp-file write fail reliably on every platform, without chmod tricks.
    mkdirSync(checkpointPath, { recursive: true })
    const manager = new CheckpointManager(config)
    // Seed in-memory discovery state directly, mirroring a run whose discovery
    // was never persisted. Any subsequent save attempt will fail.
    const discoverySave = await manager.setDiscoveredConversations([
      { id: CONVERSATION_ID, url: CONVERSATION_URL, contentHash: HASH_OLD },
    ])
    expect(discoverySave.ok).toBe(false)
    ;(
      manager as unknown as {
        currentState: {
          discoveryPhaseComplete: boolean
          discoveredConversations: { id: string; url: string }[]
          processedIds: string[]
        }
      }
    ).currentState = {
      discoveryPhaseComplete: true,
      discoveredConversations: [{ id: CONVERSATION_ID, url: CONVERSATION_URL }],
      processedIds: [],
    }
    extractorState.extractImpl = () => Promise.resolve(ok(buildConversation(HASH_NEW)))

    const pool = new WorkerPool(config, manager, createMockBrowser())
    const initResult = await pool.initialize()
    expect(initResult.ok).toBe(true)
    const result = await pool.processConversations(manager.getPendingConversations())
    expect(result.ok).toBe(true)
    await pool.close()
    const summary = result.value as ProcessingSummary

    // The markdown write succeeded before the checkpoint save failed...
    expect(existsSync(expectedMarkdownPath())).toBe(true)
    // ...but the run must reflect that progress was not persisted.
    expect(summary.checkpointPersisted).toBe(false)
    expect(summary.processed).toBe(0)
    expect(summary.failed).toBe(1)
    expect(summary.failedConversations[0]!.conversationId).toBe(CONVERSATION_ID)
    expect(summary.failedConversations[0]!.url).toBe(CONVERSATION_URL)
    expect(summary.failedConversations[0]!.reason).toMatch(/not persisted/i)

    // No checkpoint file appeared (the path is still an empty directory) and
    // nothing was marked processed.
    expect(statSync(checkpointPath).isDirectory()).toBe(true)
    expect(readdirSync(checkpointPath)).toEqual([])
    expect(manager.getPendingConversations().map((c) => c.id)).toEqual([CONVERSATION_ID])
  })
})
