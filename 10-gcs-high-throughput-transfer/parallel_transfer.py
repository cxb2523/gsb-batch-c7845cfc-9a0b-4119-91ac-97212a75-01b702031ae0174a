# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Parallel bulk upload and download using transfer_manager.

`google.cloud.storage.transfer_manager` uses a pool of worker processes to
transfer many files concurrently — substantially faster than a serial loop
for bulk data movement (pipelines, ETL, batch exports).

Key API:
  upload_many_from_filenames()  — N local files → GCS in parallel
  download_many_to_path()       — N GCS blobs → local directory in parallel

Each function returns a list of results aligned with the input list:
  None   = success
  Exception = failure for that file (others continue)

Design: set max_workers to match available CPU / network headroom.
On Cloud Run (2 vCPU) 4 workers is a safe default; scale up for bulk jobs.
"""

import os
import tempfile
import time
from pathlib import Path

from google.cloud import storage
from google.cloud.storage import transfer_manager

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

PREFIX = "demo/transfer-manager/"
WORKERS = 4
FILE_COUNT = 6
LINES_PER_FILE = 300


def _create_local_files(count: int, lines: int) -> tuple[Path, list[str]]:
    """Create `count` small text files in a temp directory. Returns (dir, names)."""
    src_dir = Path(tempfile.mkdtemp(prefix="tm_src_"))
    filenames = []
    for i in range(count):
        path = src_dir / f"part_{i:02d}.txt"
        path.write_text(f"chunk {i}\n" * lines)
        filenames.append(path.name)
    print(f"Created {count} local files in {src_dir}")
    return src_dir, filenames


def _cleanup_local(directory: Path) -> None:
    """Remove a temp directory and all its contents."""
    for p in sorted(directory.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    directory.rmdir()


def parallel_upload(bucket: storage.Bucket, src_dir: Path,
                    filenames: list[str]) -> list:
    """Upload files in parallel. Returns the result list."""
    t0 = time.perf_counter()
    results = transfer_manager.upload_many_from_filenames(
        bucket,
        filenames,
        source_directory=str(src_dir),
        blob_name_prefix=PREFIX,
        max_workers=WORKERS,
    )
    elapsed = time.perf_counter() - t0
    print(f"\n--- Parallel upload ({WORKERS} workers, {len(filenames)} files) ---")
    ok = fail = 0
    for name, result in zip(filenames, results):
        status = "OK" if result is None else f"FAILED: {result}"
        icon = "+" if result is None else "!"
        print(f"  [{icon}] {PREFIX}{name:<18} {status}")
        if result is None:
            ok += 1
        else:
            fail += 1
    print(f"Uploaded: {ok} OK, {fail} failed  |  {elapsed:.2f}s")
    return results


def parallel_download(bucket: storage.Bucket, blob_names: list[str]) -> Path:
    """Download blobs in parallel to a temp directory. Returns the dst directory."""
    dst_dir = Path(tempfile.mkdtemp(prefix="tm_dst_"))
    t0 = time.perf_counter()
    results = transfer_manager.download_many_to_path(
        bucket,
        blob_names,
        destination_directory=str(dst_dir),
        max_workers=WORKERS,
    )
    elapsed = time.perf_counter() - t0
    print(f"\n--- Parallel download ({WORKERS} workers, {len(blob_names)} blobs) ---")
    ok = fail = 0
    for name, result in zip(blob_names, results):
        local = dst_dir / name
        size = local.stat().st_size if local.exists() else 0
        status = "OK" if result is None else f"FAILED: {result}"
        icon = "+" if result is None else "!"
        print(f"  [{icon}] {name:<40} {size:>7} B  {status}")
        if result is None:
            ok += 1
        else:
            fail += 1
    print(f"Downloaded: {ok} OK, {fail} failed  |  {elapsed:.2f}s")
    return dst_dir


def run_parallel_transfer_demo(client: storage.Client) -> None:
    """End-to-end parallel upload + download + cleanup demo."""
    bucket = client.bucket(GCS_BUCKET)

    # Create local source files
    src_dir, filenames = _create_local_files(FILE_COUNT, LINES_PER_FILE)

    try:
        # Parallel upload
        parallel_upload(bucket, src_dir, filenames)

        # Parallel download
        blob_names = [f"{PREFIX}{name}" for name in filenames]
        dst_dir = parallel_download(bucket, blob_names)

        # Cleanup local dst
        _cleanup_local(dst_dir)
        print(f"Cleaned up local dst dir.")
    finally:
        # Cleanup remote objects and local src
        remote_blobs = list(client.list_blobs(GCS_BUCKET, prefix=PREFIX))
        if remote_blobs:
            bucket.delete_blobs(remote_blobs)
            print(f"\nCleanup: deleted {len(remote_blobs)} remote objects under {PREFIX}")
        _cleanup_local(src_dir)
        print("Cleaned up local src dir.")
