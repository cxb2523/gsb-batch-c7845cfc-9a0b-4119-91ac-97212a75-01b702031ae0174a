import * as assert from 'assert';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

import {ModulePrecheck, PrecheckError, InstalledModuleSource} from '../src/core/module_precheck';
import {findOfficialModuleByAction, listOfficialModules, OFFICIAL_MODULE_ACTIONS} from '../src/core/action_catalog';

// mocha globals (the repo does not depend on @types/mocha)
declare function describe(name: string, fn: () => void): void;
declare function it(name: string, fn: () => void): void;
declare function beforeEach(fn: () => void): void;
declare function afterEach(fn: () => void): void;

/** Build a stub that mimics ModuleCommand's installed module reading. */
function fakeSource(installed: { [name: string]: string[] }, directories: string[] = []): InstalledModuleSource {
  let all: any = {};
  Object.keys(installed).forEach(name => {
    all[name] = { name: name, enabled: true, actions: {} };
    installed[name].forEach(verb => {
      all[name].actions[verb] = { moduleName: name, verbName: verb, methodName: verb };
    });
  });
  all['$DEFAULT'] = {};

  return {
    listInstalledModulesArr: () => directories.slice(),
    modMgr: { listInstalledModules: () => all }
  };
}

function removeDir(dir: string) {
  if (!fs.existsSync(dir)) return;
  fs.readdirSync(dir).forEach(entry => {
    let child = path.join(dir, entry);
    if (fs.statSync(child).isDirectory()) removeDir(child);
    else fs.unlinkSync(child);
  });
  fs.rmdirSync(dir);
}

function writeScript(dir: string, name: string, content: string): string {
  let file = path.join(dir, name);
  fs.writeFileSync(file, content, 'utf8');
  return file;
}

describe('action catalog', function () {

  it('lists the official modules from package.json', function () {
    let pjson = require('../package.json');
    assert.deepEqual(listOfficialModules(), pjson.officialModules);
  });

  it('maps actions to their official module', function () {
    assert.equal(findOfficialModuleByAction('download').module, 'downloading');
    assert.equal(findOfficialModuleByAction('cheerio').module, 'cheerio');
    assert.equal(findOfficialModuleByAction('csv-to-data').module, 'csv');
    assert.equal(findOfficialModuleByAction('log').module, 'basic');
  });

  it('only maps official modules', function () {
    let official = listOfficialModules();
    OFFICIAL_MODULE_ACTIONS.forEach(entry => {
      assert.ok(official.indexOf(entry.module) >= 0, entry.module + ' is not official');
      entry.actions.forEach(action => {
        assert.equal(findOfficialModuleByAction(action).module, entry.module);
      });
    });
  });

  it('returns undefined for an action no official module provides', function () {
    assert.equal(findOfficialModuleByAction('no-such-action'), undefined);
  });
});

