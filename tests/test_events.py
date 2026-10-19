"""Tests for the SSE listener: auth errors, resilience, callbacks, lifecycle.

The device is replaced by a scripted fake session, so these tests can model
things an HTTP mock cannot: a stream that hangs, breaks mid-read or is cut.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final, Self, cast
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientConnectionError, ClientSession

import aionanoleaf2.nanoleaf as nanoleaf_module
from aionanoleaf2 import (
    AuthError,
    EffectsEvent,
    EndpointNotFoundError,
    InvalidToken,
    Nanoleaf,
    NoAuthToken,
    StateEvent,
    TouchEvent,
    TouchStreamEvent,
    Unavailable,
)

HOST: Final = "192.168.0.10"
TOKEN: Final = "TESTTOKEN0123456789"
EVENTS_URL: Final = f"http://{HOST}:16021/api/v1/{TOKEN}/events?id=1,3"

HANG: Final = object()
"""Stream item: block forever (an idle but open connection)."""


# --- Scripted fake of the aiohttp session ---------------------------------


class FakeContent:
    """Stand-in for ``ClientResponse.content``.

    Items are consumed one per ``readline`` call: ``bytes`` are returned,
    exceptions are raised, floats sleep, ``HANG`` blocks. When the items run
    out the stream ends (EOF).
    """

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    async def readline(self) -> bytes:
        while self._items:
            item = self._items.pop(0)
            if isinstance(item, float):
                await asyncio.sleep(item)
                continue
            if item is HANG:
                await asyncio.Event().wait()
            if isinstance(item, BaseException):
                raise item
            return cast(bytes, item)
        return b""


class FakeResponse:
    """Async context manager returned by ``FakeSession.get``."""

    def __init__(self, status: int = 200, items: list[Any] | None = None) -> None:
        self.status = status
        self.content = FakeContent(items or [])
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.closed = True


class _Pending:
    """One scripted ``session.get`` outcome."""

    def __init__(self, outcome: FakeResponse | BaseException) -> None:
        self._outcome = outcome

    async def __aenter__(self) -> FakeResponse:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return await self._outcome.__aenter__()

    async def __aexit__(self, *exc_info: object) -> None:
        if isinstance(self._outcome, FakeResponse):
            await self._outcome.__aexit__(*exc_info)


class FakeSession:
    """Plays back ``outcomes`` for successive ``get`` calls, then idles."""

    def __init__(self, *outcomes: FakeResponse | BaseException) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.responses: list[FakeResponse] = []

    def get(self, url: str, **kwargs: Any) -> _Pending:
        self.calls.append({"url": url, **kwargs})
        outcome = self._outcomes.pop(0) if self._outcomes else FakeResponse(200, [HANG])
        if isinstance(outcome, FakeResponse):
            self.responses.append(outcome)
        return _Pending(outcome)


# --- Helpers ---------------------------------------------------------------


def sse(type_id: int, *events: object, blank: bool = True) -> list[bytes]:
    """Return the lines of one well-formed SSE message."""
    lines = [
        f"id: {type_id}\n".encode(),
        f"data: {json.dumps({'events': list(events)})}\n".encode(),
    ]
    if blank:
        lines.append(b"\n")
    return lines


def state_event(attr: int, value: Any) -> dict[str, Any]:
    return {"attr": attr, "value": value}


def make_client(session: FakeSession, token: str | None = TOKEN) -> Nanoleaf:
    return Nanoleaf(cast(ClientSession, session), HOST, token)


async def eventually(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Wait until ``predicate`` holds; fail the test on timeout."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def stop(task: asyncio.Task[None]) -> None:
    """Cancel a listener and assert that the cancellation is passed on."""
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the reconnect delays so retry tests finish in milliseconds."""
    monkeypatch.setattr(nanoleaf_module, "_SSE_BACKOFF_INITIAL", 0.001)
    monkeypatch.setattr(nanoleaf_module, "_SSE_BACKOFF_MAX", 0.004)


# --- P1-6: authentication and permanent errors ------------------------------


