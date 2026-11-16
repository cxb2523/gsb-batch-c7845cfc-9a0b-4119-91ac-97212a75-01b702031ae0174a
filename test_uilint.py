#!/usr/bin/env python3
import io
import contextlib
import errno
import os
import tempfile
import unittest

import uilint


# A minimal XAML whose only InformativeScreenshot reference is INUSE_HASH.
XAML_TEMPLATE = '''<Activity
  xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"
  xmlns:ui="http://schemas.uipath.com/workflow/activities"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <Sequence>
    <ui:Assign DisplayName="Assign" InformativeScreenshot="%s" />
  </Sequence>
</Activity>
'''

# A XAML containing a MessageBox activity, which always produces an ERROR.
XAML_WITH_ERROR = '''<Activity
  xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"
  xmlns:ui="http://schemas.uipath.com/workflow/activities"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <Sequence>
    <ui:MessageBox DisplayName="MessageBox" Text="hello" />
  </Sequence>
</Activity>
'''

INUSE_HASH = 'inuse1'
UNUSED_HASHES = ['unused1', 'unused2', 'unused3']
ALL_HASHES = [INUSE_HASH] + UNUSED_HASHES


class RemoveScreenshotsTest(unittest.TestCase):

  def setUp(self):
    self.tmpdir = tempfile.TemporaryDirectory()
    self.projectdir = self.tmpdir.name
    self.ssdir = os.path.join(self.projectdir, '.screenshots')
    os.makedirs(self.ssdir)

    with open(os.path.join(self.projectdir, 'project.json'), 'w') as f:
      f.write('{}')
    with open(os.path.join(self.projectdir, 'Main.xaml'), 'w') as f:
      f.write(XAML_TEMPLATE % INUSE_HASH)

    for hashname in ALL_HASHES:
      with open(os.path.join(self.ssdir, '%s.png' % hashname), 'w') as f:
        f.write('screenshot data of %s' % hashname)

    self.project = uilint.Project(self.projectdir)
    self.project.lint()
    # Sanity check on the fixture: exactly the three unused screenshots
    self.assertEqual(
      self.project.stored_screenshots() - self.project.inuse_screenshots(),
      set(UNUSED_HASHES)
    )

  def tearDown(self):
    self.tmpdir.cleanup()

  def _run_remove(self, mode, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
      summary = uilint.remove_unused_screenshots(self.project, mode, **kwargs)
    return summary, buf.getvalue()

  # 1. file mode: every unused screenshot is deleted, the in-use one remains
  def test_file_mode_deletes_all_unused_screenshots(self):
    summary, output = self._run_remove('file')

    self.assertEqual(summary.mode, 'file')
    self.assertEqual(summary.deleted, 3)
    self.assertEqual(summary.failed, 0)
    self.assertEqual(summary.skipped, 0)
    self.assertIsNone(summary.checkin)

    self.assertEqual(sorted(os.listdir(self.ssdir)), ['%s.png' % INUSE_HASH])

    # Per-screenshot lines and the summary must be visible
    for hashname in UNUSED_HASHES:
      self.assertIn(self.project.screenshot_path(hashname), output)
      self.assertIn('msg:remove-status-deleted', output)
    self.assertIn('msg:remove-summary', output)
    self.assertIn('3 msg:count-deleted', output)
    self.assertIn('0 msg:count-failed', output)
    self.assertIn('0 msg:count-skipped', output)

  # 2. file mode: a single failure is recorded and the rest still get deleted
  def test_file_mode_continues_after_single_failure(self):
    failing_path = self.project.screenshot_path('unused2')

    def remover(path):
      if path == failing_path:
        raise PermissionError(errno.EACCES, 'Permission denied')
      os.remove(path)

    summary, output = self._run_remove('file', file_remover=remover)

    self.assertEqual(summary.deleted, 2)
    self.assertEqual(summary.failed, 1)
    self.assertEqual(summary.skipped, 0)

    # The failed screenshot and the in-use screenshot remain on disk
    self.assertTrue(os.path.exists(failing_path))
    self.assertTrue(os.path.exists(self.project.screenshot_path(INUSE_HASH)))
    for hashname in ('unused1', 'unused3'):
      self.assertFalse(os.path.exists(self.project.screenshot_path(hashname)))

    self.assertIn('msg:remove-status-failed', output)
    self.assertIn(failing_path, output)
    self.assertIn('[Errno 13]', output)
    self.assertIn('2 msg:count-deleted', output)
    self.assertIn('1 msg:count-failed', output)

  # 3. vsts mode: one failing `tf delete` means tf checkin must not run
  def test_vsts_mode_skips_checkin_when_any_delete_fails(self):
    commands = []

    def tf_runner(command):
      commands.append(command)
      if 'tf delete' in command and 'unused2' in command:
        return 1
      return 0

    summary, output = self._run_remove('vsts', tf_runner=tf_runner)

    self.assertEqual(summary.deleted, 2)
    self.assertEqual(summary.failed, 1)
    self.assertEqual(summary.skipped, 0)
    self.assertFalse(summary.checkin)

    delete_commands = [c for c in commands if 'tf delete' in c]
    checkin_commands = [c for c in commands if 'tf checkin' in c]
    self.assertEqual(len(delete_commands), 3)
    self.assertEqual(len(checkin_commands), 0)

    self.assertIn('msg:checkin-skipped-partial-failed', output)
    self.assertIn('msg:tf-delete-failed', output)

    # vsts mode never touches the files itself
    for hashname in ALL_HASHES:
      self.assertTrue(os.path.exists(self.project.screenshot_path(hashname)))

  # 3b. vsts mode: when every `tf delete` fails, checkin is skipped too
  def test_vsts_mode_skips_checkin_when_all_deletes_fail(self):
    commands = []

    def tf_runner(command):
      commands.append(command)
      return 1

    summary, output = self._run_remove('vsts', tf_runner=tf_runner)

    self.assertEqual(summary.deleted, 0)
    self.assertEqual(summary.failed, 3)
    self.assertFalse(summary.checkin)
    self.assertEqual(
      len([c for c in commands if 'tf checkin' in c]), 0
    )
    self.assertIn('msg:checkin-skipped-all-failed', output)

  # 3c. vsts mode: all deletes succeed -> exactly one checkin is issued
  def test_vsts_mode_checks_in_once_when_all_deletes_succeed(self):
    commands = []

    def tf_runner(command):
      commands.append(command)
      return 0

    summary, output = self._run_remove('vsts', tf_runner=tf_runner)

    self.assertEqual(summary.deleted, 3)
    self.assertEqual(summary.failed, 0)
    self.assertTrue(summary.checkin)

    checkin_indexes = [i for i, c in enumerate(commands) if 'tf checkin' in c]
    delete_indexes = [i for i, c in enumerate(commands) if 'tf delete' in c]
    self.assertEqual(len(checkin_indexes), 1)
    self.assertEqual(len(delete_indexes), 3)
    self.assertGreater(checkin_indexes[0], max(delete_indexes))
    self.assertIn('msg:checkin-succeeded', output)

  # 3d. vsts mode: a failing tf checkin is reported but deletes stay successful
  def test_vsts_mode_reports_failed_checkin(self):
    def tf_runner(command):
      return 1 if 'tf checkin' in command else 0

    summary, output = self._run_remove('vsts', tf_runner=tf_runner)

    self.assertEqual(summary.deleted, 3)
    self.assertEqual(summary.failed, 0)
    self.assertFalse(summary.checkin)
    self.assertIn('msg:checkin-failed', output)

  # 3e. vsts mode: tf itself missing (OSError) is a per-file failure, not fatal
  def test_vsts_mode_oserror_is_recorded_per_screenshot(self):
    def tf_runner(command):
      raise FileNotFoundError(errno.ENOENT, 'No such file or directory')

    summary, output = self._run_remove('vsts', tf_runner=tf_runner)

    self.assertEqual(summary.deleted, 0)
    self.assertEqual(summary.failed, 3)
    self.assertFalse(summary.checkin)
    self.assertIn('msg:remove-status-failed', output)
    self.assertIn('[Errno 2]', output)

  # 4. dryrun: only planned paths are reported, nothing is deleted or checked in
  def test_dryrun_does_not_touch_files_or_vcs(self):
    def fail_if_called(*args, **kwargs):
      raise AssertionError('No removal operation should run during dryrun')

    summary, output = self._run_remove(
      'dryrun', file_remover=fail_if_called, tf_runner=fail_if_called
    )

    self.assertEqual(summary.deleted, 0)
    self.assertEqual(summary.failed, 0)
    self.assertEqual(summary.skipped, 3)
    self.assertIsNone(summary.checkin)

    for hashname in ALL_HASHES:
      self.assertTrue(os.path.exists(self.project.screenshot_path(hashname)))

    for hashname in UNUSED_HASHES:
      self.assertIn(self.project.screenshot_path(hashname), output)
    self.assertIn('msg:remove-status-planned', output)
    self.assertNotIn('msg:remove-status-deleted', output)
    self.assertNotIn('msg:remove-status-failed', output)

  # 5. nothing to remove: empty summary and no output
  def test_no_unused_screenshots_produces_empty_summary(self):
    for hashname in UNUSED_HASHES:
      os.remove(os.path.join(self.ssdir, '%s.png' % hashname))
    project = uilint.Project(self.projectdir)
    project.lint()
    self.assertEqual(
      project.stored_screenshots() - project.inuse_screenshots(), set()
    )

    def fail_if_called(*args, **kwargs):
      raise AssertionError('Nothing should be removed')

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
      summary = uilint.remove_unused_screenshots(
        project, 'file', file_remover=fail_if_called
      )

    self.assertEqual(summary.deleted, 0)
    self.assertEqual(summary.failed, 0)
    self.assertEqual(summary.skipped, 0)
    self.assertEqual(buf.getvalue(), '')
    self.assertTrue(os.path.exists(project.screenshot_path(INUSE_HASH)))


class CliMainTest(unittest.TestCase):

  def setUp(self):
    self.tmpdir = tempfile.TemporaryDirectory()
    self.projectdir = self.tmpdir.name
    self.ssdir = os.path.join(self.projectdir, '.screenshots')
    os.makedirs(self.ssdir)

    with open(os.path.join(self.projectdir, 'project.json'), 'w') as f:
      f.write('{}')
    with open(os.path.join(self.projectdir, 'Main.xaml'), 'w') as f:
      f.write(XAML_TEMPLATE % INUSE_HASH)

    for hashname in ALL_HASHES:
      with open(os.path.join(self.ssdir, '%s.png' % hashname), 'w') as f:
        f.write('screenshot data of %s' % hashname)

  def tearDown(self):
    self.tmpdir.cleanup()

  def _run_main(self, *argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
      exitcode = uilint.main(list(argv) + ['--nologo', self.projectdir])
    return exitcode, buf.getvalue()

  # Lint ERROR keeps its nonzero exit code
  def test_lint_error_still_exits_nonzero(self):
    with open(os.path.join(self.projectdir, 'Bad.xaml'), 'w') as f:
      f.write(XAML_WITH_ERROR)

    exitcode, output = self._run_main()

    self.assertEqual(exitcode, 1)
    self.assertIn('[Error]', output)

  # A removal failure must not turn a clean lint run into a failed run
  def test_removal_failure_does_not_change_exit_code(self):
    # os.remove on a directory raises OSError on every platform
    failing = os.path.join(self.ssdir, 'unused2.png')
    os.remove(failing)
    os.makedirs(failing)

    exitcode, output = self._run_main('--remove-screenshots', 'file')

    self.assertEqual(exitcode, 0)
    self.assertTrue(os.path.isdir(failing))
    self.assertFalse(os.path.exists(os.path.join(self.ssdir, 'unused1.png')))
    self.assertFalse(os.path.exists(os.path.join(self.ssdir, 'unused3.png')))
    self.assertTrue(os.path.exists(os.path.join(self.ssdir, 'inuse1.png')))
    self.assertIn('msg:remove-status-failed', output)
    self.assertIn('msg:remove-summary', output)
    self.assertIn('1 msg:count-failed', output)

  # CLI dryrun leaves all files in place and still exits successfully
  def test_cli_dryrun_leaves_files_untouched(self):
    exitcode, output = self._run_main('--remove-screenshots', 'dryrun')

    self.assertEqual(exitcode, 0)
    for hashname in ALL_HASHES:
      self.assertTrue(os.path.join(self.ssdir, '%s.png' % hashname))
    self.assertIn('msg:remove-status-planned', output)
    self.assertNotIn('msg:remove-status-deleted', output)

  # Lint ERROR + dryrun: nonzero exit code remains, files stay untouched
  def test_lint_error_with_dryrun_still_fails_and_keeps_files(self):
    with open(os.path.join(self.projectdir, 'Bad.xaml'), 'w') as f:
      f.write(XAML_WITH_ERROR)

    exitcode, output = self._run_main('--remove-screenshots', 'dryrun')

    self.assertEqual(exitcode, 1)
    for hashname in ALL_HASHES:
      self.assertTrue(os.path.exists(
        os.path.join(self.ssdir, '%s.png' % hashname)
      ))
    self.assertIn('msg:remove-status-planned', output)

  # Missing project directory keeps its original failure semantics
  def test_missing_directory_exits_nonzero(self):
    missing = os.path.join(self.tmpdir.name, 'does-not-exist')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
      exitcode = uilint.main(['--nologo', missing])

    self.assertEqual(exitcode, 1)
    self.assertIn('msg:directory-not-found', buf.getvalue())


if __name__ == '__main__':
  unittest.main()
