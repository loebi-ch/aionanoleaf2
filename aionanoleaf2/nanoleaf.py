# Copyright 2021, Milan Meulemans.
# Modified and optimized 2025-2026 by loebi-ch
# Added support for Nanoleaf Essentials based on the work of JaspervRijbroek in 2025
# Added support for 4D/Screen Mirroring emersion modes (1D, 2D, 3D, 4D) based on the work of jonathanrobichaud4 in 2024
# Added support for IPv6 hosts based on the work of krozgrov in 2025
#
# This file is part of aionanoleaf2, the refactored version of aionanoleaf by Milan Meulemans
#
# aionanoleaf2 is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# aionanoleaf2 is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with aionanoleaf2.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import logging
import random
import socket
import struct
from collections.abc import Callable, Mapping
from contextlib import suppress
from http import HTTPStatus
from typing import Any, Final, cast
from urllib.parse import quote, unquote

from aiohttp import (
    ClientConnectionError,
    ClientError,
    ClientPayloadError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
)

from .events import (
    EffectsEvent,
    LayoutEvent,
    StateEvent,
    TouchEvent,
    TouchStreamEvent,
)
from .exceptions import (
    EndpointNotFoundError,
    InvalidEffect,
    InvalidEmersion,
    InvalidResponseError,
    InvalidToken,
    NanoleafConnectionError,
    NanoleafError,
    NanoleafTimeoutError,
    NoAuthToken,
    Unauthorized,
    Unavailable,
)
from .layout import Panel
from .typing import (
    EffectDetailsData,
    EffectsEventData,
    EmersionData,
    InfoData,
    LayoutEventData,
    StateEventData,
    TouchEventData,
)

# Models that support Screen Mirroring (e.g. Nanoleaf 4D)
EMERSION_MODELS = ["NL69"]

# Mapping between internal API mode IDs and human-readable names
EMERSION_MODES = {6: "1D", 2: "2D", 3: "3D", 5: "4D"}

# Pre-computed inverted mapping for O(1) dictionary lookups in set_emersion()
EMERSION_MODES_INVERTED = {v: k for k, v in EMERSION_MODES.items()}

# Decoded JSON document as returned by the Nanoleaf API
type JSONValue = (
    dict[str, JSONValue] | list[JSONValue] | str | int | float | bool | None
)

# RFC 6874: the zone separator '%' is percent-encoded as '%25' inside URLs
_ZONE_SEPARATOR_URL: Final = "%25"

_LOGGER: Final = logging.getLogger(__name__)

# SSE reconnect policy (seconds).
_SSE_CONNECT_TIMEOUT: Final = 5.0
_SSE_BACKOFF_INITIAL: Final = 1.0
_SSE_BACKOFF_MAX: Final = 30.0
_SSE_BACKOFF_FACTOR: Final = 2.0
# A connection that stayed up this long counts as healthy, even if it was idle.
_SSE_STABLE_AFTER: Final = 60.0

# Keys every event of a given SSE event type must carry; others are dropped.
_SSE_REQUIRED_KEYS: Final[Mapping[int, tuple[str, ...]]] = {
    StateEvent.EVENT_TYPE_ID: ("attr", "value"),
    LayoutEvent.EVENT_TYPE_ID: ("attr",),
    EffectsEvent.EVENT_TYPE_ID: ("attr", "value"),
    TouchEvent.EVENT_TYPE_ID: ("gesture", "panelId"),
}


def _backoff_delay(attempt: int) -> float:
    """Return the reconnect delay for the given attempt (0-based).

    Exponential growth from ``_SSE_BACKOFF_INITIAL`` up to ``_SSE_BACKOFF_MAX``
    with jitter: the result lies in [ceiling / 2, ceiling] so that several
    clients do not reconnect in lockstep after a controller restart.
    """
    ceiling = min(
        _SSE_BACKOFF_MAX,
        _SSE_BACKOFF_INITIAL * _SSE_BACKOFF_FACTOR ** min(attempt, 16),
    )
    return ceiling / 2 + random.uniform(0.0, ceiling / 2)


class _Backoff:
    """Attempt counter for the SSE reconnect delay."""

    def __init__(self) -> None:
        self._attempt = 0

    def reset(self) -> None:
        self._attempt = 0

    def next_delay(self) -> float:
        delay = _backoff_delay(self._attempt)
        self._attempt += 1
        return delay


def _as_object(value: JSONValue, path: str) -> dict[str, Any]:
    """Return value as JSON object or raise InvalidResponseError."""
    if not isinstance(value, dict):
        raise InvalidResponseError(
            f"Expected JSON object from '{path or '/'}', got {type(value).__name__}"
        )
    return value