async def test_401_raises_invalid_token_without_reconnecting() -> None:
    session = FakeSession(FakeResponse(401), FakeResponse(200, [HANG]))
    client = make_client(session)

    with pytest.raises(InvalidToken) as excinfo:
        async with asyncio.timeout(2):
            await client.listen_events()

    assert isinstance(excinfo.value, AuthError)
    assert len(session.calls) == 1
    assert TOKEN not in str(excinfo.value)
    assert session.responses[0].closed


async def test_401_after_established_stream_raises_invalid_token() -> None:
    received: list[StateEvent] = []
    session = FakeSession(
        FakeResponse(200, [*sse(1, state_event(2, 40))]),  # then EOF
        FakeResponse(401),
    )
    client = make_client(session)

    with pytest.raises(InvalidToken):
        async with asyncio.timeout(2):
            await client.listen_events(state_callback=received.append)

    assert [e.value for e in received] == [40]
    assert len(session.calls) == 2


async def test_404_raises_endpoint_not_found_without_reconnecting() -> None:
    session = FakeSession(FakeResponse(404))
    client = make_client(session)

    with pytest.raises(EndpointNotFoundError) as excinfo:
        async with asyncio.timeout(2):
            await client.listen_events()

    assert excinfo.value.path == "events"
    assert len(session.calls) == 1


@pytest.mark.parametrize("token", [None, ""])
async def test_missing_token_raises_before_any_io(token: str | None) -> None:
    session = FakeSession()
    client = make_client(session, token)
    opened = AsyncMock()
    client._open_udp_socket_for_touch_data_stream = opened  # type: ignore[method-assign]

    with pytest.raises(NoAuthToken):
        await client.listen_events(touch_stream_callback=lambda event: None)

    assert session.calls == []
    opened.assert_not_called()


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_other_http_errors_are_retried(status: int) -> None:
    received: list[StateEvent] = []
    session = FakeSession(
        FakeResponse(status),
        FakeResponse(status),
        FakeResponse(200, [*sse(1, state_event(2, 7)), HANG]),
    )
    client = make_client(session)

    task = asyncio.create_task(client.listen_events(state_callback=received.append))
    await eventually(lambda: bool(received))
    await stop(task)

    assert len(session.calls) == 3


# --- Request shape ------------------------------------------------------------


async def test_request_url_headers_and_timeout() -> None:
    session = FakeSession(FakeResponse(200, [HANG]))
    client = make_client(session)

    task = asyncio.create_task(client.listen_events())
    await eventually(lambda: bool(session.calls))
    await stop(task)

    call = session.calls[0]
    assert call["url"] == EVENTS_URL
    assert call["headers"] is None
    timeout = call["timeout"]
    assert timeout.total is None
    assert timeout.sock_connect == 5.0
    assert timeout.sock_read is None


async def test_url_subscribes_to_layout_and_touch_only_when_requested() -> None:
    session = FakeSession(FakeResponse(200, [HANG]))
    client = make_client(session)

    task = asyncio.create_task(
        client.listen_events(
            layout_callback=lambda e: None, touch_callback=lambda e: None
        )
    )
    await eventually(lambda: bool(session.calls))
    await stop(task)

    assert session.calls[0]["url"].endswith("/events?id=1,3,2,4")


async def test_touch_port_header_is_sent() -> None:
    session = FakeSession(FakeResponse(200, [HANG]))
    client = make_client(session)

    task = asyncio.create_task(client._listen_for_server_sent_events(socket_port=4242))
    await eventually(lambda: bool(session.calls))
    await stop(task)

    assert session.calls[0]["headers"] == {"TouchEventsPort": "4242"}
    assert session.calls[0]["url"].endswith("/events?id=1,3,4")


# --- P0-2: parsing resilience ---------------------------------------------------


