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

"""Nanoleaf exceptions.

Hierarchy::

    NanoleafError                      (alias: NanoleafException)
    ├── AuthError                      -> reauth
    │   └── InvalidToken
    ├── Unauthorized                   -> pairing mode not active (NOT reauth)
    ├── NoAuthToken                    -> client misuse
    ├── Unavailable                    -> device unreachable / unusable
    │   ├── NanoleafConnectionError
    │   └── NanoleafTimeoutError
    ├── InvalidResponseError           -> unexpected payload / content type
    ├── EndpointNotFoundError          -> HTTP 404, optional endpoint missing
    ├── InvalidEffect (ValueError)
    └── InvalidEmersion (ValueError)
"""

from __future__ import annotations

__all__ = [
    "AuthError",
    "EndpointNotFoundError",
    "InvalidEffect",
    "InvalidEmersion",
    "InvalidResponseError",
    "InvalidToken",
    "NanoleafConnectionError",
    "NanoleafError",
    "NanoleafException",
    "NanoleafTimeoutError",
    "NoAuthToken",
    "Unauthorized",
    "Unavailable",
]


class NanoleafError(Exception):
    """Root of all aionanoleaf2 exceptions."""


# Backwards-compatible alias (<= 1.1.0). Deprecated, to be removed in 2.0.0.
NanoleafException = NanoleafError


class AuthError(NanoleafError):
    """The device rejected our credentials."""


class InvalidToken(AuthError):
    """Invalid token specified (HTTP 401)."""


class Unauthorized(NanoleafError):
    """The device is not accepting new tokens (pairing mode inactive).

    Deliberately NOT an AuthError: this is a user-action-required condition
    during setup and must never trigger a reauth flow.
    """


class NoAuthToken(NanoleafError):
    """No auth_token specified."""


class Unavailable(NanoleafError):
    """Device is unavailable (transport failure or unusable HTTP status)."""


class NanoleafConnectionError(Unavailable):
    """Connection to the device could not be established or was lost."""


class NanoleafTimeoutError(Unavailable):
    """The device did not answer within the configured timeout."""


class InvalidResponseError(NanoleafError):
    """The device answered, but not with a usable JSON payload."""


class EndpointNotFoundError(NanoleafError):
    """The device answered HTTP 404 for an endpoint.

    Intentionally not an Unavailable: the device is reachable, the endpoint
    just does not exist on this model/firmware (e.g. Essentials).
    """

    def __init__(self, path: str, message: str | None = None) -> None:
        super().__init__(message or f"Endpoint not found: '{path}'")
        self.path: str = path


class InvalidEffect(NanoleafError, ValueError):
    """Invalid effect specified."""


class InvalidEmersion(NanoleafError, ValueError):
    """Invalid emersion specified."""
