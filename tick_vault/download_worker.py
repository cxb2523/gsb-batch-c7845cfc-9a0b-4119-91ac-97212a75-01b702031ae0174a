"""Download worker module for fetching tick data chunks."""

import asyncio
from dataclasses import dataclass

from httpx import AsyncClient

from .chunk import TickChunk
from .config import CONFIG
from .fetcher import RetriesExhaustedError, fetch_with_retry
from .logger import logger


@dataclass(frozen=True)
class DownloadResult:
    """
    Outcome of a single chunk download attempt.

    Attributes:
        chunk: The chunk that was attempted
        error: The error that caused the attempt to fail, or None on success.
            A None error means the chunk was either downloaded and saved to
            disk or confirmed to have no data (404); in both cases it must be
            recorded in the metadata database. A non-None error means the
            chunk failed and must NOT be recorded, so a future run retries it.
    """

    chunk: TickChunk
    error: Exception | None = None


async def download_worker(
    proxy: str | None,
    input_queue: asyncio.Queue[TickChunk | None],
    output_queue: asyncio.Queue[DownloadResult],
) -> None:
    """
    Worker coroutine that downloads tick data chunks from a queue.

    This worker processes chunks from input_queue, fetches their data,
    saves successful downloads to disk, and reports results to output_queue
    for metadata tracking. The worker runs until it receives None as a
    sentinel value or times out waiting for work.

    A chunk whose retries are exhausted (RetriesExhaustedError) is reported
    as a failed DownloadResult and the worker continues with the next chunk,
    so one failing chunk never aborts the whole batch. Fatal errors
    (ForbiddenError, unexpected RuntimeError) are still raised immediately
    to stop all workers and alert the parent process.

    Args:
        proxy: Optional proxy URL for the HTTP client (e.g., 'http://proxy:8080')
        input_queue: Queue of TickChunk objects to download. None signals shutdown.
        output_queue: Queue for reporting DownloadResult objects

    Raises:
        ForbiddenError: If access is blocked/forbidden (propagated from fetch_with_retry)
        RuntimeError: For unexpected errors (propagated from fetch_with_retry)
        FileExistsError: If attempting to save a chunk that already exists on disk

    Workflow:
        1. Fetch chunk from input_queue (with timeout)
        2. If timeout occurs, exit gracefully (assumes parent crashed)
        3. If chunk is None, break and exit
        4. Fetch data using fetch_with_retry (which handles retries internally)
        5. If retries are exhausted, report a failed result and continue
        6. If data exists (not None), save to disk using chunk.save()
        7. Put a successful DownloadResult to output_queue
    """
    logger.debug(f"Download worker started with proxy: {proxy}")
    async with AsyncClient(proxy=proxy) as client:
        while True:
            try:
                # Wait for chunk with timeout
                chunk = await asyncio.wait_for(
                    input_queue.get(), timeout=CONFIG.worker_queue_timeout
                )
            except TimeoutError:
                logger.warning("Download worker timeout - assuming parent crashed")
                # Timeout - assume parent process crashed or no more work
                break

            # None is the sentinel value to stop the worker
            if chunk is None:
                logger.debug("Download worker received stop signal")
                break

            # Fetch data with automatic retry logic
            # ForbiddenError and unexpected RuntimeError are raised immediately
            try:
                content = await fetch_with_retry(client, chunk.url)
            except RetriesExhaustedError as e:
                # Per-chunk failure: report it and keep processing other chunks.
                # The chunk is NOT forwarded to the metadata worker, so it stays
                # "not attempted" and will be retried on the next run.
                logger.warning(
                    f"Chunk failed after all retries, continuing with remaining "
                    f"chunks: {chunk.symbol} {chunk.time.isoformat()} ({e})"
                )
                await output_queue.put(DownloadResult(chunk=chunk, error=e))
                continue

            if content is not None:
                # Data exists - save to disk
                chunk.save(content)
                logger.debug(
                    f"Downloaded and saved chunk: {chunk.symbol} {chunk.time.isoformat()}"
                )
            else:
                logger.debug(
                    f"No data exists for chunk: {chunk.symbol} {chunk.time.isoformat()}"
                )

            await output_queue.put(DownloadResult(chunk=chunk))

    logger.debug("Download worker shutting down")
