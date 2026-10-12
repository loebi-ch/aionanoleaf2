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
from aiohttp import ClientSession

import aionanoleaf2


async def test() -> None:
    async with ClientSession() as session:
        nanoleaf = aionanoleaf2.Nanoleaf(session, "192.168.1.56")

        try:
            await nanoleaf.authorize()
        except aionanoleaf2.Unauthorized as ex:
            print("Not authorized:", ex)
            return

        # 1. Basis-Info und Status abfragen
        await nanoleaf.turn_on()
        await nanoleaf.get_info()

        print("=== DEVICE INFO ===")
        print("Host:", nanoleaf.host)
        print("Port:", nanoleaf.port)
        print("Name:", nanoleaf.name)
        print("Manufacturer:", nanoleaf.manufacturer)
        print("Model:", nanoleaf.model)
        print("Serial number:", nanoleaf.serial_no)
        print("Hardware version:", nanoleaf.hardware_version)
        print("Firmware version:", nanoleaf.firmware_version)
        print("Supports events:", nanoleaf.supports_events)
        print("Panels:", len(nanoleaf.panels))

        print("\n=== CURRENT STATE ===")
        print("On:", nanoleaf.is_on)
        print("Brightness:", f"{nanoleaf.brightness} [{nanoleaf.brightness_min} - {nanoleaf.brightness_max}]")
        print("Hue:", f"{nanoleaf.hue} [{nanoleaf.hue_min} - {nanoleaf.hue_max}]")
        print("Saturation:", f"{nanoleaf.saturation} [{nanoleaf.saturation_min} - {nanoleaf.saturation_max}]")
        print("Color temperature:", f"{nanoleaf.color_temperature} [{nanoleaf.color_temperature_min} - {nanoleaf.color_temperature_max}]")
        print("Color mode:", nanoleaf.color_mode)
        print("Selected effect:", nanoleaf.selected_effect)

        # 2. Effekt-Details testen
        if nanoleaf.selected_effect:
            details = await nanoleaf.get_effect_details(nanoleaf.selected_effect)
            print("Effect details loaded:", details is not None)

        # 3. State-Mutationen testen
        print("\n=== TESTING STATE MUTATIONS ===")
        await nanoleaf.set_brightness(15)
        await nanoleaf.set_color_temperature(2700)
        await asyncio.sleep(0.5)

        # 4. SSE-Streaming und Task-Cancellation testen
        if nanoleaf.supports_events:
            print("\n=== TESTING SSE EVENT STREAMING ===")
            event_received = asyncio.Event()

            async def on_state_event(event: aionanoleaf2.StateEvent) -> None:
                print(f"-> SSE Event received: {event.attribute} = {event.value}")
                event_received.set()

            listener_task = asyncio.create_task(
                nanoleaf.listen_events(state_callback=on_state_event)
            )

            # Kurze Pause für Verbindungsaufbau, dann Trigger durch Statuswechsel
            await asyncio.sleep(1.0)
            await nanoleaf.set_brightness(20)

            try:
                await asyncio.wait_for(event_received.wait(), timeout=5.0)
                print("SSE stream test passed successfully.")
            except asyncio.TimeoutError:
                print("SSE event timed out (controller did not push state within 5s).")
            finally:
                # Saubere Task-Stornierung prüfen
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    print("SSE listener task cleanly cancelled.")

        # 5. Teardown
        print("\n=== TEARDOWN ===")
        await nanoleaf.identify()
        await nanoleaf.turn_off()
        await nanoleaf.deauthorize()
        print("Test completed successfully.")


asyncio.run(test())
