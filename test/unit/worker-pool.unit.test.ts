import { describe, it, expect, vi, beforeEach } from 'vitest'
import { WorkerPool, type ProcessingSummary } from '../../src/scraper/worker-pool.js'
import { createMockBrowser, createMockCheckpointManager } from '../helpers/mock-factories.js'
import { ok, err } from 'super-result'

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

vi.mock('../../src/utils/logging/logger.js', () => ({
  logger: {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
    success: vi.fn(),
    log: vi.fn(),
    trace: vi.fn(),
  },
}))

vi.mock('../../src/utils/logging/error-bus.js', () => ({
  errorBus: { emitError: vi.fn(), on: vi.fn(), off: vi.fn() },
}))

import { logger } from '../../src/utils/logging/logger.js'

function buildExtractedConversation(contentHash = 'hash-match') {
  return {
    id: 'thread-1',
    title: 'Title',
    contentHash,
    spaceName: 'General',
    timestamp: new Date('2026-01-01T00:00:00.000Z'),
    content: 'Content',
    messages: [],
    safeFilename: 'thread-1-title',
  }
}

describe('WorkerPool Skip Logic (Unit)', () => {
  let pool: WorkerPool
  let mockCheckpoint: ReturnType<typeof createMockCheckpointManager>
  let mockBrowser: ReturnType<typeof createMockBrowser>
  const mockConfig = {
    parallelWorkers: 1,
    extractionConcurrency: 1,
    authStoragePath: '/tmp/auth.json',
    exportDir: '/tmp/exports',
  } as any

  beforeEach(() => {
    vi.clearAllMocks()
    mockCheckpoint = createMockCheckpointManager()
    mockCheckpoint.markAsProcessed = vi
      .fn()
      .mockResolvedValue({ ok: true, value: undefined, error: undefined })
    mockCheckpoint.getContentHash = vi.fn().mockReturnValue('hash-match')
    mockCheckpoint.getProcessingProgress = vi.fn().mockReturnValue({ processed: 1, total: 1 })
    mockBrowser = createMockBrowser()
    pool = new WorkerPool(mockConfig, mockCheckpoint, mockBrowser)
  })

  async function processSingle(): Promise<ProcessingSummary> {
    const initResult = await pool.initialize()
    expect(initResult.ok).toBe(true)
    const result = await pool.processConversations([{ id: 'thread-1', url: 'http://url/thread-1' }])
    expect(result.ok).toBe(true)
    return result.value as ProcessingSummary
  }

  it('hash match: skips file write, counts as up to date and logs it', async () => {
    extractorState.extractImpl = () => Promise.resolve(ok(buildExtractedConversation('hash-match')))
    mockCheckpoint.getContentHash.mockReturnValue('hash-match')

    const summary = await processSingle()

    expect(mockCheckpoint.markAsProcessed).toHaveBeenCalledWith('thread-1')
    expect(summary).toMatchObject({
      processed: 1,
      upToDate: 1,
      written: 0,
      failed: 0,
      checkpointPersisted: true,
    })
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining('Up to date'))
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining('skipped write'))
  })

  it('hash differs: writes the file, marks processed with the new hash and reports written', async () => {
    extractorState.extractImpl = () => Promise.resolve(ok(buildExtractedConversation('hash-new')))
    mockCheckpoint.getContentHash.mockReturnValue('hash-old')

    const summary = await processSingle()

    expect(mockCheckpoint.markAsProcessed).toHaveBeenCalledWith('thread-1', 'hash-new')
    expect(summary).toMatchObject({
      processed: 1,
      upToDate: 0,
      written: 1,
      failed: 0,
      checkpointPersisted: true,
    })
  })

  it('retries exhausted: does not mark processed and returns a failure entry with ID and URL', async () => {
    extractorState.extractImpl = () => Promise.resolve(err(new Error('boom')))

    const summary = await processSingle()

    // 1 initial + MAX_RETRIES (2) retries.
    expect(summary.processed).toBe(0)
    expect(summary.failed).toBe(1)
    expect(summary.failedConversations).toEqual([
      expect.objectContaining({
        conversationId: 'thread-1',
        url: 'http://url/thread-1',
        attempts: 3,
      }),
    ])
    expect(mockCheckpoint.markAsProcessed).not.toHaveBeenCalled()
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining('failed conversation(s)'))
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringMatching(/id=thread-1[\s\S]*url=http:\/\/url\/thread-1/)
    )
  })

  it('checkpoint save failure on hash hit is not swallowed and keeps the conversation failed', async () => {
    extractorState.extractImpl = () => Promise.resolve(ok(buildExtractedConversation('hash-match')))
    mockCheckpoint.getContentHash.mockReturnValue('hash-match')
    const persistenceError = new Error('disk full')
    mockCheckpoint.markAsProcessed.mockResolvedValue({
      ok: false,
      error: persistenceError,
      value: undefined,
    })

    const summary = await processSingle()

    expect(summary.processed).toBe(0)
    expect(summary.failed).toBe(1)
    expect(summary.checkpointPersisted).toBe(false)
    expect(summary.failedConversations[0]).toMatchObject({
      conversationId: 'thread-1',
      url: 'http://url/thread-1',
    })
    expect(summary.failedConversations[0]!.reason).toMatch(/not persisted/i)
    expect(logger.warn).toHaveBeenCalledWith(
      expect.stringContaining('Checkpoint persistence failed')
    )
  })
})