describe('ModulePrecheck', function () {

  let tmpDir: string;

  beforeEach(function () {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rps-precheck-'));
  });

  afterEach(function () {
    removeDir(tmpDir);
  });

  it('collects the actions used in a script without executing it', async function () {
    let file = writeScript(tmpDir, 'scrape.rps',
      'download "http://example.com/x.zip"\n' +
      'cheerio "<h2>hello</h2>" | as \'doc\'\n' +
      'csv-to-data --columns=true read-file "data.csv"\n');

    let precheck = new ModulePrecheck(fakeSource({}, []));
    let actions = await precheck.extractActions(file);

    assert.deepEqual(actions, ['download', 'cheerio', 'as', 'csv-to-data', 'read-file']);
  });

  it('reports every missing module once, with a runnable install command', async function () {
    let file = writeScript(tmpDir, 'scrape.rps',
      'download "http://example.com/x.zip"\n' +
      'cheerio "<h2>hello</h2>" | as \'doc\'\n' +
      'csv-to-data read-file "data.csv"\n');

    let precheck = new ModulePrecheck(fakeSource({}, []));
    let result = await precheck.checkFile(file);

    assert.equal(result.ok, false);
    assert.deepEqual(result.missingModules.map(m => m.module),
      ['downloading', 'cheerio', 'basic', 'csv', 'file']);
    result.missingModules.forEach(m => assert.equal(m.installCommand, 'rps install ' + m.module));
    assert.deepEqual(result.unknownActions, []);

    let report = ModulePrecheck.format(result);
    assert.ok(report.indexOf('downloading') >= 0, report);
    assert.ok(report.indexOf('rps install downloading') >= 0, report);
  });

  it('passes when every action belongs to an installed module', async function () {
    let file = writeScript(tmpDir, 'ready.rps', 'beep 3\n');

    let precheck = new ModulePrecheck(fakeSource({ beeper: ['beep'] }, ['beeper']));
    let result = await precheck.checkFile(file);

    assert.equal(result.ok, true);
    assert.deepEqual(result.missingModules, []);
    assert.deepEqual(result.unknownActions, []);
  });

  it('recognises installed modules under their runtime module name', async function () {
    // the `downloading` package registers itself as the `download` module
    let file = writeScript(tmpDir, 'ready.rps', 'download "http://example.com/x.zip"\n');

    let precheck = new ModulePrecheck(fakeSource({ download: ['download'] }, ['download']));
    let result = await precheck.checkFile(file);

    assert.equal(result.ok, true);
  });

  it('lists each module only once even when several of its actions are used', async function () {
    let file = writeScript(tmpDir, 'mixed.rps', 'csv-to-data "a,b"\ndata-to-csv [1,2]\n');

    let precheck = new ModulePrecheck(fakeSource({}, []));
    let result = await precheck.checkFile(file);

    assert.deepEqual(result.missingModules.map(m => m.module), ['csv']);
  });

  it('separates unknown actions from missing modules', async function () {
    let file = writeScript(tmpDir, 'scrape.rps', 'download "http://x"\nmy-own-action 1 2\n');

    let precheck = new ModulePrecheck(fakeSource({}, []));
    let result = await precheck.checkFile(file);

    assert.equal(result.ok, false);
    assert.deepEqual(result.missingModules.map(m => m.module), ['downloading']);
    assert.deepEqual(result.unknownActions, ['my-own-action']);

    let report = ModulePrecheck.format(result);
    assert.ok(report.indexOf('my-own-action') >= 0, report);
    assert.equal(report.indexOf('rps install my-own-action'), -1, report);
  });

  it('reports unknown actions on their own when nothing is missing', async function () {
    let file = writeScript(tmpDir, 'unknown.rps', 'beep\nfrobnicate 1\n');

    let precheck = new ModulePrecheck(fakeSource({ beeper: ['beep'] }, ['beeper']));
    let result = await precheck.checkFile(file);

    assert.deepEqual(result.missingModules, []);
    assert.deepEqual(result.unknownActions, ['frobnicate']);
    assert.equal(result.ok, false);
  });

  it('names the file when the script cannot be read', async function () {
    let file = path.join(tmpDir, 'does-not-exist.rps');
    let precheck = new ModulePrecheck(fakeSource({}, []));

    let error: any;
    try {
      await precheck.checkFile(file);
    } catch (err) {
      error = err;
    }

    assert.ok(error instanceof PrecheckError);
    assert.ok(error.message.indexOf(file) >= 0, error.message);
    assert.equal(error.filename, file);

    let result = ModulePrecheck.errorResult(file, error);
    assert.equal(result.ok, false);
    assert.ok(ModulePrecheck.format(result).indexOf(file) >= 0);
  });

  it('names the file when the script cannot be parsed', async function () {
    let file = writeScript(tmpDir, 'broken.rps', 'log "a"\n)+(\n');
    let precheck = new ModulePrecheck(fakeSource({}, []));

    let error: any;
    try {
      await precheck.checkFile(file);
    } catch (err) {
      error = err;
    }

    assert.ok(error instanceof PrecheckError);
    assert.ok(error.message.indexOf(file) >= 0, error.message);
  });

  it('keeps precheck errors out of the missing module list', function () {
    let result = ModulePrecheck.errorResult('some.rps', new Error('boom'));

    assert.equal(result.ok, false);
    assert.deepEqual(result.missingModules, []);
    assert.ok(result.error.indexOf('some.rps') >= 0);
    assert.ok(result.error.indexOf('boom') >= 0);
  });
});
