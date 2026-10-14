#!/usr/bin/env python3
"""Read-only probe: zeigt den echten HTTP-Status aller Endpunkte, die aionanoleaf2 abfragt.

Nur Standardbibliothek, keine Abhängigkeit von aionanoleaf2 oder aiohttp, kein Retry.

Aufruf:
  python probe_endpoints.py HOST --token TOKEN
  python probe_endpoints.py HOST --pair            # holt zuerst ein neues Token (Pairing-Modus nötig)
  python probe_endpoints.py HOST --pair --cleanup  # ... und löscht dieses Token am Ende wieder
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# Von aionanoleaf2 1.2.0 nur bei HTTP 404 ignoriert; jeder andere Fehlerstatus lässt get_info scheitern.
OPTIONAL = {"state", "effects/effectsList", "effects/select"}
PATHS = ["", "state", "effects/effectsList", "effects/select"]


def call(method: str, url: str) -> tuple[int | None, str, str]:
    """Return (status, content_type, body). status is None on connection error/timeout."""
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return (
                resp.status,
                resp.headers.get("Content-Type", ""),
                resp.read().decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as err:
        return (
            err.code,
            err.headers.get("Content-Type", ""),
            err.read().decode("utf-8", "replace"),
        )
    except (urllib.error.URLError, TimeoutError) as err:
        return None, "", str(err)


def shape(body: str) -> str:
    """Kurze Beschreibung des Bodys: Typ und gekürzter Inhalt."""
    if not body.strip():
        return "LEER"
    try:
        data = json.loads(body)
    except ValueError:
        return f"KEIN JSON: {body[:80]!r}"
    text = json.dumps(data, ensure_ascii=False)
    return f"{type(data).__name__}: {text[:120]}{'...' if len(text) > 120 else ''}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=16021)
    ap.add_argument("--token")
    ap.add_argument("--pair", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    host = (
        f"[{args.host}]"
        if ":" in args.host and not args.host.startswith("[")
        else args.host
    )
    base = f"http://{host}:{args.port}/api/v1"
    token = args.token

    if args.pair:
        status, _, body = call("POST", f"{base}/new")
        print(f"POST /new -> {status}")
        if status != 200:
            print("Kein Token erhalten (Pairing-Modus aktiv? 403 = nein).")
            return 1
        token = json.loads(body)["auth_token"]
        print("Token erhalten (wird nicht angezeigt).")
    if not token:
        ap.error("--token oder --pair angeben")

    worst = 0
    print(f"\n{'Endpunkt':<24}{'Status':<8}{'Content-Type':<28}Bewertung")
    for path in PATHS:
        status, ctype, body = call("GET", f"{base}/{token}/{path}")
        name = "/" + path
        if status == 200:
            verdict = "ok"
            if path == "":
                try:
                    keys = json.loads(body)
                    has = {
                        k: (k in keys and keys[k] is not None)
                        for k in ("state", "effects", "panelLayout")
                    }
                    effects = keys.get("effects") or {}
                    verdict = (
                        f"ok | Root enthält: {has} | effects.effectsList={'effectsList' in effects}, "
                        f"effects.select={'select' in effects} | model={keys.get('model')}"
                    )
                except (ValueError, AttributeError):
                    verdict = "ok, aber Root ist kein JSON-Objekt"
        elif status is None:
            verdict = f"VERBINDUNGSFEHLER: {body}"
            worst = max(worst, 2)
        elif status == 404 and path in OPTIONAL:
            verdict = "404 -> von 1.2.0 toleriert"
        elif path in OPTIONAL:
            verdict = f"!! STATUS {status} -> 1.2.0 wirft hier Unavailable, get_info schlägt fehl"
            worst = max(worst, 2)
        else:
            verdict = f"!! unerwarteter Status {status}"
            worst = max(worst, 2)
        print(f"{name:<24}{status!s:<8}{ctype[:26]:<28}{verdict}")
        if path:  # Root enthält Seriennummer/Name -> nur Unterendpunkte ausgeben
            print(f"{'':<24}Body ({len(body)} Bytes): {shape(body)}")

    if args.pair and args.cleanup:
        status, _, _ = call("DELETE", f"{base}/{token}/")
        print(f"\nCleanup: DELETE token -> {status}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
