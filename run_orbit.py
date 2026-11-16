#!/usr/bin/env python3
"""Run an OrbitPilot YAML configuration against local static pages.

Usage:
    python run_orbit.py path/to/config.yaml [--pages-dir DIR]

Exits 0 when the run is complete (no failed steps), 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys

from orbitpilot import StaticPageAdapter, load_yaml, render_manifest, run_strategy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="path to the YAML configuration")
    parser.add_argument(
        "--pages-dir",
        default=None,
        help="directory with local static pages "
        "(default: <config dir>/pages, falling back to the config's "
        "session.pages_dir if set)",
    )
    args = parser.parse_args(argv)

    config = load_yaml(open(args.config, "r", encoding="utf-8").read())
    session = (config or {}).get("session") or {}
    pages_dir = (
        args.pages_dir
        or session.get("pages_dir")
        or os.path.join(os.path.dirname(os.path.abspath(args.config)), "pages")
    )
    adapter = StaticPageAdapter(pages_dir, start_page=session.get("start_page"))

    manifest = run_strategy(config, adapter)
    print(render_manifest(manifest))
    return 0 if manifest["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