async def test_events_update_local_state_and_reach_callbacks() -> None:
    states: list[StateEvent] = []
    effects: list[EffectsEvent] = []
    touches: list[TouchEvent] = []
    session = FakeSession(
        FakeResponse(
            200,
            [
                *sse(1, state_event(1, True), state_event(2, 55), state_event(6, "hs")),
                *sse(3, {"attr": 1, "value": "Aurora"}),
                *sse(4, {"gesture": 2, "panelId": -1}),
                HANG,
            ],
        )
    )
    client = make_client(session)

    task = asyncio.create_task(
        client.listen_events(
            state_callback=states.append,
            effects_callback=effects.append,
            touch_callback=touches.append,
        )
    )
    await eventually(lambda: bool(touches))
    await stop(task)

    assert [(e.attribute, e.value) for e in states] == [
        ("is_on", True),
        ("brightness", 55),
        ("color_mode", "hs"),
    ]
    assert client.is_on is True
    assert client.brightness == 55
    assert client.color_mode == "hs"
    assert client.effect == "Aurora"
    assert [e.effect for e in effects] == ["Aurora"]
    assert touches[0].gesture_id == 2


async def test_malformed_input_does_not_end_the_loop() -> None:
    received: list[StateEvent] = []
    junk: list[Any] = [
        b"\xff\xfe\xfd\n",  # not UTF-8
        b"id: abc\n",  # non-numeric type
        b'data: {"events": []}\n',  # data without id
        b"id: 1\n",
        b'data: {"events": [{"attr": 2,\n',  # truncated JSON
        b"id: 1\n",
        b"data: not json at all\n",
        b"id: 1\n",
        b"data: [1, 2, 3]\n",  # JSON, but not an object
        b"id: 1\n",
        b'data: {"events": "nope"}\n',  # events is not a list
        b"id: 1\n",
        b'data: {"events": [42, "x", null, {"attr": 2}, {"value": 9}]}\n',  # bad events
        b"id: 99\n",
        b'data: {"events": [{"attr": 1}]}\n',  # unknown event type
        b"id: 1\n",
        b'data: {"events": ' + b"[" * 100_000 + b"\n",  # recursion bomb
        b"retry: 3000\n",
        b": comment\n",
        b"\n",
    ]
    session = FakeSession(FakeResponse(200, [*junk, *sse(1, state_event(2, 33)), HANG]))
    client = make_client(session)

    task = asyncio.create_task(client.listen_events(state_callback=received.append))
    await eventually(lambda: bool(received))
    assert not task.done()
    await stop(task)

    assert [e.value for e in received] == [33]
    assert len(session.calls) == 1  # never needed a reconnect
    assert client.brightness == 33


async def test_broken_event_between_good_events_is_skipped() -> None:
    received: list[StateEvent] = []
    payload = [state_event(2, 10), {"attr": 2}, state_event(2, 20)]
    session = FakeSession(FakeResponse(200, [*sse(1, *payload), HANG]))
    client = make_client(session)

    task = asyncio.create_task(client.listen_events(state_callback=received.append))
    await eventually(lambda: len(received) == 2)
    await stop(task)

    assert [e.value for e in received] == [10, 20]


async def test_blank_line_between_id_and_data_is_tolerated() -> None:
    received: list[StateEvent] = []
    items = [
        b"id: 1\n",
        b"\n",
        b": keep-alive\n",
        b'data: {"events": [{"attr": 2, "value": 5}]}\n',
        HANG,
    ]
    session = FakeSession(FakeResponse(200, items))
    client = make_client(session)

    task = asyncio.create_task(client.listen_events(state_callback=received.append))
    await eventually(lambda: bool(received))
    await stop(task)

    assert received[0].value == 5


async def test_oversized_line_triggers_reconnect() -> None:
    received: list[StateEvent] = []
    session = FakeSession(
        FakeResponse(200, [ValueError("Chunk too big")]),
        FakeResponse(200, [*sse(1, state_event(2, 8)), HANG]),
    )
    client = make_client(session)

    task = asyncio.create_task(client.listen_events(state_callback=received.append))
    await eventually(lambda: bool(received))
    await stop(task)

    assert len(session.calls) == 2


async def test_emersion_refresh_failure_does_not_stop_stream() -> None:
    effects: list[EffectsEvent] = []
    session = FakeSession(
        FakeResponse(200, [*sse(3, {"attr": 1, "value": "*Emersion*"}), HANG])
    )
    client = make_client(session)
    client.get_emersion = AsyncMock(side_effect=Unavailable("down"))  # type: ignore[method-assign]

    task = asyncio.create_task(client.listen_events(effects_callback=effects.append))
    await eventually(lambda: bool(effects))
    await stop(task)

    client.get_emersion.assert_awaited_once()
    assert effects[0].effect == "*Emersion*"


