import { IpcMessageV2 } from '@codifycli/schemas';
import { ChildProcess } from 'node:child_process';
import EventEmitter from 'node:events';
import { clearTimeout } from 'node:timers';

import { ctx } from '../events/context.js';
import { PluginMessage } from './plugin-message.js';
import { ipcMessageValidator } from './plugin-process.js';

type Resolve<T> = (value: T) => void;
type Reject = (reason?: Error) => void;

EventEmitter.defaultMaxListeners = 100;

// Default timeout is 10 minutes after last message, stdout, or stderr
export const TIMEOUT = 6_000_000

export interface SendIpcMessageForResultOptions {
  // Name of the plugin that owns the child process. Included in failure messages so the failing
  // collection step can be identified.
  pluginName?: string;

  // Invoked exactly once when the request cannot complete (process exit, IPC error, or timeout).
  // The caller uses this to tear down the plugin process so it does not linger as an orphan.
  onCleanup?: () => void;
}

export async function sendIpcMessageForResult(
  message: PluginMessage,
  process: ChildProcess,
  timeout: number = TIMEOUT,
  options: SendIpcMessageForResultOptions = {},
): Promise<PluginMessage> {
  let handler: MessageForResultHandler;

  return new Promise<PluginMessage>((resolve, reject) => {
    handler = new MessageForResultHandler(message, resolve, reject, timeout, options);

    // Sets listeners
    process.on('message', handler.messageListener);
    process.on('exit', handler.exitListener);
    process.on('error', handler.errorListener);
    process.stdout?.on('data', handler.startOrResetTimeout)
    process.stderr?.on('data', handler.startOrResetTimeout)

    // Send the message. A closed IPC channel (ex: the plugin already crashed) throws synchronously.
    try {
      process.send(message);
    } catch (err) {
      handler.fail(handler.buildSendError(err));
    }
  }).finally(() => {
    // Removes all listeners
    process.removeListener('message', handler.messageListener);
    process.removeListener('exit', handler.exitListener);
    process.removeListener('error', handler.errorListener);
    process.stdout?.removeListener('data', handler.startOrResetTimeout)
    process.stderr?.removeListener('data', handler.startOrResetTimeout)
    clearTimeout(handler.timerId);
  });
}

class MessageForResultHandler {
  message: PluginMessage;
  promiseResolve: Resolve<PluginMessage>;
  promiseReject: Reject;
  timerId!: NodeJS.Timeout;
  timeout: number;
  private pluginName: string;
  private onCleanup?: () => void;
  private settled = false;

  constructor(
    message: PluginMessage,
    resolve: Resolve<PluginMessage>,
    reject: Reject,
    timeout = TIMEOUT,
    options: SendIpcMessageForResultOptions = {},
  ) {
    this.message = message;
    this.promiseResolve = resolve;
    this.promiseReject = reject;
    this.timeout = timeout;
    this.pluginName = options.pluginName ?? 'unknown';
    this.onCleanup = options.onCleanup;

    this.startOrResetTimeout();
  }

  messageListener = (incomingMessage: unknown) => {
    ctx.debug(JSON.stringify(incomingMessage, null, 2));

    const message = PluginMessage.fromUnknown(incomingMessage);
    if (!message) {
      return this.fail(new Error(`Invalid message from plugin ${this.pluginName}. ${JSON.stringify(incomingMessage, null, 2)}`))
    }

    if (this.message.isSameRequest(message)) {
      this.resolve(message);
    } else {
      // Messages belonging to other in-flight requests (ex: a sudo request) only keep the wait alive
      this.startOrResetTimeout();
    }
  };

  // The child went away while a response was still pending. The request can never resolve, so fail
  // immediately instead of waiting for the timeout, and let the caller clean up the dead process.
  exitListener = (code: number | null, signal: NodeJS.Signals | null) => {
    this.fail(this.buildExitError(code, signal));
  }

  errorListener = (err: Error) => {
    this.fail(new Error(`Plugin "${this.pluginName}" encountered an IPC error while waiting for a response to command "${this.message.cmd}": ${err.message}`))
  }

  fail = (err: Error) => {
    if (this.settled) {
      return;
    }
    this.settled = true;

    if (this.timerId?.hasRef()) {
      clearTimeout(this.timerId);
    }

    // Best-effort process teardown. Cleanup must never mask the original request error.
    try {
      this.onCleanup?.();
    } catch {
      // no-op
    }

    this.promiseReject(err)
  }

  resolve = (value: PluginMessage) => {
    if (this.settled) {
      return;
    }
    this.settled = true;

    if (this.timerId.hasRef()) {
      clearTimeout(this.timerId);
    }

    this.promiseResolve(value)
  }

  startOrResetTimeout = () => {
    if (this.settled) {
      return;
    }

    if (this.timerId?.hasRef()) {
      clearTimeout(this.timerId);
    }

    this.timerId = setTimeout(() => {
      // Use date here to convert ms to minutes
      const date = new Date(this.timeout)
      const minutes = date.getMinutes().toString().padStart(2, '0');
      const seconds = date.getSeconds().toString().padStart(2, '0');
      this.fail(new Error(`Plugin "${this.pluginName}" timed out after ${minutes}:${seconds} while waiting for a response to command "${this.message.cmd}"`))
    }, this.timeout);
  }

  private buildExitError(code: number | null, signal: NodeJS.Signals | null): Error {
    if (code === null) {
      return new Error(`Plugin "${this.pluginName}" process was killed by signal ${signal ?? 'unknown'} while waiting for a response to command "${this.message.cmd}"`);
    }

    if (code === 0) {
      return new Error(`Plugin "${this.pluginName}" process exited with code 0 without sending a response to command "${this.message.cmd}"`);
    }

    return new Error(`Plugin "${this.pluginName}" process exited with code ${code} while waiting for a response to command "${this.message.cmd}"`);
  }

  buildSendError(err: unknown): Error {
    const reason = err instanceof Error ? err.message : String(err);
    return new Error(`Unable to send command "${this.message.cmd}" to plugin "${this.pluginName}": ${reason}`);
  }

  validateIpcMessage(response: unknown): response is IpcMessageV2 {
    return ipcMessageValidator(response);
  }
}
