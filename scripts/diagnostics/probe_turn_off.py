#!/usr/bin/env python3
"""Diagnose: wann geht turn_off() verloren? (Befund auf dem Essentials NL72K1)

Beobachtung: Folgt turn_off() unmittelbar auf set_effect() und set_brightness(),
meldet der Essentials danach weiter "an". Nach einer Pause funktioniert es. Dieses
Skript führt mehrere Befehlsfolgen aus und liest danach nach 0.5/1/2/4 Sekunden den
Zustand, damit sich die Ursache eingrenzen lässt (Reihenfolge, Zeitabstand, Effekt).

Es wird nur Strom, Helligkeit und Effekt verändert und am Ende wiederhergestellt.

Aufruf (im Projektordner, venv aktiv):
  python scripts/probe_turn_off.py 192.168.1.57 --pair
  python scripts/probe_turn_off.py 192.168.1.57 --token TOKEN
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

# Lokale Library (Repo-Root) vor einer evtl. installierten Version verwenden.
_ROOT = Path(__file__).resolve().parent.parent
if (_ROOT / "aionanoleaf2").is_dir():
    sys.path.insert(0, str(_ROOT))

from aiohttp import ClientSession

import aionanoleaf2
from aionanoleaf2 import Nanoleaf, NanoleafError

SAMPLE_TIMES = (0.5, 1.0, 2.0, 4.0)  # Sekunden nach dem letzten Befehl
RESERVED_EFFECTS = ("*Solid*", "*Static*", "*Dynamic*", "*Emersion*")

Sequence = Callable[[Nanoleaf], Awaitable[None]]


async def sample(light: Nanoleaf) -> list[bool]:
    """Zustand zu den Zeitpunkten SAMPLE_TIMES lesen (True = an)."""
    states: list[bool] = []
    previous = 0.0
    for moment in SAMPLE_TIMES:
        await asyncio.sleep(moment - previous)
        previous = moment
        await light.get_info()
        states.append(light.is_on)
    return states


def build_sequences(light: Nanoleaf) -> list[tuple[str, Sequence]]:
    brightness = light.brightness
    other_brightness = brightness - 10 if brightness > 10 else brightness + 10
    effect = light.effect if light.effect in light.effects_list else None
    other = next(
        (e for e in light.effects_list if e != effect and e not in RESERVED_EFFECTS),
        None,
    )

    async def only_off(nl: Nanoleaf) -> None:
        await nl.turn_off()

    async def brightness_same_then_off(nl: Nanoleaf) -> None:
        await nl.set_brightness(brightness)
        await nl.turn_off()

    async def brightness_other_then_off(nl: Nanoleaf) -> None:
        await nl.set_brightness(other_brightness)
        await nl.turn_off()

    async def brightness_pause_off(nl: Nanoleaf) -> None:
        await nl.set_brightness(brightness)
        await asyncio.sleep(1.0)
        await nl.turn_off()

    async def effect_then_off(nl: Nanoleaf) -> None:
        assert other is not None
        await nl.set_effect(other)
        await nl.turn_off()

    async def restore_like(nl: Nanoleaf) -> None:
        assert effect is not None
        await nl.set_effect(effect)
        await nl.set_brightness(brightness)
        await nl.turn_off()

    sequences: list[tuple[str, Sequence]] = [
        ("turn_off", only_off),
        ("set_brightness(gleich) -> turn_off", brightness_same_then_off),
        ("set_brightness(anders) -> turn_off", brightness_other_then_off),
        ("set_brightness -> 1s Pause -> turn_off", brightness_pause_off),
    ]
    if other is not None:
        sequences.append(("set_effect -> turn_off", effect_then_off))
    if effect is not None:
        sequences.append(("set_effect -> set_brightness -> turn_off", restore_like))
    return sequences


async def probe(light: Nanoleaf) -> None:
    await light.get_info()
    print(f"Modell {light.model}, Firmware {light.firmware_version}, {light.name}")
    original = (light.is_on, light.brightness, light.effect)
    print(
        f"Ausgangszustand: an={original[0]}, Helligkeit={original[1]}, Effekt={original[2]!r}\n"
    )

    header = "  ".join(f"{t:>4g}s" for t in SAMPLE_TIMES)
    print(f"{'Befehlsfolge':44} {header}")
    try:
        for name, sequence in build_sequences(light):
            await light.turn_on()
            await asyncio.sleep(2.0)  # Gerät sicher an, Befehle abgeschlossen
            await sequence(light)
            states = await sample(light)
            cells = "  ".join(f"{'an' if s else 'aus':>5}" for s in states)
            verdict = "AUS GING VERLOREN" if states[-1] else "OK"
            print(f"{name:44} {cells}  {verdict}")
    finally:
        await restore(light, original)


async def restore(light: Nanoleaf, original: tuple[bool, int, str]) -> None:
    is_on, brightness, effect = original
    try:
        await light.turn_on()
        if effect in light.effects_list and effect not in RESERVED_EFFECTS:
            await light.set_effect(effect)
        await light.set_brightness(brightness)
        await asyncio.sleep(1.0)
        if not is_on:
            await light.turn_off()
        print(
            f"\nZustand zurückgesetzt (an={is_on}, Helligkeit={brightness}, Effekt={effect!r})"
        )
    except NanoleafError as err:
        print(
            f"\nZurücksetzen fehlgeschlagen: {type(err).__name__}: {err} - Gerät prüfen"
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("host")
    parser.add_argument("--token")
    parser.add_argument("--pair", action="store_true")
    args = parser.parse_args()
    if not args.pair and not args.token:
        parser.error("--pair oder --token angeben")

    print(f"aionanoleaf2: {getattr(aionanoleaf2, '__file__', '?')}")
    async with ClientSession() as session:
        light = Nanoleaf(session, args.host, args.token)
        paired = False
        try:
            if args.pair:
                await light.authorize()
                paired = True
                print("Temporäres Token erhalten")
            await probe(light)
        except NanoleafError as err:
            print(f"Abbruch: {type(err).__name__}: {err}")
            return 1
        finally:
            if paired:
                await light.deauthorize()
                print("Temporäres Token gelöscht")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
