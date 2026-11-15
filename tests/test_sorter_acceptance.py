"""
Acceptance tests for the resumable streaming BTC address sorter.

Run:
    python -I tests/test_sorter_acceptance.py

Every test launches the real script as a subprocess (so multiprocessing,
exit codes and file I/O are all exercised end-to-end) inside an isolated
temporary directory.

Covered scenarios:
  1. Normal classification, including empty lines and illegal addresses.
  2. Planned mid-run stop + resume (append, no duplicates, no data loss).
  3. Input changed -> fresh restart that overwrites old outputs.
  4. Unwritable output path -> non-zero exit, no progress advancement.
  5. Simulated mid-run batch write failure -> keep written batches,
     exit non-zero, resume truncates the uncommitted tail and finishes.
  6. Multiprocessing path (threads > 1) gives identical results.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'btc_address_sorter_by_type.py')
PROGRESS_NAME = 'btc_sorter_progress.json'
FAIL_ENV = 'BTC_SORTER_FAIL_AFTER_BATCHES'

CATEGORY_FILES = {'1': '1xxxx.txt', '3': '3xxxx.txt', 'bc': 'bcxxxx.txt'}

PASS, FAIL = 0, 0
FAILURES = []


def check(condition, label):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [PASS] {label}')
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f'  [FAIL] {label}')


def run_sorter(work_dir, *extra_args, env_extra=None, expect_code=0):
    """Run the sorter script; return (returncode, stdout, stderr)."""
    cmd = [sys.executable, '-I', SCRIPT,
           '-i', 'input.txt', '-o', 'out', *extra_args]
    env = dict(os.environ)
    env.pop(FAIL_ENV, None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        cmd, cwd=work_dir, env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace')
    print(f'  $ {" ".join(cmd)}'
          + (f'  [{FAIL_ENV}={env_extra[FAIL_ENV]}]' if env_extra else '')
          + f'  -> exit {proc.returncode} (expected {expect_code})')
    check(proc.returncode == expect_code,
          f'exit code {proc.returncode} == {expect_code}')
    return proc.returncode, proc.stdout, proc.stderr


def write_input(work_dir, lines):
    path = os.path.join(work_dir, 'input.txt')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for line in lines:
            f.write(line + '\n')
    return path


def read_outputs(work_dir):
    out = {}
    for cat, name in CATEGORY_FILES.items():
        path = os.path.join(work_dir, 'out', name)
        if not os.path.exists(path):
            out[cat] = []
            continue
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        out[cat] = content.splitlines() if content else []
    return out


def read_progress(work_dir):
    with open(os.path.join(work_dir, 'out', PROGRESS_NAME),
              'r', encoding='utf-8') as f:
        return json.load(f)


def expected_outputs(lines):
    expected = {'1': [], '3': [], 'bc': []}
    empty = unrecognized = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            empty += 1
        elif line.startswith('1'):
            expected['1'].append(line)
        elif line.startswith('3'):
            expected['3'].append(line)
        elif line.startswith('bc1'):
            expected['bc'].append(line)
        else:
            unrecognized += 1
    return expected, empty, unrecognized


# Fixed sample: valid addresses of each type, blank lines, junk lines.
SAMPLE = [
    '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa',
    '3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy',
    'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4',
    '',
    '   ',
    '0notABitcoinAddress',
    'xyz123456789',
    'bcXnotbech32',
    '1BoatSLRHtKNngkdXEeobR76b53LETtpyT',
    '3P14159f73E4gFr7JterCCQh9QjiTjiZrG',
    'bc1q9zpgru6ma9s4e8eghc2xw6c7v0j7s3hqa2nl5d',
    '9abcdef',
]


def test_1_normal_classification(work_root):
    print('\n=== Test 1: normal classification, blanks and illegal lines ===')
    work = os.path.join(work_root, 't1')
    os.makedirs(work)
    write_input(work, SAMPLE)

    _, stdout, _ = run_sorter(work, '-t', '1', '-b', '4')

    expected, empty, unrecognized = expected_outputs(SAMPLE)
    outputs = read_outputs(work)
    check(outputs == expected, 'output files match expected classification')
    check(not any(' ' in line or line == ''
                  for lines in outputs.values() for line in lines),
          'no blank/whitespace line ever written to output')

    progress = read_progress(work)
    check(progress['processed_lines'] == len(SAMPLE),
          'progress.processed_lines == total input lines')
    check(progress['empty_lines'] == empty,
          f'empty line count == {empty}')
    check(progress['unrecognized_lines'] == unrecognized,
          f'unrecognized line count == {unrecognized}')
    check(progress['counts'] == {k: len(v) for k, v in expected.items()},
          'per-type hit counts correct')
    check(progress['completed'] is True, 'progress marked completed')
    check('FRESH RESTART' in stdout, 'terminal announces FRESH RESTART mode')


def test_2_resume_append(work_root):
    print('\n=== Test 2: planned stop then resume (append, no duplicates) ===')
    work = os.path.join(work_root, 't2')
    os.makedirs(work)
    lines = [f'1addrLine{i:03d}' if i % 3 == 0
             else f'3addrLine{i:03d}' if i % 3 == 1
             else f'bc1qline{i:03d}'
             for i in range(25)]
    write_input(work, lines)

    # First run: stop after 2 batches of 5 lines each -> 10 lines processed.
    _, stdout1, _ = run_sorter(work, '-t', '2', '-b', '5',
                               '--max-batches', '2')
    progress1 = read_progress(work)
    check(progress1['processed_lines'] == 10, 'first run stops at 10 lines')
    check(progress1['completed'] is False, 'first run not marked completed')
    outputs1 = read_outputs(work)
    n_written_1 = sum(len(v) for v in outputs1.values())
    check(n_written_1 == 10, 'first run wrote exactly the first 10 addresses')

    # Second run: same file (unchanged size/mtime) -> resume and finish.
    _, stdout2, _ = run_sorter(work, '-t', '2', '-b', '5')
    check('RESUME' in stdout2 and 'FRESH RESTART' not in stdout2,
          'terminal announces RESUME mode (append)')
    progress2 = read_progress(work)
    check(progress2['processed_lines'] == 25,
          'resume finishes all 25 lines')
    check(progress2['completed'] is True, 'resume marks completed')

    expected, _, _ = expected_outputs(lines)
    check(read_outputs(work) == expected,
          'appended output equals one-shot reference (no duplicates/loss)')

    # Third run on a completed input: nothing to do, exit 0.
    code3, stdout3, _ = run_sorter(work, '-t', '1', '-b', '5')
    check('already marks this input as fully processed' in stdout3,
          're-running a completed input is a no-op')


def test_3_input_changed_overwrite(work_root):
    print('\n=== Test 3: changed input forces fresh restart / overwrite ===')
    work = os.path.join(work_root, 't3')
    os.makedirs(work)
    input_path = write_input(work, SAMPLE)
    run_sorter(work, '-t', '1', '-b', '4')
    outputs_before = read_outputs(work)
    check(any(outputs_before.values()), 'first run produced output')

    # Rewrite the input with completely different content, and force an
    # mtime that visibly differs from the original file.
    new_lines = ['3newP2shLine001', '3newP2shLine002', 'bc1qnewbech0003']
    write_input(work, new_lines)
    future = time.time() + 120
    os.utime(input_path, (future, future))

    _, stdout, _ = run_sorter(work, '-t', '1', '-b', '4')
    check('FRESH RESTART' in stdout,
          'terminal announces FRESH RESTART after input change')
    progress = read_progress(work)
    check(progress['processed_lines'] == len(new_lines),
          'counters restart from zero')
    check(progress['counts']['1'] == 0,
          "old '1' hits do not leak into the new counters")

    expected, _, _ = expected_outputs(new_lines)
    check(read_outputs(work) == expected,
          'old output overwritten, only new input classified')


def test_4_unwritable_output(work_root):
    print('\n=== Test 4: unwritable output path -> non-zero exit ===')
    work = os.path.join(work_root, 't4')
    os.makedirs(work)
    write_input(work, SAMPLE)

    # An existing regular file used as the output directory cannot be
    # created/opened as a directory -> write setup must fail on every OS.
    blocker = os.path.join(work, 'blocker_file')
    with open(blocker, 'wb') as f:
        f.write(b'I am a file, not a directory')
    os.chmod(blocker, stat.S_IREAD)

    code, stdout, _ = run_sorter(work, '-t', '1', '-b', '4',
                                 '-o', 'blocker_file',
                                 expect_code=1)
    check('Cannot open output files' in stdout,
          'failure reason is printed clearly')
    progress_path = os.path.join(work, 'blocker_file', PROGRESS_NAME)
    check(not os.path.exists(progress_path),
          'no progress file is created when output cannot be opened')

    # Belt-and-braces: a read-only output *file* in a writable directory.
    # On Windows a directory's read-only bit does not block file creation,
    # but opening a read-only file for writing always raises PermissionError
    # (on POSIX too), which exercises the same failure-handling path.
    ro_out = os.path.join(work, 'ro_file_out')
    os.makedirs(ro_out)
    readonly_target = os.path.join(ro_out, '1xxxx.txt')
    with open(readonly_target, 'wb') as f:
        f.write(b'pre-existing read-only output\n')
    os.chmod(readonly_target, stat.S_IREAD)
    try:
        code2, stdout2, _ = run_sorter(
            work, '-t', '1', '-b', '4', '-o', 'ro_file_out',
            expect_code=1)
        check(code2 == 1, 'read-only output file yields non-zero exit')
        check('Cannot open output files' in stdout2,
              'read-only file failure reason is printed')
        check(not os.path.exists(os.path.join(ro_out, PROGRESS_NAME)),
              'no progress file created when an output file cannot be opened')
    finally:
        os.chmod(readonly_target, stat.S_IWRITE | stat.S_IREAD)


def test_5_write_failure_then_resume(work_root):
    print('\n=== Test 5: batch write failure keeps prior data, resume recovers')
    work = os.path.join(work_root, 't5')
    os.makedirs(work)
    lines = [f'1failLine{i:03d}' if i % 2 == 0 else f'bc1qfail{i:03d}'
             for i in range(20)]
    write_input(work, lines)

    # Fail on batch 2 (batch 1 must still be fully written and committed).
    _, stdout, _ = run_sorter(
        work, '-t', '2', '-b', '5',
        env_extra={FAIL_ENV: '2'}, expect_code=1)
    check('Write failed on batch 2' in stdout, 'failure batch is reported')
    check('Stopped all further writes' in stdout,
          'stop-on-write-failure policy is announced')

    progress = read_progress(work)
    check(progress['processed_lines'] == 5,
          'progress stays at the last committed batch (failed lines excluded)')
    check(progress['completed'] is False, 'progress not marked completed')
    outputs = read_outputs(work)
    check(sum(len(v) for v in outputs.values()) == 5,
          'exactly the first batch (5 addresses) remains on disk')

    # Simulate a torn partial write beyond the committed offsets.
    for name in CATEGORY_FILES.values():
        path = os.path.join(work, 'out', name)
        with open(path, 'ab') as f:
            f.write(b'GARBAGE_PARTIAL_LINE\n')

    # Resume must truncate the uncommitted tail, then finish normally.
    _, stdout2, _ = run_sorter(work, '-t', '1', '-b', '5')
    check('RESUME' in stdout2, 'recovery run uses RESUME mode')
    final_outputs = read_outputs(work)
    flat = [line for v in final_outputs.values() for line in v]
    check('GARBAGE_PARTIAL_LINE' not in flat,
          'uncommitted garbage tail is truncated on resume')
    expected, _, _ = expected_outputs(lines)
    check(final_outputs == expected,
          'final output matches full one-shot reference after recovery')
    check(read_progress(work)['completed'] is True,
          'recovered run marks progress completed')


def test_6_multiprocessing_equivalence(work_root):
    print('\n=== Test 6: multiprocessing results match single-process run ===')
    lines = SAMPLE * 5
    for threads in (1, 2, 4):
        work = os.path.join(work_root, f't6_t{threads}')
        os.makedirs(work)
        write_input(work, lines)
        run_sorter(work, '-t', str(threads), '-b', '7')
        expected, empty, unrecognized = expected_outputs(lines)
        check(read_outputs(work) == expected,
              f'threads={threads}: identical classification')
        progress = read_progress(work)
        check(progress['empty_lines'] == empty
              and progress['unrecognized_lines'] == unrecognized
              and progress['completed'] is True,
              f'threads={threads}: counters identical')


def main():
    work_root = tempfile.mkdtemp(prefix='btc_sorter_acceptance_')
    print(f'Temp workspace: {work_root}')
    print(f'Interpreter   : {sys.executable}')
    try:
        test_1_normal_classification(work_root)
        test_2_resume_append(work_root)
        test_3_input_changed_overwrite(work_root)
        test_4_unwritable_output(work_root)
        test_5_write_failure_then_resume(work_root)
        test_6_multiprocessing_equivalence(work_root)
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    print(f'\n{"=" * 60}')
    print(f'Result: {PASS} passed, {FAIL} failed')
    if FAIL:
        print('Failed checks:')
        for label in FAILURES:
            print(f'  - {label}')
        return 1
    print('All acceptance tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
