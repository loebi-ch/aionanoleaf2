#!/usr/bin/env python3
"""Smoke-Test für den SSE-Listener von aionanoleaf2 gegen echte Geräte.

Ablauf pro Gerät (alle Modelle mit Event-Unterstützung):
  1. get_info() und Event-Unterstützung prüfen (Essentials werden übersprungen)
  2. listen_events() als Background-Task starten
  3. Helligkeit um --delta verändern, StateEvent im Callback abwarten und prüfen,
     dass der interne Zustand der Library aktualisiert wurde
  4. Helligkeit auf den Ausgangswert zurücksetzen (auch bei Fehlern)
  5. Nur NL69 (4D Screen Mirroring): Modus wechseln, prüfen, zurückstellen
  6. Listener canceln, auf Beendigung warten, auf Leaks prüfen

Touch-Prüfungen gibt es bewusst nicht (der NL69 hat keine Touch-Hardware).
Das Skript verändert Helligkeit (und beim NL69 Screen-Mirror-Modus) kurz und
stellt die Ausgangswerte wieder her.

Aufruf (venv aktiv):
  python scripts/smoke_listen_events.py 192.168.1.56 --pair
  python scripts/smoke_listen_events.py 192.168.1.56 192.168.1.58 --token TOKEN
Exit-Code: 0 = alles OK (übersprungene Geräte zählen nicht als Fehler), 1 = Fehler.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Lokale Library (Repo-Root) vor einer evtl. installierten Version verwenden.
_ROOT = Path(__file__).resolve().parent.parent
if (_ROOT / "aionanoleaf2").is_dir():
    sys.path.insert(0, str(_ROOT))

from aiohttp import ClientSession

import aionanoleaf2
from aionanoleaf2 import EffectsEvent, Nanoleaf, NanoleafError, StateEvent

CONNECT_GRACE = 1.0  # Sekunden, bis der Stream nach dem Start als verbunden gilt
CANCEL_TIMEOUT = 5.0
EMERSION_NAMES = ("1D", "2D", "3D", "4D")
EMERSION_EFFECT = "*Emersion*"


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
    skipped: bool = False
    notes: list[str] = field(default_factory=list)

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


class EventRecorder:
    """Sammelt Events und erlaubt, gezielt auf ein Event zu warten."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self._state_waiters: list[tuple[str, object, asyncio.Event]] = []
        self._effect_waiters: list[tuple[str, asyncio.Event]] = []

    def expect_state(self, attribute: str, value: object) -> asyncio.Event:
        waiter = asyncio.Event()
        self._state_waiters.append((attribute, value, waiter))
        return waiter

    def expect_effect(self, effect: str) -> asyncio.Event:
        waiter = asyncio.Event()
        self._effect_waiters.append((effect, waiter))
        return waiter

    async def on_state(self, event: StateEvent) -> None:
        self.seen.append(f"{event.attribute}={event.value}")
        for attribute, value, waiter in self._state_waiters:
            if event.attribute == attribute and event.value == value:
                waiter.set()

    async def on_effect(self, event: EffectsEvent) -> None:
        self.seen.append(f"effect={event.effect}")
        for effect, waiter in self._effect_waiters:
            if event.effect == effect:
                waiter.set()


async def wait_for(event: asyncio.Event, timeout: float) -> bool:
    """True, wenn das Event innerhalb des Timeouts gesetzt wurde."""
    try:
        await asyncio.wait_for(event.wait(), timeout)
    except TimeoutError:
        return False
    return True


def describe_listener_failure(task: asyncio.Task[None]) -> str:
    """Erklärung, warum der Listener nicht mehr läuft."""
    if task.cancelled():
        return "Listener wurde unerwartet abgebrochen"
    exc = task.exception()
    if exc is None:
        return "Listener hat sich ohne Fehler beendet (sollte endlos laufen)"
    hint = ""
    if isinstance(exc, aionanoleaf2.AuthError):
        hint = " -> Token wird vom Gerät abgelehnt (401)"
    elif isinstance(exc, aionanoleaf2.EndpointNotFoundError):
        hint = " -> Gerät hat keinen Event-Stream (404)"
    return f"Listener beendet mit {type(exc).__name__}: {exc}{hint}"


def pick_brightness_target(light: Nanoleaf, delta: int) -> int:
    """Zielwert um delta vom aktuellen Wert, innerhalb der Gerätegrenzen."""
    current, low, high = light.brightness, light.brightness_min, light.brightness_max
    target = current + delta if current + delta <= high else current - delta
    return max(low, min(high, target))


async def set_brightness_and_wait(
    light: Nanoleaf, recorder: EventRecorder, value: int, timeout: float
) -> bool:
    """Helligkeit setzen und auf das passende StateEvent warten."""
    waiter = recorder.expect_state("brightness", value)  # vor dem Senden registrieren
    await light.set_brightness(value)
    return await wait_for(waiter, timeout)


