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


from .events import EffectsEvent, LayoutEvent, StateEvent, TouchEvent, TouchStreamEvent
from .exceptions import (
    AuthError,
    EndpointNotFoundError,
    InvalidEffect,
    InvalidEmersion,
    InvalidResponseError,
    InvalidToken,
    NanoleafConnectionError,
    NanoleafError,
    NanoleafException,
    NanoleafTimeoutError,
    NoAuthToken,
    Unauthorized,
    Unavailable,
)
from .layout import (
    Panel,
    Shape,
)
from .nanoleaf import Nanoleaf

__all__ = [
    "AuthError",
    "EffectsEvent",
    "EndpointNotFoundError",
    "InvalidEffect",
    "InvalidEmersion",
    "InvalidResponseError",
    "InvalidToken",
    "LayoutEvent",
    "Nanoleaf",
    "NanoleafConnectionError",
    "NanoleafError",
    "NanoleafException",
    "NanoleafTimeoutError",
    "NoAuthToken",
    "Panel",
    "Shape",
    "StateEvent",
    "TouchEvent",
    "TouchStreamEvent",
    "Unauthorized",
    "Unavailable",
]
