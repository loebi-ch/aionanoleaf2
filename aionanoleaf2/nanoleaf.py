# Copyright 2021, Milan Meulemans.
# Modified and optimized 2025 by loebi-ch
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
import json
import socket
import ipaddress

from .layout import Panel
from typing import Any, Callable
from .typing import InfoData, EmersionData

from aiohttp import (
    ClientConnectorError,
    ClientError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
    ClientConnectionError,
)

from .events import (
    EffectsEvent,
    LayoutEvent,
    StateEvent,
    TouchEvent,
    TouchStreamEvent,
)

from .exceptions import (
    InvalidEffect,
    InvalidEmersion,
    InvalidToken,
    NanoleafException,
    NoAuthToken,
    Unauthorized,
    Unavailable,
)

# Models that support Screen Mirroring
EMERSION_MODELS = ["NL69"]

# Mapping of integer IDs to readable Screen Mirroring mode names
EMERSION_MODES = {6: "1D", 2: "2D", 3: "3D", 5: "4D"}

# OPTIMIZATION: Pre-calculate an inverted map for lookups during set_emersion.
# This avoids iterating through the dictionary every time we set a mode.
EMERSION_MODES_INVERTED = {v: k for k, v in EMERSION_MODES.items()}


class Nanoleaf:

    # Timeout settings for HTTP requests
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
        self._host = self._format_host(host)
        self._auth_token = auth_token
        self._port = port
        self._retries = retries

        # Initialize all internal attributes to defaults.
        # This prevents AttributeErrors if properties are accessed before get_info() is called.
        self._name = ""
        self._serial_no = ""
        self._manufacturer = ""
        self._firmware_version = ""
        self._hardware_version = None
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
        self._effects_list = []
        self._effect = ""
        self._emersion_list = []
        self._emersion = ""
        self._panels = set()



# --- PROPERTIES (GETTERS) ---
    
    @property
    def host(self) -> str:
        return self._host

    @property
    def auth_token(self) -> str:
        if self._auth_token is None:
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
    def selected_effect(self) -> str | None:
        return self.effect if self.effect in self.effects_list else None
    
    @property
    def emersion_list(self) -> list[str]:
        return self._emersion_list

    @property
    def emersion(self) -> str:
        return self._emersion

    @property
    def selected_emersion(self) -> str | None:
        return self.emersion if self.emersion in self.emersion_list else None

    @property
    def panels(self) -> set[Panel]:
        return self._panels

    @property
    def _api_url(self) -> str:
        return f"http://{self.host}:{self.port}/api/v1"





# --- PUBLIC METHODS ---

# Authorize the Nanoleaf device. Requests a new auth_token from the device. Requires holding the power button on the device for 5-7s before calling.
    async def authorize(self) -> None:
        try:
            resp = await self._session.post(f"{self._api_url}/new")
        except ClientConnectorError as err:
            raise Unavailable from err
            
        if resp.status == 403:
            raise Unauthorized(
                "Hold the on-off button down for 5-7 seconds until the LEDs start flashing or activate the API in the Nanoleaf app and try again within 30 seconds."
            )
        resp.raise_for_status()
        self._auth_token = (await resp.json())["auth_token"]



# Deauthorize the Nanoleaf device. Deletes the current auth_token from the device.
    async def deauthorize(self) -> None:
        await self._request("delete", "")
        self._auth_token = None



# Get the Nanoleaf device info and state.
    async def get_info(self) -> None:
        resp = await self._request("get", "")
        data: InfoData = await resp.json()
        
        # Populate device info
        self._name = data["name"]
        self._serial_no = data["serialNo"]
        self._manufacturer = data["manufacturer"]
        self._firmware_version = data["firmwareVersion"]
        self._hardware_version = data.get("hardwareVersion")
        self._model = data["model"]
        
        # Nanoleaf Essentials are missing the state object in the main payload, so we have to fetch it separately.
        state = data.get("state")
        if state is None:
            try:
                resp = await self._request("get", "state")
                state = await resp.json()
            except Unavailable:
                state = {}

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

        # Nanoleaf Essentials are missing the effectsList in the main payload, so we have to fetch it separately.
        effects = data.get("effects", {})
        try:
            self._effects_list = effects.get("effectsList") or await self.get_effects()
        except Unavailable:
             self._effects_list = []

        # Nanoleaf Essentials are missing the selected effect, so we have to fetch it separately.
        try:
            self._effect = effects.get("select") or await self.get_selected_effect()
        except Unavailable:
            self._effect = ""

        # Populate panels layout if available.
        try:
            self._panels = {Panel(panel) for panel in data["panelLayout"]["layout"]["positionData"]}
        except KeyError:
            self._panels = set()
        
        # Populate Screen Mirroring mode if available.
        if self._model in EMERSION_MODELS:
            await self.get_emersion()



