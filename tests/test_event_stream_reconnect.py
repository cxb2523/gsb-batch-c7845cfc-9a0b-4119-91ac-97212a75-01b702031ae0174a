"""
Unit tests for the event-stream reconnection logic of :class:`PuppetService`.

Most tests are driven by a local fake `event()` stream: no network and no real
gRPC server is involved. The last test drives a real, local grpclib server to
check the reconnection end to end.
"""
# pylint: disable=W0621,W0212

import asyncio
import json
from typing import Any, AsyncIterator, Callable, List, Optional

import pytest

from grpclib.client import Channel
from grpclib.const import Cardinality, Handler as GrpcHandler
from grpclib.exceptions import GRPCError, StreamTerminatedError
from grpclib.const import Status as GrpcStatus
from grpclib.server import Server

from wechaty_grpc.wechaty import PuppetStub
from wechaty_grpc.wechaty.puppet import EventResponse

from wechaty_puppet import (
    EventErrorPayload,
    EventMessagePayload,
    EventType,
    PuppetOptions,
    ScanStatus,
)

from wechaty_puppet_service.puppet import PuppetService


def _event(event_type: EventType, payload: dict) -> EventResponse:
    """create a fake grpc event response"""
    return EventResponse(type=event_type, payload=json.dumps(payload))


def _message_event(message_id: str) -> EventResponse:
    """create a fake message event"""
    return _event(EventType.EVENT_TYPE_MESSAGE, {'messageId': message_id})


def _dong_event(data: str) -> EventResponse:
    """create a fake dong event"""
    return _event(EventType.EVENT_TYPE_DONG, {'data': data})


class FakeEventStream:
    """
    A fake `event()` server stream.

    It yields the given events, then either blocks forever (until the consumer
    is cancelled), ends, or raises a terminal exception.
    """

    def __init__(
            self,
            events: List[EventResponse],
            terminal: Optional[BaseException] = None,
            block_forever: bool = False,
    ):
        self._events = list(events)
        self._terminal = terminal
        self._block_forever = block_forever
        self._blocker = asyncio.Event()
        self.consumed = 0

    def __aiter__(self) -> AsyncIterator[EventResponse]:
        return self

    async def __anext__(self) -> EventResponse:
        if self._events:
            self.consumed += 1
            return self._events.pop(0)

        if self._terminal is not None:
            raise self._terminal

        if self._block_forever:
            # block until the stream is cancelled (or the test ends)
            await self._blocker.wait()

        raise StopAsyncIteration


class FakePuppetStub:
    """a fake PuppetStub which hands out the scripted event streams"""

    def __init__(self, streams: List[FakeEventStream]):
        self._streams = list(streams)
        self.event_calls = 0
        self.stop_calls = 0
        self.start_calls = 0

    def event(self) -> FakeEventStream:
        """open the next scripted stream"""
        self.event_calls += 1
        if not self._streams:
            raise AssertionError(
                'the event stream was opened more times than expected '
                f'({self.event_calls})'
            )
        return self._streams.pop(0)

    async def stop(self) -> None:
        """fake stop()"""
        self.stop_calls += 1

    async def start(self) -> None:
        """fake start()"""
        self.start_calls += 1


class FakeChannel:
    """a fake grpclib channel recording whether it has been closed"""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        """fake close()"""
        self.closed = True

    def is_closed(self) -> bool:
        """tell whether close() has been called"""
        return self.closed


def _make_service(
        streams: List[FakeEventStream],
        *,
        max_attempts: int = 3,
        base_delay: float = 0.001,
        max_delay: float = 0.002,
) -> 'tuple[PuppetService, FakePuppetStub, FakeChannel]':
    """build a PuppetService wired to a fake stub and a fake channel"""
    service = PuppetService(PuppetOptions(token='test-token'))
    stub = FakePuppetStub(streams)
    channel = FakeChannel()
    service._puppet_stub = stub
    service.channel = channel  # type: ignore[assignment]

    # make the reconnection loop fast for the tests
    service.EVENT_STREAM_MAX_RECONNECT_ATTEMPTS = max_attempts
    service.EVENT_STREAM_RECONNECT_BASE_DELAY = base_delay
    service.EVENT_STREAM_RECONNECT_MAX_DELAY = max_delay
    return service, stub, channel


