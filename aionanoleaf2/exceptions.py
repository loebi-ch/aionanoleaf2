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

"""Nanoleaf exceptions."""

class NanoleafException(Exception):
    """General Nanoleaf exception."""

class InvalidEffect(NanoleafException, ValueError):
    """Invalid effect specified."""

class InvalidEmersion(NanoleafException, ValueError):
    """Invalid emersion specified."""

class InvalidToken(NanoleafException):
    """Invalid token specified."""

class NoAuthToken(NanoleafException):
    """No auth_token specified."""

class Unauthorized(NanoleafException):
    """Not authorizing new tokens."""

class Unavailable(NanoleafException):
    """Device is unavailable."""