# Fetch the list of available effects for Nanoleaf Essentials.
    async def get_effects(self) -> list[str]:
        try:
            resp = await self._request("get", "effects/effectsList")
            data = await resp.json()
            if isinstance(data, dict):
                return data.get("effectsList") or []
            return data or []
        except Unavailable:
            return []



# Fetch the currently active effect for Nanoleaf Essentials.
    async def get_selected_effect(self) -> str | None:
        try:
            resp = await self._request("get", "effects/select")
            data = await resp.json()
            if isinstance(data, dict):
                return data.get("select") or None
            return data
        except Unavailable:
            return None



# Fetch the current Screen Mirroring mode.
    async def get_emersion(self) -> None:
        self._emersion_list = list(EMERSION_MODES.values())
        # The command to get screen mirror mode is a specific 'write' command
        emersion_request = await self._request("put", "effects", {"write": {"command": "getScreenMirrorMode"}})
        emersion_data: EmersionData = await emersion_request.json()
        # Map the integer ID back to the string name
        self._emersion = EMERSION_MODES.get(emersion_data["screenMirrorMode"], "Unknown")



# Update the state of the Nanoleaf device (on/off, brightness, color). Supports both absolute values and relative increments.
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
        data = {}

        # Helper to construct the JSON payload
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
        _add_topic_to_data("on", on)  # API requires 'on' to be processed last
        
        if data:
            await self._request("put", "state", data)



# Activate a specific effect on the device.
    async def set_effect(self, effect: str) -> None:
        if effect not in self.effects_list:
            raise InvalidEffect
        await self._request("put", "effects", {"select": effect})

    
    
# Activate a Screen Mirroring mode on the device (if supported).
    async def set_emersion(self, emersion: str) -> None:
        if emersion not in self.emersion_list:
            raise InvalidEmersion
        
        # OPTIMIZATION: Use the inverted dictionary for fast lookup.
        # This replaces the slow list index search.
        emersion_int = EMERSION_MODES_INVERTED.get(emersion)
        if emersion_int is None:
             raise InvalidEmersion(f"Could not find ID for emersion mode {emersion}")

        await self._request(
            "put", 
            "effects", 
            {"write": {"command": "activateScreenMirror", "screenMirrorMode": emersion_int}}
        )
        
        # Refresh the state to ensure it applied
        await self.get_emersion()



# Set absolute or relative brightness with or without transition of the Nanoleaf device.
    async def set_brightness(
        self, brightness: int, relative: bool = False, transition: int | None = None
    ) -> None:
        await self._set_state("brightness", brightness, relative, transition)



# Set absolute or relative saturation of the Nanoleaf device.
    async def set_saturation(self, saturation: int, relative: bool = False) -> None:
        await self._set_state("sat", saturation, relative)



# Set absolute or relative hue of the Nanoleaf device.
    async def set_hue(self, hue: int, relative: bool = False) -> None:
        await self._set_state("hue", hue, relative)



# Set absolute or relative color temperature of the Nanoleaf device.
    async def set_color_temperature(
        self, color_temperature: int, relative: bool = False
    ) -> None:
        await self._set_state("ct", color_temperature, relative)



# Turn the Nanoleaf device on.
    async def turn_on(self) -> None:
        await self._set_state("on", True)



# Turn the Nanoleaf device off.
    async def turn_off(self, transition: int | None = None) -> None:
        if transition is None:
            await self._set_state("on", False)
        else:
            # If transition is requested, dim to 0 first
            await self.set_brightness(0, transition=transition)



# Flash the panels of the Nanoleaf device for identification.
    async def identify(self) -> None:
        await self._request("put", "identify")