async def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """poll a predicate until it becomes true, or fail the test"""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError('the condition was not met in time')


def _run(coro: Any) -> Any:
    """run a coroutine on a fresh event loop"""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# reconnection
# ---------------------------------------------------------------------------

def test_reconnects_after_connection_error_and_keeps_consuming() -> None:
    """a stream interrupted by a connection error is re-opened and consumed"""

    async def scenario() -> None:
        stream_1 = FakeEventStream(
            [_message_event('m1'), _message_event('m2')],
            terminal=StreamTerminatedError(),
        )
        stream_2 = FakeEventStream(
            [_message_event('m3')],
            block_forever=True,
        )
        service, stub, _ = _make_service([stream_1, stream_2])

        received: List[str] = []
        service.on('message',
                   lambda payload: received.append(payload.message_id))
        error_payloads: List[EventErrorPayload] = []
        service.on('error', error_payloads.append)

        task = asyncio.ensure_future(service._listen_for_event())
        try:
            await _wait_until(lambda: received == ['m1', 'm2', 'm3'])
            assert stub.event_calls == 2
            assert not error_payloads
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    _run(scenario())


def test_reconnects_when_the_stream_ends_normally() -> None:
    """a gracefully ended stream is also retried"""

    async def scenario() -> None:
        # the first stream simply ends (server restarted gracefully)
        stream_1 = FakeEventStream([_message_event('m1')])
        stream_2 = FakeEventStream([_message_event('m2')], block_forever=True)
        service, stub, _ = _make_service([stream_1, stream_2])

        received: List[str] = []
        service.on('message',
                   lambda payload: received.append(payload.message_id))

        task = asyncio.ensure_future(service._listen_for_event())
        try:
            await _wait_until(lambda: received == ['m1', 'm2'])
            assert stub.event_calls == 2
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    _run(scenario())


def test_reconnect_delay_grows() -> None:
    """the reconnection attempts use a growing (bounded) delay"""

    async def scenario() -> None:
        service, _, _ = _make_service([], max_attempts=4,
                                      base_delay=0.01, max_delay=0.04)

        delays = [
            min(
                service.EVENT_STREAM_RECONNECT_BASE_DELAY * (2 ** attempt),
                service.EVENT_STREAM_RECONNECT_MAX_DELAY
            )
            for attempt in range(service.EVENT_STREAM_MAX_RECONNECT_ATTEMPTS)
        ]
        assert delays == [0.01, 0.02, 0.04, 0.04]

    _run(scenario())


# ---------------------------------------------------------------------------
# duplicate suppression
# ---------------------------------------------------------------------------

def test_replayed_event_is_not_dispatched_twice() -> None:
    """the event replayed by the re-opened stream is suppressed"""

    async def scenario() -> None:
        # after the disconnection the server replays the last event (m2)
        stream_1 = FakeEventStream(
            [_message_event('m1'), _message_event('m2')],
            terminal=StreamTerminatedError(),
        )
        stream_2 = FakeEventStream(
            [_message_event('m2'), _message_event('m3')],
            block_forever=True,
        )
        service, stub, _ = _make_service([stream_1, stream_2])

        received: List[str] = []
        service.on('message',
                   lambda payload: received.append(payload.message_id))

        task = asyncio.ensure_future(service._listen_for_event())
        try:
            await _wait_until(lambda: received == ['m1', 'm2', 'm3'])
            assert stub.event_calls == 2
            # a little longer, to be sure no late duplicate arrives
            await asyncio.sleep(0.05)
            assert received == ['m1', 'm2', 'm3']
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    _run(scenario())


def test_heartbeat_and_dong_are_never_suppressed() -> None:
    """liveness events replayed on reconnection are still forwarded"""

    async def scenario() -> None:
        heartbeat = _event(
            EventType.EVENT_TYPE_HEARTBEAT,
            {'data': 'Wechaty Puppet gRPC stream connect successfully'}
        )
        stream_1 = FakeEventStream(
            [_dong_event('a'), heartbeat],
            terminal=StreamTerminatedError(),
        )
        stream_2 = FakeEventStream(
            [_dong_event('a'), heartbeat],
            block_forever=True,
        )
        service, _, _ = _make_service([stream_1, stream_2])

        dongs: List[Any] = []
        heartbeats: List[Any] = []
        service.on('dong', dongs.append)
        service.on('heartbeat', heartbeats.append)

        task = asyncio.ensure_future(service._listen_for_event())
        try:
            await _wait_until(
                lambda: len(dongs) == 2 and len(heartbeats) == 2
            )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    _run(scenario())


