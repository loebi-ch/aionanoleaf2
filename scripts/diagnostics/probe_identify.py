#!/usr/bin/env python3
"""Read-only probe: warum antwortet identify() mit HTTP 400?

Sendet PUT /identify in mehreren Varianten und zeigt Status und Antwort-Body.
Nur Standardbibliothek. Das Gerät blinkt bei Erfolg kurz; es wird sonst nichts
verändert. Mit --pair wird ein temporäres Token angelegt und am Ende gelöscht.

Aufruf:
  python scripts/probe_identify.py 192.168.1.57 --pair
  python scripts/probe_identify.py 192.168.1.57 --token TOKEN
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PORT = 16021
TIMEOUT = 5.0


def call(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Return (status, content type, body text); status 0 = no HTTP answer."""
    request = urllib.request.Request(
        url, data=body, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
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
        return 0, "", f"{type(err).__name__}: {err}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("host")
    parser.add_argument("--token")
    parser.add_argument("--pair", action="store_true")
    args = parser.parse_args()
    if not args.pair and not args.token:
        parser.error("--pair oder --token angeben")

    base = f"http://{args.host}:{PORT}/api/v1"
    token = args.token
    paired = False
    if args.pair:
        status, _, text = call("POST", f"{base}/new")
        if status != 200:
            print(f"Pairing fehlgeschlagen: HTTP {status} {text[:200]}")
            return 2
        token = json.loads(text)["auth_token"]
        paired = True
        print("Temporäres Token erhalten")

    json_header = {"Content-Type": "application/json"}
    variants = [
        ("PUT ohne Body", "PUT", None, {}),
        ("PUT mit leerem Body (Content-Length: 0)", "PUT", b"", {}),
        ("PUT mit {} (JSON)", "PUT", b"{}", json_header),
        ("POST ohne Body", "POST", b"", {}),
    ]
    try:
        for label, method, body, headers in variants:
            status, ctype, text = call(
                method, f"{base}/{token}/identify", body, headers
            )
            print(f"{label:42} -> HTTP {status}  {ctype}  {text[:300]!r}")
    finally:
        if paired:
            status, _, _ = call("DELETE", f"{base}/{token}/")
            print(f"Temporäres Token gelöscht: HTTP {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
