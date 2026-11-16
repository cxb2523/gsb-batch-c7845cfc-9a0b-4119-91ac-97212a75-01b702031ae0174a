import fs from 'fs';
import {Runner} from 'rpscript-parser';
import {ModuleCommand} from '../commands/modules';
import {findOfficialModuleByAction, OfficialModuleActions} from './action_catalog';

export interface MissingModule {
  /** Official module name, also what `rps install` expects. */
  module: string;
  installCommand: string;
}

export interface PrecheckResult {
  filename: string;
  /** Actions (verbs) found in the script, in order of appearance. */
  actions: string[];
  missingModules: MissingModule[];
  unknownActions: string[];
  /** True when every action can be resolved and none of them is unknown. */
  ok: boolean;
  /** Set when the precheck itself could not be completed. */
  error?: string;
}

/** Minimal view of ModuleCommand the precheck needs; keeps the check testable. */
export interface InstalledModuleSource {
  listInstalledModulesArr(): string[];
  modMgr: { listInstalledModules(): any };
}

/** Raised when the script cannot be read or parsed by the precheck. */
export class PrecheckError extends Error {
  filename: string;

  constructor(filename: string, cause: any, stage?: string) {
    let reason = PrecheckError.reasonOf(cause);
    let detail = stage ? stage + ' (' + reason + ')' : reason;
    super('module precheck error for ' + filename + ': ' + detail);
    this.name = 'PrecheckError';
    this.filename = filename;
  }

  static reasonOf(cause: any): string {
    if (!cause) return String(cause);
    if (cause.message) return cause.message;
    if (cause.constructor && cause.constructor.name) return cause.constructor.name;
    return String(cause);
  }
}

/**
 * Checks whether a script is runnable on this machine before the runner is
 * asked to execute it.
 *
 * The script is transpiled once to collect the actions it uses. Every action is
 * then mapped to the official module that provides it (see action_catalog). When
 * that module is installed, the action is ready; when it is not, the module is
 * reported as missing together with the command that installs it. Actions no
 * official module provides are reported separately as unknown, so they never
 * turn into an install suggestion.
 */
export class ModulePrecheck {

  private injectedSource: InstalledModuleSource;
  private source: InstalledModuleSource;
  private installedNames: string[];
  private installedActionNames: string[];

  /**
   * @param source optional installed-module source. By default a ModuleCommand is
   * created on first use, which reads the installed modules the same way the
   * `modules` command does.
   */
  constructor(source?: InstalledModuleSource) {
    this.injectedSource = source;
  }

  /**
   * Read and transpile the script only - nothing is executed.
   * Throws PrecheckError (which always names the file) when the script cannot be
   * read or parsed.
   */
  extractActions(filename: string): Promise<string[]> {
    let content: string;
    try {
      content = fs.readFileSync(filename, 'utf8');
    } catch (err) {
      throw new PrecheckError(filename, err, 'unable to read script');
    }

    return new Promise<string[]>((resolve, reject) => {
      let parsed;
      try {
        // keyword checking is what the precheck replaces, so it is off here.
        let runner = new Runner({skipKeywordCheck: true, skipRun: true});
        parsed = runner.convertToTS(filename, content);
      } catch (err) {
        reject(new PrecheckError(filename, err, 'unable to parse script'));
        return;
      }

      Promise.resolve(parsed).then(
        (result: any) => resolve(result && result.verbs ? result.verbs.slice() : []),
        err => reject(new PrecheckError(filename, err, 'unable to parse script')));
    });
  }

  async checkFile(filename: string): Promise<PrecheckResult> {
    let actions = await this.extractActions(filename);
    return this.checkActions(filename, actions);
  }

  /** Map already-extracted actions to missing modules / unknown actions. */
  checkActions(filename: string, actions: string[]): PrecheckResult {
    let installedNames = this.getInstalledNames();
    let installedActions = this.getInstalledActionNames();

    let missing: MissingModule[] = [];
    let missingNames: string[] = [];
    let unknown: string[] = [];
    let seen: string[] = [];

    actions.forEach(action => {
      let name = (action || '').trim();
      if (!name || seen.indexOf(name) >= 0) return;
      seen.push(name);

      let entry = findOfficialModuleByAction(name);

      // an installed module may already provide this action
      if (entry && this.isModuleInstalled(entry, installedNames)) return;
      if (installedActions.indexOf(name) >= 0) return;

      if (!entry) {
        unknown.push(name);
        return;
      }

      if (missingNames.indexOf(entry.module) < 0) {
        missingNames.push(entry.module);
        missing.push({module: entry.module, installCommand: 'rps install ' + entry.module});
      }
    });

    return {
      filename: filename,
      actions: seen,
      missingModules: missing,
      unknownActions: unknown,
      ok: missing.length === 0 && unknown.length === 0
    };
  }

  /** Build a failed result from a precheck error, making sure the file is named. */
  static errorResult(filename: string, error: any): PrecheckResult {
    let message: string;
    if (error && error.name === 'PrecheckError' && error.message)
      message = error.message;
    else
      message = 'module precheck error for ' + filename + ': ' + PrecheckError.reasonOf(error);

    return {
      filename: filename,
      actions: [],
      missingModules: [],
      unknownActions: [],
      ok: false,
      error: message
    };
  }

  /** Human readable report for a failed precheck. */
  static format(result: PrecheckResult): string {
    if (result.error) return result.error;

    let lines: string[] = ['Module precheck failed for ' + result.filename];

    if (result.missingModules.length > 0) {
      lines.push('Missing module(s):');
      result.missingModules.forEach(m => lines.push('  - ' + m.module + '  (' + m.installCommand + ')'));
      lines.push('Install the missing module(s) and run again:');
      lines.push('  rps install ' + result.missingModules.map(m => m.module).join(' '));
    }

    if (result.unknownActions.length > 0) {
      lines.push('Unknown action(s) - not provided by any official module:');
      result.unknownActions.forEach(a => lines.push('  - ' + a));
      lines.push('Check the action name(s) used in the script.');
    }

    return lines.join('\n');
  }

  /** Names of installed modules, read the same way the modules command does. */
  getInstalledNames(): string[] {
    if (!this.installedNames) {
      let names = this.getSource().listInstalledModulesArr();
      this.installedNames = names.map(name => ModulePrecheck.normalizeModuleName(name));
    }
    return this.installedNames;
  }

  /** Verbs registered by the installed modules, used for non-official modules. */
  getInstalledActionNames(): string[] {
    if (!this.installedActionNames) {
      let names: string[] = [];
      let all: any = this.getSource().modMgr.listInstalledModules();

      Object.keys(all || {}).forEach(key => {
        if (key === '$DEFAULT') return;
        let actions = (all[key] || {}).actions || {};
        Object.keys(actions).forEach(method => {
          let verb = actions[method] && actions[method].verbName;
          if (verb) names.push(verb);
        });
      });
      this.installedActionNames = names;
    }
    return this.installedActionNames;
  }

  private getSource(): InstalledModuleSource {
    if (!this.source) this.source = this.injectedSource || new ModuleCommand();
    return this.source;
  }

  private isModuleInstalled(entry: OfficialModuleActions, installedNames: string[]): boolean {
    return installedNames.indexOf(entry.module) >= 0 ||
      installedNames.indexOf(entry.runtimeModuleName) >= 0;
  }

  private static normalizeModuleName(name: string): string {
    let value = (name || '').trim();
    if (value.indexOf('/') >= 0) value = value.substring(value.lastIndexOf('/') + 1);
    if (value.indexOf('rpscript-api-') === 0) value = value.substring('rpscript-api-'.length);
    return value;
  }
}
