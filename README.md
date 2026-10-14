# aionanoleaf2

[![PyPI](https://img.shields.io/pypi/v/aionanoleaf2)](https://pypi.org/project/aionanoleaf2/) ![PyPI - Python Version](https://img.shields.io/pypi/pyversions/aionanoleaf2) ![PyPI - Downloads](https://img.shields.io/pypi/dm/aionanoleaf2) [![PyPI - License](https://img.shields.io/pypi/l/aionanoleaf2?color=blue)](https://github.com/loebi-ch/aionanoleaf2/blob/master/LICENSE)

Async Python client for the local [Nanoleaf OpenAPI](https://forum.nanoleaf.me/docs/openapi). It replaces the unmaintained `aionanoleaf` package and is the library behind the Home Assistant [Nanoleaf integration](https://www.home-assistant.io/integrations/nanoleaf/).

## Features

- Fully typed (`py.typed`), `asyncio` only, Python 3.12 or newer.
- Control power, brightness, hue, saturation, color temperature and effects.
- Nanoleaf Essentials support (devices without `state` or `panelLayout` in the root document).
- Screen Mirroring modes (1D, 2D, 3D, 4D) for the Nanoleaf 4D (`NL69`).
- Resilient Server-Sent Events (SSE) listener: automatic reconnect with exponential backoff and jitter, tolerant of malformed events, sync and async callbacks.
- Optional high-frequency UDP touch stream.
- IPv6 hosts including RFC 6874 zone IDs.
- No hidden network state: **you** own the `aiohttp.ClientSession`.
- One exception hierarchy that maps cleanly to application-level errors.

## Installation

```bash
pip install aionanoleaf2
```

## Quick start

```python
import asyncio

from aiohttp import ClientSession

from aionanoleaf2 import Nanoleaf, Unauthorized


async def main() -> None:
    async with ClientSession() as session:
        nanoleaf = Nanoleaf(session, "192.168.1.28")
        try:
            # Hold the power button for 5-7 seconds first, then call within 30 s
            await nanoleaf.authorize()
        except Unauthorized:
            print("The device is not in pairing mode.")
            return
        print("Store this token:", nanoleaf.auth_token)

        await nanoleaf.get_info()
        print(nanoleaf.name, nanoleaf.model, nanoleaf.firmware_version)

        await nanoleaf.turn_on()
        await nanoleaf.set_brightness(60)
        await nanoleaf.set_color_temperature(2700)


asyncio.run(main())
```

On later runs, pass the stored token instead of pairing again:

```python
nanoleaf = Nanoleaf(session, "192.168.1.28", auth_token="YOUR_TOKEN")
```

### The session is yours

The library never creates an `aiohttp.ClientSession`. Pass one in and close it yourself. In Home Assistant, use `async_get_clientsession(hass)`.

### Constructor

```python
Nanoleaf(session, host, auth_token=None, port=16021, retries=3)
```

| Argument | Meaning |
| --- | --- |
| `session` | The `aiohttp.ClientSession` used for all requests. |
| `host` | IPv4, IPv6 (with or without brackets, optional zone ID) or hostname. |
| `auth_token` | Token from `authorize()`. Without it every request raises `NoAuthToken`. |
| `port` | API port, default `16021`. |
| `retries` | Attempts per request on timeouts and connection errors. |

Requests use a 5 second total and 3 second connect timeout. HTTP 401 and 404 are never retried.

## Reading and controlling the device

`get_info()` fetches the device description, state, effects and layout and stores them on the instance. Read the values through properties:

```python
await nanoleaf.get_info()

nanoleaf.is_on
nanoleaf.brightness, nanoleaf.brightness_min, nanoleaf.brightness_max
nanoleaf.hue, nanoleaf.saturation, nanoleaf.color_temperature, nanoleaf.color_mode
nanoleaf.effects_list, nanoleaf.effect, nanoleaf.selected_effect
nanoleaf.panels  # set[Panel]
nanoleaf.supports_events  # False on Essentials, None before get_info()
```

Setters send one request each:

```python
await nanoleaf.turn_on()
await nanoleaf.turn_off()  # or turn_off(transition=3)
await nanoleaf.set_brightness(80, transition=2)  # transition in seconds
await nanoleaf.set_brightness(-10, relative=True)
await nanoleaf.set_hue(120)
await nanoleaf.set_saturation(70)
await nanoleaf.set_color_temperature(4000)
await nanoleaf.set_effect("Northern Lights")  # raises InvalidEffect if unknown
await nanoleaf.identify()  # flash the panels
```

`set_state(...)` changes several attributes in a single request. Relative changes are not idempotent, so a retried request after a timeout could apply them twice.

Properties update when you call `get_info()` or when an event arrives through `listen_events()`. Setters do not update the local values.

## Live updates with `listen_events`

```python
from aionanoleaf2 import Nanoleaf, StateEvent, EffectsEvent, InvalidToken


async def on_state(event: StateEvent) -> None:
    print("State:", event.attribute, event.value)


def on_effect(event: EffectsEvent) -> None:
    print("Effect:", event.effect)


async def watch(nanoleaf: Nanoleaf) -> None:
    try:
        await nanoleaf.listen_events(
            state_callback=on_state,
            effects_callback=on_effect,
        )
    except InvalidToken:
        print("Token revoked, authorize again.")
```

- `listen_events()` runs until the task is **cancelled**. Run it as a background task, for example `task = asyncio.create_task(watch(nanoleaf))`, and call `task.cancel()` to stop it. Cancellation also stops callback tasks and closes the UDP socket.
- Callbacks may be plain functions or coroutine functions. An exception inside a callback is logged and never stops the listener.
- The instance state is updated before the callback is called, so `nanoleaf.brightness` already holds the new value in `on_state`.
- Dropped connections, timeouts and unexpected HTTP statuses are retried with exponential backoff (1 s up to 30 s, with jitter). The delay resets after an event arrives or after a connection stayed up for a minute.
- HTTP 401 raises `InvalidToken` (retrying cannot help). HTTP 404 raises `EndpointNotFoundError` (the device has no event stream).
- Check `supports_events` first: Essentials devices have no event stream.

Available callbacks: `state_callback`, `layout_callback`, `effects_callback`, `touch_callback` (`TouchEvent`) and `touch_stream_callback` (`TouchStreamEvent`). Touch events only exist on devices with touch hardware.

For the UDP touch stream you can choose the local endpoint with `local_ip=` and `local_port=` (default: all interfaces, random port). The Nanoleaf must be able to reach that address.

## Screen Mirroring (Nanoleaf 4D)

```python
await nanoleaf.get_info()  # reads the mode automatically on NL69
print(nanoleaf.emersion_list)  # ['1D', '2D', '3D', '4D']
print(nanoleaf.selected_emersion)  # e.g. '4D'

await nanoleaf.set_emersion("2D")  # raises InvalidEmersion for other values
```

`get_emersion()` refreshes the current mode on demand. While `listen_events()` runs, the mode is refreshed automatically when the device switches to `*Emersion*`.

## Exceptions

All exceptions derive from `NanoleafError`, so one `except` clause is enough when you do not care about the cause.

```
NanoleafError
├── AuthError                 credentials rejected
│   └── InvalidToken          HTTP 401
├── Unauthorized              pairing mode not active (HTTP 403 on authorize)
├── NoAuthToken               no token configured, nothing was sent
├── Unavailable               device unreachable or unusable
│   ├── NanoleafConnectionError
│   └── NanoleafTimeoutError
├── InvalidResponseError      reply is not usable JSON
├── EndpointNotFoundError     HTTP 404 (has .path)
├── InvalidEffect             also a ValueError
└── InvalidEmersion           also a ValueError
```

`Unauthorized` is deliberately **not** an `AuthError`: it only means "enable pairing mode and try again" during setup and must never trigger a re-authentication.

| Exception | Typical handling |
| --- | --- |
| `AuthError`, `NoAuthToken` | Ask the user for a new token (re-authentication). |
| `Unauthorized` | Tell the user to put the device into pairing mode. |
| `Unavailable` (connection, timeout) | Device offline, try again later. |
| `InvalidResponseError`, `EndpointNotFoundError` | Unexpected firmware behavior. |
| `InvalidEffect`, `InvalidEmersion` | Invalid user input. |

### Mapping to Home Assistant

| aionanoleaf2 | Home Assistant |
| --- | --- |
| `AuthError`, `NoAuthToken` (coordinator) | `ConfigEntryAuthFailed` |
| `NanoleafError` (coordinator) | `UpdateFailed` |
| `InvalidEffect` (service call) | `ServiceValidationError` |
| `AuthError` (service call) | re-authentication and `HomeAssistantError` |
| other `NanoleafError` (service call) | `HomeAssistantError` |

## Migrating from 1.1.0 / `aionanoleaf`

- **Python 3.12 or newer** is required.
- **Exceptions:** `NanoleafException` still works as an alias of `NanoleafError`, but is deprecated and will be removed in 2.0.0. Catch `NanoleafError` instead. Transport problems raise `NanoleafConnectionError` or `NanoleafTimeoutError` (both `Unavailable`).
- **`get_info()` and the `get_*` methods now raise** on timeouts and connection errors. Only HTTP 404 on optional endpoints (for example on Essentials) is tolerated; catch `NanoleafError` around refreshes.
- **`listen_events()`** raises `InvalidToken` on HTTP 401 instead of reconnecting forever, and `EndpointNotFoundError` on HTTP 404.
- **Hosts** are normalized idempotently: `Nanoleaf.host` can be passed back into the constructor unchanged.

## Command line helpers

The `scripts/` folder (not part of the PyPI package) contains checks to run against real devices. Run them from the repository root with the project's virtual environment active. They need a token (`--token`) or a device in pairing mode (`--pair`).

| Script | Purpose |
| --- | --- |
| `scripts/probe_endpoints.py` | Read-only probe of the HTTP status of every endpoint (standard library only). |
| `scripts/smoke_get_info.py` | Read-only `get_info()` check. |
| `scripts/smoke_listen_events.py` | Starts the listener, changes the brightness briefly and checks the event, clean cancel, and (NL69) the Screen Mirroring mode. Restores the previous values. |
| `scripts/smoke_control.py` | Exercises the write path (power, brightness, color, effects, identify), reads each result back and restores the previous state. |

```bash
python scripts/smoke_get_info.py 192.168.1.56 --token TOKEN
python scripts/smoke_listen_events.py 192.168.1.56 --pair
python scripts/smoke_control.py 192.168.1.56 --token TOKEN
```

## Development

```bash
pip install -e .
pip install -r requirements-test.txt
pytest
```

## Credits and license

Based on [aionanoleaf](https://github.com/milanmeu/aionanoleaf) by Milan Meulemans. Essentials support builds on work by JaspervRijbroek, Screen Mirroring on work by jonathanrobichaud4 and IPv6 support on work by krozgrov.

Licensed under the [GNU Lesser General Public License v3 or later](https://github.com/loebi-ch/aionanoleaf2/blob/master/LICENSE).
