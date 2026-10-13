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
import ipaddress
import json
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
from .typing import EffectDetailsData, EmersionData, InfoData

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
        """Start listening for Server-Sent Events (SSE) and optional UDP touch stream."""
        socket_port: int | None = None

        # Open local UDP socket first if high-frequency touch streaming is requested
        if touch_stream_callback is not None:
            socket_port = await self._open_udp_socket_for_touch_data_stream(
                touch_stream_callback, local_ip, local_port
            )

        # Start persistent SSE listener loop
        await self._listen_for_server_sent_events(
            state_callback,
            layout_callback,
            effects_callback,
            touch_callback,
            socket_port,
        )

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
        callback: Callable,
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
            lambda: _NanoleafTouchProtocol(self._raw_host, callback),
            local_addr=(local_ip, local_port),
        )
        touch_socket: socket.socket = transport.get_extra_info("socket")
        socket_port = touch_socket.getsockname()[1]

        if socket_port is None:
            raise NanoleafError("Could not determine port of socket")
        return socket_port

    async def _listen_for_server_sent_events(
        self,
        state_callback: Callable[[StateEvent], Any] | None = None,
        layout_callback: Callable[[LayoutEvent], Any] | None = None,
        effects_callback: Callable[[EffectsEvent], Any] | None = None,
        touch_callback: Callable[[TouchEvent], Any] | None = None,
        socket_port: int | None = None,
    ) -> None:
        """Consume Server-Sent Events (SSE) stream with automatic reconnect logic."""
        # Assemble URL query parameters specifying subscribed event classes
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

        # Infinite read timeout for long-polling streaming; strict 5s connection timeout
        request_timeout = ClientTimeout(total=None, sock_connect=5, sock_read=None)

        while True:
            try:
                async with self._session.get(
                    request_url, headers=request_headers, timeout=request_timeout
                ) as resp:
                    if resp.status != 200:
                        # Non-OK response -> wait and retry connection
                        await asyncio.sleep(5)
                        continue

                    while True:
                        line_bytes = await resp.content.readline()
                        if not line_bytes:
                            # Stream terminated by server (EOF) -> reconnect
                            break

                        line = line_bytes.decode("utf-8").strip()
                        if not line:
                            # Skip SSE protocol keep-alive empty lines
                            continue

                        # Parse SSE event type header line 'id: <event_type_id>'
                        if line.startswith("id:"):
                            try:
                                event_type_id = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                continue

                            # Consume subsequent payload line 'data: <json>'
                            data_bytes = await resp.content.readline()
                            data_line = data_bytes.decode("utf-8").strip()

                            if not data_line.startswith("data:"):
                                continue

                            try:
                                json_str = data_line.split(":", 1)[1].strip()
                                data = json.loads(json_str)
                            except (IndexError, json.JSONDecodeError):
                                continue

                            # Dispatch each event within the payload array
                            for event_data in data.get("events", []):
                                if event_type_id == StateEvent.EVENT_TYPE_ID:
                                    event = StateEvent(event_data)
                                    # Maintain local instance state synchronization
                                    if hasattr(self, f"_{event.attribute}"):
                                        setattr(
                                            self, f"_{event.attribute}", event.value
                                        )
                                    if state_callback:
                                        asyncio.create_task(state_callback(event))

                                elif event_type_id == LayoutEvent.EVENT_TYPE_ID:
                                    layout_event = LayoutEvent(event_data)
                                    if layout_callback:
                                        asyncio.create_task(
                                            layout_callback(layout_event)
                                        )

                                elif event_type_id == EffectsEvent.EVENT_TYPE_ID:
                                    effects_event = EffectsEvent(event_data)
                                    self._effect = effects_event.effect

                                    # When 4D Screen Mirroring is triggered, update active mode
                                    if effects_event.effect == "*Emersion*":
                                        try:
                                            await self.get_emersion()
                                        except Unavailable:
                                            # Catch failures to prevent aborting the entire SSE loop
                                            pass
                                    if effects_callback:
                                        asyncio.create_task(
                                            effects_callback(effects_event)
                                        )

                                elif event_type_id == TouchEvent.EVENT_TYPE_ID:
                                    touch_event = TouchEvent(event_data)
                                    if touch_callback:
                                        asyncio.create_task(touch_callback(touch_event))

            except asyncio.CancelledError:
                # Re-raise cancellation immediately to ensure clean task shutdown without reconnecting
                raise

            except (TimeoutError, ClientError):
                # Connection dropped -> backoff and attempt reconnect
                await asyncio.sleep(5)


class _NanoleafTouchProtocol(asyncio.DatagramProtocol):
    """AsyncIO DatagramProtocol handling binary UDP touch streaming packets."""

    def __init__(
        self, raw_nanoleaf_host: str, callback: Callable[[TouchStreamEvent], Any]
    ) -> None:
        self._raw_nanoleaf_host = raw_nanoleaf_host
        self._callback = callback
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
        asyncio.create_task(self._callback(event))