# --- P0-2: callbacks -----------------------------------------------------------


async def test_sync_and_async_callbacks_both_fire() -> None:
    sync_seen: list[int] = []
    async_seen: list[int] = []

    def on_state(event: StateEvent) -> None:
        sync_seen.append(cast(int, event.value))

    async def on_effect(event: EffectsEvent) -> None:
        await asyncio.sleep(0)
        async_seen.append(len(event.effect))

    session = FakeSession(
        FakeResponse(
            200,
            [*sse(1, state_event(2, 21)), *sse(3, {"attr": 1, "value": "Rain"}), HANG],
        )
    )
    client = make_client(session)

    task = asyncio.create_task(
        client.listen_events(state_callback=on_state, effects_callback=on_effect)
    )
    await eventually(lambda: bool(sync_seen) and bool(async_seen))
    await stop(task)

    assert sync_seen == [21]
    assert async_seen == [4]


async def test_callback_exceptions_do_not_stop_the_stream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    def sync_boom(event: StateEvent) -> None:
        calls.append(cast(int, event.value))
        raise RuntimeError("sync callback failed")

    async def async_boom(event: EffectsEvent) -> None:
        raise RuntimeError("async callback failed")

    session = FakeSession(
        FakeResponse(
            200,
            [
                *sse(1, state_event(2, 1)),
                *sse(3, {"attr": 1, "value": "A"}),
                *sse(1, state_event(2, 2)),
                HANG,
            ],
        )
    )
    client = make_client(session)

    with caplog.at_level(logging.ERROR, logger="aionanoleaf2.nanoleaf"):
        task = asyncio.create_task(
            client.listen_events(state_callback=sync_boom, effects_callback=async_boom)
        )
        await eventually(lambda: calls == [1, 2])
        await eventually(lambda: not client._background_tasks)
        assert not task.done()
        await stop(task)

    messages = [r.getMessage() for r in caplog.records]
    assert any("raised" in m for m in messages)
    assert any("Async event callback failed" in m for m in messages)
    assert len(session.calls) == 1


async def test_async_callback_tasks_are_referenced_until_done() -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow(event: StateEvent) -> None:
        started.set()
        await release.wait()

    session = FakeSession(FakeResponse(200, [*sse(1, state_event(2, 3)), HANG]))
    client = make_client(session)

    task = asyncio.create_task(client.listen_events(state_callback=slow))
    await asyncio.wait_for(started.wait(), 2)
    assert len(client._background_tasks) == 1

    release.set()
    await eventually(lambda: not client._background_tasks)
    await stop(task)


async def test_awaitable_returning_callback_is_supported() -> None:
    done: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    def returns_future(event: StateEvent) -> Awaitable[None]:
        async def _inner() -> None:
            done.set_result(cast(int, event.value))

        return _inner()

    session = FakeSession(FakeResponse(200, [*sse(1, state_event(2, 12)), HANG]))
    client = make_client(session)

    task = asyncio.create_task(client.listen_events(state_callback=returns_future))
    assert await asyncio.wait_for(done, 2) == 12
    await stop(task)


# --- P2-3: reconnect, backoff, cancellation ----------------------------------------


@pytest.mark.parametrize(
    ("attempt", "low", "high"),
    [(0, 0.5, 1.0), (1, 1.0, 2.0), (3, 4.0, 8.0), (5, 15.0, 30.0), (50, 15.0, 30.0)],
)
def test_backoff_delay_grows_with_jitter_and_is_capped(
    monkeypatch: pytest.MonkeyPatch, attempt: int, low: float, high: float
) -> None:
    monkeypatch.setattr(nanoleaf_module, "_SSE_BACKOFF_INITIAL", 1.0)
    monkeypatch.setattr(nanoleaf_module, "_SSE_BACKOFF_MAX", 30.0)
    samples = [nanoleaf_module._backoff_delay(attempt) for _ in range(200)]
    assert all(low <= value <= high for value in samples)
    assert len(set(samples)) > 1  # jitter is applied