def _split_host(host: str) -> tuple[str, str | None]:
    """Split a host into (address, decoded zone ID).

    Bracketed input is treated as URL form (RFC 6874: zone separator '%25',
    zone percent-encoded). Unbracketed input is treated as raw form
    ('%', zone literal). This is what makes formatting idempotent.
    """
    value = host.strip()
    bracketed = len(value) >= 2 and value[0] == "[" and value[-1] == "]"
    if bracketed:
        value = value[1:-1]

    address, sep, zone = value.partition("%")
    if not sep:
        return address, None
    if bracketed and zone.startswith("25"):
        zone = unquote(zone[2:])
    return address, (zone or None)


class Nanoleaf:
    """Async API client for Nanoleaf controllers and Essentials devices."""

    # Default timeout settings for HTTP requests: 3s socket connect, 5s total
    _REQUEST_TIMEOUT = ClientTimeout(total=5, sock_connect=3)

    # --- CONSTRUCTOR ---
    def __init__(
        self,
        session: ClientSession,
        host: str,
        auth_token: str | None = None,
        port: int = 16021,
        retries: int = 3,
    ) -> None:
        self._session = session

        # Unbracketed host IP (without brackets or zone indices) for low-level socket comparisons
        self._raw_host: str = _split_host(host)[0]

        # URL-safe host representation (IPv6 bracketed according to RFC 6874)
        self._host = self._format_host(host)

        self._auth_token = auth_token
        self._port = port
        self._retries = retries

        # Initialize internal state attributes to defaults to prevent AttributeErrors prior to get_info()
        self._name = ""
        self._serial_no = ""
        self._manufacturer = ""
        self._firmware_version = ""
        self._hardware_version: str | None = None
        self._model = ""
        self._is_on = False
        self._brightness = 0
        self._brightness_max = 100
        self._brightness_min = 0
        self._hue = 0
        self._hue_max = 360
        self._hue_min = 0
        self._saturation = 0
        self._saturation_max = 100
        self._saturation_min = 0
        self._color_temperature = 0
        self._color_temperature_max = 0
        self._color_temperature_min = 0
        self._color_mode = ""
        self._effects_list: list[str] = []
        self._effect = ""
        self._effect_details: EffectDetailsData = {}
        self._emersion_list: list[str] = []
        self._emersion = ""
        self._panels: set[Panel] = set()

        # None signals that capability has not yet been fetched via get_info()
        self._supports_events: bool | None = None

        # Strong references to running async callback tasks (prevents GC)
        self._background_tasks: set[asyncio.Future[Any]] = set()
        # Local UDP endpoint for the touch stream, closed when listening ends
        self._touch_transport: asyncio.BaseTransport | None = None

    # --- PROPERTIES (GETTERS) ---

    @property
    def host(self) -> str:
        """Return the formatted host address (e.g., '[fe80::1]' or '192.168.1.10')."""
        return self._host

    @property
    def raw_host(self) -> str:
        """Return the unbracketed raw IP address."""
        return self._raw_host

    @property
    def auth_token(self) -> str:
        """Return active authentication token; raises NoAuthToken if not configured."""
        if not self._auth_token:
            raise NoAuthToken(
                "Authorize or set an auth_token before making this request."
            )
        return self._auth_token

    @property
    def port(self) -> int:
        return self._port

    @property
    def name(self) -> str:
        return self._name

    @property
    def serial_no(self) -> str:
        return self._serial_no

    @property
    def manufacturer(self) -> str:
        return self._manufacturer

    @property
    def firmware_version(self) -> str:
        return self._firmware_version

    @property
    def hardware_version(self) -> str | None:
        return self._hardware_version

    @property
    def model(self) -> str:
        return self._model

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def brightness(self) -> int:
        return self._brightness

    @property
    def brightness_max(self) -> int:
        return self._brightness_max

    @property
    def brightness_min(self) -> int:
        return self._brightness_min

    @property
    def hue(self) -> int:
        return self._hue

    @property
    def hue_max(self) -> int:
        return self._hue_max

    @property
    def hue_min(self) -> int:
        return self._hue_min

    @property
    def saturation(self) -> int:
        return self._saturation

    @property
    def saturation_max(self) -> int:
        return self._saturation_max

    @property
    def saturation_min(self) -> int:
        return self._saturation_min

    @property
    def color_temperature(self) -> int:
        return self._color_temperature

    @property
    def color_temperature_max(self) -> int:
        return self._color_temperature_max

    @property
    def color_temperature_min(self) -> int:
        return self._color_temperature_min

    @property
    def color_mode(self) -> str:
        return self._color_mode

    @property
    def effects_list(self) -> list[str]:
        return self._effects_list

    @property
    def effect(self) -> str:
        return self._effect

    @property
    def effect_details(self) -> EffectDetailsData:
        return self._effect_details

    @property
    def selected_effect(self) -> str | None:
        """Return currently active effect if present in effects list."""
        return self.effect if self.effect in self.effects_list else None

    @property
    def emersion_list(self) -> list[str]:
        return self._emersion_list

    @property
    def emersion(self) -> str:
        return self._emersion

    @property
    def selected_emersion(self) -> str | None:
        """Return currently active Screen Mirroring mode."""
        return self.emersion if self.emersion in self.emersion_list else None

    @property
    def panels(self) -> set[Panel]:
        return self._panels

    @property
    def supports_events(self) -> bool | None:
        """Return whether device supports SSE event streaming (Essentials = False)."""
        return self._supports_events

    @property
    def _api_url(self) -> str:
        return f"http://{self.host}:{self.port}/api/v1"

    # --- PUBLIC METHODS ---

    async def authorize(self) -> None:
        """Authorize the client with the device and store the obtained auth_token.

        Prerequisite: Enable API access in the Nanoleaf app (or hold the power button
        for 5-7 seconds on legacy Light Panels) and request the token within 30 seconds.
        """
        try:
            # The context manager releases the response to the connection pool
            async with self._session.post(
                f"{self._api_url}/new", timeout=self._REQUEST_TIMEOUT
            ) as resp:
                if resp.status == HTTPStatus.FORBIDDEN:
                    raise Unauthorized(
                        "Enable API access in the Nanoleaf app (or hold the on-off button for 5-7s on legacy Light Panels) and try again within 30 seconds."
                    )
                if resp.status >= HTTPStatus.BAD_REQUEST:
                    raise Unavailable(f"Authorization failed: HTTP {resp.status}")
                payload = await self._decode_body(resp, "new")
        except TimeoutError as err:
            raise NanoleafTimeoutError(
                f"Authorization request to {self._host} timed out"
            ) from err
        except ClientError as err:
            raise NanoleafConnectionError(
                f"Authorization request failed: {type(err).__name__}"
            ) from err

        token = _as_object(payload, "new").get("auth_token")
        if not isinstance(token, str) or not token:
            raise InvalidResponseError("Authorization response has no auth_token")
        self._auth_token = token

    async def deauthorize(self) -> None:
        """Delete current auth_token on the controller and reset client instance."""
        await self._request("delete", "")
        self._auth_token = None

    async def get_info(self) -> None:
        """Fetch device information, layout, and state values, updating instance attributes."""
        data = cast(InfoData, _as_object(await self._request("get", ""), ""))

        # Extract base device metadata from root payload
        self._name = data.get("name", "")
        self._serial_no = data.get("serialNo", "")
        self._manufacturer = data.get("manufacturer", "")
        self._firmware_version = data.get("firmwareVersion", "")
        self._hardware_version = data.get("hardwareVersion")
        self._model = data.get("model", "")

        # Nanoleaf Essentials firmware does not include the 'state' object in the root payload
        # Fallback: Query state explicitly via the sub-endpoint '/api/v1/{token}/state'
        raw_state = data.get("state")
        state: dict[str, Any] = dict(raw_state) if raw_state is not None else {}
        if not state:
            # HTTP 404 means "endpoint not supported" and is ignored. Transport
            # errors (timeout, connection) propagate: the device is unavailable.
            with suppress(EndpointNotFoundError):
                state = _as_object(await self._request("get", "state"), "state")

        # Safely extract state properties, falling back to current instance values
        self._is_on = state.get("on", {}).get("value", False)

        brightness = state.get("brightness", {})
        self._brightness = brightness.get("value", self._brightness)
        self._brightness_max = brightness.get("max", self._brightness_max)
        self._brightness_min = brightness.get("min", self._brightness_min)

        hue = state.get("hue", {})
        self._hue = hue.get("value", self._hue)
        self._hue_max = hue.get("max", self._hue_max)
        self._hue_min = hue.get("min", self._hue_min)

        sat = state.get("sat", {})
        self._saturation = sat.get("value", self._saturation)
        self._saturation_max = sat.get("max", self._saturation_max)
        self._saturation_min = sat.get("min", self._saturation_min)

        ct = state.get("ct", {})
        self._color_temperature = ct.get("value", self._color_temperature)
        self._color_temperature_max = ct.get("max", self._color_temperature_max)
        self._color_temperature_min = ct.get("min", self._color_temperature_min)

        self._color_mode = state.get("colorMode", self._color_mode)

        # Essentials devices may omit effectsList from the root payload
        effects = data.get("effects")
        if effects is not None:
            self._effects_list = effects.get("effectsList") or await self.get_effects()
            self._effect = (
                effects.get("select") or await self.get_selected_effect()
            ) or ""
        else:
            self._effects_list = await self.get_effects()
            self._effect = await self.get_selected_effect() or ""

        # Parse panel layout if available (safeguarded against missing keys or None objects)
        try:
            panel_layout = data.get("panelLayout")
            if panel_layout and "layout" in panel_layout:
                self._panels = {
                    Panel(panel)
                    for panel in panel_layout["layout"].get("positionData", [])
                }
            else:
                self._panels = set()
        except (KeyError, TypeError):
            self._panels = set()

        # Query Screen Mirroring mode only for supported hardware controllers (e.g. NL69)
        if self._model in EMERSION_MODELS:
            await self.get_emersion()

        # Determine SSE support: Essentials devices do not expose panelLayout
        # and reject or lack the event stream endpoint entirely
        self._supports_events = (
            "panelLayout" in data and data["panelLayout"] is not None
        )

    async def get_effects(self) -> list[str]:
        """Fetch list of all available lighting effects."""
        try:
            payload = await self._request("get", "effects/effectsList")
        except EndpointNotFoundError:
            return []
        if isinstance(payload, dict):
            payload = payload.get("effectsList")
        if not isinstance(payload, list):
            return []
        return [effect for effect in payload if isinstance(effect, str)]

    async def get_effect_details(self, effect: str) -> EffectDetailsData | None:
        """Fetch metadata, palettes, and animation structure of a specific effect."""
        try:
            payload = await self._request(
                "put",
                "effects",
                {
                    "write": {
                        "command": "request",
                        "animName": effect,
                    },
                },
            )
        except EndpointNotFoundError:
            return None
        self._effect_details = cast(EffectDetailsData, _as_object(payload, "effects"))
        return self._effect_details

    async def get_selected_effect(self) -> str | None:
        """Fetch name of currently active effect."""
        try:
            payload = await self._request("get", "effects/select")
        except EndpointNotFoundError:
            return None
        if isinstance(payload, dict):
            payload = payload.get("select")
        return payload if isinstance(payload, str) and payload else None

    async def get_emersion(self) -> None:
        """Fetch active Screen Mirroring mode (1D, 2D, 3D, or 4D)."""
        self._emersion_list = list(EMERSION_MODES.values())
        try:
            payload = await self._request(
                "put", "effects", {"write": {"command": "getScreenMirrorMode"}}
            )
            mode = cast(EmersionData, _as_object(payload, "effects"))[
                "screenMirrorMode"
            ]
        except (EndpointNotFoundError, InvalidResponseError, KeyError):
            self._emersion = "Unknown"
            return
        self._emersion = (
            EMERSION_MODES.get(mode, "Unknown") if isinstance(mode, int) else "Unknown"
        )

    async def set_state(
        self,
        on: bool | None = None,
        brightness: int | None = None,
        brightness_relative: bool = False,
        brightness_transition: int | None = None,
        color_temperature: int | None = None,
        color_temperature_relative: bool = False,
        hue: int | None = None,
        hue_relative: bool = False,
        saturation: int | None = None,
        saturation_relative: bool = False,
    ) -> None:
        """Update multiple state attributes in a single atomic HTTP PUT request."""
        data: dict[str, Any] = {}

        # Internal helper to construct relative vs. absolute JSON topic payloads
        def _add_topic_to_data(
            topic: str, value: int | bool | None, relative: bool = False
        ) -> None:
            if value is not None:
                if relative:
                    data[topic] = {"increment": value}
                else:
                    data[topic] = {"value": value}

        _add_topic_to_data("brightness", brightness, brightness_relative)
        if brightness_transition is not None and "brightness" in data:
            data["brightness"]["duration"] = brightness_transition

        _add_topic_to_data("ct", color_temperature, color_temperature_relative)
        _add_topic_to_data("hue", hue, hue_relative)
        _add_topic_to_data("sat", saturation, saturation_relative)

        # The Nanoleaf API requires 'on' to be the final property processed in the payload
        _add_topic_to_data("on", on)

        if data:
            await self._request("put", "state", data)

    async def set_effect(self, effect: str) -> None:
        """Activate a pre-configured lighting effect by name."""
        if effect not in self.effects_list:
            raise InvalidEffect
        await self._request("put", "effects", {"select": effect})

    async def set_emersion(self, emersion: str) -> None:
        """Activate a Screen Mirroring mode (1D, 2D, 3D, or 4D)."""
        if emersion not in self.emersion_list:
            raise InvalidEmersion

        emersion_int = EMERSION_MODES_INVERTED.get(emersion)
        if emersion_int is None:
            raise InvalidEmersion(f"Could not find ID for emersion mode {emersion}")

        await self._request(
            "put",
            "effects",
            {
                "write": {
                    "command": "activateScreenMirror",
                    "screenMirrorMode": emersion_int,
                }
            },
        )
        # Refresh state immediately to maintain local consistency
        await self.get_emersion()

    async def set_brightness(
        self, brightness: int, relative: bool = False, transition: int | None = None
    ) -> None:
        """Set brightness (0-100) absolutely or relatively, optionally with transition."""
        await self._set_state("brightness", brightness, relative, transition)

    async def set_saturation(self, saturation: int, relative: bool = False) -> None:
        """Set saturation (0-100) absolutely or relatively."""
        await self._set_state("sat", saturation, relative)

    async def set_hue(self, hue: int, relative: bool = False) -> None:
        """Set hue (0-360) absolutely or relatively."""
        await self._set_state("hue", hue, relative)

    async def set_color_temperature(
        self, color_temperature: int, relative: bool = False
    ) -> None:
        """Set color temperature in Kelvin (e.g. 1200-6500) absolutely or relatively."""
        await self._set_state("ct", color_temperature, relative)

    async def turn_on(self) -> None:
        """Turn the device on."""
        await self._set_state("on", True)

    async def turn_off(self, transition: int | None = None) -> None:
        """Turn the device off. If transition is specified, dim down before powering off."""
        if transition is None:
            await self._set_state("on", False)
        else:
            await self.set_brightness(0, transition=transition)

        # Brief delay to accommodate read-after-write latency on firmware 5.x+
        await asyncio.sleep(0.3)

    async def identify(self) -> None:
        """Flash device panels briefly for visual identification."""
        await self._request("put", "identify")

    async def listen_events(
        self,
        state_callback: Callable[[StateEvent], Any] | None = None,
        layout_callback: Callable[[LayoutEvent], Any] | None = None,
        effects_callback: Callable[[EffectsEvent], Any] | None = None,
        touch_callback: Callable[[TouchEvent], Any] | None = None,
        touch_stream_callback: Callable[[TouchStreamEvent], Any] | None = None,
        *,
        local_ip: str | None = None,
        local_port: int | None = None,
    ) -> None:
        """Listen for Server-Sent Events (SSE) and the optional UDP touch stream.

        Runs until cancelled. Callbacks may be plain functions or coroutine
        functions; an exception inside a callback is logged and never stops the
        listener. Transient network errors are retried with exponential backoff.

        Raises:
            NoAuthToken: no auth token configured (nothing is opened or sent).
            InvalidToken: the device rejected the token (HTTP 401). Reconnecting
                cannot succeed; the caller should start a re-authentication.
            EndpointNotFoundError: the device has no event stream (HTTP 404).
        """
        # Fail fast before a UDP socket is opened.
        _ = self.auth_token

        self._close_touch_transport()
        socket_port: int | None = None
        try:
            # Open local UDP socket first if high-frequency touch streaming is requested
            if touch_stream_callback is not None:
                socket_port = await self._open_udp_socket_for_touch_data_stream(
                    touch_stream_callback, local_ip, local_port
                )

            await self._listen_for_server_sent_events(
                state_callback,
                layout_callback,
                effects_callback,
                touch_callback,
                socket_port,
            )
        finally:
            self._close_touch_transport()

    # --- PRIVATE HELPER METHODS ---

    async def _request(
        self,
        method: str,
        path: str,
        data: Mapping[str, Any] | None = None,
    ) -> JSONValue:
        """Execute an authorized request and return the decoded JSON body.

        Raises:
            NoAuthToken: no token configured (no request is sent).
            InvalidToken: HTTP 401 (never retried).
            EndpointNotFoundError: HTTP 404 (never retried).
            Unavailable: other HTTP status >= 400.
            InvalidResponseError: body is not usable JSON.
            NanoleafTimeoutError / NanoleafConnectionError: transport failure
                after all retries are exhausted.
        """
        token = self._auth_token
        if not token:  # None or empty string: fail before any I/O
            raise NoAuthToken(
                "Authorize or set an auth_token before making this request."
            )
        url = f"{self._api_url}/{token}/{path}"
        attempts = max(1, self._retries)
        last_error: Exception | None = None

        for _ in range(attempts):
            try:
                async with self._session.request(
                    method, url, json=data, timeout=self._REQUEST_TIMEOUT
                ) as resp:
                    status = resp.status
                    if status == HTTPStatus.UNAUTHORIZED:
                        raise InvalidToken("The auth token was rejected by the device")
                    if status == HTTPStatus.NOT_FOUND:
                        raise EndpointNotFoundError(path)
                    if status >= HTTPStatus.BAD_REQUEST:
                        raise Unavailable(
                            f"Device returned HTTP {status} for '{path or '/'}'"
                        )
                    return await self._decode_body(resp, path)
            except TimeoutError as err:  # also ServerTimeoutError
                last_error = err
            except ClientConnectionError as err:
                last_error = err
            except ClientError as err:
                # e.g. ClientPayloadError: not retried, but still a transport problem
                raise NanoleafConnectionError(
                    f"HTTP request failed: {type(err).__name__}"
                ) from err

        if isinstance(last_error, TimeoutError):
            raise NanoleafTimeoutError(
                f"Request to {self._host} timed out after {attempts} attempt(s)"
            ) from last_error
        raise NanoleafConnectionError(
            f"Could not reach {self._host} after {attempts} attempt(s)"
        ) from last_error

    @staticmethod
    async def _decode_body(resp: ClientResponse, path: str) -> JSONValue:
        """Decode a successful response; empty bodies and 204 yield None."""
        if resp.status == HTTPStatus.NO_CONTENT:
            return None
        body = await resp.read()
        if not body:
            return None
        if resp.content_type != "application/json" and not resp.content_type.endswith(
            "+json"
        ):
            raise InvalidResponseError(
                f"Unexpected content type '{resp.content_type}' from '{path or '/'}'"
            )
        try:
            return cast(JSONValue, json.loads(body))
        except ValueError as err:  # JSONDecodeError and UnicodeDecodeError
            raise InvalidResponseError(f"Invalid JSON from '{path or '/'}'") from err

    @staticmethod
    def _format_host(host: str) -> str:
        """Return URL-safe host; IPv6 is bracketed, zone ID encoded per RFC 6874.

        Idempotent: _format_host(_format_host(x)) == _format_host(x).
        """
        address, zone = _split_host(host)
        try:
            ipaddress.IPv6Address(address)
        except ValueError:
            # IPv4 address or hostname: returned unchanged
            return host.strip()

        if zone is None:
            return f"[{address}]"
        return f"[{address}{_ZONE_SEPARATOR_URL}{quote(zone, safe='')}]"

    async def _set_state(
        self,
        topic: str,
        value: int | bool,
        relative: bool = False,
        transition: int | None = None,
    ) -> None:
        """Helper to modify a single state attribute."""
        data: dict[str, Any]
        if relative:
            data = {topic: {"increment": value}}
        else:
            data = {topic: {"value": value}}
        if transition is not None:
            data[topic]["duration"] = transition
        await self._request("put", "state", data)

    async def _open_udp_socket_for_touch_data_stream(
        self,
        callback: Callable[[TouchStreamEvent], Any],
        local_ip: str | None = None,
        local_port: int | None = None,
    ) -> int:
        """Open local UDP datagram endpoint to receive binary touch data packets."""
        if local_ip is None:
            local_ip = "0.0.0.0"
        if local_port is None:
            local_port = 0  # 0 delegates port selection to the OS (ephemeral port)

        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _NanoleafTouchProtocol(
                self._raw_host, lambda event: self._invoke_callback(callback, event)
            ),
            local_addr=(local_ip, local_port),
        )
        self._touch_transport = transport
        touch_socket: socket.socket = transport.get_extra_info("socket")
        socket_port: int | None = touch_socket.getsockname()[1]

        if socket_port is None:
            self._close_touch_transport()
            raise NanoleafError("Could not determine port of socket")
        return socket_port

    def _close_touch_transport(self) -> None:
        """Close the local UDP touch endpoint, if one is open."""
        transport, self._touch_transport = self._touch_transport, None
        if transport is not None:
            transport.close()

    async def _listen_for_server_sent_events(
        self,
        state_callback: Callable[[StateEvent], Any] | None = None,
        layout_callback: Callable[[LayoutEvent], Any] | None = None,
        effects_callback: Callable[[EffectsEvent], Any] | None = None,
        touch_callback: Callable[[TouchEvent], Any] | None = None,
        socket_port: int | None = None,
    ) -> None:
        """Consume the SSE stream with automatic reconnect.

        Terminal conditions are raised: ``InvalidToken`` (HTTP 401),
        ``EndpointNotFoundError`` (HTTP 404) and cancellation. Everything else
        (connection errors, timeouts, other HTTP errors, end of stream) is
        retried with exponential backoff and jitter.
        """
        # Never log this URL: it contains the auth token.
        request_url = (
            f"{self._api_url}/{self.auth_token}/events?"
            f"id={StateEvent.EVENT_TYPE_ID},{EffectsEvent.EVENT_TYPE_ID}"
        )
        if layout_callback is not None:
            request_url += f",{LayoutEvent.EVENT_TYPE_ID}"

        if touch_callback is not None or socket_port is not None:
            request_url += f",{TouchEvent.EVENT_TYPE_ID}"

        # Inform controller where to direct UDP touch stream if active
        request_headers = None
        if socket_port is not None:
            request_headers = {"TouchEventsPort": str(socket_port)}

        # Persistent stream: no total/read timeout, strict connect timeout.
        request_timeout = ClientTimeout(
            total=None, sock_connect=_SSE_CONNECT_TIMEOUT, sock_read=None
        )

        callbacks: dict[int, Callable[[Any], Any] | None] = {
            StateEvent.EVENT_TYPE_ID: state_callback,
            LayoutEvent.EVENT_TYPE_ID: layout_callback,
            EffectsEvent.EVENT_TYPE_ID: effects_callback,
            TouchEvent.EVENT_TYPE_ID: touch_callback,
        }
        backoff = _Backoff()
        loop = asyncio.get_running_loop()

        try:
            while True:
                connected_at: float | None = None
                try:
                    async with self._session.get(
                        request_url, headers=request_headers, timeout=request_timeout
                    ) as resp:
                        if resp.status == HTTPStatus.UNAUTHORIZED:
                            raise InvalidToken("Event stream rejected the auth token.")
                        if resp.status == HTTPStatus.NOT_FOUND:
                            raise EndpointNotFoundError("events")
                        if resp.status == HTTPStatus.OK:
                            connected_at = loop.time()
                            await self._read_sse_stream(resp, callbacks, backoff)
                            _LOGGER.debug(
                                "Event stream of %s closed by device", self._host
                            )
                        else:
                            _LOGGER.warning(
                                "Event stream of %s returned HTTP %s",
                                self._host,
                                resp.status,
                            )
                except (ClientError, TimeoutError) as err:
                    _LOGGER.debug(
                        "Event stream of %s failed (%s: %s)",
                        self._host,
                        type(err).__name__,
                        err,
                    )

                # A long-lived connection is healthy even when no event arrived.
                if (
                    connected_at is not None
                    and loop.time() - connected_at >= _SSE_STABLE_AFTER
                ):
                    backoff.reset()
                await asyncio.sleep(backoff.next_delay())
        except asyncio.CancelledError:
            # The response is already closed by 'async with'; stop pending callbacks.
            await self._cancel_background_tasks()
            raise

    async def _read_sse_stream(
        self,
        resp: ClientResponse,
        callbacks: Mapping[int, Callable[[Any], Any] | None],
        backoff: _Backoff,
    ) -> None:
        """Read one SSE response until EOF.

        Each event is a pair of lines ``id: <type>`` and ``data: <json>``.
        Blank lines, comments and unknown fields are ignored; undecodable lines
        and malformed events are skipped so one bad chunk cannot end the stream.
        """
        event_type_id: int | None = None
        while True:
            try:
                raw = await resp.content.readline()
            except ValueError as err:
                # aiohttp raises ValueError for lines beyond its buffer limit
                raise ClientPayloadError("SSE line exceeds buffer limit") from err
            if not raw:
                return  # EOF -> caller reconnects

            try:
                line = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                _LOGGER.debug("Skipping undecodable SSE line from %s", self._host)
                event_type_id = None
                continue

            if not line or line.startswith(":"):
                continue

            field, _, value = line.partition(":")
            value = value.strip()
            if field == "id":
                try:
                    event_type_id = int(value)
                except ValueError:
                    event_type_id = None
            elif field == "data" and event_type_id is not None:
                type_id, event_type_id = event_type_id, None
                if await self._process_sse_data(type_id, value, callbacks):
                    backoff.reset()

    async def _process_sse_data(
        self,
        type_id: int,
        payload: str,
        callbacks: Mapping[int, Callable[[Any], Any] | None],
    ) -> bool:
        """Dispatch the events of one ``data:`` payload; return True if any succeeded."""
        required = _SSE_REQUIRED_KEYS.get(type_id)
        if required is None:
            _LOGGER.debug("Ignoring SSE event type %s", type_id)
            return False

        try:
            data = json.loads(payload)
        except (ValueError, RecursionError):
            _LOGGER.debug("Skipping SSE payload that is not valid JSON")
            return False
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
            _LOGGER.debug("Skipping SSE payload without an 'events' list")
            return False

        handled = False
        for event_data in events:
            if not isinstance(event_data, dict) or not all(
                key in event_data for key in required
            ):
                _LOGGER.debug("Skipping malformed SSE event of type %s", type_id)
                continue
            try:
                await self._dispatch_event(type_id, event_data, callbacks)
            except Exception:  # a bad event must not end the stream
                _LOGGER.exception("Failed to process SSE event of type %s", type_id)
            else:
                handled = True
        return handled

    async def _dispatch_event(
        self,
        type_id: int,
        event_data: dict[str, Any],
        callbacks: Mapping[int, Callable[[Any], Any] | None],
    ) -> None:
        """Update local state for one validated event and notify its callback."""
        event: StateEvent | LayoutEvent | EffectsEvent | TouchEvent
        if type_id == StateEvent.EVENT_TYPE_ID:
            event = StateEvent(cast(StateEventData, event_data))
            # Maintain local instance state synchronization
            if hasattr(self, f"_{event.attribute}"):
                setattr(self, f"_{event.attribute}", event.value)
        elif type_id == LayoutEvent.EVENT_TYPE_ID:
            event = LayoutEvent(cast(LayoutEventData, event_data))
        elif type_id == EffectsEvent.EVENT_TYPE_ID:
            event = EffectsEvent(cast(EffectsEventData, event_data))
            self._effect = event.effect
            # When 4D Screen Mirroring is triggered, update active mode
            if event.effect == "*Emersion*":
                try:
                    await self.get_emersion()
                except NanoleafError as err:
                    _LOGGER.debug("Could not refresh screen mirror mode: %s", err)
        else:
            event = TouchEvent(cast(TouchEventData, event_data))
        self._invoke_callback(callbacks.get(type_id), event)

    def _invoke_callback[E](
        self, callback: Callable[[E], Any] | None, event: E
    ) -> None:
        """Run a sync callback or schedule an async one; never raises."""
        if callback is None:
            return
        try:
            result = callback(event)
        except Exception:  # consumer code must not end the stream
            _LOGGER.exception("Event callback %r raised", callback)
            return
        if inspect.isawaitable(result):
            task = asyncio.ensure_future(result)
            self._background_tasks.add(task)
            task.add_done_callback(self._on_callback_task_done)

    def _on_callback_task_done(self, task: asyncio.Future[Any]) -> None:
        """Release the task reference and surface a failed callback."""
        self._background_tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            _LOGGER.error("Async event callback failed", exc_info=exc)

    async def _cancel_background_tasks(self) -> None:
        """Cancel running callback tasks and wait until they have finished."""
        tasks = [task for task in self._background_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class _NanoleafTouchProtocol(asyncio.DatagramProtocol):
    """AsyncIO DatagramProtocol handling binary UDP touch streaming packets."""

    def __init__(
        self,
        raw_nanoleaf_host: str,
        dispatch: Callable[[TouchStreamEvent], None],
    ) -> None:
        """Initialize the protocol.

        ``dispatch`` receives every valid event. It must not block and must not
        raise; ``Nanoleaf._invoke_callback`` fulfils both and also takes care of
        async callbacks (task reference, error logging, cancellation).
        """
        self._raw_nanoleaf_host = raw_nanoleaf_host
        self._dispatch = dispatch
        self.transport: asyncio.BaseTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # Validate sender IP: strip brackets and scope identifiers
        # ensuring IPv6 packets are not dropped inadvertently
        incoming_ip = addr[0].strip("[]").split("%")[0]
        if incoming_ip != self._raw_nanoleaf_host:
            return

        # Binary protocol parsing via struct unpack (minimum 5 bytes required):
        # Format: >HBH
        # > : Big-Endian (Network Byte Order)
        # H : 2 bytes unsigned short -> Panel ID 1
        # B : 1 byte unsigned char   -> High nibble: Touch Type (0-4), Low nibble: Strength (0-15)
        # H : 2 bytes unsigned short -> Panel ID 2 (swipes; otherwise sentinel 0xFFFF)
        if len(data) < 5:
            return

        panel_id, type_and_strength, panel_id_2 = struct.unpack(">HBH", data[:5])

        # Bitmasking: upper 4 bits for touch type, lower 4 bits for strength
        touch_type_id = (type_and_strength >> 4) & 0x0F
        strength = type_and_strength & 0x0F

        event = TouchStreamEvent(
            panel_id=panel_id,
            touch_type_id=touch_type_id,
            strength=strength,
            panel_id_2=panel_id_2,
        )
        self._dispatch(event)