# Listen to the Nanoleaf device events.
    async def listen_events(
        self,
        state_callback: Callable[[StateEvent], Any] | None = None,
        layout_callback: Callable[[LayoutEvent], Any] | None = None,
        effects_callback: Callable[[EffectsEvent], Any] | None = None,
        touch_callback: Callable[[TouchEvent], Any] | None = None,
        touch_stream_callback: Callable[[Any], Any] | None = None,
        *,
        local_ip: str | None = None,
        local_port: int | None = None,
    ) -> None:
        socket_port: int | None = None
        
        # If user wants touch stream, setup UDP socket first
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





# --- PRIVATE HELPER METHODS ---

# Make an authorized request to the Nanoleaf device. Handles retries for network errors but fails fast for logic/auth errors.
    async def _request(
        self, method: str, path: str, data: dict | None = None
    ) -> ClientResponse:
        url = f"{self._api_url}/{self.auth_token}/{path}"
        json_data = json.dumps(data) if data is not None else None
        last_error = None
        
        # Logic to retry only on transient network errors.
        for attempt in range(self._retries):
            try:
                resp = await self._session.request(
                    method, url, data=json_data, timeout=self._REQUEST_TIMEOUT
                )
                
                # Immediate failure on 401 (Auth invalid), no point in retrying.
                if resp.status == 401:
                    raise InvalidToken
                
                resp.raise_for_status()
                return resp
                
            except (ClientConnectionError, asyncio.TimeoutError) as err:
                # Store error and try again if it's a connection issue.
                last_error = err
                
            except ClientError as err:
                # Re-raise 401s if they happen inside ClientError wrapper
                if hasattr(err, 'status') and err.status == 401:
                     raise InvalidToken
                # Other HTTP errors (e.g. 404, 422) are raised immediately.
                raise err

        # If we exit the loop, retries were exhausted.
        if last_error:
            raise Unavailable from last_error
            
        raise Unavailable("Unknown error occurred!")



# Bracket IPv6 literals and percent-encode zone IDs per RFC 6874.
    def _format_host(self, host: str) -> str:
        if not host:
            return host
            
        #Remove brackets
        raw = host.strip().strip("[]")
        
        #Split IP and zones
        parts = raw.split("%", 1)
        ip_part = parts[0]
        
        try:
            # Check for valid IPv6
            ipaddress.IPv6Address(ip_part)
            
            # Format IPv6
            if len(parts) > 1:
                return f"[{ip_part}%25{parts[1]}]"
            return f"[{ip_part}]"
        except ValueError:
            return host  # No valid IPv6 (we just return the original value)



# Set a single state attribute.
    async def _set_state(
        self,
        topic: str,
        value: int | bool,
        relative: bool = False,
        transition: int | None = None,
    ) -> None:
        data: dict
        if relative:
            data = {topic: {"increment": value}}
        else:
            data = {topic: {"value": value}}
        if transition is not None:
            data[topic]["duration"] = transition
        await self._request("put", "state", data)



# Open a local UDP socket to receive high-frequency touch stream events.
    async def _open_udp_socket_for_touch_data_stream(
        self,
        callback: Callable,
        local_ip: str | None = None,
        local_port: int | None = None,
    ) -> int:
        if local_ip is None:
            local_ip = "0.0.0.0"
        if local_port is None:
            local_port = 0 # 0 means OS chooses a free port
            
        loop = asyncio.get_running_loop()
        # Create a Datagram (UDP) endpoint
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _NanoleafTouchProtocol(self.host, callback),
            local_addr=(local_ip, local_port),
        )
        touch_socket: socket.socket = transport.get_extra_info("socket")
        socket_port = touch_socket.getsockname()[1]
        
        if socket_port is None:
            raise NanoleafException("Could not determine port of socket")
        return socket_port



