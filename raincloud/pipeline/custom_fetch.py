# SPDX-FileCopyrightText: 2026 Raincloud Maintainers
# SPDX-License-Identifier: Apache-2.0
"""Named custom fetch helpers, dispatched by `fetch.notes` when
`fetch.type = "custom"` (the names are declared in
`raincloud._registry.CUSTOM_FETCHERS`). Each function returns a list of `Path`
objects that the subsequent extract / parse / transform stages will consume as
inputs — same contract as the stock `fetch_http` / `fetch_kaggle` handlers.

Download through `fetch.fetch_url` into `fetch.slug_dir(...)`, so a custom
fetcher gets the stage's guarantees: receipt-checked caching, atomic writes,
the fetch deadline and the truncation check.
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

from .fetch import fetch_url, slug_dir, url_filename
from .spec import display_path, spec_field


def public_bi_fetch(spec: dict, *, verify: bool = False) -> list[Path]:
    """Fetch a Public BI Benchmark workload.

    `verify` re-hashes cached files against their receipts (`fetch --verify`).

    Sources:
      - Partition list:  GitHub `cwida/public_bi_benchmark/benchmark/<W>/data-urls.txt`
                         (cached beside the partitions, so a fully cached
                          build needs no network)
      - Partition data:  `http://event.cwi.nl/da/PublicBIbenchmark/<W>/<W>_N.csv.bz2`
                         (upgraded to https, since CWI serves both)
      - Schema:          GitHub `benchmark/<W>/tables/<W>_N.table.sql`, one per
                         partition, read by the `public_bi_merge` handler

    Each partition's data and its schema settle as one unit: a partition whose
    download fails is recorded and reported on stderr (naming the partition),
    its files stay out of the returned list, and the remaining partitions are
    still fetched. A rerun then hits the receipts of the partitions that
    settled and retries only the ones that failed. Only the partition list
    itself is fatal — without it there is nothing to settle.
    """
    workload = spec_field(spec, "transform.params.workload")
    if not workload:
        raise ValueError("public_bi_fetch: spec missing transform.params.workload")
    target_dir = slug_dir(spec["slug"], spec)
    base = f"https://raw.githubusercontent.com/cwida/public_bi_benchmark/master/benchmark/{workload}"

    # 1. The partition list. Fails the whole fetch: no list, no partitions.
    urls_txt = fetch_url(f"{base}/data-urls.txt", target_dir / "data-urls.txt", verify=verify)
    url_lines = [ln.strip() for ln in urls_txt.read_text().splitlines() if ln.strip()]

    failures: list[str] = []

    # 2. Each partition bz2. A failed download leaves nothing under the final
    # name (`fetch._atomic_download`), so the partition is simply unsettled:
    # recorded here, retried on the next run, never returned as a result.
    data: dict[int, Path] = {}
    for n, raw_url in enumerate(url_lines, start=1):
        url = raw_url.replace("http://", "https://")
        try:
            dest = fetch_url(url, target_dir / url_filename(url), verify=verify)
        except Exception as e:
            failures.append(f"partition {n} data ({url}): {type(e).__name__}: {e}")
            print(f"  [warn] partition {n} of {workload}: data download failed: {e}",
                  file=sys.stderr)
            continue
        print(f"      -> {display_path(dest)} ({dest.stat().st_size:,} B)")
        data[n] = dest

    # 3. Each settled partition's `<W>_N.table.sql`. The handler needs them all
    # because some workloads have schema drift across partitions (MLB,
    # SalariesFrance, TrainsUK1, Wins, Rentabilidad, TableroSistemaPenal).
    schemas: dict[int, Path] = {}
    unsettled: set[int] = set()
    for n in sorted(data):
        schema_dest = target_dir / f"{workload}_{n}.table.sql"
        try:
            fetch_url(f"{base}/tables/{workload}_{n}.table.sql", schema_dest, verify=verify)
        except urllib.error.HTTPError as e:
            # Only a definitive "upstream has no such schema" is a missing
            # schema: `public_bi_merge` drops that partition and says so. Any
            # other failure leaves the partition's outcome unknown, so the
            # whole unit counts as failed rather than silently shrinking the
            # build.
            if e.code == 404:
                print(f"  [warn] no schema for partition {n} of {workload}: {e}", file=sys.stderr)
                continue
            unsettled.add(n)
            failures.append(f"partition {n} schema: HTTP {e.code}: {e}")
            print(f"  [warn] partition {n} of {workload}: schema download failed: {e}",
                  file=sys.stderr)
            continue
        except Exception as e:
            unsettled.add(n)
            failures.append(f"partition {n} schema: {type(e).__name__}: {e}")
            print(f"  [warn] partition {n} of {workload}: schema download failed: {e}",
                  file=sys.stderr)
            continue
        schemas[n] = schema_dest

    # A partition whose schema could not be fetched has not settled: its data
    # stays on disk (receipt-checked cache for the next run) but is not handed
    # back as a result — returning it would make `public_bi_merge` fail on the
    # missing schema, or worse, a half-fetched unit would read as success.
    out = [data[n] for n in sorted(data) if n not in unsettled]
    out += [schemas[n] for n in sorted(schemas) if n not in unsettled]
    if failures:
        print(f"  [warn] {workload}: {len(failures)} of {len(url_lines)} partition(s) "
              f"failed; rerun to retry them (settled partitions are cached):", file=sys.stderr)
        for failure in failures:
            print(f"    - {failure}", file=sys.stderr)
    return out
