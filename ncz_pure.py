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

import math
import struct


BLOCK_TYPE_LAYER_TABLE = 6
BLOCK_TYPE_GEOMETRY = 21
BLOCK_TYPE_GEOMETRY_EXTENDED = 22
BLOCK_TYPE_VERSION = 25
BLOCK_TYPE_NAMED_DATA = 28
EXTENDED_HEADER_SIZE = 28

GEOMETRY_MINIMUM_BYTES = {
    1: 87,
    2: 39,
    3: 74,
    4: 120,
    5: 94,
    6: 95,
    7: 113,
    9: 24,
    10: 124,
    11: 82,
    12: 122,
    13: 122,
    15: 90,
}
EXTENDED_HEADER_GEOMETRY_TYPES = frozenset({1, 4, 5, 6, 7, 9, 10, 13})

EMBEDDED_GEOMETRY_CONTAINER_TYPES = frozenset(
    {0, 5, 14, 48, 108, 111, 132, 150, 180}
)


def parse_ncz(file_path):
    """Parse an NCZ drawing using the platform-independent Python backend."""
    with open(file_path, "rb") as handle:
        return _NCZParser(handle.read()).parse()


class _NCZParser:
    """Stateful NCZ binary parser with isolated block and entity readers."""

    def __init__(self, data):
        self.data = data
        self.layer_names = []
        self.layer_colors = []
        self.entities = []
        self.attribute_tables = []
        self.version_name = ""
        self.epsg = ""
        self.projection_text = ""
        self.unsupported = {}

    def parse(self):
        self._scan_blocks()
        self._finalize_entities()
        self.attribute_tables = self._extract_attribute_tables()
        return self._build_result()

    def _scan_blocks(self):
        cursor = 0
        while cursor + 5 < len(self.data):
            block_size = self._read_uint32(cursor + 1) + 4
            total_block_size = block_size + 1
            if block_size < 4 or cursor + total_block_size > len(self.data):
                cursor += 1
                continue
            block_type = self.data[cursor]
            if block_type == BLOCK_TYPE_VERSION and not self.version_name:
                self.version_name = self._read_legacy_string(
                    cursor + 6,
                    self.data[cursor + 5],
                )
            elif block_type == BLOCK_TYPE_NAMED_DATA:
                self._parse_named_data_block(cursor, block_size)
            elif block_type == BLOCK_TYPE_LAYER_TABLE:
                self._parse_layer_table(cursor, block_size)
            elif (
                block_type
                in (BLOCK_TYPE_GEOMETRY, BLOCK_TYPE_GEOMETRY_EXTENDED)
                and block_size >= 7
            ):
                extended_header_size = (
                    EXTENDED_HEADER_SIZE
                    if block_type == BLOCK_TYPE_GEOMETRY_EXTENDED
                    else 0
                )
                self._parse_geometry(cursor, block_size, extended_header_size)
            elif block_type in EMBEDDED_GEOMETRY_CONTAINER_TYPES:
                self._parse_embedded_geometry(cursor, block_size)
            cursor += total_block_size

    def _parse_named_data_block(self, offset, block_size):
        block_end = min(len(self.data), offset + block_size + 1)
        if offset + 6 > block_end:
            return

        block_name = self._read_legacy_string(
            offset + 6,
            self.data[offset + 5],
        )
        if block_name == "MPROJ" and offset + 22 <= block_end:
            projection = {1: "Geographic", 2: "6", 3: "3"}.get(
                self.data[offset + 16],
                "Undefined",
            )
            datum = {
                0: "WGS-84",
                1: "ITRF",
                4: "ED50",
                254: "ED50-HGK",
            }.get(self.data[offset + 17], "Undefined")
            self.projection_text = (
                f"{datum} / {projection} / Zone {self.data[offset + 21]}"
            )
        elif block_name == "TILED_XML":
            self.epsg = self._read_epsg(offset, block_end - offset)
        elif block_name == "LEX.ST2" and offset + 21 <= block_end:
            layer_count = self.data[offset + 20]
            for index in range(layer_count):
                item_offset = offset + 79 + (index * 256)
                if item_offset + 3 > block_end:
                    break
                self.layer_colors.append(
                    self._to_argb(
                        self.data[item_offset],
                        self.data[item_offset + 1],
                        self.data[item_offset + 2],
                    )
                )

    def _parse_layer_table(self, offset, block_size):
        block_end = min(len(self.data), offset + block_size + 1)
        if offset + 18 > block_end:
            return

        layer_count = self.data[offset + 16] + self.data[offset + 17] * 256
        for index in range(layer_count):
            item_offset = offset + 18 + (index * 29)
            if item_offset + 29 > block_end:
                break
            layer_name = self._read_legacy_string(
                item_offset + 5,
                self.data[item_offset + 4],
            )
            if layer_name.strip():
                self.layer_names.append(layer_name)

    def _finalize_entities(self):
        if any(
            entity["geometry_kind"] == "SmartObject"
            for entity in self.entities
        ):
            self.entities = [
                entity
                for entity in self.entities
                if not (
                    entity["geometry_kind"] == "Symbol"
                    and entity["layer_code"] == 0
                    and entity.get("label_text") == "S0"
                )
            ]

        for entity in self.entities:
            if not entity["layer_name"]:
                entity["layer_name"] = self._layer_name(entity["layer_code"])
            if entity["color_argb"] is None:
                entity["color_argb"] = self._geometry_color(
                    entity["layer_code"],
                    0,
                )

    def _build_result(self):
        return {
            "entities": self.entities,
            "attribute_tables": self.attribute_tables,
            "layer_names": self.layer_names,
            "layer_colors": self.layer_colors,
            "version_name": self.version_name,
            "epsg": self.epsg,
            "projection_text": self.projection_text,
            "unsupported_geometry_types": self.unsupported,
        }

    def _parse_geometry(self, offset, block_size, extended_header_size):
        if block_size < 7 or offset + 6 >= len(self.data):
            return
        geometry_type = self.data[offset + 6]
        minimum_size = GEOMETRY_MINIMUM_BYTES.get(geometry_type)
        if minimum_size is not None:
            if geometry_type in EXTENDED_HEADER_GEOMETRY_TYPES:
                minimum_size += extended_header_size
            if block_size + 1 < minimum_size:
                return
        if geometry_type == 1:
            self._parse_point(offset, extended_header_size)
        elif geometry_type == 2:
            self._parse_line(offset, block_size)
        elif geometry_type == 3:
            self._parse_circle(offset)
        elif geometry_type == 4:
            self._parse_arc(offset, extended_header_size)
        elif geometry_type == 5:
            self._parse_text(offset, extended_header_size)
        elif geometry_type == 6:
            self._parse_symbol(offset, block_size, extended_header_size)
        elif geometry_type == 7:
            self._parse_multiline(offset, block_size, extended_header_size)
        elif geometry_type == 9:
            self._parse_compressed_curve(offset, block_size, extended_header_size)
        elif geometry_type == 10:
            self._parse_box(offset, block_size, extended_header_size)
        elif geometry_type == 11:
            self._parse_map_sheet(offset, block_size)
        elif geometry_type == 12:
            self._parse_triangle(offset, block_size)
        elif geometry_type == 13:
            self._parse_block_reference(offset, block_size, extended_header_size)
        elif geometry_type == 15:
            self._parse_smart_object(offset, block_size)
        else:
            self.unsupported[geometry_type] = self.unsupported.get(geometry_type, 0) + 1

    def _parse_embedded_geometry(self, offset, block_size):
        cursor = offset + 5
        end = min(len(self.data), offset + block_size)
        while cursor + 6 < end:
            is_geometry = self.data[cursor] in (
                BLOCK_TYPE_GEOMETRY,
                BLOCK_TYPE_GEOMETRY_EXTENDED,
            )
            has_matching_type = self.data[cursor + 5] == self.data[cursor + 6]
            if not is_geometry or not has_matching_type:
                cursor += 1
                continue
            inner_block_size = self._read_uint32(cursor + 1) + 4
            total_inner = inner_block_size + 1
            if inner_block_size < 7 or cursor + total_inner > end:
                cursor += 1
                continue
            extended_header_size = (
                EXTENDED_HEADER_SIZE
                if self.data[cursor] == BLOCK_TYPE_GEOMETRY_EXTENDED
                else 0
            )
            self._parse_geometry(cursor, inner_block_size, extended_header_size)
            cursor += total_inner

    def _parse_point(self, offset, extended_header_size):
        layer_code = self.data[offset + 7]
        raw_x = self._read_float64(offset + 8)
        raw_y = self._read_float64(offset + 16)
        z = self._read_float32(offset + 24)
        if z == 0:
            z = self._read_float32(offset + 28)
        if not self._valid_xy(raw_x, raw_y):
            return
        name_length = self.data[offset + extended_header_size + 86]
        self._append_entity(
            "Point",
            layer_code,
            self.data[offset + 37],
            [self._coordinate(raw_x, raw_y, z)],
            name=self._read_legacy_string(
                offset + extended_header_size + 87,
                name_length,
            ),
        )

    def _parse_line(self, offset, block_size):
        layer_code = self.data[offset + 7]
        raw_x1 = self._read_float64(offset + 8)
        raw_y1 = self._read_float64(offset + 16)
        z1 = self._read_float32(offset + 24)
        raw_x2 = self._read_float64(offset + block_size - 19)
        raw_y2 = self._read_float64(offset + block_size - 11)
        z2 = self._read_float32(offset + block_size - 3)
        if not self._valid_xy(raw_x1, raw_y1) or not self._valid_xy(
            raw_x2,
            raw_y2,
        ):
            return
        self._append_entity(
            "Line",
            layer_code,
            self.data[offset + 37],
            [
                self._coordinate(raw_x1, raw_y1, z1),
                self._coordinate(raw_x2, raw_y2, z2),
            ],
        )

    def _parse_multiline(self, offset, block_size, extended_header_size):
        layer_code = self.data[offset + 7]
        text_length = self.data[offset + extended_header_size + 86]
        text = self._read_legacy_string(offset + extended_header_size + 87, text_length)
        point_count = (block_size + 1 - 113 - extended_header_size) // 24
        if point_count < 2:
            return
        block_end = min(len(self.data), offset + block_size + 1)
        coordinates = []
        for index in range(point_count):
            coordinate_offset = index * 24 + (offset + extended_header_size + 113)
            if coordinate_offset + 24 > block_end:
                break
            coordinates.append(
                self._coordinate(
                    self._read_float64(coordinate_offset),
                    self._read_float64(coordinate_offset + 8),
                    self._read_float64(coordinate_offset + 16),
                )
            )
        if len(coordinates) < 2:
            return
        is_closed = self._is_nearly_closed(coordinates)
        if is_closed and (not self._same_coordinate(coordinates[0], coordinates[-1])):
            first = coordinates[0]
            coordinates.append(
                {"x": first["x"], "y": first["y"], "z": first["z"]}
            )
        is_box, box_width, box_height, box_rotation, coordinates = self._box_metrics(coordinates)
        self._append_entity(
            "Polygon" if is_closed else "Polyline",
            layer_code,
            self.data[offset + 37],
            coordinates,
            label_text=text,
            is_closed=is_closed,
            box_width=box_width,
            box_height=box_height,
            rotation_degrees=box_rotation if is_box else 0.0,
        )

    def _parse_compressed_curve(self, offset, block_size, extended_header_size):
        origin_x = self._read_float64(offset + 8)
        origin_y = self._read_float64(offset + 16)
        if not self._valid_xy(origin_x, origin_y):
            return
        point_data_offset = offset + extended_header_size + 122
        end_offset = offset + block_size + 1
        if point_data_offset + 8 > end_offset:
            return
        coordinates = []
        invalid_streak = 0
        for record_offset in range(point_data_offset, end_offset - 7, 18):
            delta_x = self._read_float32(record_offset)
            delta_y = self._read_float32(record_offset + 4)
            if not math.isfinite(delta_x) or not math.isfinite(delta_y):
                invalid_streak += 1
                if coordinates and invalid_streak >= 4:
                    break
                continue
            x = origin_x + delta_x
            y = origin_y + delta_y
            if not self._valid_xy(x, y):
                invalid_streak += 1
                if coordinates and invalid_streak >= 4:
                    break
                continue
            invalid_streak = 0
            coord = self._coordinate(x, y, 0.0)
            if coordinates:
                prev = coordinates[-1]
                if abs(prev['x'] - coord['x']) < 0.0001 and abs(prev['y'] - coord['y']) < 0.0001:
                    continue
            coordinates.append(coord)
        if len(coordinates) < 2:
            return
        self._append_entity(
            "Polyline",
            self.data[offset + 7],
            self.data[offset + 37],
            coordinates,
        )

    def _parse_circle(self, offset):
        layer_code = self.data[offset + 7]
        raw_x = self._read_float64(offset + 8)
        raw_y = self._read_float64(offset + 16)
        z = self._read_float32(offset + 24)
        if not self._valid_xy(raw_x, raw_y):
            return
        x2 = self._read_float64(offset + 50)
        x3 = self._read_float64(offset + 66)
        self._append_entity(
            "Circle",
            layer_code,
            self.data[offset + 37],
            [self._coordinate(raw_x, raw_y, z)],
            radius=abs(x2 - x3) / 2.0,
        )

    def _parse_arc(self, offset, extended_header_size):
        layer_code = self.data[offset + 7]
        raw_x = self._read_float64(offset + 8)
        raw_y = self._read_float64(offset + 16)
        z = self._read_float32(offset + 24)
        if not self._valid_xy(raw_x, raw_y):
            return
        self._append_entity(
            "Arc",
            layer_code,
            self.data[offset + 37],
            [self._coordinate(raw_x, raw_y, z)],
            radius=self._read_float64(offset + extended_header_size + 86),
            start_angle=self._read_float64(offset + extended_header_size + 104),
            end_angle=self._read_float64(offset + extended_header_size + 112),
        )

    def _parse_text(self, offset, extended_header_size):
        layer_code = self.data[offset + 7]
        raw_x = self._read_float64(offset + 8)
        raw_y = self._read_float64(offset + 16)
        z = self._read_float32(offset + 24)
        if z == 0:
            z = self._read_float32(offset + 28)
        if not self._valid_xy(raw_x, raw_y):
            return
        text = self._read_text_payload(offset, extended_header_size)
        if not text:
            return
        text_height = self._read_positive_float(offset + extended_header_size + 86)
        if text_height is None:
            text_height = self._read_positive_float(offset + 86)
        if text_height is None:
            return
        rotation_degrees = (
            self._read_float32(offset + extended_header_size + 90)
            * (180.0 / math.pi)
            % 360.0
        )
        self._append_entity(
            "Text",
            layer_code,
            self.data[offset + 37],
            [self._coordinate(raw_x, raw_y, z)],
            label_text=text,
            text_height=text_height,
            rotation_degrees=rotation_degrees,
        )

    def _parse_symbol(self, offset, block_size, extended_header_size):
        layer_code = self.data[offset + 7]
        raw_x = self._read_float64(offset + 8)
        raw_y = self._read_float64(offset + 16)
        z = self._read_float32(offset + 24)
        if not self._valid_xy(raw_x, raw_y):
            return
        block_end = min(len(self.data), offset + block_size + 1)
        symbol_offset = offset + extended_header_size + 94
        if symbol_offset < offset or symbol_offset >= block_end:
            symbol_offset = offset + 94
        symbol_code = (
            self.data[symbol_offset]
            if 0 <= symbol_offset < block_end
            else 0
        )
        symbol_size = self._read_positive_float(offset + extended_header_size + 86)
        if symbol_size is None:
            symbol_size = self._read_positive_float(offset + 86)
        if symbol_size is None:
            symbol_size = 5.0
        rotation_degrees = (
            self._read_float32(offset + extended_header_size + 90)
            * (180.0 / math.pi)
            % 360.0
        )
        self._append_entity(
            "Symbol",
            layer_code,
            self.data[offset + 37],
            [self._coordinate(raw_x, raw_y, z)],
            label_text=f"S{symbol_code}",
            text_height=symbol_size,
            rotation_degrees=rotation_degrees,
        )

    def _parse_block_reference(self, offset, block_size, extended_header_size):
        raw_x = self._read_float64(offset + 8)
        raw_y = self._read_float64(offset + 16)
        z = self._read_float32(offset + 24)
        if not self._valid_xy(raw_x, raw_y):
            return
        block_name = self._read_length_prefixed_name(
            offset + extended_header_size + 86,
            offset + block_size + 1,
        )
        rotation_degrees = (
            self._read_float32(offset + extended_header_size + 118)
            * (180.0 / math.pi)
            % 360.0
        )
        self._append_entity(
            "Block",
            self.data[offset + 7],
            self.data[offset + 37],
            [self._coordinate(raw_x, raw_y, z)],
            label_text=block_name,
            rotation_degrees=rotation_degrees,
        )

    def _parse_box(self, offset, block_size, extended_header_size):
        layer_code = self.data[offset + 7]
        raw_x1 = self._read_float64(offset + 8)
        raw_y1 = self._read_float64(offset + 16)
        raw_x2 = self._read_float64(offset + extended_header_size + 104)
        raw_y2 = self._read_float64(offset + extended_header_size + 112)
        rotation_radians = self._read_float32(offset + extended_header_size + 120)
        if not self._valid_xy(raw_x1, raw_y1) or not self._valid_xy(
            raw_x2,
            raw_y2,
        ):
            return
        width = abs(raw_x2 - raw_x1)
        height = abs(raw_y2 - raw_y1)
        rotation_degrees = rotation_radians * (180.0 / math.pi) % 360.0
        angle_radians = rotation_degrees * (math.pi / 180.0)
        side_x = math.sin(angle_radians)
        side_y = math.cos(angle_radians)
        bottom_x = math.cos(angle_radians)
        bottom_y = -math.sin(angle_radians)
        p0x = raw_x1
        p0y = raw_y1
        p1x = p0x + bottom_x * width
        p1y = p0y + bottom_y * width
        p2x = p1x + side_x * height
        p2y = p1y + side_y * height
        p3x = p0x + side_x * height
        p3y = p0y + side_y * height
        coordinates = [
            self._coordinate(p0x, p0y, 0.0),
            self._coordinate(p1x, p1y, 0.0),
            self._coordinate(p2x, p2y, 0.0),
            self._coordinate(p3x, p3y, 0.0),
            self._coordinate(p0x, p0y, 0.0),
        ]
        self._append_entity(
            "Polygon",
            layer_code,
            self.data[offset + 37],
            coordinates,
            is_closed=True,
            box_width=width,
            box_height=height,
            rotation_degrees=rotation_degrees,
            label_text=self._read_plan_box_name(offset, block_size),
        )

    def _parse_map_sheet(self, offset, block_size):
        layer_code = self.data[offset + 7]
        raw_x1 = self._read_float64(offset + 50)
        raw_y1 = self._read_float64(offset + 58)
        raw_x2 = self._read_float64(offset + 66)
        raw_y2 = self._read_float64(offset + 74)
        if not self._valid_xy(raw_x1, raw_y1) or not self._valid_xy(
            raw_x2,
            raw_y2,
        ):
            return
        min_x = min(raw_x1, raw_x2)
        max_x = max(raw_x1, raw_x2)
        min_y = min(raw_y1, raw_y2)
        max_y = max(raw_y1, raw_y2)
        if abs(max_x - min_x) < 0.001 or abs(max_y - min_y) < 0.001:
            return
        sheet_name = self._read_length_prefixed_name(
            offset + 86,
            offset + block_size + 1,
        )
        coordinates = [
            self._coordinate(min_x, min_y, 0.0),
            self._coordinate(max_x, min_y, 0.0),
            self._coordinate(max_x, max_y, 0.0),
            self._coordinate(min_x, max_y, 0.0),
            self._coordinate(min_x, min_y, 0.0),
        ]
        self._append_entity(
            "MapSheet",
            layer_code,
            self.data[offset + 37],
            coordinates,
            is_closed=True,
            box_width=max_x - min_x,
            box_height=max_y - min_y,
            label_text=sheet_name,
        )

    def _parse_triangle_vertex(self, offset, x_offset, y_offset, z_offset=None):
        if offset + x_offset + 8 > len(self.data) or offset + y_offset + 8 > len(self.data):
            return None
        x = self._read_float64(offset + x_offset)
        y = self._read_float64(offset + y_offset)
        z = 0.0
        if z_offset is not None and offset + z_offset + 4 <= len(self.data):
            z = self._read_float32(offset + z_offset)
        if not self._valid_xy(x, y):
            return None
        return self._coordinate(x, y, z)

    def _parse_triangle(self, offset, block_size):
        a = self._parse_triangle_vertex(offset, 8, 16, 24)
        b = self._parse_triangle_vertex(offset, 86, 94)
        c = self._parse_triangle_vertex(offset, 106, 114)
        if a is None or b is None or c is None:
            return
        area2 = abs(
            (b["x"] - a["x"]) * (c["y"] - a["y"])
            - (b["y"] - a["y"]) * (c["x"] - a["x"])
        )
        if area2 <= 0.0001:
            return
        self._append_entity(
            "Triangle",
            self.data[offset + 7],
            self.data[offset + 37],
            [a, b, c],
        )

    def _parse_smart_object(self, offset, block_size):
        layer_code = self.data[offset + 7]
        block_end = min(len(self.data), offset + block_size + 1)
        raw_x1 = self._read_float64(offset + 8)
        raw_y1 = self._read_float64(offset + 16)
        if not self._valid_xy(raw_x1, raw_y1):
            return
        width = (
            self._read_float64(offset + 169)
            if offset + 177 <= block_end
            else 0.0
        )
        height = (
            self._read_float64(offset + 177)
            if offset + 185 <= block_end
            else 0.0
        )
        grid_x = (
            self._read_float64(offset + 185)
            if offset + 193 <= block_end
            else 0.0
        )
        grid_y = (
            self._read_float64(offset + 193)
            if offset + 201 <= block_end
            else 0.0
        )
        raw_x2 = self._read_float64(offset + 66)
        raw_y2 = self._read_float64(offset + 74)
        if width <= 0.0 or height <= 0.0:
            if not self._valid_xy(raw_x2, raw_y2):
                return
            width = abs(raw_x2 - raw_x1)
            height = abs(raw_y2 - raw_y1)
        if width < 0.001 or height < 0.001:
            return
        angle_grads = self._read_float32(offset + 82)
        rotation_degrees = angle_grads * 0.9 % 360.0 if math.isfinite(angle_grads) else 0.0
        scale = self._read_float32(offset + 86)
        if not math.isfinite(scale):
            scale = 0.0
        angle = math.radians(rotation_degrees)
        bottom_x = math.sin(angle)
        bottom_y = math.cos(angle)
        side_x = math.cos(angle)
        side_y = -math.sin(angle)
        p0x = raw_x1
        p0y = raw_y1
        p1x = p0x + bottom_x * width
        p1y = p0y + bottom_y * width
        p2x = p1x + side_x * height
        p2y = p1y + side_y * height
        p3x = p0x + side_x * height
        p3y = p0y + side_y * height
        smart_payload = self.data[offset:block_end]
        label = (
            "BASIC"
            if b"BASIC" in smart_payload
            else self._read_ascii_token(offset + 145, block_end)
        )
        coordinates = [
            self._coordinate(p0x, p0y, 0.0),
            self._coordinate(p1x, p1y, 0.0),
            self._coordinate(p2x, p2y, 0.0),
            self._coordinate(p3x, p3y, 0.0),
            self._coordinate(p0x, p0y, 0.0),
        ]
        self._append_entity(
            "SmartObject",
            layer_code,
            self.data[offset + 37],
            coordinates,
            is_closed=True,
            box_width=width,
            box_height=height,
            rotation_degrees=rotation_degrees,
            scale=scale,
            grid_x=grid_x,
            grid_y=grid_y,
            label_text=label,
        )

    def _append_entity(self, geometry_kind, layer_code, color_code, coordinates, **extra):
        entity = {
            "geometry_kind": geometry_kind,
            "layer_code": layer_code,
            "layer_name": self._layer_name(layer_code),
            "color_argb": self._geometry_color(layer_code, color_code),
            "name": extra.get("name", ""),
            "label_text": extra.get("label_text", ""),
            "text_height": extra.get("text_height", 0.0),
            "rotation_degrees": extra.get("rotation_degrees", 0.0),
            "box_width": extra.get("box_width", 0.0),
            "box_height": extra.get("box_height", 0.0),
            "scale": extra.get("scale", 0.0),
            "grid_x": extra.get("grid_x", 0.0),
            "grid_y": extra.get("grid_y", 0.0),
            "radius": extra.get("radius", 0.0),
            "start_angle": extra.get("start_angle", 0.0),
            "end_angle": extra.get("end_angle", 0.0),
            "is_closed": extra.get("is_closed", False),
            "coordinates": coordinates,
        }
        self.entities.append(entity)

    def _extract_attribute_tables(self):
        markers = []
        needle = b'@TAB'
        cursor = 0
        while True:
            marker_offset = self.data.find(needle, cursor)
            if marker_offset < 0:
                break
            end_offset = marker_offset + 4
            while end_offset < len(self.data) and 48 <= self.data[end_offset] <= 57:
                end_offset += 1
            table_ref = self.data[marker_offset:end_offset].decode('ascii', errors='ignore')
            record_start = marker_offset
            ref_length = end_offset - marker_offset
            if marker_offset > 0 and self.data[marker_offset - 1] == ref_length:
                record_start = marker_offset - 1
            markers.append({'record_start': record_start, 'table_ref': table_ref})
            cursor = end_offset
        if not markers:
            return []
        tables = {}
        for index, marker in enumerate(markers):
            next_start = len(self.data)
            if index + 1 < len(markers):
                candidate = markers[index + 1]['record_start']
                if candidate > marker['record_start']:
                    next_start = candidate
            record_end = min(len(self.data), next_start)
            if record_end <= marker['record_start']:
                continue
            record_bytes = self.data[marker['record_start']:record_end]
            rows = tables.setdefault(marker['table_ref'], [])
            rows.append(
                self._parse_attribute_row(
                    record_bytes,
                    marker["table_ref"],
                    len(rows) + 1,
                )
            )
        return [
            {"table_ref": table_ref, "rows": rows}
            for table_ref, rows in sorted(tables.items())
            if rows
        ]

    def _parse_attribute_row(self, record_bytes, table_ref, row_index):
        row = {
            "row_index": row_index,
            "columns": {
                "row_variant": "unknown",
                "record_length": len(record_bytes),
            },
        }
        if len(record_bytes) >= 11:
            row["columns"]["table_ref_inline"] = (
                record_bytes[1:11]
                .decode("ascii", errors="ignore")
                .strip("\x00 ")
            )
        label_length = record_bytes[28] if len(record_bytes) > 28 else 0
        has_label = 1 <= label_length <= 64 and 29 + label_length <= len(record_bytes)
        if has_label:
            label_bytes = record_bytes[29:29 + label_length]
            if all((32 <= item < 127 for item in label_bytes)):
                label_text = label_bytes.decode('ascii', errors='ignore').strip('\x00 ')
            else:
                label_text = ''
        else:
            label_text = ''
        if label_text:
            sep_offset = 29 + label_length
            coord_1_x = self._safe_round(self._chunk_float64(record_bytes, sep_offset + 8))
            coord_1_y = self._safe_round(self._chunk_float64(record_bytes, sep_offset + 16))
            coord_2_x = self._safe_round(self._chunk_float64(record_bytes, sep_offset + 50))
            coord_2_y = self._safe_round(self._chunk_float64(record_bytes, sep_offset + 58))
            coord_3_x = self._safe_round(self._chunk_float64(record_bytes, sep_offset + 66))
            coord_3_y = self._safe_round(self._chunk_float64(record_bytes, sep_offset + 74))
            row['columns']['row_variant'] = 'label'
            row['columns']['label'] = label_text
            row['columns']['label_length'] = label_length
            row['columns']['prefix_float'] = self._safe_round(self._chunk_float32(record_bytes, 17))
            row['columns']['code_u16'] = self._chunk_uint16(record_bytes, 25)
            row['columns']['separator_1'] = record_bytes[sep_offset] if sep_offset < len(record_bytes) else 0
            row['columns']['style_code'] = self._chunk_uint32(record_bytes, sep_offset + 1)
            row['columns']['flag_1'] = record_bytes[sep_offset + 5] if sep_offset + 5 < len(record_bytes) else 0
            row['columns']['flag_2'] = record_bytes[sep_offset + 6] if sep_offset + 6 < len(record_bytes) else 0
            row['columns']['flag_3'] = record_bytes[sep_offset + 7] if sep_offset + 7 < len(record_bytes) else 0
            row['columns']['coord_1_x'] = coord_1_x
            row['columns']['coord_1_y'] = coord_1_y
            row['columns']['separator_2'] = record_bytes[sep_offset + 35] if sep_offset + 35 < len(record_bytes) else 0
            row['columns']['scale_float'] = self._safe_round(self._chunk_float32(record_bytes, sep_offset + 46))
            row['columns']['coord_2_x'] = coord_2_x
            row['columns']['coord_2_y'] = coord_2_y
            row['columns']['coord_3_x'] = coord_3_x
            row['columns']['coord_3_y'] = coord_3_y
            return row
        if len(record_bytes) >= 119:
            coord_0_x = self._safe_round(self._chunk_float64(record_bytes, 17))
            coord_0_y = self._safe_round(self._chunk_float64(record_bytes, 25))
            coord_1_x = self._safe_round(self._chunk_float64(record_bytes, 45))
            coord_1_y = self._safe_round(self._chunk_float64(record_bytes, 53))
            coord_2_x = self._safe_round(self._chunk_float64(record_bytes, 87))
            coord_2_y = self._safe_round(self._chunk_float64(record_bytes, 95))
            coord_3_x = self._safe_round(self._chunk_float64(record_bytes, 103))
            coord_3_y = self._safe_round(self._chunk_float64(record_bytes, 111))
            plausible_coordinates = (
                self._looks_like_xy(coord_0_x, coord_0_y)
                and self._looks_like_xy(coord_1_x, coord_1_y)
                and self._looks_like_xy(coord_2_x, coord_2_y)
            )
            if not plausible_coordinates:
                values = self._collect_ascii_fields(record_bytes)
                row['columns']['ascii_values'] = ' | '.join((item for item in values if item != table_ref))
                return row
            row['columns']['row_variant'] = 'segment'
            row['columns']['coord_0_x'] = coord_0_x
            row['columns']['coord_0_y'] = coord_0_y
            row['columns']['style_code'] = self._chunk_uint32(record_bytes, 37)
            row['columns']['flag_1'] = record_bytes[41] if 41 < len(record_bytes) else 0
            row['columns']['flag_2'] = record_bytes[42] if 42 < len(record_bytes) else 0
            row['columns']['flag_3'] = record_bytes[43] if 43 < len(record_bytes) else 0
            row['columns']['flag_4'] = record_bytes[44] if 44 < len(record_bytes) else 0
            row['columns']['coord_1_x'] = coord_1_x
            row['columns']['coord_1_y'] = coord_1_y
            row['columns']['separator_2'] = record_bytes[72] if 72 < len(record_bytes) else 0
            row['columns']['coord_2_x'] = coord_2_x
            row['columns']['coord_2_y'] = coord_2_y
            row['columns']['coord_3_x'] = coord_3_x
            row['columns']['coord_3_y'] = coord_3_y
            return row
        values = self._collect_ascii_fields(record_bytes)
        row['columns']['ascii_values'] = ' | '.join((item for item in values if item != table_ref))
        return row

    def _collect_ascii_fields(self, chunk):
        values = []
        seen = set()
        for index in range(max(0, len(chunk) - 1)):
            value_length = chunk[index]
            if value_length <= 0 or value_length > 64 or index + 1 + value_length > len(chunk):
                continue
            raw_value = chunk[index + 1:index + 1 + value_length]
            if not raw_value or not all((32 <= item < 127 for item in raw_value)):
                continue
            value = raw_value.decode('ascii', errors='ignore').strip('\x00 ')
            if not value:
                continue
            if not all((ord(char) >= 32 or char == '\t' for char in value)):
                continue
            if value in seen:
                continue
            seen.add(value)
            values.append(value)
        return values

    def _chunk_uint16(self, chunk, offset):
        if offset < 0 or offset + 2 > len(chunk):
            return 0
        return int.from_bytes(chunk[offset:offset + 2], 'little', signed=False)

    def _chunk_uint32(self, chunk, offset):
        if offset < 0 or offset + 4 > len(chunk):
            return 0
        return int.from_bytes(chunk[offset:offset + 4], 'little', signed=False)

    def _chunk_float32(self, chunk, offset):
        if offset < 0 or offset + 4 > len(chunk):
            return 0.0
        return struct.unpack_from('<f', chunk, offset)[0]

    def _chunk_float64(self, chunk, offset):
        if offset < 0 or offset + 8 > len(chunk):
            return 0.0
        return struct.unpack_from('<d', chunk, offset)[0]

    def _safe_round(self, value):
        if not math.isfinite(value):
            return None
        if abs(value) < 1e-12:
            return 0.0
        return round(float(value), 6)

    def _looks_like_xy(self, x, y):
        return (
            x is not None
            and y is not None
            and math.isfinite(x)
            and math.isfinite(y)
            and abs(x) <= 100_000_000
            and abs(y) <= 100_000_000
            and (abs(x) >= 1_000 or abs(y) >= 1_000)
        )

    def _read_text_payload(self, offset, extended_header_size):
        text = self._read_length_prefixed_text(offset + extended_header_size + 97, offset + extended_header_size + 98)
        if text:
            return text
        text = self._read_length_prefixed_text(offset + extended_header_size + 86, offset + extended_header_size + 87)
        if text:
            return text
        text = self._read_length_prefixed_text(offset + 97, offset + 98)
        if text:
            return text
        return self._read_length_prefixed_text(offset + 86, offset + 87)

    def _read_length_prefixed_text(self, length_offset, text_offset):
        if length_offset < 0 or length_offset >= len(self.data) or text_offset < 0 or (text_offset >= len(self.data)):
            return ''
        text_length = self.data[length_offset]
        if text_length <= 0 or text_length > 240 or text_offset + text_length > len(self.data):
            return ''
        return self._read_legacy_string(text_offset, text_length).strip('\x00 ')

    def _read_positive_float(self, offset):
        if offset < 0 or offset + 4 > len(self.data):
            return None
        value = self._read_float32(offset)
        if not math.isfinite(value) or value <= 0.0 or value > 100000.0:
            return None
        return float(value)

    def _read_plan_box_name(self, offset, block_size):
        end = min(len(self.data), offset + block_size + 1)
        for index in range(offset, max(offset, end - 4)):
            if index + 4 > end:
                break
            if self.data[index:index + 4].lower() != b'plan':
                continue
            cursor = index + 4
            while cursor < end and cursor - index < 32 and self._is_token_byte(self.data[cursor]):
                cursor += 1
            if cursor <= index + 4:
                continue
            name = self.data[index:cursor].decode('ascii', errors='ignore').strip('\x00 ')
            if len(name) > 4 and name[4:].isdigit():
                return name
        return ''

    def _read_length_prefixed_name(self, start, end):
        bounded_end = min(end, len(self.data))
        for index in range(max(0, start), max(0, bounded_end - 2)):
            value_length = self.data[index]
            if value_length <= 0 or value_length > 64 or index + 1 + value_length > bounded_end:
                continue
            value = self._read_legacy_string(index + 1, value_length).strip('\x00 ')
            if value and all((ord(char) >= 32 or char == '\t' for char in value)):
                return value
        return ''

    def _read_ascii_token(self, start, end):
        bounded_end = min(end, len(self.data))
        cursor = max(0, start)
        while cursor < bounded_end:
            if not self._is_token_byte(self.data[cursor]):
                cursor += 1
                continue
            token_start = cursor
            while cursor < bounded_end and self._is_token_byte(self.data[cursor]):
                cursor += 1
            if cursor - token_start >= 3:
                return self.data[token_start:cursor].decode('ascii', errors='ignore')
        return ''

    def _is_token_byte(self, value):
        return 48 <= value <= 57 or 65 <= value <= 90 or 97 <= value <= 122 or (value in (45, 95))

    def _read_epsg(self, offset, max_length):
        for index in range(max_length - 3):
            if offset + index + 2 >= len(self.data):
                break
            if self.data[offset + index:offset + index + 3] == b'SRS':
                chars = []
                cursor = 0
                while offset + index + cursor < len(self.data) and self.data[offset + index + cursor] != 62:
                    chars.append(self._decode_legacy_char(self.data[offset + index + cursor]))
                    cursor += 1
                return ''.join(chars).replace('SRS:', '').replace('"', '')
        return ''

    def _box_metrics(self, coordinates):
        if len(coordinates) < 5:
            return (False, 0.0, 0.0, 0.0, coordinates)
        unique_points = coordinates
        if self._same_coordinate(coordinates[0], coordinates[-1]):
            unique_points = coordinates[:-1]
        unique_points = self._simplify_collinear_ring(unique_points)
        if len(unique_points) != 4:
            return (False, 0.0, 0.0, 0.0, coordinates)

        def vector(a, b):
            return (b['x'] - a['x'], b['y'] - a['y'])

        def length(v):
            return math.sqrt(v[0] * v[0] + v[1] * v[1])
        edges = [
            vector(unique_points[0], unique_points[1]),
            vector(unique_points[1], unique_points[2]),
            vector(unique_points[2], unique_points[3]),
            vector(unique_points[3], unique_points[0]),
        ]
        lengths = [length(edge) for edge in edges]
        if any((item < 0.001 for item in lengths)):
            return (False, 0.0, 0.0, 0.0, coordinates)
        opposite_equal = self._nearly_equal(lengths[0], lengths[2]) and self._nearly_equal(lengths[1], lengths[3])
        right_angles = all(
            (
                self._nearly_orthogonal(
                    edges[0],
                    edges[1],
                    lengths[0],
                    lengths[1],
                ),
                self._nearly_orthogonal(
                    edges[1],
                    edges[2],
                    lengths[1],
                    lengths[2],
                ),
                self._nearly_orthogonal(
                    edges[2],
                    edges[3],
                    lengths[2],
                    lengths[3],
                ),
                self._nearly_orthogonal(
                    edges[3],
                    edges[0],
                    lengths[3],
                    lengths[0],
                ),
            )
        )
        if not opposite_equal or not right_angles:
            return (False, 0.0, 0.0, 0.0, coordinates)
        rotation_degrees = math.degrees(math.atan2(edges[0][1], edges[0][0])) % 360.0
        return (True, lengths[0], lengths[1], rotation_degrees, coordinates)

    def _simplify_collinear_ring(self, points):
        if len(points) <= 4:
            return points
        simplified = list(points)
        removed = True
        while removed and len(simplified) > 4:
            removed = False
            for index in range(len(simplified)):
                prev_point = simplified[(index - 1) % len(simplified)]
                current = simplified[index]
                next_point = simplified[(index + 1) % len(simplified)]
                ax = current['x'] - prev_point['x']
                ay = current['y'] - prev_point['y']
                bx = next_point['x'] - current['x']
                by = next_point['y'] - current['y']
                a_len = math.sqrt(ax * ax + ay * ay)
                b_len = math.sqrt(bx * bx + by * by)
                if a_len < 0.001 or b_len < 0.001:
                    simplified.pop(index)
                    removed = True
                    break
                cross = abs(ax * by - ay * bx) / (a_len * b_len)
                if cross <= 0.02:
                    simplified.pop(index)
                    removed = True
                    break
        return simplified

    def _is_nearly_closed(self, coordinates):
        if len(coordinates) < 4:
            return False
        first = coordinates[0]
        last = coordinates[-1]
        if self._same_coordinate(first, last):
            return True
        if len(coordinates) < 5:
            return False
        second = coordinates[1]
        penultimate = coordinates[-2]
        first_edge = self._distance(first, second)
        last_edge = self._distance(penultimate, last)
        closure_gap = self._distance(first, last)
        reference_length = min(first_edge, last_edge)
        if reference_length <= 0.001:
            return False
        tolerance = max(reference_length * 0.2, 0.05)
        return closure_gap <= tolerance

    def _nearly_orthogonal(self, a, b, a_length, b_length):
        normalized_dot = abs((a[0] * b[0] + a[1] * b[1]) / (a_length * b_length))
        return normalized_dot <= 0.03

    def _nearly_equal(self, a, b):
        tolerance = max(max(abs(a), abs(b)) * 0.02, 0.02)
        return abs(a - b) <= tolerance

    def _same_coordinate(self, a, b):
        return abs(a['x'] - b['x']) < 0.001 and abs(a['y'] - b['y']) < 0.001 and (abs(a['z'] - b['z']) < 0.001)

    def _distance(self, a, b):
        dx = a['x'] - b['x']
        dy = a['y'] - b['y']
        dz = a['z'] - b['z']
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _coordinate(self, raw_x, raw_y, z):
        return {'x': raw_y, 'y': raw_x, 'z': z}

    def _valid_xy(self, x, y):
        return (
            math.isfinite(x)
            and math.isfinite(y)
            and abs(x) <= 100_000_000
            and abs(y) <= 100_000_000
        )

    def _geometry_color(self, layer_code, color_code):
        if color_code == 1:
            return self._to_argb(0, 0, 255)
        if color_code == 255:
            return self._to_argb(255, 0, 0)
        if color_code != 0:
            return None
        if 0 <= layer_code < len(self.layer_colors):
            return self._normalize_layer_color(self.layer_colors[layer_code])
        if 0 <= layer_code - 1 < len(self.layer_colors):
            return self._normalize_layer_color(self.layer_colors[layer_code - 1])
        return None

    def _layer_name(self, layer_code):
        if 0 <= layer_code < len(self.layer_names):
            return self.layer_names[layer_code]
        if 0 <= layer_code - 1 < len(self.layer_names):
            return self.layer_names[layer_code - 1]
        return ''

    def _normalize_layer_color(self, argb):
        red = argb >> 16 & 255
        green = argb >> 8 & 255
        blue = argb & 255
        if red == 0 and green == 0 and (blue <= 1):
            return self._to_argb(0, 0, 0)
        return argb

    def _to_argb(self, red, green, blue):
        return 255 << 24 | red << 16 | green << 8 | blue

    def _read_legacy_string(self, offset, length):
        chars = []
        for index in range(length):
            if offset + index >= len(self.data):
                break
            chars.append(self._decode_legacy_char(self.data[offset + index]))
        return ''.join(chars).rstrip('\x00')

    def _decode_legacy_char(self, value):
        return {221: 'İ', 222: 'Ş', 208: 'Ğ', 240: 'ğ', 253: 'ı', 254: 'ş'}.get(value, chr(value))

    def _read_uint32(self, offset):
        return int.from_bytes(self.data[offset:offset + 4], 'little', signed=False)

    def _read_float64(self, offset):
        return struct.unpack_from('<d', self.data, offset)[0]

    def _read_float32(self, offset):
        return struct.unpack_from('<f', self.data, offset)[0]