# Listen to events. Long-running task to listen to Server-Sent Events (SSE).
# SIGNIFICANT FIX: The parsing logic was rewritten to be robust against whitespace changes and encoding issues.
    async def _listen_for_server_sent_events(
        self,
        state_callback: Callable[[StateEvent], Any] | None = None,
        layout_callback: Callable[[LayoutEvent], Any] | None = None,
        effects_callback: Callable[[EffectsEvent], Any] | None = None,
        touch_callback: Callable[[TouchEvent], Any] | None = None,
        socket_port: int | None = None,
    ) -> None:

        # Construct URL based on desired event IDs
        request_url = (
            f"{self._api_url}/{self.auth_token}/events?"
            f"id={StateEvent.EVENT_TYPE_ID},{EffectsEvent.EVENT_TYPE_ID}"
        )
        if layout_callback is not None:
            request_url += f",{LayoutEvent.EVENT_TYPE_ID}"
        
        if touch_callback is not None or socket_port is not None:
            request_url += f",{TouchEvent.EVENT_TYPE_ID}"
            
        request_headers = None
        if socket_port is not None:
            # Inform device where to send UDP touch stream
            request_headers = {"TouchEventsPort": str(socket_port)}
            
        # Infinite timeout for the read, but strict timeout for connecting
        request_timeout = ClientTimeout(total=None, sock_connect=5, sock_read=None)
        
        while True:
            try:
                async with self._session.get(
                    request_url, headers=request_headers, timeout=request_timeout
                ) as resp:
                    if resp.status != 200:
                        # Wait and retry on failure
                        await asyncio.sleep(5)
                        continue

                    while True:
                        # FIX: Read line bytes and decode properly
                        line_bytes = await resp.content.readline()
                        if not line_bytes: # Stream closed by server
                            break
                            
                        # Safely decode and strip whitespace
                        line = line_bytes.decode('utf-8').strip()
                        if not line: # Skip empty keep-alive lines
                            continue

                        # Parse 'id: <event_id>'
                        if line.startswith("id:"):
                            try:
                                # Split by first colon only to handle malformed data gracefully
                                event_type_id = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                continue 

                            # Read the next line which should be 'data: <json>'
                            data_bytes = await resp.content.readline()
                            data_line = data_bytes.decode('utf-8').strip()
                            
                            if not data_line.startswith("data:"):
                                continue
                            
                            # Parse JSON data
                            try:
                                json_str = data_line.split(":", 1)[1].strip()
                                data = json.loads(json_str)
                            except (IndexError, json.JSONDecodeError):
                                continue

                            # Dispatch events to callbacks
                            for event_data in data.get("events", []):
                                if event_type_id == StateEvent.EVENT_TYPE_ID:
                                    event = StateEvent(event_data)
                                    # Update internal state if attribute exists
                                    if hasattr(self, f"_{event.attribute}"):
                                        setattr(self, f"_{event.attribute}", event.value)
                                    if state_callback:
                                        asyncio.create_task(state_callback(event))

                                elif event_type_id == LayoutEvent.EVENT_TYPE_ID:
                                    layout_event = LayoutEvent(event_data)
                                    if layout_callback:
                                        asyncio.create_task(layout_callback(layout_event))

                                elif event_type_id == EffectsEvent.EVENT_TYPE_ID:
                                    effects_event = EffectsEvent(event_data)
                                    self._effect = effects_event.effect
                                    # If Screen Mirroring mode is active, fetch details
                                    if effects_event.effect == "*Emersion*":
                                        await self.get_emersion()
                                    if effects_callback:
                                        asyncio.create_task(effects_callback(effects_event))

                                elif event_type_id == TouchEvent.EVENT_TYPE_ID:
                                    touch_event = TouchEvent(event_data)
                                    if touch_callback:
                                        asyncio.create_task(touch_callback(touch_event))
            except ClientError:
                # Connection dropped, wait and reconnect
                await asyncio.sleep(5)



# Protocol to handle UDP touch stream packets from the Nanoleaf device.
class _NanoleafTouchProtocol(asyncio.DatagramProtocol):

    def __init__(
        self, nanoleaf_host: str, callback: Callable[[TouchStreamEvent], Any]
    ) -> None:
        self._nanoleaf_host = nanoleaf_host
        self._callback = callback

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Any) -> None:
        # Security check: only accept packets from the known Nanoleaf host
        if addr[0] != self._nanoleaf_host:
            return
            
        # Parse the binary protocol manually
        # Note: This could be optimized using struct.unpack instead of string manipulation
        binary = bin(int.from_bytes(data, byteorder="big"))
        binary = binary[3:]  # Remove '0b1' prefix padding if present
        
        event = TouchStreamEvent(
            panel_id=int(binary[:16], 2),        # First 2 bytes
            touch_type_id=int(binary[16:20], 2), # Nibble after panel id
            strength=int(binary[20:24], 2),      # Nibble after touch type
            panel_id_2=int(binary[24:], 2),      # Remaining bits
        )
        asyncio.create_task(self._callback(event))
