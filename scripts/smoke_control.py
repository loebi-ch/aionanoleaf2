#!/usr/bin/env python3
"""Smoke-Test der Steuerbefehle von aionanoleaf2 gegen echte Geräte.

Prüft den Schreibpfad: turn_on/turn_off, set_brightness, set_color_temperature,
set_hue/set_saturation, set_effect, get_effect_details und identify. Nach jedem
Befehl wird der Zustand mit get_info() zurückgelesen und verglichen.

Der Ausgangszustand (An/Aus, Helligkeit, Farbe/Farbtemperatur, Effekt) wird
vorher gemerkt und am Ende wiederhergestellt, auch wenn ein Schritt fehlschlägt.
Der Listener wird hier nicht getestet: dafür gibt es smoke_listen_events.py.

Aufruf (im Projektordner, venv aktiv, damit die lokale Library importiert wird):
  python scripts/smoke_control.py 192.168.1.56 --pair
  python scripts/smoke_control.py 192.168.1.56 192.168.1.57 --token TOKEN
  python scripts/smoke_control.py 192.168.1.56 --token TOKEN --skip-identify
Exit-Code: 0 = alle Geräte ok, 1 = mindestens ein Fehler.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Lokale Library (Repo-Root) vor einer evtl. installierten Version verwenden.
_ROOT = Path(__file__).resolve().parent.parent
if (_ROOT / "aionanoleaf2").is_dir():
    sys.path.insert(0, str(_ROOT))

from aiohttp import ClientSession

import aionanoleaf2
from aionanoleaf2 import (
    InvalidEffect,
    Nanoleaf,
    NanoleafError,
    Unavailable,
)

SETTLE_SECONDS = 0.5  # Read-after-write-Latenz der Firmware
LATE_READ_SECONDS = 3.0  # zweiter Lesevorgang, wenn der Zustand zunächst abweicht
RESERVED_EFFECTS = ("*Solid*", "*Static*", "*Dynamic*", "*Emersion*")


class Console:
    """Terminalausgabe mit eindeutigen Markern, optional farbig."""

    def __init__(self, color: bool) -> None:
        self._color = color

    def _paint(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self._color else text

    def title(self, text: str) -> None:
        print(f"\n{self._paint('1', f'=== {text} ===')}")

    def step(self, text: str) -> None:
        print(f"\n{self._paint('1;36', '>')} {text}")

    def ok(self, text: str) -> None:
        print(f"  [{self._paint('1;32', '  OK  ')}] {text}")

    def fail(self, text: str) -> None:
        print(f"  [{self._paint('1;31', 'FEHLER')}] {text}")

    def warn(self, text: str) -> None:
        print(f"  [{self._paint('1;33', 'HINWEIS')}] {text}")

    def info(self, text: str) -> None:
        print(f"  [ .... ] {text}")


@dataclass
class Result:
    """Ergebnis eines Geräts."""

    out: Console
    failures: int = 0

    def check(self, condition: bool, ok_text: str, fail_text: str) -> bool:
        if condition:
            self.out.ok(ok_text)
        else:
            self.failures += 1
            self.out.fail(fail_text)
        return condition

    def error(self, text: str) -> None:
        self.failures += 1
        self.out.fail(text)


@dataclass(frozen=True)
class Snapshot:
    """Zustand, der am Ende wiederhergestellt wird."""

    is_on: bool
    brightness: int
    hue: int
    saturation: int
    color_temperature: int
    color_mode: str
    effect: str


def take_snapshot(light: Nanoleaf) -> Snapshot:
    return Snapshot(
        is_on=light.is_on,
        brightness=light.brightness,
        hue=light.hue,
        saturation=light.saturation,
        color_temperature=light.color_temperature,
        color_mode=light.color_mode,
        effect=light.effect,
    )


async def refresh(light: Nanoleaf) -> None:
    """Kurz warten und den Zustand vom Gerät lesen."""
    await asyncio.sleep(SETTLE_SECONDS)
    await light.get_info()


def close(actual: int, expected: int, tolerance: int) -> bool:
    return abs(actual - expected) <= tolerance


def pick_brightness(light: Nanoleaf, delta: int = 10) -> int:
    current = light.brightness
    target = (
        current + delta if current + delta <= light.brightness_max else current - delta
    )
    return max(light.brightness_min, min(light.brightness_max, target))


def pick_other_effect(light: Nanoleaf) -> str | None:
    """Ein anderer, vom Benutzer wählbarer Effekt (ohne reservierte)."""
    for effect in light.effects_list:
        if effect != light.effect and effect not in RESERVED_EFFECTS:
            return effect
    return None


async def check_power_and_brightness(light: Nanoleaf, res: Result) -> None:
    out = res.out
    out.step("An/Aus und Helligkeit")
    await light.turn_on()
    await refresh(light)
    res.check(light.is_on, "turn_on(): Gerät ist an", "turn_on(): Gerät meldet 'aus'")

    target = pick_brightness(light)
    original = light.brightness
    if target == original:
        res.error(f"Kein Testwert für die Helligkeit möglich (Wert {original})")
    else:
        await light.set_brightness(target)
        await refresh(light)
        res.check(
            close(light.brightness, target, 1),
            f"set_brightness({target}): Gerät meldet {light.brightness}",
            f"set_brightness({target}): Gerät meldet {light.brightness}",
        )

    await light.set_brightness(original, transition=1)
    await asyncio.sleep(1.0 + SETTLE_SECONDS)
    await light.get_info()
    res.check(
        close(light.brightness, original, 1),
        f"set_brightness({original}, transition=1): Gerät meldet {light.brightness}",
        f"set_brightness({original}, transition=1): Gerät meldet {light.brightness}",
    )


async def check_color(light: Nanoleaf, res: Result) -> None:
    out = res.out
    out.step("Farbtemperatur, Farbton und Sättigung")
    ct_min, ct_max = light.color_temperature_min, light.color_temperature_max
    if ct_max > ct_min:
        ct_target = (ct_min + ct_max) // 2
        await light.set_color_temperature(ct_target)
        await refresh(light)
        res.check(
            close(light.color_temperature, ct_target, 100),
            f"set_color_temperature({ct_target}): Gerät meldet {light.color_temperature}",
            f"set_color_temperature({ct_target}): Gerät meldet {light.color_temperature}",
        )
    else:
        out.warn("Keine Farbtemperatur-Spanne gemeldet - übersprungen")

    hue_target = (light.hue + 90) % (light.hue_max + 1)
    sat_target = 80 if light.saturation < 80 else 40
    await light.set_hue(hue_target)
    await light.set_saturation(sat_target)
    await refresh(light)
    res.check(
        close(light.hue, hue_target, 2),
        f"set_hue({hue_target}): Gerät meldet {light.hue}",
        f"set_hue({hue_target}): Gerät meldet {light.hue}",
    )
    res.check(
        close(light.saturation, sat_target, 2),
        f"set_saturation({sat_target}): Gerät meldet {light.saturation}",
        f"set_saturation({sat_target}): Gerät meldet {light.saturation}",
    )


async def check_effects(light: Nanoleaf, res: Result) -> None:
    out = res.out
    out.step("Effekte")
    out.info(f"{len(light.effects_list)} Effekte, aktuell: {light.effect!r}")

    try:
        await light.set_effect("__does-not-exist__")
    except InvalidEffect:
        out.ok("set_effect() mit unbekanntem Namen wirft InvalidEffect (ohne Request)")
    else:
        res.error("set_effect() mit unbekanntem Namen wirft keine InvalidEffect")

    current = light.selected_effect
    if current is not None:
        details = await light.get_effect_details(current)
        if details is None:
            out.warn("get_effect_details(): Gerät kennt den Endpunkt nicht (404)")
        else:
            res.check(
                details.get("animName", current) == current,
                f"get_effect_details({current!r}) liefert Details",
                f"get_effect_details({current!r}) liefert fremden Effekt: "
                f"{details.get('animName')!r}",
            )

    other = pick_other_effect(light)
    if other is None:
        out.warn("Kein zweiter wählbarer Effekt vorhanden - set_effect übersprungen")
        return
    await light.set_effect(other)
    await refresh(light)
    res.check(
        light.effect == other,
        f"set_effect({other!r}): Gerät meldet {light.effect!r}",
        f"set_effect({other!r}): Gerät meldet {light.effect!r}",
    )


async def check_identify(light: Nanoleaf, res: Result) -> None:
    res.out.step("Identify (Panels blinken kurz)")
    try:
        await light.identify()
    except Unavailable as err:
        if "HTTP 400" not in str(err):
            raise
        # Bekannt: Essentials (NL72K1) beantworten PUT /identify mit HTTP 400
        res.out.warn(
            f"Gerät unterstützt identify() nicht (HTTP 400, Modell {light.model})"
        )
        return
    res.out.ok("identify() ohne Fehler")


async def check_power_off(light: Nanoleaf, res: Result) -> None:
    res.out.step("Ausschalten")
    await light.turn_off()
    await refresh(light)
    res.check(
        not light.is_on, "turn_off(): Gerät ist aus", "turn_off(): Gerät meldet 'an'"
    )


async def restore(light: Nanoleaf, snap: Snapshot, res: Result) -> None:
    """Ausgangszustand wiederherstellen; Fehler werden gemeldet, nicht geworfen."""
    out = res.out
    out.step("Ausgangszustand wiederherstellen")
    try:
        await light.turn_on()  # Befehle wirken nur bei eingeschaltetem Gerät sicher
        if snap.effect in light.effects_list and snap.effect not in RESERVED_EFFECTS:
            await light.set_effect(snap.effect)
        elif snap.color_mode == "ct":
            await light.set_color_temperature(snap.color_temperature)
        else:
            await light.set_hue(snap.hue)
            await light.set_saturation(snap.saturation)
        await light.set_brightness(snap.brightness)
        if not snap.is_on:
            # Der Essentials ignoriert turn_off(), wenn es unmittelbar auf set_effect()
            # folgt (siehe diagnostics/probe_turn_off.py): kurz warten.
            await asyncio.sleep(SETTLE_SECONDS)
            await light.turn_off()
        await refresh(light)
    except NanoleafError as err:
        res.error(
            f"Wiederherstellen fehlgeschlagen ({type(err).__name__}: {err}) "
            "- bitte Zustand am Gerät prüfen"
        )
        return

    def restored() -> bool:
        return light.is_on == snap.is_on and close(light.brightness, snap.brightness, 1)

    if not restored():
        # Diagnose: braucht das Gerät nur länger, oder hat es den Befehl ignoriert?
        out.info(
            f"Zustand weicht ab (an={light.is_on}, Helligkeit={light.brightness}), "
            f"lese nach {LATE_READ_SECONDS:g}s erneut"
        )
        await asyncio.sleep(LATE_READ_SECONDS)
        try:
            await light.get_info()
        except NanoleafError as err:
            res.error(f"get_info() fehlgeschlagen: {type(err).__name__}: {err}")
            return
        if restored():
            out.warn(
                f"Zustand stimmt erst nach {LATE_READ_SECONDS:g}s "
                f"(Gerät reagiert verzögert)"
            )
    res.check(
        restored(),
        f"Zustand wiederhergestellt (an={snap.is_on}, Helligkeit={snap.brightness}, "
        f"Modus={snap.color_mode}, Effekt={snap.effect!r})",
        f"Zustand weicht ab: an={light.is_on} (vorher {snap.is_on}), "
        f"Helligkeit={light.brightness} (vorher {snap.brightness}) "
        "- bitte Gerät prüfen",
    )


async def check_device(light: Nanoleaf, out: Console, *, identify: bool) -> Result:
    """Alle Prüfungen für ein bereits autorisiertes Gerät."""
    res = Result(out)
    out.step("Geräteinformationen lesen")
    try:
        await light.get_info()
    except NanoleafError as err:
        res.error(f"get_info() fehlgeschlagen: {type(err).__name__}: {err}")
        return res
    out.info(f"Modell {light.model}, Firmware {light.firmware_version}, {light.name}")
    snap = take_snapshot(light)
    out.info(
        f"Ausgangszustand: an={snap.is_on}, Helligkeit={snap.brightness}, "
        f"Modus={snap.color_mode}, Effekt={snap.effect!r}"
    )

    checks = [check_power_and_brightness, check_color, check_effects]
    if identify:
        checks.append(check_identify)
    checks.append(check_power_off)
    try:
        for check in checks:
            # Ein fehlgeschlagener Befehl bricht die übrigen Prüfungen nicht ab
            try:
                await check(light, res)
            except NanoleafError as err:
                res.error(f"Befehl fehlgeschlagen: {type(err).__name__}: {err}")
    finally:
        await restore(light, snap, res)
    return res


async def run_device(
    session: ClientSession,
    host: str,
    token: str | None,
    pair: bool,
    out: Console,
    identify: bool,
) -> Result:
    out.title(host)
    light = Nanoleaf(session, host, token)
    paired = False
    try:
        if pair:
            out.step("Token anfordern (Pairing-Modus nötig)")
            try:
                await light.authorize()
            except NanoleafError as err:
                res = Result(out)
                res.error(f"authorize() fehlgeschlagen: {type(err).__name__}: {err}")
                return res
            paired = True
            out.ok("Temporäres Token erhalten")
        return await check_device(light, out, identify=identify)
    except Exception as err:  # noqa: BLE001  # unerwartet: Gerät melden, nächstes Gerät prüfen
        res = Result(out)
        res.error(f"Unerwarteter Fehler: {type(err).__name__}: {err}")
        return res
    finally:
        if paired:
            try:
                await light.deauthorize()
                out.ok("Temporäres Token gelöscht")
            except NanoleafError as err:
                out.warn(f"Token konnte nicht gelöscht werden: {err}")


def enable_color(requested: bool) -> bool:
    """Farbe nur für Terminals, mit ANSI-Unterstützung unter Windows."""
    if not requested or os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        os.system("")  # aktiviert ANSI-Escape-Verarbeitung in der Windows-Konsole
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("hosts", nargs="+", help="IP-Adressen oder Hostnamen")
    parser.add_argument("--token", help="vorhandenes Auth-Token")
    parser.add_argument("--pair", action="store_true", help="temporäres Token anlegen")
    parser.add_argument(
        "--skip-identify", action="store_true", help="identify() nicht ausführen"
    )
    parser.add_argument("--no-color", action="store_true", help="keine Farben")
    args = parser.parse_args()
    if not args.pair and not args.token:
        parser.error("--pair oder --token angeben")

    out = Console(enable_color(not args.no_color))
    print(f"aionanoleaf2: {getattr(aionanoleaf2, '__file__', '?')}")

    results: list[Result] = []
    async with ClientSession() as session:
        for host in args.hosts:
            results.append(
                await run_device(
                    session, host, args.token, args.pair, out, not args.skip_identify
                )
            )

    failed = sum(1 for r in results if r.failures)
    out.title("Gesamtergebnis")
    if failed:
        out.fail(f"{failed} von {len(results)} Gerät(en) mit Fehlern")
        return 1
    out.ok("Alle Geräte OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
