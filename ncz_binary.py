# Jeomatik NCZ Reader
# Copyright (C) 2026 Erdinç Örsan ÜNAL
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .ncz_pure import parse_ncz


@dataclass
class NCZCoordinate:
    x: float
    y: float
    z: float = 0.0


@dataclass
class NCZEntity:
    geometry_kind: str
    layer_code: int
    layer_name: str = ""
    color_argb: Optional[int] = None
    name: str = ""
    label_text: str = ""
    text_height: float = 0.0
    rotation_degrees: float = 0.0
    box_width: float = 0.0
    box_height: float = 0.0
    scale: float = 0.0
    grid_x: float = 0.0
    grid_y: float = 0.0
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0
    is_closed: bool = False
    coordinates: list[NCZCoordinate] = field(default_factory=list)


@dataclass
class NCZAttributeRow:
    row_index: int
    columns: dict[str, object] = field(default_factory=dict)


@dataclass
class NCZAttributeTable:
    table_ref: str
    rows: list[NCZAttributeRow] = field(default_factory=list)


@dataclass
class NCZParseResult:
    entities: list[NCZEntity] = field(default_factory=list)
    attribute_tables: list[NCZAttributeTable] = field(default_factory=list)
    layer_names: list[str] = field(default_factory=list)
    layer_colors: list[int] = field(default_factory=list)
    parser_backend: str = ""
    version_name: str = ""
    epsg: str = ""
    projection_text: str = ""
    unsupported_geometry_types: dict[int, int] = field(default_factory=dict)


class NCZBinaryReader:
    def __init__(self, file_path):
        self.file_path = file_path

    def parse(self):
        payload = parse_ncz(self.file_path)
        if not isinstance(payload, dict):
            raise ValueError("NCZ ayrıştırıcısı geçerli bir sonuç üretemedi.")

        return NCZParseResult(
            entities=[
                self._entity_from_dict(item)
                for item in payload["entities"]
            ],
            attribute_tables=[
                self._attribute_table_from_dict(item)
                for item in payload.get("attribute_tables", [])
            ],
            layer_names=list(payload["layer_names"]),
            layer_colors=list(payload["layer_colors"]),
            parser_backend="pure-python",
            version_name=payload.get("version_name", ""),
            epsg=payload.get("epsg", ""),
            projection_text=payload.get("projection_text", ""),
            unsupported_geometry_types={
                int(key): value
                for key, value in dict(
                    payload.get("unsupported_geometry_types", {})
                ).items()
            },
        )

    def _entity_from_dict(self, payload):
        return NCZEntity(
            geometry_kind=payload["geometry_kind"],
            layer_code=payload["layer_code"],
            layer_name=payload.get("layer_name", ""),
            color_argb=payload.get("color_argb"),
            name=payload.get("name", ""),
            label_text=payload.get("label_text", ""),
            text_height=payload.get("text_height", 0.0),
            rotation_degrees=payload.get("rotation_degrees", 0.0),
            box_width=payload.get("box_width", 0.0),
            box_height=payload.get("box_height", 0.0),
            scale=payload.get("scale", 0.0),
            grid_x=payload.get("grid_x", 0.0),
            grid_y=payload.get("grid_y", 0.0),
            radius=payload.get("radius", 0.0),
            start_angle=payload.get("start_angle", 0.0),
            end_angle=payload.get("end_angle", 0.0),
            is_closed=payload.get("is_closed", False),
            coordinates=[
                NCZCoordinate(x=coord["x"], y=coord["y"], z=coord.get("z", 0.0))
                for coord in payload.get("coordinates", [])
            ],
        )

    def _attribute_table_from_dict(self, payload):
        return NCZAttributeTable(
            table_ref=payload.get("table_ref", ""),
            rows=[
                NCZAttributeRow(
                    row_index=item.get("row_index", 0),
                    columns=dict(item.get("columns", {})),
                )
                for item in payload.get("rows", [])
            ],
        )
