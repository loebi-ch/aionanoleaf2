#!/usr/bin/env python3
"""Diagnose: wann antwortet PUT /state mit HTTP 400? (Befund auf dem NL52 Elements)

Beobachtung: Mit laufendem Event-Stream lehnt das Gerät Zustandsänderungen mit
HTTP 400 ab (Einschalten im ausgeschalteten Zustand), ohne Event-Stream nicht.
Dieses Skript schickt rohe Anfragen (ohne aionanoleaf2, ohne Retry) und zeigt
Status, Dauer, Antwort-Body und den Zustand danach, jeweils ohne und mit
geöffnetem Event-Stream. So sieht man auch, ob ein 400 die Änderung trotzdem
ausgeführt hat.

Nur Standardbibliothek. Es werden nur Strom und Helligkeit verändert und am
Ende wiederhergestellt. Mit --pair wird ein temporäres Token angelegt/gelöscht.

Aufruf:
  python scripts/probe_state_400.py 192.168.1.56 --pair
  python scripts/probe_state_400.py 192.168.1.56 --token TOKEN
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Self

PORT = 16021
TIMEOUT = 10.0


def call(
    method: str, url: str, payload: object | None = None
) -> tuple[int, str, float]:
    """Return (status, body text, seconds); status 0 = no HTTP answer."""
    body = None if payload is None else json.dumps(payload).encode()
    headers = {} if body is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            status, text = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        status, text = err.code, err.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as err:
        status, text = 0, f"{type(err).__name__}: {err}"
    return status, text, time.monotonic() - started


class EventStream:
    """Hält einen SSE-Stream dauerhaft offen (wie der Listener) und zählt Zeilen."""

    def __init__(self, host: str, path: str) -> None:
        self._conn = http.client.HTTPConnection(host, PORT, timeout=5.0)
        self._path = path
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.connected = threading.Event()
        self.lines = 0
        self.ended = False

    def _run(self) -> None:
        try:
            self._conn.request("GET", self._path)
            resp = self._conn.getresponse()
            if resp.status != 200:
                return
            if self._conn.sock is not None:
                self._conn.sock.settimeout(
                    None
                )  # kein Lese-Timeout: Stream bleibt offen
            self.connected.set()
            while resp.readline():
                self.lines += 1
        except (OSError, http.client.HTTPException):
            pass
        finally:
            self.ended = True

    def __enter__(self) -> Self:
        self._thread.start()
        self.connected.wait(timeout=5.0)
        time.sleep(1.0)  # wie CONNECT_GRACE im Smoke-Test
        return self

    def __exit__(self, *exc_info: object) -> None:
        sock = self._conn.sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)  # weckt das blockierende readline()
        self._conn.close()
        self._thread.join(timeout=3.0)


def read_state(base: str) -> tuple[bool | None, int | None]:
    status, text, _ = call("GET", f"{base}/state")
    if status != 200:
        return None, None
    data = json.loads(text)
    return data.get("on", {}).get("value"), data.get("brightness", {}).get("value")


def scenario(base: str, label: str, brightness: int) -> None:
    print(f"\n--- {label} ---")
    print(f"{'Befehl':34} {'Status':>6} {'Dauer':>7}  {'Antwort':20} Zustand danach")
    steps: list[tuple[str, dict[str, object]]] = [
        ("on=false", {"on": {"value": False}}),
        ("on=true", {"on": {"value": True}}),
        (f"brightness={brightness}", {"brightness": {"value": brightness}}),
        ("on=false", {"on": {"value": False}}),
        (f"brightness={brightness} (aus)", {"brightness": {"value": brightness}}),
    ]
    for name, payload in steps:
        status, text, seconds = call("PUT", f"{base}/state", payload)
        time.sleep(0.7)
        is_on, level = read_state(base)
        print(
            f"{name:34} {status:>6} {seconds:>6.2f}s  {text[:20]!r:20} "
            f"an={is_on}, Helligkeit={level}"
        )
        time.sleep(0.7)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("host")
    parser.add_argument("--token")
    parser.add_argument("--pair", action="store_true")
    args = parser.parse_args()
    if not args.pair and not args.token:
        parser.error("--pair oder --token angeben")

    root = f"http://{args.host}:{PORT}/api/v1"
    token = args.token
    paired = False
    if args.pair:
        status, text, _ = call("POST", f"{root}/new")
        if status != 200:
            print(f"Pairing fehlgeschlagen: HTTP {status} {text[:200]}")
            return 2
        token = json.loads(text)["auth_token"]
        paired = True
        print("Temporäres Token erhalten")

    base = f"{root}/{token}"
    original_on, original_brightness = read_state(base)
    print(f"Ausgangszustand: an={original_on}, Helligkeit={original_brightness}")
    test_brightness = 30 if (original_brightness or 0) != 30 else 40
    try:
        scenario(base, "OHNE Event-Stream", test_brightness)
        with EventStream(args.host, f"/api/v1/{token}/events?id=1,3") as stream:
            scenario(base, "MIT offenem Event-Stream", test_brightness)
            print(
                f"\n(Event-Stream: verbunden={stream.connected.is_set()}, "
                f"bis zum Ende offen={not stream.ended}, {stream.lines} Zeilen empfangen)"
            )
    finally:
        call("PUT", f"{base}/state", {"on": {"value": True}})
        time.sleep(0.7)
        if original_brightness is not None:
            call("PUT", f"{base}/state", {"brightness": {"value": original_brightness}})
        time.sleep(0.7)
        if original_on is False:
            call("PUT", f"{base}/state", {"on": {"value": False}})
        print(
            f"\nZustand zurückgesetzt (an={original_on}, Helligkeit={original_brightness})"
        )
        if paired:
            status, _, _ = call("DELETE", f"{base}/")
            print(f"Temporäres Token gelöscht: HTTP {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
