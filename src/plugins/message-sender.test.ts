import { ChildProcess } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { Readable } from 'stream';
import { clearInterval, clearTimeout } from 'node:timers';
import { describe, expect, it, vi } from 'vitest';

import { PluginMessage } from './plugin-message.js';
import { sendIpcMessageForResult } from './message-sender.js';
import { returnMessageCmd } from './plugin-process.js';

describe('Message sender tests', async () => {
  const mockChildProcess = () => {
    const process = new ChildProcess();
    process.stdout = new EventEmitter() as Readable;
    process.stderr = new EventEmitter() as Readable
    process.send = () => true;

    return process;
  }

  it('Is able to send a message and await a result', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('message1', null)

    const result = await Promise.all([
      sendIpcMessageForResult(message, cProcess),
      setTimeout(() => {
        cProcess.emit('message', { cmd: returnMessageCmd('message1'), data: null, requestId: message.requestId });
      }, 100),
    ])

    expect(result[0]).toMatchObject({
      cmd: returnMessageCmd('message1')
    })
  })

  it('Clears all listeners', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('message1', null)

    const result = await Promise.all([
      sendIpcMessageForResult(message, cProcess),
      setTimeout(() => {
        cProcess.emit('message', { cmd: returnMessageCmd('message1'), data: null, requestId: message.requestId });
      }, 100),
    ])

    expect(cProcess.listenerCount('messsage')).to.eq(0);
    expect(cProcess.listenerCount('message')).to.eq(0);
    expect(cProcess.listenerCount('exit')).to.eq(0);
    expect(cProcess.listenerCount('error')).to.eq(0);
    expect(cProcess.stdout!.listenerCount('data')).to.eq(0);
    expect(cProcess.stderr!.listenerCount('data')).to.eq(0);
  })

  it('Is able to send a message and timeout if not received', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('message1', null)
    const onCleanup = vi.fn();

    await expect(() => Promise.all([
      sendIpcMessageForResult(message, cProcess, 100, { pluginName: 'test-plugin', onCleanup }),
      setTimeout(() => {
        cProcess.emit('message', { cmd: returnMessageCmd('message1'), data: null, requestId: message.requestId });
      }, 200),
    ])).rejects.toThrowError();
  })

  it('Is able to send a message and not timeout if stdout is returned', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('message1', null)

    // Explanation:
    //   1. Send a message and set the timeout to be 100ms
    //   2. Make the return message come back in 200ms
    //   3. Periodically send a stdout every 50ms. This should not throw
    const [result, timer, interval] = await Promise.all([
      sendIpcMessageForResult(message, cProcess, 100),
      setTimeout(() => {
        cProcess.emit('message', { cmd: returnMessageCmd('message1'), data: null, requestId: message.requestId });
      }, 200),
      setInterval(() => {
        cProcess.stdout!.emit('data', 'message');
      }, 50),
    ])

    clearInterval(interval);
    clearTimeout(timer);
    expect(result).toMatchObject({
      cmd: returnMessageCmd('message1')
    })
  })

  it('Is able to send a message and not timeout if stderr is returned', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('message1', null)

    // Explanation:
    //   1. Send a message and set the timeout to be 100ms
    //   2. Make the return message come back in 200ms
    //   3. Periodically send a stdout every 50ms. This should not throw
    const [result, timer, interval] = await Promise.all([
      sendIpcMessageForResult(message, cProcess, 100),
      setTimeout(() => {
        cProcess.emit('message', { cmd: returnMessageCmd('message1'), data: null, requestId: message.requestId });
      }, 200),
      setInterval(() => {
        cProcess.stderr!.emit('data', 'message');
      }, 50),
    ])

    clearInterval(interval);
    clearTimeout(timer);
    expect(result).toMatchObject({
      cmd: returnMessageCmd('message1')
    })
  })

  it('Is able to send a message and not timeout if a non-resolving message is sent (like a sudo request)', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('message1', null)

    // Explanation:
    //   1. Send a message and set the timeout to be 100ms
    //   2. Make the return message come back in 200ms
    //   3. Periodically send a stdout every 50ms. This should not throw
    const [result, timer, interval] = await Promise.all([
      sendIpcMessageForResult(message, cProcess, 100),
      setTimeout(() => {
        cProcess.emit('message', { cmd: returnMessageCmd('message1'), data: null, requestId: message.requestId });
      }, 200),
      setInterval(() => {
        cProcess.emit('message', { cmd: 'non-resolving', data: null, requestId: 'dshghdjsag' })
      }, 50),
    ])

    clearInterval(interval);
    clearTimeout(timer);
    expect(result).toMatchObject({
      cmd: returnMessageCmd('message1')
    })
  })

  it('rejects immediately with an exit error when the plugin process exits non-zero while waiting', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('plan', null)
    const onCleanup = vi.fn();
    const start = Date.now();

    const [error] = await Promise.all([
      sendIpcMessageForResult(message, cProcess, 60_000, { pluginName: 'brew', onCleanup }).then(
        () => {
          throw new Error('Expected the request to be rejected');
        },
        (err: Error) => err,
      ),
      setTimeout(() => cProcess.emit('exit', 1, null), 50),
    ])

    // Fails fast instead of waiting for the 60s timeout
    expect(Date.now() - start).toBeLessThan(5_000);
    expect(error.message).toContain('exited with code 1');
    expect(error.message).toContain('brew');
    expect(error.message).toContain('plan');
    expect(onCleanup).toHaveBeenCalledTimes(1);
    expect(cProcess.listenerCount('exit')).to.eq(0);
  })

  it('rejects when the plugin process is killed by a signal while waiting', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('apply', null)
    const onCleanup = vi.fn();

    const [error] = await Promise.all([
      sendIpcMessageForResult(message, cProcess, 60_000, { pluginName: 'brew', onCleanup }).then(
        () => {
          throw new Error('Expected the request to be rejected');
        },
        (err: Error) => err,
      ),
      setTimeout(() => cProcess.emit('exit', null, 'SIGKILL'), 50),
    ])

    expect(error.message).toContain('killed by signal SIGKILL');
    expect(error.message).toContain('brew');
    expect(error.message).toContain('apply');
    expect(onCleanup).toHaveBeenCalledTimes(1);
  })

  it('rejects when the plugin process exits cleanly without sending a response', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('validate', null)
    const onCleanup = vi.fn();

    const [error] = await Promise.all([
      sendIpcMessageForResult(message, cProcess, 60_000, { pluginName: 'brew', onCleanup }).then(
        () => {
          throw new Error('Expected the request to be rejected');
        },
        (err: Error) => err,
      ),
      setTimeout(() => cProcess.emit('exit', 0, null), 50),
    ])

    expect(error.message).toContain('exited with code 0 without sending a response');
    expect(error.message).toContain('validate');
    expect(onCleanup).toHaveBeenCalledTimes(1);
  })

  it('rejects with a distinct timeout error containing the plugin and command names', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('findInstances', null)
    const onCleanup = vi.fn();

    const [error] = await Promise.all([
      sendIpcMessageForResult(message, cProcess, 100, { pluginName: 'brew', onCleanup }).then(
        () => {
          throw new Error('Expected the request to be rejected');
        },
        (err: Error) => err,
      ),
    ])

    expect(error.message).toContain('timed out');
    expect(error.message).toContain('brew');
    expect(error.message).toContain('findInstances');
    expect(error.message).not.toContain('exited');
    expect(onCleanup).toHaveBeenCalledTimes(1);
  })

  it('does not invoke cleanup on the successful path', async () => {
    const cProcess = mockChildProcess();
    const message = PluginMessage.create('message1', null)
    const onCleanup = vi.fn();

    await Promise.all([
      sendIpcMessageForResult(message, cProcess, 100, { pluginName: 'brew', onCleanup }),
      setTimeout(() => {
        cProcess.emit('message', { cmd: returnMessageCmd('message1'), data: null, requestId: message.requestId });
      }, 50),
    ])

    expect(onCleanup).not.toHaveBeenCalled();
  })

  it('settles concurrent requests on the same process independently when it exits', async () => {
    const cProcess = mockChildProcess();
    const first = PluginMessage.create('plan', null)
    const second = PluginMessage.create('apply', null)
    const firstCleanup = vi.fn();
    const secondCleanup = vi.fn();

    const results = await Promise.allSettled([
      sendIpcMessageForResult(first, cProcess, 60_000, { pluginName: 'brew', onCleanup: firstCleanup }),
      sendIpcMessageForResult(second, cProcess, 60_000, { pluginName: 'brew', onCleanup: secondCleanup }),
      setTimeout(() => cProcess.emit('exit', 1, null), 50),
    ])

    expect(results[0].status).to.eq('rejected');
    expect(results[1].status).to.eq('rejected');
    expect((results[0] as PromiseRejectedResult).reason.message).toContain('plan');
    expect((results[1] as PromiseRejectedResult).reason.message).toContain('apply');
    expect(firstCleanup).toHaveBeenCalledTimes(1);
    expect(secondCleanup).toHaveBeenCalledTimes(1);
    expect(cProcess.listenerCount('message')).to.eq(0);
    expect(cProcess.listenerCount('exit')).to.eq(0);
  })

  it('does not let one process failing affect a request waiting on a different process', async () => {
    const failingProcess = mockChildProcess();
    const healthyProcess = mockChildProcess();
    const healthyMessage = PluginMessage.create('plan', null)

    const [, healthyResult] = await Promise.all([
      sendIpcMessageForResult(PluginMessage.create('apply', null), failingProcess, 60_000, { pluginName: 'crashed-plugin' })
        .catch(() => 'failed' as const),
      sendIpcMessageForResult(healthyMessage, healthyProcess, 60_000, { pluginName: 'healthy-plugin' }),
      setTimeout(() => failingProcess.emit('exit', 1, null), 25),
      setTimeout(() => {
        healthyProcess.emit('message', { cmd: returnMessageCmd('plan'), data: null, requestId: healthyMessage.requestId });
      }, 75),
    ])

    expect(healthyResult).toMatchObject({ cmd: returnMessageCmd('plan') });
    expect(healthyProcess.listenerCount('exit')).to.eq(0);
  })
});
