import { errorBus } from '../utils/logging/error-bus.js'
import { type Browser, type BrowserContext } from '@playwright/test'
import {
  ConversationExtractor,
  type ExtractedConversation,
  NoDataError,
  ExtractionError,
} from './conversation-extractor.js'
import { CheckpointManager, type ConversationMeta } from './checkpoint-manager.js'
import { logger } from '../utils/logging/logger.js'
import { type Config } from '../utils/config.js'
import { isTypedError, BaseAppError } from '../utils/errors.js'
import { join } from 'node:path'
import { writeFileSync, existsSync, mkdirSync, statSync } from 'node:fs'
import { slugify, safeLog } from '../utils/shell-safety.js'
import { ok, err, from, createResult, type Result } from 'super-result'
import { errorMessageOf } from '../utils/extract-error-message.js'
import { RateLimiter } from './rate-limiter.js'

export class ContextRefreshError extends BaseAppError {}
type ContextRefreshErrorInstance = InstanceType<typeof ContextRefreshError>

const MAX_RETRIES = 2
const POLLING_INTERVAL_MS = 100

/**
 * A conversation that could not be completed this run. It is deliberately NOT
 * marked as processed, so it stays in the checkpoint's pending list and will be
 * picked up again on the next run.
 */
export interface FailedConversation {
  conversationId: string
  url: string
  /** Number of attempts made this run (initial extraction + retries). */
  attempts: number
  reason: string
  cause?: unknown
}

/**
 * Verifiable outcome of a single processConversations run. Counters are tracked
 * by the pool itself instead of being derived from checkpoint state, so a
 * failed checkpoint save can never make a failed conversation look processed.
 */
export interface ProcessingSummary {
  total: number
  /** Successfully finished this run (written + already-up-to-date). */
  processed: number
  /** Written to disk because the hash was missing or had changed. */
  written: number
  /** Skipped because the content hash already matched the checkpoint. */
  upToDate: number
  /** Still pending after this run; never counted as processed. */
  failed: number
  failedConversations: FailedConversation[]
  /**
   * False when at least one checkpoint save failed. The markdown file may exist
   * on disk in that case, but progress/hash was not persisted, so the affected
   * conversations must be retried on the next run.
   */
  checkpointPersisted: boolean
}

interface ExtractionWorker {
  id: number
  extractor: ConversationExtractor
  isBusy: boolean
}

interface QueueItem {
  meta: ConversationMeta
  attempts: number
}

export class WorkerPool {
  private readonly workers: ExtractionWorker[] = []
  private sharedBrowserContext: BrowserContext | null = null
  private isRefreshing = false
  private readonly rateLimiter: RateLimiter
  private readonly resultFactory = createResult<Error>((error: unknown) =>
    error instanceof Error ? error : new Error(String(error))
  )

  private failedConversations: FailedConversation[] = []
  private writtenCount = 0
  private upToDateCount = 0
  private checkpointSaveFailures = 0

  constructor(
    private readonly config: Config,
    private readonly checkpointManager: CheckpointManager,
    private readonly browser: Browser
  ) {
    this.rateLimiter = new RateLimiter(this.config.extractionConcurrency)
  }

  async initialize(): Promise<Result<void, ContextRefreshErrorInstance>> {
    return this.resultFactory.from(async () => await this.createWorkers())
  }

  private async createWorkers(): Promise<void> {
    this.sharedBrowserContext = await this.browser.newContext({
      storageState: this.config.authStoragePath,
    })
    for (let i = 0; i < this.config.parallelWorkers; i++) {
      this.workers.push({
        id: i,
        extractor: new ConversationExtractor(this.config, this.sharedBrowserContext),
        isBusy: false,
      })
    }
  }

  async processConversations(
    conversationsToProcess: ConversationMeta[]
  ): Promise<Result<ProcessingSummary, ContextRefreshErrorInstance>> {
    // Reset per-run bookkeeping so the same pool can be invoked more than once.
    this.failedConversations = []
    this.writtenCount = 0
    this.upToDateCount = 0
    this.checkpointSaveFailures = 0

    const queue: QueueItem[] = conversationsToProcess.map((meta) => ({ meta, attempts: 0 }))
    const activeTasks: Promise<Result<void, ContextRefreshErrorInstance>>[] = []

    while (queue.length > 0 || activeTasks.length > 0) {
      const worker = this.workers.find((w) => !w.isBusy)

      if (worker && queue.length > 0) {
        const item = queue.shift()!
        worker.isBusy = true

        const task = this.runExtraction(worker, item, queue)
        activeTasks.push(task)

        try {
          await task
        } finally {
          worker.isBusy = false
          activeTasks.splice(activeTasks.indexOf(task), 1)
        }
      } else {
        await new Promise((resolve) => setTimeout(resolve, POLLING_INTERVAL_MS))
      }
    }

    const summary = this.buildSummary(conversationsToProcess.length)
    this.logRunOutcome(summary)
    return ok(summary)
  }

