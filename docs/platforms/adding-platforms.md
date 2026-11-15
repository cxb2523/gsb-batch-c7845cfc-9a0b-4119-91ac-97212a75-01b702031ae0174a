# Adding New Platforms

Social Data Pipeline supports two ways to add new platforms:

1. **Custom platform** (config-only) — Run `sdp source add <name>` and select `custom`. No code required.
2. **Built-in platform** (with custom parser) — For platforms needing specialized logic (computed fields, format handling, etc.).

---

## Option 1: Custom Platform (Recommended)

If your data is standard JSON/NDJSON or CSV, use the custom platform system. No code changes needed.

```bash
python sdp.py source add <name>
```

Select `custom` as the platform type. The interactive setup generates `config/sources/<name>/platform.yaml` with file patterns, fields, types, and indexes.

See [Custom Platforms](custom.md) for full details.

---

## Option 2: Built-in Platform (Custom Parser)

For platforms needing specialized parsing logic (like Reddit's deletion detection or base-36 ID conversion):

### 1. Create Platform Template

Create `config/templates/{platform}.yaml` with all sections (`db_schema`, `data_types`, `file_patterns`, `indexes`, `field_types`, `fields`). This template is copied to `config/sources/<name>/platform.yaml` when a source is added with `sdp source add`.

### 2. Create Parser Module

Create `social_data_pipeline/platforms/{platform}/parser.py` with these functions:

```python
def transform_json(data, dataset, data_type_config, fields_to_extract, fallback_tracker=None):
    """Transform a single JSON record into a list of CSV values."""
    ...

def process_single_file(input_file, output_file, data_type, data_type_config, fields_to_extract):
    """Process a single JSON file to Parquet/CSV.

    Returns a core.parser.ParseSettlement describing the per-file outcome
    (success_rows, bad_rows, output path, timestamp-fallback usage, status).
    Whole-file failures return a 'failed' settlement after temp-file cleanup;
    bad lines are skipped and counted rather than aborting the file.
    """
    ...

def parse_to_csv(input_file, output_dir, data_type, platform_config, use_type_subdir=True):
    """Main entry point. Parse a JSON file to Parquet/CSV. Returns output path.

    Writes a per-input-file `<input>.settlement.json` beside the output and
    raises ParseFailureError (carrying the settlement) on whole-file failure.
    """
    ...

def parse_files_parallel(files, output_dir, platform_config, workers):
    """Parse multiple files in parallel.

    Returns one ParseSettlement per input file, in input order. A single file
    failing never interrupts the others; callers branch on settlement.status
    ('success' / 'partial_success' / 'failed').
    """
    ...
```

The shared settlement machinery lives in `social_data_pipeline/core/parser.py`
(`ParseSettlement`, `iter_ndjson_records`, `write_settlement`). Reuse it so
every platform produces the same verifiable per-file records and settlement
JSONs are named after the input file (parallel-safe).

### 3. Register the Platform

Update `social_data_pipeline/orchestrators/parse.py` to handle your platform in the `get_platform_parser()` function:

```python
def get_platform_parser(platform):
    if platform == 'reddit':
        from ..platforms.reddit import parser
        return parser
    elif platform == 'my_platform':
        from ..platforms.my_platform import parser
        return parser
    elif platform.startswith('custom/'):
        from ..platforms.custom import parser
        return parser
    else:
        raise ConfigurationError(f"Unknown platform: {platform}")
```

### 4. Run

```bash
python sdp.py source add <name>     # Select your platform during setup
python sdp.py run parse --source <name>
```
