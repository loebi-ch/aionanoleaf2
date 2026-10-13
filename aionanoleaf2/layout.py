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

"""Nanoleaf layout."""

from __future__ import annotations

from dataclasses import dataclass

from .typing import PositionData


@dataclass(frozen=True)
class Shape:
    """Panel shape."""

    name: str
    side_length: int | None


LIGHT_PANELS_TRIANGLE = Shape("Triangle", 150)
LIGHT_PANELS_RHYTHM = Shape("Rhythm", None)
CANVAS_SQUARE = Shape("Square", 100)
CANVAS_CONTROL_SQUARE_MASTER = Shape("Control Square Master", 100)
CANVAS_CONTROL_SQUARE_PASSIVE = Shape("Control Square Passive", 100)
SHAPES_HEXAGON = Shape("Hexagon (Shapes)", 67)
SHAPES_TRIANGLE = Shape("Triangle (Shapes)", 134)
SHAPES_MINI_TRIANGLE = Shape("Mini Triangle (Shapes)", 67)
SHAPES_CONTROLLER = Shape("Shapes Controller", None)
ELEMENTS_HEXAGONS = Shape("Elements Hexagons", 134)
ELEMENTS_HEXAGONS_CORNER = Shape("Elements Hexagons - Corner", None)
LINES_CONNECTOR = Shape("Lines Connector", 11)
LINES_LIGHT = Shape("Light Lines", 154)
LINES_LIGHT_SINGLE_ZONE = Shape("Light Lines - Single Zone", 77)
LINES_CONTROLLER_CAP = Shape("Controller Cap", 11)
LINES_POWER_CONNECTOR = Shape("Power Connector", 11)


class Panel:
    """Nanoleaf panel."""

    def __init__(self, panel_data: PositionData) -> None:
        """Init Nanoleaf panel."""
        self._id = panel_data["panelId"]
        self._x_coordinate = panel_data["x"]
        self._y_coordinate = panel_data["y"]
        self._orientation = panel_data["o"]
        self._shape_type_id = panel_data["shapeType"]

    @property
    def id(self) -> int:
        """Return the ID."""
        return self._id

    @property
    def x_coordinate(self) -> int:
        """Return the x coordinate."""
        return self._x_coordinate

    @property
    def y_coordinate(self) -> int:
        """Return the y coordinate."""
        return self._y_coordinate

    @property
    def orientation(self) -> int:
        """Return the orientation."""
        return self._orientation

    @property
    def shape(self) -> Shape:
        """Return the shape."""
        return {
            0: LIGHT_PANELS_TRIANGLE,
            1: LIGHT_PANELS_RHYTHM,
            2: CANVAS_SQUARE,
            3: CANVAS_CONTROL_SQUARE_MASTER,
            4: CANVAS_CONTROL_SQUARE_PASSIVE,
            7: SHAPES_HEXAGON,
            8: SHAPES_TRIANGLE,
            9: SHAPES_MINI_TRIANGLE,
            12: SHAPES_CONTROLLER,
            14: ELEMENTS_HEXAGONS,
            15: ELEMENTS_HEXAGONS_CORNER,
            16: LINES_CONNECTOR,
            17: LINES_LIGHT,
            18: LINES_LIGHT_SINGLE_ZONE,
            19: LINES_CONTROLLER_CAP,
            20: LINES_POWER_CONNECTOR,
        }.get(self._shape_type_id, Shape(str(self._shape_type_id), None))
