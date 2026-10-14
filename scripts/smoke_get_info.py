#!/usr/bin/env python3
"""Read-only Smoke-Test: führt aionanoleaf2.Nanoleaf.get_info() gegen echte Geräte aus.

Ändert keinen Gerätezustand (kein set_*, kein identify). Mit --pair wird ein
temporäres Token angelegt und am Ende wieder gelöscht.

Aufruf (im Projektordner, venv aktiv, damit die lokale 1.2.0 importiert wird):
  python smoke_get_info.py 192.168.1.56 192.168.1.57 --pair
  python smoke_get_info.py 192.168.1.57 --token TOKEN
Exit-Code: 0 = alle Geräte ok, 1 = mindestens ein Gerät fehlgeschlagen.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from aiohttp import ClientSession

import aionanoleaf2
from aionanoleaf2 import Nanoleaf, NanoleafError


def _fail(host: str, err: Exception) -> None:
    print(f"  FEHLER {type(err).__name__}: {err}")
    print(f"  -> Gerät {host} nicht ok")


async def check(
    session: ClientSession, host: str, token: str | None, pair: bool
) -> bool:
    print(f"\n=== {host} ===")
    print(f"aionanoleaf2 {getattr(aionanoleaf2, '__file__', '?')}")
    light = Nanoleaf(session, host, token)
    paired = False
    ok = True
    try:
        if pair:
            await light.authorize()
            paired = True
            print("authorize: ok")
        await light.get_info()
        print(f"Modell:          {light.model}")
        print(f"Firmware:        {light.firmware_version}")
        print(f"Name:            {light.name}")
        print(
            f"Zustand:         on={light.is_on}, brightness={light.brightness}"
            f" ({light.brightness_min}-{light.brightness_max}), color_mode={light.color_mode!r}"
        )
        print(
            f"Effekte:         {len(light.effects_list)} Stück, aktiv={light.effect!r}"
        )
        print(f"Panels:          {len(light.panels)}")
        print(f"supports_events: {light.supports_events}")
        print(f"emersion:        {light.emersion!r}")
        checks = {
            "model gesetzt": bool(light.model),
            "effects_list ist list[str]": all(
                isinstance(e, str) for e in light.effects_list
            ),
            "effect ist str": isinstance(light.effect, str),
            "supports_events ist bool": isinstance(light.supports_events, bool),
        }
        for label, passed in checks.items():
            print(f"  [{'ok' if passed else 'FAIL'}] {label}")
        ok = all(checks.values())
    except NanoleafError as err:
        _fail(host, err)
        ok = False
    except Exception as err:  # noqa: BLE001 - CLI-Tool fängt unerwartete Fehler bewusst ab
        _fail(host, err)
        ok = False
    finally:
        if paired:
            try:
                await light.deauthorize()
                print("deauthorize: ok")
            except NanoleafError as err:
                print(f"  WARNUNG: Token konnte nicht gelöscht werden: {err}")
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hosts", nargs="+")
    ap.add_argument("--token")
    ap.add_argument("--pair", action="store_true")
    args = ap.parse_args()
    if not args.pair and not args.token:
        ap.error("--pair oder --token angeben")
    async with ClientSession() as session:
        results = [await check(session, h, args.token, args.pair) for h in args.hosts]
    print("\nGesamtergebnis:", "ALLES OK" if all(results) else "FEHLER")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
