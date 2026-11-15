# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Object operations: upload, download, list, copy/rename, delete, metadata.

GCS is a flat key-value store — "folders" are just common name prefixes.
Use the delimiter="/" listing pattern to emulate folder-style navigation
without changing the underlying flat structure.

Rename = copy + delete (GCS has no native rename operation).
"""

import os
import tempfile
from pathlib import Path

from google.cloud import storage


def upload_file(bucket: storage.Bucket, local_path: str, blob_name: str) -> storage.Blob:
    """Upload a local file to GCS. Returns the Blob after reload."""
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path, content_type="text/csv")
    blob.reload()
    print(f"\n--- upload_from_filename ---")
    print(f"Source      : {local_path}")
    print(f"Destination : gs://{bucket.name}/{blob.name}")
    print(f"Size        : {blob.size} bytes")
    print(f"MD5         : {blob.md5_hash}")
    return blob


def upload_string(bucket: storage.Bucket, content: str, blob_name: str,
                  content_type: str = "text/plain") -> storage.Blob:
    """Upload in-memory string content to GCS. Returns the Blob after reload."""
    blob = bucket.blob(blob_name)
    blob.upload_from_string(content, content_type=content_type)
    blob.reload()
    print(f"\n--- upload_from_string ---")
    print(f"Destination : gs://{bucket.name}/{blob.name}")
    print(f"Size        : {blob.size} bytes")
    print(f"Content-Type: {blob.content_type}")
    return blob


def list_objects(client: storage.Client, bucket_name: str,
                 prefix: str = "", delimiter: str = None) -> list[storage.Blob]:
    """
    List objects under a prefix. With delimiter="/", emulates folder listing:
    - iterator (blobs) = objects at this level
    - iterator.prefixes = subfolders
    """
    blobs = list(client.list_blobs(bucket_name, prefix=prefix, delimiter=delimiter))
    print(f"\n--- list_blobs (prefix='{prefix}') ---")
    if blobs:
        for blob in blobs:
            updated = blob.updated.strftime("%Y-%m-%d %H:%M:%S") if blob.updated else "-"
            print(f"  {blob.name:<55} {blob.size:>10} B   {updated}")
    else:
        print("  (no objects found)")
    return blobs


def get_metadata(blob: storage.Blob) -> None:
    """Read and display standard + custom metadata for a blob."""
    blob.reload()
    print(f"\n--- Object metadata: {blob.name} ---")
    print(f"Content type   : {blob.content_type}")
    print(f"Size           : {blob.size} bytes")
    print(f"MD5            : {blob.md5_hash}")
    print(f"Cache control  : {blob.cache_control}")
    print(f"Custom metadata: {blob.metadata}")
    print(f"Generation     : {blob.generation}")
    print(f"Metageneration : {blob.metageneration}")
    print(f"Updated        : {blob.updated}")


def set_metadata(blob: storage.Blob, content_type: str = None,
                 cache_control: str = None, custom: dict = None) -> None:
    """Update standard and custom metadata via a PATCH (sends only changed fields)."""
    if content_type:
        blob.content_type = content_type
    if cache_control:
        blob.cache_control = cache_control
    if custom:
        blob.metadata = custom
    blob.patch()
    print(f"\n--- Metadata updated for {blob.name} ---")
    get_metadata(blob)


def copy_blob(bucket: storage.Bucket, source_blob: storage.Blob,
              dest_name: str) -> storage.Blob:
    """Copy a blob within the same bucket. Returns the copy."""
    copied = bucket.copy_blob(source_blob, bucket, new_name=dest_name)
    print(f"\n--- copy_blob ---")
    print(f"Source      : gs://{bucket.name}/{source_blob.name}")
    print(f"Destination : gs://{bucket.name}/{copied.name}")
    return copied


def rename_blob(bucket: storage.Bucket, source_blob: storage.Blob,
                new_name: str) -> storage.Blob:
    """
    Rename (move) a blob within the same bucket.
    GCS has no native rename — this is copy + delete of the source.
    """
    renamed = bucket.copy_blob(source_blob, bucket, new_name=new_name)
    bucket.delete_blob(source_blob.name)
    print(f"\n--- rename_blob (copy + delete) ---")
    print(f"Original    : gs://{bucket.name}/{source_blob.name}  (deleted)")
    print(f"Renamed to  : gs://{bucket.name}/{renamed.name}")
    return renamed


def download_to_file(blob: storage.Blob) -> Path:
    """Download a blob to a local temp file. Returns the Path."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        local_path = Path(tmp.name)
    blob.download_to_filename(str(local_path))
    print(f"\n--- download_to_filename ---")
    print(f"Source     : gs://{blob.bucket.name}/{blob.name}")
    print(f"Local path : {local_path}")
    print(f"Local size : {local_path.stat().st_size} bytes")
    return local_path


def download_as_text(blob: storage.Blob, preview_lines: int = 3) -> str:
    """Download a blob as a UTF-8 string and print a preview."""
    text = blob.download_as_text()
    lines = text.splitlines()
    print(f"\n--- download_as_text ---")
    print(f"Total lines : {len(lines)}")
    print(f"Header      : {lines[0] if lines else '(empty)'}")
    for row in lines[1: 1 + preview_lines]:
        print(f"Row         : {row}")
    return text


def delete_single(blob: storage.Blob) -> None:
    """Delete a single blob."""
    name = blob.name
    blob.delete()
    print(f"\n--- delete_blob ---")
    print(f"Deleted: gs://{blob.bucket.name}/{name}")


def delete_by_prefix(client: storage.Client, bucket_name: str, prefix: str) -> int:
    """
    Bulk-delete all objects under a prefix.
    Always materializes and prints the list before deleting — safety check.
    Returns count of deleted objects.
    """
    bucket = client.bucket(bucket_name)
    to_delete = list(client.list_blobs(bucket_name, prefix=prefix))
    print(f"\n--- bulk delete: {len(to_delete)} object(s) under '{prefix}' ---")
    for blob in to_delete:
        print(f"  will delete: {blob.name} ({blob.size} B)")

    if to_delete:
        bucket.delete_blobs(to_delete)
        print(f"Bulk delete complete ({len(to_delete)} objects).")
    else:
        print("Nothing to delete.")
    return len(to_delete)