  async close(): Promise<void> {
    // best-effort teardown; if the context is already gone, Playwright throws and we ignore.
    const closeResult = await from(async () => {
      await this.sharedBrowserContext?.close()
    })
    if (!closeResult.ok) {
      logger.debug('close: sharedBrowserContext.close failed', errorMessageOf(closeResult.error))
    }
    this.sharedBrowserContext = null
  }

  private async runExtraction(
    worker: ExtractionWorker,
    item: QueueItem,
    queue: QueueItem[]
  ): Promise<Result<void, ContextRefreshErrorInstance>> {
    await this.rateLimiter.acquire()
    try {
      const extractResult = await worker.extractor.extract(item.meta.url, item.meta.id)
      if (!extractResult.ok) {
        await this.handleFailure(worker, item, queue, extractResult.error)
        return err(new ContextRefreshError('Extraction failed'))
      }
      await this.handleSuccess(worker, item, extractResult.value)
    } finally {
      this.rateLimiter.release()
    }
    return ok(undefined)
  }

  private async handleSuccess(
    worker: ExtractionWorker,
    item: QueueItem,
    conversation: ExtractedConversation
  ): Promise<void> {
    const meta = item.meta
    const existingHash = this.checkpointManager.getContentHash(meta.id)
    const isUpToDate = !!existingHash && existingHash === conversation.contentHash

    if (isUpToDate) {
      // Content unchanged: skip the disk write entirely, but still record the
      // conversation as handled for this run.
      const markResult = await this.checkpointManager.markAsProcessed(meta.id)
      if (!markResult.ok) {
        this.handleCheckpointSaveFailure(
          meta,
          item.attempts,
          'mark as processed (up-to-date skip)',
          markResult.error
        )
        return
      }

      this.upToDateCount++
      const { processed, total } = this.checkpointManager.getProcessingProgress()
      const progressLabel = `[${processed}/${total}]`
      logger.info(
        `${progressLabel} Up to date: ${safeLog(conversation.title)} (hash match, skipped write)`
      )
      worker.extractor.recoverTimeout()
      return
    }

    const writeResult = await this.writeConversationAsMarkdown(conversation)
    if (!writeResult.ok) {
      errorBus.emitError('Failed to write conversation', writeResult.error, {
        conversationId: meta.id,
        url: meta.url,
      })
      this.recordFailure(
        meta,
        item.attempts,
        `Markdown write failed: ${errorMessageOf(writeResult.error)}`,
        writeResult.error
      )
      return
    }

    // Only persist the content hash after the atomic write has succeeded, so a
    // crash between the write and this save simply re-writes identical content
    // on the next run instead of ever skipping a file that does not exist.
    const markResult = await this.checkpointManager.markAsProcessed(
      meta.id,
      conversation.contentHash
    )
    if (!markResult.ok) {
      this.handleCheckpointSaveFailure(
        meta,
        item.attempts,
        'persist content hash after write',
        markResult.error
      )
      return
    }

    this.writtenCount++
    const { processed, total } = this.checkpointManager.getProcessingProgress()
    const progressLabel = `[${processed}/${total}]`
    logger.info(`${progressLabel} Processed: ${safeLog(conversation.title)}`)
    worker.extractor.recoverTimeout()
  }

  private async writeConversationAsMarkdown(
    conversation: ExtractedConversation
  ): Promise<Result<void, ContextRefreshErrorInstance>> {
    const data = conversation
    const outputDir = this.config.exportDir
    const safeSpaceName = slugify(data.spaceName)
    const spaceSpecificDirectory = join(outputDir, safeSpaceName)

    return from(async () => {
      if (!existsSync(spaceSpecificDirectory)) {
        mkdirSync(spaceSpecificDirectory, { recursive: true })
      }

      const fileName = `${data.safeFilename}.md`
      const destinationFilePath = join(spaceSpecificDirectory, fileName)

      const headerTitle = `# ${data.title}\n\n`
      const metadataBlock =
        `**Space:** ${data.spaceName}  \n` +
        `**ID:** ${data.id}  \n` +
        `**Date:** ${data.timestamp.toISOString()}  \n\n`
      const content = headerTitle + metadataBlock + data.content

      const tmpPath = `${destinationFilePath}.tmp`
      writeFileSync(tmpPath, content, 'utf-8')

      const fs = await import('node:fs')
      fs.renameSync(tmpPath, destinationFilePath)

      if (!existsSync(destinationFilePath) || statSync(destinationFilePath).size === 0) {
        throw new ContextRefreshError(`Exported file is missing or empty: ${destinationFilePath}`)
      }
    })
  }

