# aioNanoleaf2 package 
[![PyPI](https://img.shields.io/pypi/v/aionanoleaf2)](https://pypi.org/project/aionanoleaf2/) ![PyPI - Downloads](https://img.shields.io/pypi/dm/aionanoleaf2) [![PyPI - License](https://img.shields.io/pypi/l/aionanoleaf2?color=blue)](https://github.com/loebi-ch/aionanoleaf2/blob/master/LICENSE)

This async Python wrapper for the Nanoleaf API replaces the unmaintained `aionanoleaf` package.

The library includes:
- Support for Nanoleaf Essentials devices.
- Support for Screen Mirroring emersion modes (1D, 2D, 3D, 4D).
- Support for IPv6 hosts and RFC 6874 zone IDs.
- Connection leak protection via async response context managers.
- Resilient Server-Sent Events (SSE) streaming and binary UDP touch parsing.

## Installation
```bash
pip install aionanoleaf2
```

## Example
```python
from aiohttp import ClientSession
from asyncio import run

import aionanoleaf2


async def test():
    async with ClientSession() as session:
        nanoleaf = aionanoleaf2.Nanoleaf(session, "192.168.1.28")
        try:
            await nanoleaf.authorize()
        except aionanoleaf2.Unauthorized as ex:
            print("Not authorized:", ex)
            return

        await nanoleaf.turn_on()
        await nanoleaf.get_info()

        print("IT'S WORKING!")
        print("Host:", nanoleaf.host)
        print("Port:", nanoleaf.port)
        print("Name:", nanoleaf.name)
        print("Manufacturer:", nanoleaf.manufacturer)
        print("Model:", nanoleaf.model)
        print("Serial number:", nanoleaf.serial_no)
        print("Hardware version:", nanoleaf.hardware_version)
        print("Firmware version:", nanoleaf.firmware_version)
        print("On:", nanoleaf.is_on)
        print(
            "Brightness:",
            f"{nanoleaf.brightness} [{nanoleaf.brightness_min} - {nanoleaf.brightness_max}]",
        )
        print("Hue:", f"{nanoleaf.hue} [{nanoleaf.hue_min} - {nanoleaf.hue_max}]")
        print(
            "Saturation:",
            f"{nanoleaf.saturation} [{nanoleaf.saturation_min} - {nanoleaf.saturation_max}]",
        )
        print(
            "Color temperature:",
            f"{nanoleaf.color_temperature} [{nanoleaf.color_temperature_min} - {nanoleaf.color_temperature_max}]",
        )
        print("Color mode:", nanoleaf.color_mode)
        print("Effects:", nanoleaf.effects_list)
        print("Selected effect:", nanoleaf.selected_effect)
        print("Emersions:", nanoleaf.emersion_list)
        print("Selected emersion:", nanoleaf.selected_emersion)
        print("Panels:", len(nanoleaf.panels))

        await nanoleaf.identify()
        await nanoleaf.turn_off()
        await nanoleaf.deauthorize()


run(test())
```
