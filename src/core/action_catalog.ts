/**
 * Static catalogue of the actions exposed by the official RPScript modules.
 *
 * `module` follows the `officialModules` whitelist in package.json, which is also
 * the name accepted by `rps install <module>`. `runtimeModuleName` is the name the
 * module class registers itself with through the `@rpsModule` decorator; that is
 * the name used by the installed-module list, and it differs from the package name
 * for a few modules (the `downloading` package provides the `download` module).
 *
 * Generated from the published official module packages. Keep in sync when an
 * official module adds or renames actions.
 */

export interface OfficialModuleActions {
  module: string;
  runtimeModuleName: string;
  actions: string[];
}

export const OFFICIAL_MODULE_ACTIONS: OfficialModuleActions[] = [
  {
    module: "basic",
    runtimeModuleName: "basic",
    actions: [
      'log', 'stdout', 'as', 'assign',
      'listen-once', 'listen-on', 'exit', 'wait',
      'stringify', 'abs', 'ceil', 'math-max',
      'math-min', 'floor', 'pow', 'random',
      'round', 'truncate', 'adjust', 'all',
      'any', 'aperture', 'append', 'chain',
      'concat', 'contains', 'drop', 'drop-last',
      'drop-last-while', 'drop-repeats', 'drop-repeats-with', 'drop-while',
      'ends-with', 'filter', 'find', 'find-index',
      'find-last', 'find-last-index', 'flatten', 'for-each',
      'from-pairs', 'group-by', 'group-with', 'head',
      'index-of', 'index-by', 'init', 'insert',
      'insert-all', 'intersperse', 'join', 'last',
      'last-index-of', 'length', 'map', 'map-accum',
      'map-accum-right', 'merge-all', 'none', 'nth',
      'pair', 'partition', 'pluck', 'prepend',
      'range', 'reduce', 'reduce-by', 'reduce-right',
      'reduce-while', 'reject', 'remove', 'repeat',
      'reverse', 'scan', 'slice', 'sort',
      'split-at', 'split-every', 'split-when', 'starts-with',
      'tail', 'take', 'take-last', 'take-last-while',
      'take-while', 'times', 'transpose', 'unfold',
      'uniq', 'uniq-by', 'uniq-with', 'unnest',
      'update', 'without', 'xprod', 'zip',
      'zip-obj', 'zip-with', 'keys', 'assoc',
      'assoc-path', 'clone', 'dissoc', 'dissoc-path',
      'eq-props', 'evolve', 'for-each-obj-indexed', 'has',
      'invert', 'invert-obj', 'map-obj-indexed', 'merge',
      'merge-deep-left', 'merge-deep-right', 'merge-deep-with', 'merge-with',
      'merge-with-key', 'obj-of', 'omit', 'path',
      'path-or', 'pick', 'pick-all', 'pick-by',
      'project', 'prop', 'prop-or', 'props',
      'to-pairs', 'values', 'where', 'where-eq',
      '__', 'add-index', 'always', 'ap',
      'apply', 'apply-spec', 'apply-to', 'ascend',
      'call', 'comparator', 'compose', 'converge',
      'descend', 'empty', 'f', 'identity',
      'juxt', 'nth-arg', 'of', 'pipe',
      't', 'tap', 'try-catch', 'use-with',
      'unapply', 'clamp', 'count-by', 'difference',
      'difference-with', 'eq-by', 'equals', 'gt',
      'gte', 'identical', 'inner-join', 'intersection',
      'lt', 'lte', 'max', 'max-by',
      'min', 'min-by', 'path-eq', 'prop-eq',
      'sort-by', 'sort-with', 'symmetric-difference', 'symmetric-difference-with',
      'union', 'union-with', 'all-pass', 'and',
      'any-pass', 'both', 'complement', 'cond',
      'default-to', 'either', 'if-else', 'is-empty',
      'not', 'or', 'path-satisfies', 'prop-satisfies',
      'unless', 'until', 'when', 'trim',
      'match', 'replace', 'split', 'test',
      'to-lower', 'to-string', 'to-upper', 'add',
      'dec', 'divide', 'inc', 'math-mod',
      'mean', 'median', 'modulo', 'multiply',
      'negate', 'product', 'subtract', 'sum',
      'is', 'is-nil', 'prop-is', 'type',
      'r', 'regexp'
    ]
  },
  {
    module: "open",
    runtimeModuleName: "open",
    actions: [
      'open'
    ]
  },
  {
    module: "zip",
    runtimeModuleName: "zip",
    actions: [
      'compress', 'extract'
    ]
  },
  {
    module: "beeper",
    runtimeModuleName: "beeper",
    actions: [
      'beep'
    ]
  },
  {
    module: "notifier",
    runtimeModuleName: "notifier",
    actions: [
      'notify'
    ]
  },
  {
    module: "figlet",
    runtimeModuleName: "figlet",
    actions: [
      'figlet'
    ]
  },
  {
    module: "file",
    runtimeModuleName: "file",
    actions: [
      'read-file', 'append-to-file', 'write-file', 'delete-file',
      'file-exists', 'rename-file', 'file-stat'
    ]
  },
  {
    module: "csv",
    runtimeModuleName: "csv",
    actions: [
      'csv-to-data', 'data-to-csv'
    ]
  },
  {
    module: "date",
    runtimeModuleName: "date",
    actions: [
      'date-of', 'from-time', 'add-date', 'clone-date',
      'date-convert-to', 'date-diff', 'date-equals', 'date-format',
      'date-get', 'is-leap-year', 'is-date-valid', 'max-date',
      'min-date', 'parse-date', 'set-date', 'subtract-date',
      'unix-time'
    ]
  },
  {
    module: "hogan",
    runtimeModuleName: "hogan",
    actions: [
      'hogan'
    ]
  },
  {
    module: "downloading",
    runtimeModuleName: "download",
    actions: [
      'download'
    ]
  },
  {
    module: "request",
    runtimeModuleName: "request",
    actions: [
      'request'
    ]
  },
  {
    module: "cheerio",
    runtimeModuleName: "cheerio",
    actions: [
      'cheerio', 'selector'
    ]
  }
];

const pjson = require('../../package.json');

/** Names from the `officialModules` whitelist in package.json. */
export function listOfficialModules(): string[] {
  return (pjson.officialModules || []).slice();
}

export function isOfficialModule(moduleName: string): boolean {
  return listOfficialModules().indexOf(moduleName) >= 0;
}

/** Catalogue entries restricted to the official whitelist. */
export function getOfficialModuleActions(): OfficialModuleActions[] {
  return OFFICIAL_MODULE_ACTIONS.filter(entry => isOfficialModule(entry.module));
}

let actionIndex: { [action: string]: OfficialModuleActions } = undefined;

/**
 * Resolve the official module providing an action (verb), or undefined when no
 * official module claims it.
 */
export function findOfficialModuleByAction(action: string): OfficialModuleActions {
  if (!actionIndex) {
    actionIndex = {};
    getOfficialModuleActions().forEach(entry => {
      entry.actions.forEach(verb => { actionIndex[verb] = entry; });
    });
  }
  return actionIndex[action];
}