def test_repeated_payload_after_the_replay_prefix_is_forwarded() -> None:
    """an identical payload later in the stream is a distinct event"""

    async def scenario() -> None:
        # the message m1 is replayed first, then the *new* events follow: the
        # same payload arriving again later is a real event and must pass
        stream_1 = FakeEventStream(
            [_message_event('m1')],
            terminal=StreamTerminatedError(),
        )
        stream_2 = FakeEventStream(
            [_message_event('m1'), _message_event('m2'),
             _message_event('m1')],
            block_forever=True,
        )
        service, _, _ = _make_service([stream_1, stream_2])

        received: List[str] = []
        service.on('message',
                   lambda payload: received.append(payload.message_id))

        task = asyncio.ensure_future(service._listen_for_event())
        try:
            await _wait_until(
                lambda: received == ['m1', 'm2', 'm1']
            )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    _run(scenario())


def test_login_payload_can_repeat_across_sessions() -> None:
    """the same login payload of a later session is not suppressed"""

    async def scenario() -> None:
        login = _event(EventType.EVENT_TYPE_LOGIN,
                       {'contactId': 'user-1'})
        stream_1 = FakeEventStream(
            [login],
            terminal=StreamTerminatedError(),
        )
        # after the interruption the stream replays login, then the account
        # logs out and logs in again: that second login is a new session
        stream_2 = FakeEventStream(
            [
                login,
                _event(EventType.EVENT_TYPE_LOGOUT,
                       {'contactId': 'user-1', 'data': 'logout'}),
                login,
            ],
            block_forever=True,
        )
        service, _, _ = _make_service([stream_1, stream_2])

        logins: List[Any] = []
        service.on('login', logins.append)

        task = asyncio.ensure_future(service._listen_for_event())
        try:
            await _wait_until(lambda: len(logins) == 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    _run(scenario())


# ---------------------------------------------------------------------------
# error reporting
# ---------------------------------------------------------------------------

def test_non_connection_error_is_reported_and_does_not_reconnect() -> None:
    """a business-level error is reported through the error event"""

    async def scenario() -> None:
        stream_1 = FakeEventStream(
            [],
            terminal=ValueError('unexpected json payload'),
        )
        service, stub, _ = _make_service([stream_1])

        errors: List[EventErrorPayload] = []
        service.on('error', errors.append)

        await service._listen_for_event()

        assert stub.event_calls == 1
        assert len(errors) == 1
        assert 'unexpected json payload' in errors[0].data

    _run(scenario())


def test_authentication_error_does_not_reconnect() -> None:
    """a grpc business status is not treated as a connection error"""

    async def scenario() -> None:
        stream_1 = FakeEventStream(
            [],
            terminal=GRPCError(GrpcStatus.UNAUTHENTICATED, 'Invalid TOKEN'),
        )
        service, stub, _ = _make_service([stream_1])

        errors: List[EventErrorPayload] = []
        service.on('error', errors.append)

        await service._listen_for_event()

        assert stub.event_calls == 1
        assert len(errors) == 1

    _run(scenario())


def test_reconnect_budget_is_bounded_and_error_is_reported() -> None:
    """when the attempts are exhausted the caller is notified via error"""

    async def scenario() -> None:
        max_attempts = 2
        streams = [
            FakeEventStream([], terminal=StreamTerminatedError())
            for _ in range(max_attempts + 1)
        ]
        service, stub, _ = _make_service(
            streams, max_attempts=max_attempts
        )

        errors: List[EventErrorPayload] = []
        service.on('error', errors.append)

        await service._listen_for_event()

        # the initial connection plus `max_attempts` reconnections
        assert stub.event_calls == max_attempts + 1
        assert len(errors) == 1
        assert 'exhausted' in errors[0].data

    _run(scenario())


def test_reconnect_budget_resets_after_a_stable_connection() -> None:
    """a stream which lived long enough is not part of the failure burst"""

    async def scenario() -> None:
        # simulate a long-lived stream by shrinking the stability threshold:
        # two failures then a healthy stream, then two failures again. With a
        # budget of 2, the second burst must be retried instead of exhausting
        # the budget accumulated by the first one.
        streams = [
            FakeEventStream([], terminal=StreamTerminatedError()),
            FakeEventStream([], terminal=StreamTerminatedError()),
            FakeEventStream([_message_event('healthy')],
                            block_forever=True),
        ]
        service, stub, _ = _make_service(streams, max_attempts=2)
        # every freshly re-opened stream counts as stable
        service.EVENT_STREAM_STABLE_CONNECTION_SECONDS = 0.0

        received: List[str] = []
        service.on('message',
                   lambda payload: received.append(payload.message_id))

        task = asyncio.ensure_future(service._listen_for_event())
        try:
            await _wait_until(lambda: received == ['healthy'])
            # 1 initial + 2 reconnections: the budget was reset in between
            assert stub.event_calls == 3
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    _run(scenario())


def test_error_without_listener_does_not_break_the_loop() -> None:
    """with no `error` listener the failure is still reported safely"""

    async def scenario() -> None:
        stream_1 = FakeEventStream([], terminal=ValueError('boom'))
        service, _, _ = _make_service([stream_1])

        # no listener registered: pyee raises on `error`, the loop must not
        await service._listen_for_event()

    _run(scenario())


# ---------------------------------------------------------------------------
# stop semantics
# ---------------------------------------------------------------------------

def test_stop_interrupts_the_reconnection_wait() -> None:
    """stop() terminates a pending reconnection and opens no new stream"""

    async def scenario() -> None:
        # the reconnection delay is long: stop() must interrupt it
        stream_1 = FakeEventStream([], terminal=StreamTerminatedError())
        service, stub, channel = _make_service(
            [stream_1], max_attempts=5, base_delay=30.0, max_delay=30.0
        )

        task = asyncio.ensure_future(service._listen_for_event())
        service._event_listen_task = task

        # wait until the loop is waiting for the reconnection delay
        await _wait_until(lambda: stub.event_calls == 1)
        await asyncio.sleep(0.05)
        assert not task.done()

        stopped_at = asyncio.get_event_loop().time()
        await service.stop()
        elapsed = asyncio.get_event_loop().time() - stopped_at

        assert elapsed < 1.0, 'stop() should not wait for the full delay'
        assert task.done()
        # no new connection was attempted after stop()
        assert stub.event_calls == 1
        assert channel.closed is True

    _run(scenario())


def test_stop_while_consuming_an_in_flight_stream() -> None:
    """stop() also terminates an in-flight event stream"""

    async def scenario() -> None:
        stream_1 = FakeEventStream(
            [_message_event('m1')], block_forever=True
        )
        service, stub, channel = _make_service([stream_1])

        received: List[str] = []
        service.on('message',
                   lambda payload: received.append(payload.message_id))

        task = asyncio.ensure_future(service._listen_for_event())
        service._event_listen_task = task

        await _wait_until(lambda: received == ['m1'])

        await service.stop()

        assert task.done()
        assert stub.event_calls == 1
        assert stub.stop_calls == 1
        assert channel.closed is True

    _run(scenario())


def test_full_start_stop_lifecycle_with_fake_endpoint() -> None:
    """start() consumes the stream and stop() releases the channel"""

    async def scenario() -> None:
        stream_1 = FakeEventStream(
            [_message_event('m1')], block_forever=True
        )
        service, stub, channel = _make_service([stream_1])
        # bypass the network bootstrap: the fake stub/channel are already set
        service._init_puppet = lambda: None  # type: ignore[method-assign]

        received: List[str] = []
        service.on('message',
                   lambda payload: received.append(payload.message_id))

        start_task = asyncio.ensure_future(service.start())
        try:
            await _wait_until(lambda: received == ['m1'])
            assert stub.start_calls == 1

            await service.stop()
            await asyncio.wait_for(start_task, timeout=2.0)
        finally:
            if not start_task.done():
                start_task.cancel()

        assert channel.closed is True

    _run(scenario())


# ---------------------------------------------------------------------------
# payload fidelity
# ---------------------------------------------------------------------------

def test_dispatched_payloads_keep_their_fields_and_types() -> None:
    """the event payloads are unchanged by the reconnection refactoring"""

    async def scenario() -> None:
        scan_event = _event(
            EventType.EVENT_TYPE_SCAN,
            {'status': ScanStatus.Waiting.value, 'qrcode': 'https://qr'}
        )
        stream_1 = FakeEventStream([scan_event, _message_event('m1')])
        service, _, _ = _make_service([stream_1])

        scans: List[Any] = []
        messages: List[EventMessagePayload] = []
        service.on('scan', scans.append)
        service.on('message', messages.append)

        await service._listen_for_event()

        assert len(scans) == 1
        assert scans[0].status == ScanStatus.Waiting
        assert scans[0].qrcode == 'https://qr'
        assert scans[0].data is None

        assert len(messages) == 1
        assert messages[0].message_id == 'm1'

    _run(scenario())


# ---------------------------------------------------------------------------
# end-to-end: a real local grpclib server
# ---------------------------------------------------------------------------

class _RealEventService:
    """
    A minimal implementation of the `wechaty.Puppet/Event` server stream.

    Every connection emits its own messages, then the connection is dropped
    on demand (`drop_connections`), which is what a service restart looks
    like from the client side.
    """

    def __init__(self) -> None:
        self.connections = 0
        # how many currently-open connections should be ended (one-shot)
        self._connections_to_drop = 0

    def __mapping__(self) -> 'dict[str, GrpcHandler]':
        return {
            '/wechaty.Puppet/Event': GrpcHandler(
                self.event,
                Cardinality.UNARY_STREAM,
                EventResponse,
                EventResponse,
            )
        }

    async def event(self, stream: Any) -> None:
        """send the events of one connection, then keep it open"""
        self.connections += 1
        connection_id = self.connections
        await stream.send_message(_message_event(f'conn{connection_id}-1'))
        await stream.send_message(_message_event(f'conn{connection_id}-2'))
        while self._connections_to_drop == 0:
            await asyncio.sleep(0.01)
        # the connection is dropped once, as a server restart would do; the
        # next connection stays open
        self._connections_to_drop -= 1

    def drop_connections(self) -> None:
        """make the currently open connection end (simulates a restart)"""
        self._connections_to_drop = self.connections


def test_reconnects_against_a_real_grpc_server() -> None:
    """the stream is re-opened after the server drops the connection"""

    async def scenario() -> None:
        event_service = _RealEventService()
        server = Server([event_service])
        await server.start('127.0.0.1', 0)
        # pylint: disable=W0212
        server_socket = server._server.sockets[0]  # type: ignore[union-attr]
        port = server_socket.getsockname()[1]

        channel = Channel('127.0.0.1', port)
        service = PuppetService(PuppetOptions(token='test-token'))
        service._puppet_stub = PuppetStub(channel)
        service.channel = channel
        service.EVENT_STREAM_MAX_RECONNECT_ATTEMPTS = 5
        service.EVENT_STREAM_RECONNECT_BASE_DELAY = 0.01
        service.EVENT_STREAM_RECONNECT_MAX_DELAY = 0.02

        received: List[str] = []
        service.on('message',
                   lambda payload: received.append(payload.message_id))
        errors: List[EventErrorPayload] = []
        service.on('error', errors.append)

        task = asyncio.ensure_future(service._listen_for_event())
        service._event_listen_task = task
        try:
            await _wait_until(
                lambda: received == ['conn1-1', 'conn1-2'], timeout=5.0
            )

            # simulate a service restart: the connection is dropped and the
            # reconnection loop has to open a new stream on the same channel
            event_service.drop_connections()

            await _wait_until(
                lambda: received == [
                    'conn1-1', 'conn1-2', 'conn2-1', 'conn2-2'
                ],
                timeout=5.0,
            )
            assert event_service.connections == 2
            assert not errors
        finally:
            await service.stop()
            server.close()
            await server.wait_closed()

        assert channel._connected is False

    _run(scenario())