def test_backoff_delay_survives_huge_attempt_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nanoleaf_module, "_SSE_BACKOFF_INITIAL", 1.0)
    monkeypatch.setattr(nanoleaf_module, "_SSE_BACKOFF_MAX", 30.0)
    assert nanoleaf_module._backoff_delay(1_000_000) <= 30.0


def _record_attempts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace the delay function with a recorder that does not sleep."""
    attempts: list[int] = []

    def fake_delay(attempt: int) -> float:
        attempts.append(attempt)
        return 0.0

    monkeypatch.setattr(nanoleaf_module, "_backoff_delay", fake_delay)
    return attempts


async def test_connection_errors_back_off_and_receiving_an_event_resets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = _record_attempts(monkeypatch)
    received: list[StateEvent] = []
    session = FakeSession(
        ClientConnectionError("down"),
        TimeoutError(),
        ClientConnectionError("down"),
        FakeResponse(200, sse(1, state_event(2, 4))),  # event, then EOF
        ClientConnectionError("down again"),
        FakeResponse(200, [HANG]),
    )
    client = make_client(session)

    task = asyncio.create_task(client.listen_events(state_callback=received.append))
    await eventually(lambda: len(session.calls) == 6)
    await stop(task)

    assert [e.value for e in received] == [4]
    # 3 failures escalate; the event resets; EOF and the next error start over.
    assert attempts == [0, 1, 2, 0, 1]


async def test_stable_connection_resets_backoff_without_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = _record_attempts(monkeypatch)
    monkeypatch.setattr(nanoleaf_module, "_SSE_STABLE_AFTER", 0.02)
    session = FakeSession(
        ClientConnectionError("down"),
        ClientConnectionError("down"),
        FakeResponse(
            200, [0.05]
        ),  # idle for longer than the stability window, then EOF
        FakeResponse(200, [HANG]),
    )
    client = make_client(session)

    task = asyncio.create_task(client.listen_events())
    await eventually(lambda: len(session.calls) == 4)
    await stop(task)

    assert attempts == [0, 1, 0]


async def test_quick_eof_without_events_keeps_backing_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = _record_attempts(monkeypatch)
    session = FakeSession(
        FakeResponse(200, []), FakeResponse(200, []), FakeResponse(200, [HANG])
    )
    client = make_client(session)

    task = asyncio.create_task(client.listen_events())
    await eventually(lambda: len(session.calls) == 3)
    await stop(task)

    assert attempts == [0, 1]  # no hot reconnect loop


def _stream_records(
    caplog: pytest.LogCaptureFixture, level: int
) -> list[logging.LogRecord]:
    """Return the records of one level that report the event stream state."""
    return [
        r
        for r in caplog.records
        if r.levelno == level and "unavailable" in r.getMessage()
    ]


async def test_outage_is_logged_once_and_recovery_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = FakeSession(
        ClientConnectionError("down"),
        TimeoutError(),
        ClientConnectionError("down"),
        FakeResponse(200, sse(1, state_event(2, 4))),  # event, then EOF
        ClientConnectionError("down again"),
        FakeResponse(200, [HANG]),
    )
    client = make_client(session)

    with caplog.at_level(logging.DEBUG, logger="aionanoleaf2.nanoleaf"):
        task = asyncio.create_task(client.listen_events(state_callback=lambda e: None))
        await eventually(lambda: len(session.calls) == 6)
        await stop(task)

    warnings = _stream_records(caplog, logging.WARNING)
    # First failure of each outage warns; the repeats within it are only DEBUG.
    assert [("ClientConnectionError: down" in w.getMessage()) for w in warnings] == [
        True,
        True,
    ]
    assert "down again" in warnings[1].getMessage()
    assert len(_stream_records(caplog, logging.DEBUG)) == 2
    restored = [r for r in caplog.records if r.levelno == logging.INFO]
    assert [r.getMessage() for r in restored] == [f"Event stream of {HOST} restored"]
    assert all(TOKEN not in r.getMessage() for r in caplog.records)


async def test_repeated_http_errors_are_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = FakeSession(
        FakeResponse(500),
        FakeResponse(500),
        FakeResponse(503),
        FakeResponse(200, [HANG]),
    )
    client = make_client(session)

    with caplog.at_level(logging.DEBUG, logger="aionanoleaf2.nanoleaf"):
        task = asyncio.create_task(client.listen_events())
        await eventually(lambda: len(session.calls) == 4)
        await stop(task)

    warnings = _stream_records(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "HTTP 500" in warnings[0].getMessage()
    assert len(_stream_records(caplog, logging.DEBUG)) == 2
    # A connection that opens but never delivers an event is not "restored" yet.
    assert not [r for r in caplog.records if r.levelno == logging.INFO]


async def test_stable_connection_reports_recovery_without_events(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(nanoleaf_module, "_SSE_STABLE_AFTER", 0.02)
    session = FakeSession(
        ClientConnectionError("down"),
        FakeResponse(200, [0.05]),  # idle longer than the stability window, then EOF
        FakeResponse(200, [HANG]),
    )
    client = make_client(session)

    with caplog.at_level(logging.INFO, logger="aionanoleaf2.nanoleaf"):
        task = asyncio.create_task(client.listen_events())
        await eventually(lambda: len(session.calls) == 3)
        await stop(task)

    assert len(_stream_records(caplog, logging.WARNING)) == 1
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO] == [
        f"Event stream of {HOST} restored"
    ]


async def test_cancel_closes_response_and_propagates() -> None:
    session = FakeSession(FakeResponse(200, [HANG]))
    client = make_client(session)

    task = asyncio.create_task(client.listen_events())
    await eventually(lambda: bool(session.calls))
    await asyncio.sleep(0.01)
    await stop(task)

    assert session.responses[0].closed
    assert len(session.calls) == 1  # no reconnect after cancellation


async def test_cancel_during_backoff_propagates() -> None:
    session = FakeSession(ClientConnectionError("down"))
    client = make_client(session)

    task = asyncio.create_task(client.listen_events())
    await eventually(lambda: bool(session.calls))
    await stop(task)


async def test_cancel_cancels_running_callback_tasks() -> None:
    started = asyncio.Event()
    finished: list[str] = []

    async def never_ending(event: StateEvent) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            finished.append("cancelled")
            raise

    session = FakeSession(FakeResponse(200, [*sse(1, state_event(2, 3)), HANG]))
    client = make_client(session)

    task = asyncio.create_task(client.listen_events(state_callback=never_ending))
    await asyncio.wait_for(started.wait(), 2)
    assert len(client._background_tasks) == 1
    await stop(task)

    assert finished == ["cancelled"]
    assert not client._background_tasks


# --- UDP touch endpoint lifecycle -----------------------------------------------------


class FakeTransport:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _install_fake_udp(client: Nanoleaf, transport: FakeTransport) -> None:
    async def fake_open(
        callback: Callable[[TouchStreamEvent], Any],
        local_ip: str | None = None,
        local_port: int | None = None,
    ) -> int:
        client._touch_transport = cast(Any, transport)
        return 5555

    client._open_udp_socket_for_touch_data_stream = fake_open  # type: ignore[method-assign]


async def test_udp_endpoint_is_closed_on_cancel() -> None:
    transport = FakeTransport()
    session = FakeSession(FakeResponse(200, [HANG]))
    client = make_client(session)
    _install_fake_udp(client, transport)

    task = asyncio.create_task(
        client.listen_events(touch_stream_callback=lambda event: None)
    )
    await eventually(lambda: bool(session.calls))
    assert session.calls[0]["headers"] == {"TouchEventsPort": "5555"}
    await stop(task)

    assert transport.close_calls == 1
    assert client._touch_transport is None


async def test_udp_endpoint_is_closed_on_invalid_token() -> None:
    transport = FakeTransport()
    client = make_client(FakeSession(FakeResponse(401)))
    _install_fake_udp(client, transport)

    with pytest.raises(InvalidToken):
        async with asyncio.timeout(2):
            await client.listen_events(touch_stream_callback=lambda event: None)

    assert transport.close_calls == 1
