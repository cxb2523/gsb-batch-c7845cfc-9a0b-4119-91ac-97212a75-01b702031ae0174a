import { describe, it, expect, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { ChildProcess } from 'node:child_process';

import { Readable } from 'stream';
import { PluginProcess } from './plugin-process.js';
import { mock } from 'node:test';
import { PluginMessage } from './plugin-message.js';
import { sendIpcMessageForResult } from './message-sender.js';

describe('Plugin IPC Bridge tests', async () => {

  const mockChildProcess = () => {
    const process = new ChildProcess();
    process.stdout = new EventEmitter() as Readable;
    process.stderr = new EventEmitter() as Readable
    process.send = () => true;

    return process;
  }

  it('send a message', async () => {
    const process = mockChildProcess();
    const sendMock = mock.method(process, 'send');
    const message = PluginMessage.create('message', 'data');

    const ipcBridge = new PluginProcess(process);
    ipcBridge.sendMessage(message)

    expect(sendMock.mock.calls.length).to.eq(1);
    expect(sendMock.mock.calls[0].arguments[0]).toMatchObject({ cmd: 'message', data: 'data', requestId: expect.stringContaining('') });
  })

  it('send a message and receives the response', async () => {
    const process = mockChildProcess();
    const ipcBridge = new PluginProcess(process);
    const message = PluginMessage.create('message', 'data');

    const [result] = await Promise.all([
      sendIpcMessageForResult(message, ipcBridge.process),
      setTimeout(() => process.emit('message', { cmd: 'message_Response', data: 'data', requestId: message.requestId }), 50),
    ]);

    expect(result).toMatchObject({
      "cmd": "message_Response",
      "data": "data",
    });
  });

  it('validates bad responses', async () => {
    const process = mockChildProcess();
    const ipcBridge = new PluginProcess(process);
    const message = PluginMessage.create('message', 'data');

    await expect(async () => Promise.all([
      sendIpcMessageForResult(message, ipcBridge.process),
        setTimeout(() => process.emit('message', 'data'), 50),
      ])
    ).rejects.toThrow()
  });

  it('does not leave additional listeners', async () => {
    const process = mockChildProcess();
    const ipcBridge = new PluginProcess(process);
    const message = PluginMessage.create('message', 'data');

    // NodeJS promise.all is executed in order
    await Promise.all([
      sendIpcMessageForResult(message, ipcBridge.process),
      setTimeout(() => expect(process.listeners('message').length).to.eq(1), 25),
      setTimeout(() => process.emit('message', { cmd: 'message_Response', data: 'data', requestId: message.requestId }), 50),
    ]);

    expect(process.listeners('message').length).to.eq(0);
    expect(process.stdout!.listeners('data').length).to.eq(0);
    expect(process.stderr!.listeners('data').length).to.eq(0);
  });

  it('does not interfere with existing listeners', async () => {
    const process = mockChildProcess();
    const ipcBridge = new PluginProcess(process);
    const message = PluginMessage.create('message', 'data');
    process.on('message', () => {})

    await Promise.all([
      sendIpcMessageForResult(message, ipcBridge.process),
      setTimeout(() => expect(process.listeners('message').length).to.eq(2), 25),
      setTimeout(() =>process.emit('message', { cmd: 'message_Response', data: 'data', requestId: message.requestId }), 50),
    ]);

    expect(process.listeners('message').length).to.eq(1);
  });

  it('allows new listeners to be added while waiting for the result', async () => {
    const process = mockChildProcess();
    const ipcBridge = new PluginProcess(process);
    const message = PluginMessage.create('message', 'data');

    await Promise.all([
      sendIpcMessageForResult(message, ipcBridge.process),
      setTimeout(() => {
        process.on('message', () => {})
        expect(process.listeners('message').length).to.eq(2)
      }, 25),
      setTimeout(() => process.emit('message', { cmd: 'message_Response', data: 'data', requestId: message.requestId }), 50),
    ]);

    expect(process.listeners('message').length).to.eq(1);
  });

  it('stores the plugin name', () => {
    const process = mockChildProcess();
    const ipcBridge = new PluginProcess(process, 'brew');

    expect(ipcBridge.name).to.eq('brew');
  });

  it('sendMessageForResult rejects with plugin and command names when the process exits', async () => {
    const process = mockChildProcess();
    const ipcBridge = new PluginProcess(process, 'brew');
    const killSpy = vi.spyOn(ipcBridge, 'kill');

    const [error] = await Promise.all([
      ipcBridge.sendMessageForResult('plan', { id: 'resource-1' }).then(
        () => {
          throw new Error('Expected the request to be rejected');
        },
        (err: Error) => err,
      ),
      setTimeout(() => process.emit('exit', 1, null), 50),
    ]);

    expect(error.message).toContain('exited with code 1');
    expect(error.message).to.contain('brew');
    expect(error.message).to.contain('plan');
    expect(killSpy).toHaveBeenCalledTimes(1);
  });

  it('sendMessageForResult rejects with a timeout error and kills the plugin process', async () => {
    const process = mockChildProcess();
    const ipcBridge = new PluginProcess(process, 'brew');
    const killSpy = vi.spyOn(ipcBridge, 'kill');

    const message = PluginMessage.create('apply', 'data');
    const [error] = await Promise.all([
      sendIpcMessageForResult(message, ipcBridge.process, 100, {
        pluginName: ipcBridge.name,
        onCleanup: () => ipcBridge.kill(),
      }).then(
        () => {
          throw new Error('Expected the request to be rejected');
        },
        (err: Error) => err,
      ),
    ]);

    expect(error.message).to.contain('timed out');
    expect(error.message).to.contain('brew');
    expect(error.message).to.contain('apply');
    expect(killSpy).toHaveBeenCalledTimes(1);
  });

  it('does not kill the plugin process when the response succeeds', async () => {
    const process = mockChildProcess();
    const ipcBridge = new PluginProcess(process, 'brew');
    const killSpy = vi.spyOn(ipcBridge, 'kill');
    let sentRequestId: string | undefined;
    process.send = (msg) => {
      sentRequestId = (msg as PluginMessage).requestId;
      return true;
    };

    await Promise.all([
      ipcBridge.sendMessageForResult('message', 'data'),
      setTimeout(() => process.emit('message', { cmd: 'message_Response', data: 'data', requestId: sentRequestId }), 50),
    ]);

    expect(killSpy).not.toHaveBeenCalled();
  });
});