async def wait_for_emersion(light: Nanoleaf, mode: str, timeout: float) -> bool:
    """Modus pollen, bis das Gerät ihn meldet (Read-after-write-Latenz)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        await light.get_emersion()
        if light.emersion == mode:
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.5)


async def check_brightness(
    light: Nanoleaf,
    recorder: EventRecorder,
    task: asyncio.Task[None],
    res: Result,
    delta: int,
    timeout: float,
) -> None:
    out = res.out
    out.step("Helligkeit ändern und StateEvent abwarten")
    original = light.brightness
    target = pick_brightness_target(light, delta)
    if target == original:
        res.error(
            f"Kein Testwert möglich (Helligkeit {original}, "
            f"Bereich {light.brightness_min}-{light.brightness_max})"
        )
        return
    out.info(f"Ausgangswert {original}, Testwert {target}")

    mutated = False
    try:
        mutated = True  # ab dem Senden gilt der Wert als verändert
        received = await set_brightness_and_wait(light, recorder, target, timeout)
        if not received:
            reason = (
                describe_listener_failure(task)
                if task.done()
                else (
                    f"kein StateEvent innerhalb {timeout:g}s "
                    f"(empfangen: {recorder.seen or 'nichts'})"
                )
            )
            res.error(f"StateEvent brightness={target} nicht angekommen: {reason}")
        else:
            res.check(True, f"StateEvent brightness={target} im Callback empfangen", "")
            res.check(
                light.brightness == target,
                f"Interner Zustand aktualisiert (brightness={light.brightness})",
                f"Interner Zustand nicht aktualisiert (brightness={light.brightness}, "
                f"erwartet {target})",
            )
    except NanoleafError as err:
        res.error(f"Helligkeit setzen fehlgeschlagen: {type(err).__name__}: {err}")
    finally:
        if mutated:
            out.step("Helligkeit zurücksetzen")
            try:
                restored = await set_brightness_and_wait(
                    light, recorder, original, timeout
                )
                res.check(
                    restored and light.brightness == original,
                    f"Ausgangswert {original} wiederhergestellt (Event empfangen)",
                    f"Zurücksetzen auf {original} nicht bestätigt "
                    "- bitte Helligkeit am Gerät prüfen",
                )
            except NanoleafError as err:
                res.error(
                    f"Zurücksetzen fehlgeschlagen ({type(err).__name__}: {err}) "
                    f"- bitte Helligkeit am Gerät manuell auf {original} stellen"
                )


async def check_emersion(
    light: Nanoleaf, recorder: EventRecorder, res: Result, timeout: float
) -> None:
    """NL69: Screen-Mirror-Modus lesen, wechseln, prüfen, zurückstellen."""
    out = res.out
    out.step("NL69: Screen-Mirroring-Modus (4D)")
    original_effect, original_on = light.effect, light.is_on

    try:
        await light.get_emersion()
    except NanoleafError as err:
        res.error(f"get_emersion() fehlgeschlagen: {type(err).__name__}: {err}")
        return
    original = light.emersion
    out.info(f"Aktueller Modus: {original!r}, verfügbar: {light.emersion_list}")
    if not res.check(
        list(light.emersion_list) == list(EMERSION_NAMES),
        "emersion_list enthält 1D, 2D, 3D, 4D",
        f"emersion_list unerwartet: {light.emersion_list}",
    ):
        return
    if not res.check(
        original in EMERSION_NAMES,
        f"Ausgangsmodus erkannt: {original}",
        f"Ausgangsmodus nicht erkannt ({original!r}) - Wechsel wird nicht getestet",
    ):
        return

    target = "2D" if original != "2D" else "3D"
    effect_waiter = (
        recorder.expect_effect(EMERSION_EFFECT)
        if original_effect != EMERSION_EFFECT
        else None
    )
    switched = False
    try:
        switched = True
        await light.set_emersion(target)
        res.check(
            await wait_for_emersion(light, target, timeout),
            f"Moduswechsel {original} -> {target} greift",
            f"Modus ist nach {timeout:g}s nicht {target} (gemeldet: {light.emersion!r})",
        )
        if effect_waiter is not None:
            if await wait_for(effect_waiter, timeout):
                res.check(True, f"EffectsEvent {EMERSION_EFFECT} empfangen", "")
            else:
                out.warn(
                    f"Kein EffectsEvent {EMERSION_EFFECT} innerhalb {timeout:g}s "
                    f"(empfangen: {recorder.seen or 'nichts'})"
                )
    except NanoleafError as err:
        res.error(f"Moduswechsel fehlgeschlagen: {type(err).__name__}: {err}")
    finally:
        if switched:
            out.step("Screen-Mirroring-Zustand zurückstellen")
            await restore_emersion_state(
                light, res, original, original_effect, original_on, timeout
            )


async def restore_emersion_state(
    light: Nanoleaf,
    res: Result,
    original: str,
    original_effect: str,
    original_on: bool,
    timeout: float,
) -> None:
    out = res.out
    try:
        await light.set_emersion(original)
        res.check(
            await wait_for_emersion(light, original, timeout),
            f"Ausgangsmodus {original} wiederhergestellt",
            f"Ausgangsmodus {original} nicht bestätigt - bitte am Gerät prüfen",
        )
        if original_effect != EMERSION_EFFECT:
            if original_effect in light.effects_list:
                await light.set_effect(original_effect)
                out.ok(f"Ausgangseffekt {original_effect!r} wiederhergestellt")
            else:
                out.warn(
                    f"Ausgangseffekt {original_effect!r} nicht wiederherstellbar "
                    "(nicht in der Effektliste) - bitte am Gerät prüfen"
                )
        if not original_on:
            await light.turn_off()
            out.ok("Gerät wieder ausgeschaltet (war vorher aus)")
    except NanoleafError as err:
        res.error(
            f"Zurückstellen fehlgeschlagen ({type(err).__name__}: {err}) "
            "- bitte Modus/Effekt am Gerät manuell prüfen"
        )


async def check_cancel(
    light: Nanoleaf,
    task: asyncio.Task[None],
    baseline: set[asyncio.Task[Any]],
    leaked_errors: list[str],
    res: Result,
) -> None:
    out = res.out
    out.step("Listener canceln und aufräumen")
    task.cancel()
    _, pending = await asyncio.wait({task}, timeout=CANCEL_TIMEOUT)
    if pending:
        res.error(f"Listener beendet sich {CANCEL_TIMEOUT:g}s nach cancel() nicht")
        return
    res.check(
        task.cancelled(),
        "Listener sauber beendet (CancelledError durchgereicht)",
        "Listener nicht als cancelled beendet: "
        + (describe_listener_failure(task) if not task.cancelled() else ""),
    )

    await asyncio.sleep(0.2)  # Done-Callbacks und Cleanup ablaufen lassen
    current = asyncio.current_task()
    leaked = [
        t
        for t in asyncio.all_tasks()
        if t not in baseline and t is not current and not t.done()
    ]
    res.check(
        not leaked,
        "Keine übrig gebliebenen Tasks",
        f"{len(leaked)} Task(s) laufen weiter: {[t.get_name() for t in leaked]}",
    )
    background = getattr(light, "_background_tasks", None)
    if background is not None:
        res.check(
            not background,
            "Callback-Task-Set der Library ist leer",
            f"{len(background)} Callback-Task(s) noch im Set",
        )
    res.check(
        not leaked_errors,
        "Keine unbehandelten Task-Exceptions",
        f"Unbehandelte Exceptions: {leaked_errors}",
    )


async def check_device(
    light: Nanoleaf, out: Console, *, delta: int, timeout: float
) -> Result:
    """Alle Prüfungen für ein bereits autorisiertes Gerät."""
    res = Result(out)
    loop = asyncio.get_running_loop()
    leaked_errors: list[str] = []
    previous_handler = loop.get_exception_handler()

    def handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        leaked_errors.append(str(context.get("exception") or context.get("message")))

    loop.set_exception_handler(handler)
    baseline = set(asyncio.all_tasks())
    task: asyncio.Task[None] | None = None
    recorder = EventRecorder()
    try:
        out.step("Geräteinformationen lesen")
        try:
            await light.get_info()
        except NanoleafError as err:
            res.error(f"get_info() fehlgeschlagen: {type(err).__name__}: {err}")
            return res
        out.info(
            f"Modell {light.model}, Firmware {light.firmware_version}, {light.name}"
        )
        if not light.supports_events:
            out.warn("Gerät unterstützt keine Events (z. B. Essentials) - übersprungen")
            res.skipped = True
            return res
        out.ok("Gerät unterstützt Events")

        out.step("Listener starten")
        task = asyncio.create_task(
            light.listen_events(
                state_callback=recorder.on_state, effects_callback=recorder.on_effect
            ),
            name="smoke-listen-events",
        )
        await asyncio.sleep(CONNECT_GRACE)
        if task.done():
            res.error(describe_listener_failure(task))
            return res
        out.ok("Listener läuft")

        await check_brightness(light, recorder, task, res, delta, timeout)
        if light.model == "NL69" and not task.done():
            await check_emersion(light, recorder, res, timeout)

        if task.done():
            res.error(describe_listener_failure(task))
        else:
            await check_cancel(light, task, baseline, leaked_errors, res)
        return res
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=CANCEL_TIMEOUT)
        loop.set_exception_handler(previous_handler)


async def run_device(
    session: ClientSession,
    host: str,
    token: str | None,
    pair: bool,
    out: Console,
    delta: int,
    timeout: float,
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
        return await check_device(light, out, delta=delta, timeout=timeout)
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
    parser.add_argument("--delta", type=int, default=10, help="Helligkeitsschritt (10)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Event-Timeout in s")
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
                    session, host, args.token, args.pair, out, args.delta, args.timeout
                )
            )

    failed = sum(1 for r in results if r.failures)
    skipped = sum(1 for r in results if r.skipped)
    out.title("Gesamtergebnis")
    if failed:
        out.fail(f"{failed} von {len(results)} Gerät(en) mit Fehlern")
        return 1
    suffix = f" ({skipped} übersprungen)" if skipped else ""
    out.ok(f"Alle Geräte OK{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
