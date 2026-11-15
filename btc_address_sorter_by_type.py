"""
BTC Address Sorter by Type — streaming / resumable batch edition.

Changes compared to the original all-in-memory version:
  * Input is read line by line in fixed-size batches (no full-file load,
    no shared result list held by worker processes).
  * Worker processes only return the classification result of *their own
    batch*; the main process aggregates and flushes every batch to
    1xxxx.txt / 3xxxx.txt / bcxxxx.txt.
  * A progress file (btc_sorter_progress.json by default) is maintained
    atomically after every successfully written batch.
  * Re-runs resume after the last committed line (append mode) when the
    input file is unchanged (same size + mtime); otherwise processing
    restarts from line 0 and old outputs are overwritten.
  * Any batch write failure stops further writes, keeps what is already
    on disk, leaves the progress file at the last committed line and
    exits with a non-zero status.

Classification rules are unchanged prefix checks: '1', '3', 'bc1'.
Unrecognized / empty lines are only counted, never written to output.
"""

import argparse
import json
import multiprocessing
import os
import sys
import tempfile
from datetime import datetime, timezone

# Windows consoles commonly default to a legacy code page (e.g. GBK/cp936)
# which cannot encode the bullet/em-dash characters used in the UI; force
# UTF-8 with replacement before colorama wraps the streams.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

from colorama import Fore, Style, init
from tqdm import tqdm

try:
    from pyfiglet import Figlet
    _HAS_FIGLET = True
except Exception:  # pragma: no cover - optional cosmetic dependency
    _HAS_FIGLET = False

init(autoreset=True)

CATEGORIES = ('1', '3', 'bc')
OUTPUT_FILES = {'1': '1xxxx.txt', '3': '3xxxx.txt', 'bc': 'bcxxxx.txt'}
PROGRESS_FILE_NAME = 'btc_sorter_progress.json'
DEFAULT_BATCH_SIZE = 10000
PROGRESS_VERSION = 1

# Test/operational hook: when set to a positive integer N, the main process
# simulates a write failure on the N-th batch *before* writing it.
FAIL_AFTER_ENV = 'BTC_SORTER_FAIL_AFTER_BATCHES'

# Exit codes
EXIT_OK = 0
EXIT_WRITE_FAILURE = 1
EXIT_SETUP_FAILURE = 2


# --------------------------------------------------------------------------
# Classification (pure function, runs inside worker processes)
# --------------------------------------------------------------------------
def classify_address(address):
    """Prefix-based classification, identical rules to the original script."""
    if address.startswith('1'):
        return '1'
    if address.startswith('3'):
        return '3'
    if address.startswith('bc1'):
        return 'bc'
    return None


def classify_batch(payload):
    """Classify one batch.

    Parameters
    ----------
    payload : tuple(start_offset, end_offset, raw_lines)
        Byte offsets are echoed back untouched so the main process can
        advance its progress file in deterministic (input) order.

    Returns
    -------
    (start_offset, end_offset, total_lines, results, empty, unrecognized)
    where `results` only contains this batch's addresses — workers never
    hold results from other batches.
    """
    start_offset, end_offset, raw_lines = payload
    results = {'1': [], '3': [], 'bc': []}
    empty_lines = 0
    unrecognized_lines = 0

    for raw in raw_lines:
        try:
            line = raw.decode('utf-8')
        except UnicodeDecodeError:
            line = raw.decode('utf-8', errors='replace')
        line = line.strip()
        if not line:
            empty_lines += 1
            continue
        key = classify_address(line)
        if key is None:
            unrecognized_lines += 1
        else:
            results[key].append(line)

    return (start_offset, end_offset, len(raw_lines),
            results, empty_lines, unrecognized_lines)


# --------------------------------------------------------------------------
# Streaming reader
# --------------------------------------------------------------------------
def read_batches(binary_file, batch_size):
    """Yield (start_offset, end_offset, [raw byte lines]) batches.

    Offsets are plain byte positions (file is opened in binary mode),
    which makes resume seeks exact and encoding-independent.
    """
    batch = []
    start_offset = binary_file.tell()
    for raw_line in binary_file:
        batch.append(raw_line)
        if len(batch) >= batch_size:
            end_offset = binary_file.tell()
            yield start_offset, end_offset, batch
            batch = []
            start_offset = end_offset
    if batch:
        yield start_offset, binary_file.tell(), batch


