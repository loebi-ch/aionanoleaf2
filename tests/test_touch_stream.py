"""Tests for the UDP touch stream: callback dispatch, error handling, packet filter."""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any, Final, cast
from unittest.mock import MagicMock

import pytest
from aiohttp import ClientSession

from aionanoleaf2 import Nanoleaf, TouchStreamEvent
from aionanoleaf2.nanoleaf import _NanoleafTouchProtocol

HOST: Final = "192.168.0.10"
TOKEN: Final = "TESTTOKEN0123456789"
# Panel 16, touch type 1 (Down) / strength 7, no second panel
PACKET: Final = struct.pack(">HBH", 16, (1 << 4) | 7, 0xFFFF)


def _client(host: str = HOST) -> Nanoleaf:
    return Nanoleaf(cast(ClientSession, MagicMock()), host, TOKEN)


def _protocol(
    client: Nanoleaf, callback: Any, host: str = HOST
) -> _NanoleafTouchProtocol:
    return _NanoleafTouchProtocol(
        host, lambda event: client._invoke_callback(callback, event)
    )


def _assert_event(event: TouchStreamEvent) -> None:
    assert event.panel_id == 16
    assert event.touch_type == "Down"
    assert event.strength == 7
    assert event.panel_id_2 is None


async def test_sync_callback_receives_event() -> None:
    client = _client()
    received: list[TouchStreamEvent] = []

    _protocol(client, received.append).datagram_received(PACKET, (HOST, 4711))

    assert len(received) == 1
    _assert_event(received[0])
    assert not client._background_tasks


async def test_async_callback_is_awaited_and_task_is_tracked() -> None:
    client = _client()
    received: list[TouchStreamEvent] = []
    release = asyncio.Event()

    async def callback(event: TouchStreamEvent) -> None:
        await release.wait()
        received.append(event)

    _protocol(client, callback).datagram_received(PACKET, (HOST, 4711))

    # A strong reference prevents garbage collection while the callback runs
    assert len(client._background_tasks) == 1
    assert not received

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(received) == 1
    _assert_event(received[0])
    assert not client._background_tasks  # released by the done callback


async def test_failing_sync_callback_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client()

    def callback(event: TouchStreamEvent) -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="aionanoleaf2.nanoleaf"):
        # Must not propagate into the event loop's datagram handling
        _protocol(client, callback).datagram_received(PACKET, (HOST, 4711))

    assert "Event callback" in caplog.text


async def test_failing_async_callback_is_logged_and_released(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client()

    async def callback(event: TouchStreamEvent) -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="aionanoleaf2.nanoleaf"):
        _protocol(client, callback).datagram_received(PACKET, (HOST, 4711))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert "Async event callback failed" in caplog.text
    assert not client._background_tasks


async def test_pending_async_callback_is_cancelled_with_listener() -> None:
    client = _client()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def callback(event: TouchStreamEvent) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    _protocol(client, callback).datagram_received(PACKET, (HOST, 4711))
    await started.wait()

    await client._cancel_background_tasks()

    assert cancelled.is_set()
    assert not client._background_tasks


@pytest.mark.parametrize(
    ("sender", "data"),
    [
        pytest.param("192.168.0.99", PACKET, id="foreign-sender"),
        pytest.param(HOST, PACKET[:4], id="too-short"),
        pytest.param(HOST, b"", id="empty"),
    ],
)
async def test_invalid_datagrams_are_ignored(sender: str, data: bytes) -> None:
    client = _client()
    received: list[TouchStreamEvent] = []

    _protocol(client, received.append).datagram_received(data, (sender, 4711))

    assert received == []
    assert not client._background_tasks


async def test_ipv6_sender_with_scope_id_is_accepted() -> None:
    client = _client("fe80::1%eth0")
    received: list[TouchStreamEvent] = []
    protocol = _protocol(client, received.append, host=client.raw_host)

    protocol.datagram_received(PACKET, ("fe80::1%eth0", 4711))

    assert len(received) == 1


async def test_datagram_over_real_udp_socket_reaches_async_callback() -> None:
    """End to end: real loopback socket opened by the library."""
    client = _client("127.0.0.1")
    received: asyncio.Queue[TouchStreamEvent] = asyncio.Queue()

    async def callback(event: TouchStreamEvent) -> None:
        received.put_nowait(event)

    port = await client._open_udp_socket_for_touch_data_stream(
        callback, local_ip="127.0.0.1"
    )
    try:
        loop = asyncio.get_running_loop()
        sender, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=("127.0.0.1", port)
        )
        try:
            sender.sendto(PACKET)
            event = await asyncio.wait_for(received.get(), timeout=2)
        finally:
            sender.close()
    finally:
        client._close_touch_transport()

    _assert_event(event)
    assert client._touch_transport is None
