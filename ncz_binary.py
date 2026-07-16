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

from dataclasses import dataclass, field
import json
import os
import subprocess
from typing import Optional

from .ncz_pure import parse_ncz as parse_ncz_pure

try:
    from . import _ncz_native
except ImportError as exc:
    _ncz_native = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


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
        payload = parse_ncz_pure(self.file_path)
        backend = "pure-python"

        if payload is None:
            payload = self._parse_with_native_module()
            if payload is not None:
                backend = "native-module"

        if payload is None:
            payload = self._parse_with_cli()
            if payload is not None:
                backend = "native-cli"

        return NCZParseResult(
            entities=[self._entity_from_dict(item) for item in payload["entities"]],
            attribute_tables=[self._attribute_table_from_dict(item) for item in payload.get("attribute_tables", [])],
            layer_names=list(payload["layer_names"]),
            layer_colors=list(payload["layer_colors"]),
            parser_backend=backend,
            version_name=payload.get("version_name", ""),
            epsg=payload.get("epsg", ""),
            projection_text=payload.get("projection_text", ""),
            unsupported_geometry_types={
                int(key): value
                for key, value in dict(payload.get("unsupported_geometry_types", {})).items()
            },
        )

    def _parse_with_native_module(self):
        if _ncz_native is None:
            return None
        return _ncz_native.parse(self.file_path)

    def _parse_with_cli(self):
        cli_path = os.path.join(os.path.dirname(__file__), "ncz_native_cli")
        if not os.path.exists(cli_path):
            return None

        try:
            completed = subprocess.run(
                [cli_path, self.file_path],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            return None
        except OSError:
            return None

        return json.loads(completed.stdout)

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