# --------------------------------------------------------------------------
# Progress file
# --------------------------------------------------------------------------
def new_progress(input_file, input_size, input_mtime, batch_size, threads):
    return {
        'version': PROGRESS_VERSION,
        'input_file': os.path.abspath(input_file),
        'input_size': input_size,
        'input_mtime': input_mtime,
        'input_mtime_iso': datetime.fromtimestamp(
            input_mtime, tz=timezone.utc).isoformat(),
        'processed_lines': 0,
        'input_offset': 0,
        'counts': {'1': 0, '3': 0, 'bc': 0},
        'empty_lines': 0,
        'unrecognized_lines': 0,
        'output_offsets': {'1': 0, '3': 0, 'bc': 0},
        'batch_size': batch_size,
        'threads': threads,
        'completed': False,
        'updated_at': None,
    }


def save_progress_atomic(progress_path, progress):
    """Write progress via temp-file + os.replace so it can never be torn."""
    progress = dict(progress)
    progress['updated_at'] = datetime.now(timezone.utc).isoformat()
    directory = os.path.dirname(os.path.abspath(progress_path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix='.progress-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmp_file:
            json.dump(progress, tmp_file, indent=2, ensure_ascii=False)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, progress_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return progress


def decide_mode(progress_path, input_file, input_size, input_mtime,
                batch_size, threads):
    """Return (progress_dict, mode) with mode in {'resume', 'fresh'}."""
    fresh = new_progress(input_file, input_size, input_mtime,
                         batch_size, threads)
    if not os.path.exists(progress_path):
        return fresh, 'fresh', 'no progress file found'

    try:
        with open(progress_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        same_input = (
            os.path.abspath(str(saved.get('input_file', '')))
            == os.path.abspath(input_file)
        )
        same_size = saved.get('input_size') == input_size
        same_mtime = saved.get('input_mtime') == input_mtime
        if not (same_input and same_size and same_mtime):
            reason = (
                f"input changed (saved size={saved.get('input_size')}, "
                f"mtime={saved.get('input_mtime')}; current "
                f"size={input_size}, mtime={input_mtime})")
            return fresh, 'fresh', reason

        # Reuse saved state, filling any missing keys defensively.
        merged = fresh
        for key, value in saved.items():
            merged[key] = value
        for cat in CATEGORIES:
            merged['counts'].setdefault(cat, 0)
            merged['output_offsets'].setdefault(cat, 0)
        return merged, 'resume', 'progress file matches input (size & mtime)'
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return fresh, 'fresh', f'progress file unreadable ({exc}); ignoring'


# --------------------------------------------------------------------------
# Output handling (main process only)
# --------------------------------------------------------------------------
def open_output_handles(output_dir, mode, progress):
    """Open the three output files.

    fresh  -> create/truncate at offset 0.
    resume -> open existing files, seek to the committed offset and cut off
              any uncommitted tail so appends stay duplication-free.
    """
    os.makedirs(output_dir, exist_ok=True)
    handles = {}
    for cat in CATEGORIES:
        path = os.path.join(output_dir, OUTPUT_FILES[cat])
        if mode == 'fresh':
            handle = open(path, 'wb')
            handle.truncate(0)
        else:
            if os.path.exists(path):
                handle = open(path, 'r+b')
            else:
                handle = open(path, 'w+b')
            offset = int(progress['output_offsets'].get(cat, 0))
            handle.seek(offset)
            handle.truncate(offset)
        handles[cat] = handle
    return handles


def write_batch(handles, results):
    """Append one batch; return new byte offsets. Raises OSError on failure."""
    new_offsets = {}
    for cat in CATEGORIES:
        lines = results[cat]
        handle = handles[cat]
        if lines:
            payload = ('\n'.join(lines) + '\n').encode('utf-8')
            handle.write(payload)
        new_offsets[cat] = handle.tell()
    return new_offsets


# --------------------------------------------------------------------------
# CLI / UI
# --------------------------------------------------------------------------
def clear_terminal():
    os.system('cls' if os.name == 'nt' else 'clear')


def title():
    if _HAS_FIGLET:
        print(Fore.CYAN + Figlet(font='slant').renderText('BitMorphX')
              + Style.RESET_ALL)
    else:
        print(Fore.CYAN + 'BitMorphX' + Style.RESET_ALL)
    print(Fore.YELLOW + "  BTC ADDRESS SORTER — STREAMING / RESUMABLE BATCH MODE\n")
    print(Fore.GREEN + "  Supported Bitcoin Address Formats:" + Style.RESET_ALL)
    print(Fore.GREEN + "  • Legacy (P2PKH)         → Base58  → Starts with '1'")
    print(Fore.GREEN + "  • Nested SegWit (P2SH)   → Base58  → Starts with '3'")
    print(Fore.GREEN + "  • Native SegWit (P2WPKH) → Bech32  → Starts with 'bc1'\n"
          + Style.RESET_ALL)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Stream-classify BTC addresses with resumable batch writes.')
    parser.add_argument('-i', '--input', default=None,
                        help="Input file (default: btc_address.txt)")
    parser.add_argument('-o', '--output-dir', default='.',
                        help="Directory for 1xxxx.txt/3xxxx.txt/bcxxxx.txt "
                             "and the progress file (default: current dir)")
    parser.add_argument('-t', '--threads', type=int, default=None,
                        help='Worker processes, clamped to 1..CPU count')
    parser.add_argument('-b', '--batch-size', type=int,
                        default=DEFAULT_BATCH_SIZE,
                        help=f'Lines per batch (default: {DEFAULT_BATCH_SIZE})')
    parser.add_argument('-p', '--progress-file', default=None,
                        help=f'Progress JSON path (default: '
                             f'<output-dir>/{PROGRESS_FILE_NAME})')
    parser.add_argument('--max-batches', type=int, default=0,
                        help='Stop cleanly after N batches (planned stop, '
                             'exit 0); useful for chunked runs and tests')
    return parser.parse_args(argv)


def apply_interactive_defaults(args):
    """Backwards-compatible prompts when launched without CLI arguments."""
    cpu_cores = multiprocessing.cpu_count()
    if args.input is None and sys.stdin.isatty():
        args.input = input(
            Fore.YELLOW
            + "Enter input file name [default: btc_address.txt]: "
            + Style.RESET_ALL).strip() or 'btc_address.txt'
        if args.threads is None:
            try:
                raw = input(
                    Fore.YELLOW
                    + f"Enter number of CPU threads (1-{cpu_cores}) "
                      f"[default: {cpu_cores}]: "
                    + Style.RESET_ALL) or str(cpu_cores)
                args.threads = int(raw)
            except ValueError:
                args.threads = cpu_cores
    if args.input is None:
        args.input = 'btc_address.txt'
    if args.threads is None:
        args.threads = cpu_cores
    args.threads = max(1, min(int(args.threads), cpu_cores))
    if args.batch_size < 1:
        args.batch_size = DEFAULT_BATCH_SIZE
    if args.max_batches < 0:
        args.max_batches = 0
    return args


def print_summary(progress):
    print(Fore.CYAN + '\n  Progress file state:' + Style.RESET_ALL)
    print(f"    processed lines    : {progress['processed_lines']:,}")
    print(f"    hits '1' (1xxxx)   : {progress['counts']['1']:,}")
    print(f"    hits '3' (3xxxx)   : {progress['counts']['3']:,}")
    print(f"    hits 'bc1' (bcxxxx): {progress['counts']['bc']:,}")
    print(f"    empty lines skipped: {progress['empty_lines']:,}")
    print(f"    unrecognized lines : {progress['unrecognized_lines']:,}")
    print(f"    completed          : {progress['completed']}")


# --------------------------------------------------------------------------
# Main pipeline
# --------------------------------------------------------------------------
def run(args):
    input_file = args.input
    if not os.path.isfile(input_file):
        print(Fore.RED + f"File '{input_file}' not found.\n" + Style.RESET_ALL)
        return EXIT_SETUP_FAILURE

    stat_result = os.stat(input_file)
    input_size = stat_result.st_size
    input_mtime = stat_result.st_mtime

    progress_path = (args.progress_file
                     or os.path.join(args.output_dir, PROGRESS_FILE_NAME))

    progress, mode, reason = decide_mode(
        progress_path, input_file, input_size, input_mtime,
        args.batch_size, args.threads)

    if mode == 'resume':
        print(Fore.GREEN
              + "[MODE] RESUME — progress file found and input unchanged "
                "(same size & mtime)." + Style.RESET_ALL)
        print(Fore.GREEN
              + f"       Continuing from line {progress['processed_lines']} "
                f"(byte offset {progress['input_offset']}); "
                "output files are appended to." + Style.RESET_ALL)
        print(Fore.GREEN + f"       ({reason})" + Style.RESET_ALL)
    else:
        print(Fore.YELLOW
              + "[MODE] FRESH RESTART — starting from line 0 and overwriting "
                "old output files." + Style.RESET_ALL)
        print(Fore.YELLOW + f"       Reason: {reason}" + Style.RESET_ALL)

    if mode == 'resume' and progress.get('completed'):
        print(Fore.GREEN
              + "\nNothing to do: the saved progress already marks this "
                "input as fully processed." + Style.RESET_ALL)
        print_summary(progress)
        return EXIT_OK

    try:
        handles = open_output_handles(args.output_dir, mode, progress)
    except OSError as exc:
        print(Fore.RED
              + f"\n✘ Cannot open output files in '{args.output_dir}': {exc}"
              + Style.RESET_ALL)
        print(Fore.RED + "✘ No data written; exiting with non-zero status."
              + Style.RESET_ALL)
        return EXIT_WRITE_FAILURE

    # Record the starting state immediately so every run leaves a progress
    # file behind, even if the first batch fails.
    save_progress_atomic(progress_path, progress)

    fail_after_batch = 0
    env_value = os.environ.get(FAIL_AFTER_ENV, '').strip()
    if env_value:
        try:
            fail_after_batch = max(0, int(env_value))
        except ValueError:
            fail_after_batch = 0

    pool = None
    write_error = None
    batch_number = 0
    try:
        with open(input_file, 'rb') as input_handle, \
                tqdm(total=input_size, initial=progress['input_offset'],
                     desc='Sorting', unit='B', unit_scale=True,
                     colour='green') as progress_bar:
            input_handle.seek(progress['input_offset'])
            payloads = read_batches(input_handle, args.batch_size)

            if args.threads > 1:
                pool = multiprocessing.Pool(processes=args.threads)
                # imap preserves input order, so progress advancement stays
                # deterministic even with multiple workers.
                result_stream = pool.imap(classify_batch, payloads, chunksize=1)
            else:
                result_stream = (classify_batch(payload) for payload in payloads)

            for (start_offset, end_offset, total_lines, results,
                 empty_lines, unrecognized_lines) in result_stream:
                batch_number += 1

                try:
                    if fail_after_batch and batch_number == fail_after_batch:
                        raise OSError(
                            "simulated write failure "
                            f"({FAIL_AFTER_ENV}={fail_after_batch})")
                    new_offsets = write_batch(handles, results)
                    for handle in handles.values():
                        handle.flush()
                        os.fsync(handle.fileno())
                except OSError as exc:
                    write_error = exc
                    break

                # Batch is durable now — advance progress only to here.
                progress['processed_lines'] += total_lines
                progress['input_offset'] = end_offset
                progress['output_offsets'] = new_offsets
                for cat in CATEGORIES:
                    progress['counts'][cat] += len(results[cat])
                progress['empty_lines'] += empty_lines
                progress['unrecognized_lines'] += unrecognized_lines
                save_progress_atomic(progress_path, progress)
                progress_bar.update(end_offset - start_offset)

                if args.max_batches and batch_number >= args.max_batches:
                    print(Fore.CYAN
                          + f"\nPlanned stop after {batch_number} batch(es) "
                            "(--max-batches); progress saved, rerun to resume."
                          + Style.RESET_ALL)
                    break
    except OSError as exc:
        write_error = exc
    finally:
        for handle in handles.values():
            try:
                handle.close()
            except OSError:
                pass
        if pool is not None:
            if write_error is not None:
                pool.terminate()
            else:
                pool.close()
            pool.join()

    if write_error is not None:
        print(Fore.RED
              + f"\n✘ Write failed on batch {batch_number}: {write_error}"
              + Style.RESET_ALL)
        print(Fore.RED
              + "✘ Stopped all further writes. Already written batches are "
                "kept; progress remains at line "
                f"{progress['processed_lines']} (the failed batch is not "
                "counted)." + Style.RESET_ALL)
        print(Fore.RED + f"✘ Progress file: {progress_path}" + Style.RESET_ALL)
        print_summary(progress)
        return EXIT_WRITE_FAILURE

    if progress['input_offset'] >= input_size:
        progress['completed'] = True
        save_progress_atomic(progress_path, progress)

    print(Fore.LIGHTGREEN_EX
          + "\n✔ Done! Output saved as 1xxxx.txt, 3xxxx.txt, bcxxxx.txt"
          + Style.RESET_ALL)
    print(Fore.CYAN + f"  Progress file: {progress_path}" + Style.RESET_ALL)
    print_summary(progress)
    return EXIT_OK


def main(argv=None):
    clear_terminal()
    title()
    args = apply_interactive_defaults(parse_args(argv))
    print(Fore.CYAN
          + f"Input: {args.input} | output dir: {args.output_dir} | "
            f"threads: {args.threads} | batch size: {args.batch_size}\n"
          + Style.RESET_ALL)
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