  private async handleFailure(
    worker: ExtractionWorker,
    item: QueueItem,
    queue: QueueItem[],
    error: unknown
  ): Promise<void> {
    const isTimeout = isTypedError(error, NoDataError)
    const isContextLost = isTypedError(error, ExtractionError)

    if (isTimeout) worker.extractor.recoverTimeout()

    if (isContextLost) {
      logger.warn('Browser context lost. Refreshing worker context...')
      await this.refreshContext()
    }

    if (item.attempts < MAX_RETRIES) {
      item.attempts++
      logger.warn(`Retrying ${item.meta.url} (attempt ${item.attempts}/${MAX_RETRIES})...`)
      await new Promise((resolve) => setTimeout(resolve, 500 * item.attempts))
      queue.push(item)
      return
    }

    // Retries exhausted: leave the conversation unmarked (it stays pending in
    // the checkpoint) and surface it in the run's failure list.
    const totalAttempts = item.attempts + 1
    errorBus.emitError(`Failed to process ${item.meta.url} after ${MAX_RETRIES} retries`, error, {
      url: item.meta.url,
      attempts: totalAttempts,
      conversationId: item.meta.id,
    })
    this.recordFailure(
      item.meta,
      totalAttempts,
      `Extraction failed after ${totalAttempts} attempt(s): ${errorMessageOf(error)}`,
      error
    )
  }

  /**
   * A checkpoint save must never fail silently. Record the persistence error so
   * the run summary reports the conversation as not persisted (and therefore
   * still pending), and emit it on the error bus for diagnostics.
   */
  private handleCheckpointSaveFailure(
    meta: ConversationMeta,
    attempts: number,
    phase: string,
    error: unknown
  ): void {
    this.checkpointSaveFailures++
    errorBus.emitError(`Checkpoint save failed while trying to ${phase}`, error, {
      conversationId: meta.id,
      url: meta.url,
    })
    this.recordFailure(
      meta,
      attempts,
      `Checkpoint not persisted (${phase}): ${errorMessageOf(error)}`,
      error
    )
  }

  private recordFailure(
    meta: ConversationMeta,
    attempts: number,
    reason: string,
    cause?: unknown
  ): void {
    // A conversation can reach here only once at terminal failure, but guard
    // against duplicates so the printed list stays a 1:1 mapping of pending IDs.
    if (this.failedConversations.some((failure) => failure.conversationId === meta.id)) return
    this.failedConversations.push({
      conversationId: meta.id,
      url: meta.url,
      attempts,
      reason,
      ...(cause === undefined ? {} : { cause }),
    })
  }

  private buildSummary(total: number): ProcessingSummary {
    const processed = this.writtenCount + this.upToDateCount
    return {
      total,
      processed,
      written: this.writtenCount,
      upToDate: this.upToDateCount,
      failed: this.failedConversations.length,
      failedConversations: [...this.failedConversations],
      checkpointPersisted: this.checkpointSaveFailures === 0,
    }
  }

  private logRunOutcome(summary: ProcessingSummary): void {
    if (summary.failed === 0) {
      logger.info(
        `Extraction run finished: ${summary.processed}/${summary.total} handled ` +
          `(${summary.written} written, ${summary.upToDate} already up to date, 0 failed).`
      )
      return
    }

    logger.warn(
      `Extraction run finished with ${summary.failed} failed conversation(s) ` +
        `(not counted as processed; they remain pending for the next run):`
    )
    for (const failure of summary.failedConversations) {
      // Each line is independently checkable and always carries ID + URL.
      logger.warn(
        `  failed id=${failure.conversationId} url=${failure.url} ` +
          `attempts=${failure.attempts} reason=${failure.reason}`
      )
    }

    if (!summary.checkpointPersisted) {
      logger.warn(
        'Checkpoint persistence failed for at least one conversation; its progress was not saved and it will be retried on the next run.'
      )
    }
  }

  private async refreshContext(): Promise<void> {
    if (this.isRefreshing) return
    this.isRefreshing = true
    const result = await this.replaceBrowserContext()
    if (!result.ok) {
      errorBus.emitError('Failed to refresh worker context', result.error)
    }
    this.isRefreshing = false
  }

  private async replaceBrowserContext(): Promise<Result<void, ContextRefreshErrorInstance>> {
    return this.resultFactory.from(async () => {
      // best-effort close before re-creating; a stale context is what we are refreshing away.
      const closeResult = await from(async () => {
        await this.sharedBrowserContext?.close()
      })
      if (!closeResult.ok) {
        logger.debug(
          'refreshContext: sharedBrowserContext.close failed',
          errorMessageOf(closeResult.error)
        )
      }
      this.sharedBrowserContext = await this.browser.newContext({
        storageState: this.config.authStoragePath,
      })
      for (const worker of this.workers) {
        worker.extractor = new ConversationExtractor(this.config, this.sharedBrowserContext)
      }
    })
  }
}
