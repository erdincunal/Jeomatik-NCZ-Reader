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


EMBEDDED_GEOMETRY_CONTAINER_TYPES = {0, 5, 14, 48, 108, 111, 132, 150, 180}


def parse_ncz(file_path):
    with open(file_path, "rb") as handle:
        data = handle.read()

    layer_names = []
    layer_colors = []
    entities = []
    attribute_tables = []
    version_name = ""
    epsg = ""
    projection_text = ""
    unsupported = {}

    def read_int32(offset):
        return int.from_bytes(data[offset : offset + 4], "little", signed=False)

    def read_double(offset):
        return struct.unpack_from("<d", data, offset)[0]

    def read_float(offset):
        return struct.unpack_from("<f", data, offset)[0]

    def convert_turkish_char(value):
        return {
            221: "İ",
            222: "Ş",
            208: "Ğ",
            240: "ğ",
            253: "ı",
            254: "ş",
        }.get(value, chr(value))

    def read_turkish_string(offset, length):
        chars = []
        for index in range(length):
            if offset + index >= len(data):
                break
            chars.append(convert_turkish_char(data[offset + index]))
        return "".join(chars).rstrip("\0")

    def to_argb(red, green, blue):
        return (255 << 24) | (red << 16) | (green << 8) | blue

    def normalize_layer_color(argb):
        red = (argb >> 16) & 0xFF
        green = (argb >> 8) & 0xFF
        blue = argb & 0xFF
        if red == 0 and green == 0 and blue <= 1:
            return to_argb(0, 0, 0)
        return argb

    def get_layer_name(layer_code):
        if 0 <= layer_code < len(layer_names):
            return layer_names[layer_code]
        if 0 <= layer_code - 1 < len(layer_names):
            return layer_names[layer_code - 1]
        return ""

    def resolve_geometry_color(layer_code, color_code):
        if color_code == 1:
            return to_argb(0, 0, 255)
        if color_code == 255:
            return to_argb(255, 0, 0)
        if color_code != 0:
            return None

        if 0 <= layer_code < len(layer_colors):
            return normalize_layer_color(layer_colors[layer_code])
        if 0 <= layer_code - 1 < len(layer_colors):
            return normalize_layer_color(layer_colors[layer_code - 1])
        return None

    def is_valid_coordinate(x, y):
        return (
            not math.isnan(x)
            and not math.isnan(y)
            and not math.isinf(x)
            and not math.isinf(y)
            and abs(x) <= 100000000
            and abs(y) <= 100000000
        )

    def create_map_coordinate(raw_x, raw_y, z):
        return {"x": raw_y, "y": raw_x, "z": z}

    def distance(a, b):
        dx = a["x"] - b["x"]
        dy = a["y"] - b["y"]
        dz = a["z"] - b["z"]
        return math.sqrt((dx * dx) + (dy * dy) + (dz * dz))

    def coordinates_equal(a, b):
        return (
            abs(a["x"] - b["x"]) < 0.001
            and abs(a["y"] - b["y"]) < 0.001
            and abs(a["z"] - b["z"]) < 0.001
        )

    def nearly_equal(a, b):
        tolerance = max(max(abs(a), abs(b)) * 0.02, 0.02)
        return abs(a - b) <= tolerance

    def is_nearly_orthogonal(a, b, a_length, b_length):
        normalized_dot = abs(((a[0] * b[0]) + (a[1] * b[1])) / (a_length * b_length))
        return normalized_dot <= 0.03

    def simplify_collinear_points(points):
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

                ax = current["x"] - prev_point["x"]
                ay = current["y"] - prev_point["y"]
                bx = next_point["x"] - current["x"]
                by = next_point["y"] - current["y"]
                a_len = math.sqrt((ax * ax) + (ay * ay))
                b_len = math.sqrt((bx * bx) + (by * by))

                if a_len < 0.001 or b_len < 0.001:
                    simplified.pop(index)
                    removed = True
                    break

                cross = abs((ax * by) - (ay * bx)) / (a_len * b_len)
                if cross <= 0.02:
                    simplified.pop(index)
                    removed = True
                    break

        return simplified

    def is_approximately_closed(coordinates):
        if len(coordinates) < 4:
            return False

        first = coordinates[0]
        last = coordinates[-1]
        if coordinates_equal(first, last):
            return True

        if len(coordinates) < 5:
            return False

        second = coordinates[1]
        penultimate = coordinates[-2]
        first_edge = distance(first, second)
        last_edge = distance(penultimate, last)
        closure_gap = distance(first, last)
        reference_length = min(first_edge, last_edge)
        if reference_length <= 0.001:
            return False

        tolerance = max(reference_length * 0.2, 0.05)
        return closure_gap <= tolerance

    def try_get_box_metrics(coordinates):
        if len(coordinates) < 5:
            return False, 0.0, 0.0, 0.0, coordinates

        unique_points = coordinates
        if coordinates_equal(coordinates[0], coordinates[-1]):
            unique_points = coordinates[:-1]

        unique_points = simplify_collinear_points(unique_points)
        if len(unique_points) != 4:
            return False, 0.0, 0.0, 0.0, coordinates

        def vector(a, b):
            return (b["x"] - a["x"], b["y"] - a["y"])

        def length(v):
            return math.sqrt((v[0] * v[0]) + (v[1] * v[1]))

        edges = [
            vector(unique_points[0], unique_points[1]),
            vector(unique_points[1], unique_points[2]),
            vector(unique_points[2], unique_points[3]),
            vector(unique_points[3], unique_points[0]),
        ]
        lengths = [length(edge) for edge in edges]
        if any(item < 0.001 for item in lengths):
            return False, 0.0, 0.0, 0.0, coordinates

        opposite_equal = nearly_equal(lengths[0], lengths[2]) and nearly_equal(lengths[1], lengths[3])
        right_angles = (
            is_nearly_orthogonal(edges[0], edges[1], lengths[0], lengths[1])
            and is_nearly_orthogonal(edges[1], edges[2], lengths[1], lengths[2])
            and is_nearly_orthogonal(edges[2], edges[3], lengths[2], lengths[3])
            and is_nearly_orthogonal(edges[3], edges[0], lengths[3], lengths[0])
        )
        if not opposite_equal or not right_angles:
            return False, 0.0, 0.0, 0.0, coordinates

        rotation_degrees = (math.degrees(math.atan2(edges[0][1], edges[0][0])) % 360.0)
        return True, lengths[0], lengths[1], rotation_degrees, coordinates

    def read_epsg(offset, max_length):
        for index in range(max_length - 3):
            if offset + index + 2 >= len(data):
                break
            if data[offset + index : offset + index + 3] == b"SRS":
                chars = []
                cursor = 0
                while offset + index + cursor < len(data) and data[offset + index + cursor] != 62:
                    chars.append(convert_turkish_char(data[offset + index + cursor]))
                    cursor += 1
                return "".join(chars).replace("SRS:", "").replace('"', "")
        return ""

    def read_length_prefixed_text(length_offset, text_offset):
        if length_offset < 0 or length_offset >= len(data) or text_offset < 0 or text_offset >= len(data):
            return ""

        text_length = data[length_offset]
        if text_length <= 0 or text_length > 240 or text_offset + text_length > len(data):
            return ""

        return read_turkish_string(text_offset, text_length).strip("\0 ")

    def read_text_payload(offset, gis_difference):
        text = read_length_prefixed_text(offset + gis_difference + 97, offset + gis_difference + 98)
        if text:
            return text

        text = read_length_prefixed_text(offset + gis_difference + 86, offset + gis_difference + 87)
        if text:
            return text

        text = read_length_prefixed_text(offset + 97, offset + 98)
        if text:
            return text

        return read_length_prefixed_text(offset + 86, offset + 87)

    def try_read_positive_float(offset):
        if offset < 0 or offset + 4 > len(data):
            return None

        value = read_float(offset)
        if not math.isfinite(value) or value <= 0.0 or value > 100000.0:
            return None
        return float(value)

    def is_plan_name_character(value):
        return (
            48 <= value <= 57
            or 65 <= value <= 90
            or 97 <= value <= 122
            or value in (45, 95)
        )

    def read_plan_box_name(offset, block_size):
        end = min(len(data), offset + block_size + 1)
        for index in range(offset, max(offset, end - 4)):
            if index + 4 > end:
                break
            if data[index : index + 4].lower() != b"plan":
                continue

            cursor = index + 4
            while cursor < end and cursor - index < 32 and is_plan_name_character(data[cursor]):
                cursor += 1

            if cursor <= index + 4:
                continue

            name = data[index:cursor].decode("ascii", errors="ignore").strip("\0 ")
            if len(name) > 4 and name[4:].isdigit():
                return name
        return ""

    def read_length_prefixed_name(start, end):
        bounded_end = min(end, len(data))
        for index in range(max(0, start), max(0, bounded_end - 2)):
            value_length = data[index]
            if value_length <= 0 or value_length > 64 or index + 1 + value_length > bounded_end:
                continue

            value = read_turkish_string(index + 1, value_length).strip("\0 ")
            if value and all(ord(char) >= 32 or char == "\t" for char in value):
                return value
        return ""

    def read_ascii_token(start, end):
        bounded_end = min(end, len(data))
        cursor = max(0, start)
        while cursor < bounded_end:
            if not is_plan_name_character(data[cursor]):
                cursor += 1
                continue

            token_start = cursor
            while cursor < bounded_end and is_plan_name_character(data[cursor]):
                cursor += 1
            if cursor - token_start >= 3:
                return data[token_start:cursor].decode("ascii", errors="ignore")
        return ""

    def collect_length_prefixed_ascii_fields(chunk):
        values = []
        seen = set()
        for index in range(max(0, len(chunk) - 1)):
            value_length = chunk[index]
            if value_length <= 0 or value_length > 64 or index + 1 + value_length > len(chunk):
                continue

            raw_value = chunk[index + 1 : index + 1 + value_length]
            if not raw_value or not all(32 <= item < 127 for item in raw_value):
                continue

            value = raw_value.decode("ascii", errors="ignore").strip("\0 ")
            if not value:
                continue
            if not all(ord(char) >= 32 or char == "\t" for char in value):
                continue
            if value in seen:
                continue
            seen.add(value)
            values.append(value)
        return values

    def read_uint16_from(chunk, offset):
        if offset < 0 or offset + 2 > len(chunk):
            return 0
        return int.from_bytes(chunk[offset : offset + 2], "little", signed=False)

    def read_uint32_from(chunk, offset):
        if offset < 0 or offset + 4 > len(chunk):
            return 0
        return int.from_bytes(chunk[offset : offset + 4], "little", signed=False)

    def read_float_from(chunk, offset):
        if offset < 0 or offset + 4 > len(chunk):
            return 0.0
        return struct.unpack_from("<f", chunk, offset)[0]

    def read_double_from(chunk, offset):
        if offset < 0 or offset + 8 > len(chunk):
            return 0.0
        return struct.unpack_from("<d", chunk, offset)[0]

    def safe_round(value):
        if not math.isfinite(value):
            return None
        if abs(value) < 1e-12:
            return 0.0
        return round(float(value), 6)

    def looks_like_coordinate_pair(x, y):
        return (
            x is not None
            and y is not None
            and math.isfinite(x)
            and math.isfinite(y)
            and abs(x) <= 100000000
            and abs(y) <= 100000000
            and (abs(x) >= 1000 or abs(y) >= 1000)
        )

    def parse_attribute_row(record_bytes, table_ref, row_index):
        row = {
            "row_index": row_index,
            "columns": {
                "row_variant": "unknown",
                "record_length": len(record_bytes),
            },
        }

        if len(record_bytes) >= 11:
            row["columns"]["table_ref_inline"] = record_bytes[1:11].decode("ascii", errors="ignore").strip("\0 ")

        label_length = record_bytes[28] if len(record_bytes) > 28 else 0
        has_label = 1 <= label_length <= 64 and 29 + label_length <= len(record_bytes)
        if has_label:
            label_bytes = record_bytes[29 : 29 + label_length]
            if all(32 <= item < 127 for item in label_bytes):
                label_text = label_bytes.decode("ascii", errors="ignore").strip("\0 ")
            else:
                label_text = ""
        else:
            label_text = ""

        if label_text:
            sep_offset = 29 + label_length
            coord_1_x = safe_round(read_double_from(record_bytes, sep_offset + 8))
            coord_1_y = safe_round(read_double_from(record_bytes, sep_offset + 16))
            coord_2_x = safe_round(read_double_from(record_bytes, sep_offset + 50))
            coord_2_y = safe_round(read_double_from(record_bytes, sep_offset + 58))
            coord_3_x = safe_round(read_double_from(record_bytes, sep_offset + 66))
            coord_3_y = safe_round(read_double_from(record_bytes, sep_offset + 74))

            row["columns"]["row_variant"] = "label"
            row["columns"]["label"] = label_text
            row["columns"]["label_length"] = label_length
            row["columns"]["prefix_float"] = safe_round(read_float_from(record_bytes, 17))
            row["columns"]["code_u16"] = read_uint16_from(record_bytes, 25)
            row["columns"]["separator_1"] = record_bytes[sep_offset] if sep_offset < len(record_bytes) else 0
            row["columns"]["style_code"] = read_uint32_from(record_bytes, sep_offset + 1)
            row["columns"]["flag_1"] = record_bytes[sep_offset + 5] if sep_offset + 5 < len(record_bytes) else 0
            row["columns"]["flag_2"] = record_bytes[sep_offset + 6] if sep_offset + 6 < len(record_bytes) else 0
            row["columns"]["flag_3"] = record_bytes[sep_offset + 7] if sep_offset + 7 < len(record_bytes) else 0
            row["columns"]["coord_1_x"] = coord_1_x
            row["columns"]["coord_1_y"] = coord_1_y
            row["columns"]["separator_2"] = record_bytes[sep_offset + 35] if sep_offset + 35 < len(record_bytes) else 0
            row["columns"]["scale_float"] = safe_round(read_float_from(record_bytes, sep_offset + 46))
            row["columns"]["coord_2_x"] = coord_2_x
            row["columns"]["coord_2_y"] = coord_2_y
            row["columns"]["coord_3_x"] = coord_3_x
            row["columns"]["coord_3_y"] = coord_3_y
            return row

        if len(record_bytes) >= 119:
            coord_0_x = safe_round(read_double_from(record_bytes, 17))
            coord_0_y = safe_round(read_double_from(record_bytes, 25))
            coord_1_x = safe_round(read_double_from(record_bytes, 45))
            coord_1_y = safe_round(read_double_from(record_bytes, 53))
            coord_2_x = safe_round(read_double_from(record_bytes, 87))
            coord_2_y = safe_round(read_double_from(record_bytes, 95))
            coord_3_x = safe_round(read_double_from(record_bytes, 103))
            coord_3_y = safe_round(read_double_from(record_bytes, 111))

            if not (
                looks_like_coordinate_pair(coord_0_x, coord_0_y)
                and looks_like_coordinate_pair(coord_1_x, coord_1_y)
                and looks_like_coordinate_pair(coord_2_x, coord_2_y)
            ):
                values = collect_length_prefixed_ascii_fields(record_bytes)
                row["columns"]["ascii_values"] = " | ".join(item for item in values if item != table_ref)
                return row

            row["columns"]["row_variant"] = "segment"
            row["columns"]["coord_0_x"] = coord_0_x
            row["columns"]["coord_0_y"] = coord_0_y
            row["columns"]["style_code"] = read_uint32_from(record_bytes, 37)
            row["columns"]["flag_1"] = record_bytes[41] if 41 < len(record_bytes) else 0
            row["columns"]["flag_2"] = record_bytes[42] if 42 < len(record_bytes) else 0
            row["columns"]["flag_3"] = record_bytes[43] if 43 < len(record_bytes) else 0
            row["columns"]["flag_4"] = record_bytes[44] if 44 < len(record_bytes) else 0
            row["columns"]["coord_1_x"] = coord_1_x
            row["columns"]["coord_1_y"] = coord_1_y
            row["columns"]["separator_2"] = record_bytes[72] if 72 < len(record_bytes) else 0
            row["columns"]["coord_2_x"] = coord_2_x
            row["columns"]["coord_2_y"] = coord_2_y
            row["columns"]["coord_3_x"] = coord_3_x
            row["columns"]["coord_3_y"] = coord_3_y
            return row

        values = collect_length_prefixed_ascii_fields(record_bytes)
        row["columns"]["ascii_values"] = " | ".join(item for item in values if item != table_ref)
        return row

    def extract_attribute_tables():
        markers = []
        needle = b"@TAB"
        cursor = 0
        while True:
            marker_offset = data.find(needle, cursor)
            if marker_offset < 0:
                break

            end_offset = marker_offset + 4
            while end_offset < len(data) and 48 <= data[end_offset] <= 57:
                end_offset += 1

            table_ref = data[marker_offset:end_offset].decode("ascii", errors="ignore")
            record_start = marker_offset
            ref_length = end_offset - marker_offset
            if marker_offset > 0 and data[marker_offset - 1] == ref_length:
                record_start = marker_offset - 1

            markers.append(
                {
                    "record_start": record_start,
                    "table_ref": table_ref,
                }
            )
            cursor = end_offset

        if not markers:
            return []

        tables = {}
        for index, marker in enumerate(markers):
            next_start = len(data)
            if index + 1 < len(markers):
                candidate = markers[index + 1]["record_start"]
                if candidate > marker["record_start"]:
                    next_start = candidate

            record_end = min(len(data), next_start)
            if record_end <= marker["record_start"]:
                continue

            record_bytes = data[marker["record_start"] : record_end]
            rows = tables.setdefault(marker["table_ref"], [])
            rows.append(parse_attribute_row(record_bytes, marker["table_ref"], len(rows) + 1))

        return [
            {
                "table_ref": table_ref,
                "rows": rows,
            }
            for table_ref, rows in sorted(tables.items())
            if rows
        ]

    def append_entity(geometry_kind, layer_code, color_code, coordinates, **extra):
        entity = {
            "geometry_kind": geometry_kind,
            "layer_code": layer_code,
            "layer_name": get_layer_name(layer_code),
            "color_argb": resolve_geometry_color(layer_code, color_code),
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
        entities.append(entity)

    def read_point(offset, gis_difference):
        layer_code = data[offset + 7]
        raw_x = read_double(offset + 8)
        raw_y = read_double(offset + 16)
        z = read_float(offset + 24)
        if z == 0:
            z = read_float(offset + 28)
        if not is_valid_coordinate(raw_x, raw_y):
            return
        name_length = data[offset + gis_difference + 86]
        append_entity(
            "Point",
            layer_code,
            data[offset + 37],
            [create_map_coordinate(raw_x, raw_y, z)],
            name=read_turkish_string(offset + gis_difference + 87, name_length),
        )

    def read_line(offset, block_size):
        layer_code = data[offset + 7]
        raw_x1 = read_double(offset + 8)
        raw_y1 = read_double(offset + 16)
        z1 = read_float(offset + 24)
        raw_x2 = read_double(offset + block_size - 19)
        raw_y2 = read_double(offset + block_size - 11)
        z2 = read_float(offset + block_size - 3)
        if not is_valid_coordinate(raw_x1, raw_y1) or not is_valid_coordinate(raw_x2, raw_y2):
            return
        append_entity(
            "Line",
            layer_code,
            data[offset + 37],
            [create_map_coordinate(raw_x1, raw_y1, z1), create_map_coordinate(raw_x2, raw_y2, z2)],
        )

    def read_text(offset, gis_difference):
        layer_code = data[offset + 7]
        raw_x = read_double(offset + 8)
        raw_y = read_double(offset + 16)
        z = read_float(offset + 24)
        if z == 0:
            z = read_float(offset + 28)
        if not is_valid_coordinate(raw_x, raw_y):
            return
        text = read_text_payload(offset, gis_difference)
        if not text:
            return
        text_height = try_read_positive_float(offset + gis_difference + 86)
        if text_height is None:
            text_height = try_read_positive_float(offset + 86)
        if text_height is None:
            return
        append_entity(
            "Text",
            layer_code,
            data[offset + 37],
            [create_map_coordinate(raw_x, raw_y, z)],
            label_text=text,
            text_height=text_height,
            rotation_degrees=(read_float(offset + gis_difference + 90) * (180.0 / math.pi)) % 360.0,
        )

    def read_symbol(offset, gis_difference):
        layer_code = data[offset + 7]
        raw_x = read_double(offset + 8)
        raw_y = read_double(offset + 16)
        z = read_float(offset + 24)
        if not is_valid_coordinate(raw_x, raw_y):
            return
        symbol_offset = offset + gis_difference + 94
        if symbol_offset < offset or symbol_offset >= len(data):
            symbol_offset = offset + 94
        symbol_code = data[symbol_offset] if 0 <= symbol_offset < len(data) else 0
        symbol_size = try_read_positive_float(offset + gis_difference + 86)
        if symbol_size is None:
            symbol_size = try_read_positive_float(offset + 86)
        if symbol_size is None:
            symbol_size = 5.0
        append_entity(
            "Symbol",
            layer_code,
            data[offset + 37],
            [create_map_coordinate(raw_x, raw_y, z)],
            label_text=f"S{symbol_code}",
            text_height=symbol_size,
            rotation_degrees=(read_float(offset + gis_difference + 90) * (180.0 / math.pi)) % 360.0,
        )

    def read_multiline(offset, block_size, gis_difference):
        layer_code = data[offset + 7]
        text_length = data[offset + gis_difference + 86]
        text = read_turkish_string(offset + gis_difference + 87, text_length)
        point_count = (block_size + 1 - 113 - gis_difference) // 24
        if point_count < 2:
            return
        coordinates = []
        for index in range(point_count):
            coordinate_offset = (index * 24) + (offset + gis_difference + 113)
            if coordinate_offset + 24 > len(data):
                break
            coordinates.append(
                create_map_coordinate(
                    read_double(coordinate_offset),
                    read_double(coordinate_offset + 8),
                    read_double(coordinate_offset + 16),
                )
            )
        if len(coordinates) < 2:
            return

        is_closed = is_approximately_closed(coordinates)
        if is_closed and not coordinates_equal(coordinates[0], coordinates[-1]):
            first = coordinates[0]
            coordinates.append({"x": first["x"], "y": first["y"], "z": first["z"]})

        is_box, box_width, box_height, box_rotation, coordinates = try_get_box_metrics(coordinates)
        append_entity(
            "Polygon" if is_closed else "Polyline",
            layer_code,
            data[offset + 37],
            coordinates,
            label_text=text,
            is_closed=is_closed,
            box_width=box_width,
            box_height=box_height,
            rotation_degrees=box_rotation if is_box else 0.0,
        )

    def read_box(offset, block_size, gis_difference):
        layer_code = data[offset + 7]
        raw_x1 = read_double(offset + 8)
        raw_y1 = read_double(offset + 16)
        raw_x2 = read_double(offset + gis_difference + 104)
        raw_y2 = read_double(offset + gis_difference + 112)
        rotation_radians = read_float(offset + gis_difference + 120)
        if not is_valid_coordinate(raw_x1, raw_y1) or not is_valid_coordinate(raw_x2, raw_y2):
            return
        width = abs(raw_x2 - raw_x1)
        height = abs(raw_y2 - raw_y1)
        rotation_degrees = (rotation_radians * (180.0 / math.pi)) % 360.0
        angle_radians = rotation_degrees * (math.pi / 180.0)

        # NetCAD kutu acisini sol-alt koseden yukari cikan kenara gore yorumluyor.
        side_x = math.sin(angle_radians)
        side_y = math.cos(angle_radians)
        bottom_x = math.cos(angle_radians)
        bottom_y = -math.sin(angle_radians)

        p0x = raw_x1
        p0y = raw_y1
        p1x = p0x + (bottom_x * width)
        p1y = p0y + (bottom_y * width)
        p2x = p1x + (side_x * height)
        p2y = p1y + (side_y * height)
        p3x = p0x + (side_x * height)
        p3y = p0y + (side_y * height)

        append_entity(
            "Polygon",
            layer_code,
            data[offset + 37],
            [
                create_map_coordinate(p0x, p0y, 0.0),
                create_map_coordinate(p1x, p1y, 0.0),
                create_map_coordinate(p2x, p2y, 0.0),
                create_map_coordinate(p3x, p3y, 0.0),
                create_map_coordinate(p0x, p0y, 0.0),
            ],
            is_closed=True,
            box_width=width,
            box_height=height,
            rotation_degrees=rotation_degrees,
            label_text=read_plan_box_name(offset, block_size),
        )

    def read_map_sheet(offset, block_size):
        layer_code = data[offset + 7]
        raw_x1 = read_double(offset + 50)
        raw_y1 = read_double(offset + 58)
        raw_x2 = read_double(offset + 66)
        raw_y2 = read_double(offset + 74)
        if not is_valid_coordinate(raw_x1, raw_y1) or not is_valid_coordinate(raw_x2, raw_y2):
            return

        min_x = min(raw_x1, raw_x2)
        max_x = max(raw_x1, raw_x2)
        min_y = min(raw_y1, raw_y2)
        max_y = max(raw_y1, raw_y2)
        if abs(max_x - min_x) < 0.001 or abs(max_y - min_y) < 0.001:
            return

        sheet_name = read_length_prefixed_name(offset + 86, offset + block_size + 1)
        append_entity(
            "MapSheet",
            layer_code,
            data[offset + 37],
            [
                create_map_coordinate(min_x, min_y, 0.0),
                create_map_coordinate(max_x, min_y, 0.0),
                create_map_coordinate(max_x, max_y, 0.0),
                create_map_coordinate(min_x, max_y, 0.0),
                create_map_coordinate(min_x, min_y, 0.0),
            ],
            is_closed=True,
            box_width=max_x - min_x,
            box_height=max_y - min_y,
            label_text=sheet_name,
        )

    def read_compressed_curve(offset, block_size, gis_difference):
        origin_x = read_double(offset + 8)
        origin_y = read_double(offset + 16)
        if not is_valid_coordinate(origin_x, origin_y):
            return

        point_data_offset = offset + gis_difference + 122
        end_offset = offset + block_size + 1
        if point_data_offset + 8 > end_offset:
            return

        coordinates = []
        invalid_streak = 0
        for record_offset in range(point_data_offset, end_offset - 7, 18):
            delta_x = read_float(record_offset)
            delta_y = read_float(record_offset + 4)
            if not math.isfinite(delta_x) or not math.isfinite(delta_y):
                invalid_streak += 1
                if coordinates and invalid_streak >= 4:
                    break
                continue

            x = origin_x + delta_x
            y = origin_y + delta_y
            if not is_valid_coordinate(x, y):
                invalid_streak += 1
                if coordinates and invalid_streak >= 4:
                    break
                continue

            invalid_streak = 0
            coord = create_map_coordinate(x, y, 0.0)
            if coordinates:
                prev = coordinates[-1]
                if abs(prev["x"] - coord["x"]) < 0.0001 and abs(prev["y"] - coord["y"]) < 0.0001:
                    continue
            coordinates.append(coord)

        if len(coordinates) < 2:
            return

        append_entity(
            "Polyline",
            data[offset + 7],
            data[offset + 37],
            coordinates,
        )

    def read_smart_object(offset, block_size):
        layer_code = data[offset + 7]
        raw_x1 = read_double(offset + 8)
        raw_y1 = read_double(offset + 16)
        if not is_valid_coordinate(raw_x1, raw_y1):
            return

        width = read_double(offset + 169) if offset + 177 <= len(data) else 0.0
        height = read_double(offset + 177) if offset + 185 <= len(data) else 0.0
        grid_x = read_double(offset + 185) if offset + 193 <= len(data) else 0.0
        grid_y = read_double(offset + 193) if offset + 201 <= len(data) else 0.0
        raw_x2 = read_double(offset + 66)
        raw_y2 = read_double(offset + 74)
        if width <= 0.0 or height <= 0.0:
            if not is_valid_coordinate(raw_x2, raw_y2):
                return
            width = abs(raw_x2 - raw_x1)
            height = abs(raw_y2 - raw_y1)
        if width < 0.001 or height < 0.001:
            return

        angle_grads = read_float(offset + 82)
        rotation_degrees = (angle_grads * 0.9) % 360.0 if math.isfinite(angle_grads) else 0.0
        scale = read_float(offset + 86)
        if not math.isfinite(scale):
            scale = 0.0

        angle = math.radians(rotation_degrees)
        bottom_x = math.sin(angle)
        bottom_y = math.cos(angle)
        side_x = math.cos(angle)
        side_y = -math.sin(angle)
        p0x = raw_x1
        p0y = raw_y1
        p1x = p0x + (bottom_x * width)
        p1y = p0y + (bottom_y * width)
        p2x = p1x + (side_x * height)
        p2y = p1y + (side_y * height)
        p3x = p0x + (side_x * height)
        p3y = p0y + (side_y * height)
        end = offset + block_size + 1
        smart_payload = data[offset:end]
        label = "BASIC" if b"BASIC" in smart_payload else read_ascii_token(offset + 145, end)
        append_entity(
            "SmartObject",
            layer_code,
            data[offset + 37],
            [
                create_map_coordinate(p0x, p0y, 0.0),
                create_map_coordinate(p1x, p1y, 0.0),
                create_map_coordinate(p2x, p2y, 0.0),
                create_map_coordinate(p3x, p3y, 0.0),
                create_map_coordinate(p0x, p0y, 0.0),
            ],
            is_closed=True,
            box_width=width,
            box_height=height,
            rotation_degrees=rotation_degrees,
            scale=scale,
            grid_x=grid_x,
            grid_y=grid_y,
            label_text=label,
        )

    def read_triangle_vertex(offset, x_offset, y_offset, z_offset=None):
        if offset + x_offset + 8 > len(data) or offset + y_offset + 8 > len(data):
            return None
        x = read_double(offset + x_offset)
        y = read_double(offset + y_offset)
        z = read_float(offset + z_offset) if z_offset is not None and offset + z_offset + 4 <= len(data) else 0.0
        if not is_valid_coordinate(x, y):
            return None
        return create_map_coordinate(x, y, z)

    def read_triangle(offset, block_size):
        a = read_triangle_vertex(offset, 8, 16, 24)
        b = read_triangle_vertex(offset, 86, 94)
        c = read_triangle_vertex(offset, 106, 114)
        if a is None or b is None or c is None:
            return

        area2 = abs((b["x"] - a["x"]) * (c["y"] - a["y"]) - (b["y"] - a["y"]) * (c["x"] - a["x"]))
        if area2 <= 0.0001:
            return

        append_entity(
            "Triangle",
            data[offset + 7],
            data[offset + 37],
            [a, b, c],
        )

    def read_block_reference(offset, block_size, gis_difference):
        raw_x = read_double(offset + 8)
        raw_y = read_double(offset + 16)
        z = read_float(offset + 24)
        if not is_valid_coordinate(raw_x, raw_y):
            return

        block_name = read_length_prefixed_name(offset + gis_difference + 86, offset + block_size + 1)
        append_entity(
            "Block",
            data[offset + 7],
            data[offset + 37],
            [create_map_coordinate(raw_x, raw_y, z)],
            label_text=block_name,
            rotation_degrees=(read_float(offset + gis_difference + 118) * (180.0 / math.pi)) % 360.0,
        )

    def read_circle(offset):
        layer_code = data[offset + 7]
        raw_x = read_double(offset + 8)
        raw_y = read_double(offset + 16)
        z = read_float(offset + 24)
        if not is_valid_coordinate(raw_x, raw_y):
            return
        x2 = read_double(offset + 50)
        x3 = read_double(offset + 66)
        append_entity(
            "Circle",
            layer_code,
            data[offset + 37],
            [create_map_coordinate(raw_x, raw_y, z)],
            radius=abs(x2 - x3) / 2.0,
        )

    def read_arc(offset, gis_difference):
        layer_code = data[offset + 7]
        raw_x = read_double(offset + 8)
        raw_y = read_double(offset + 16)
        z = read_float(offset + 24)
        if not is_valid_coordinate(raw_x, raw_y):
            return
        append_entity(
            "Arc",
            layer_code,
            data[offset + 37],
            [create_map_coordinate(raw_x, raw_y, z)],
            radius=read_double(offset + gis_difference + 86),
            start_angle=read_double(offset + gis_difference + 104),
            end_angle=read_double(offset + gis_difference + 112),
        )

    def read_geometry(offset, block_size, gis_difference):
        if block_size < 7 or offset + 6 >= len(data):
            return
        geometry_type = data[offset + 6]
        if geometry_type == 1:
            read_point(offset, gis_difference)
        elif geometry_type == 2:
            read_line(offset, block_size)
        elif geometry_type == 3:
            read_circle(offset)
        elif geometry_type == 4:
            read_arc(offset, gis_difference)
        elif geometry_type == 5:
            read_text(offset, gis_difference)
        elif geometry_type == 6:
            read_symbol(offset, gis_difference)
        elif geometry_type == 7:
            read_multiline(offset, block_size, gis_difference)
        elif geometry_type == 9:
            read_compressed_curve(offset, block_size, gis_difference)
        elif geometry_type == 10:
            read_box(offset, block_size, gis_difference)
        elif geometry_type == 11:
            read_map_sheet(offset, block_size)
        elif geometry_type == 12:
            read_triangle(offset, block_size)
        elif geometry_type == 13:
            read_block_reference(offset, block_size, gis_difference)
        elif geometry_type == 15:
            read_smart_object(offset, block_size)
        else:
            unsupported[geometry_type] = unsupported.get(geometry_type, 0) + 1

    def read_embedded_geometry_blocks(offset, block_size):
        cursor = offset + 5
        end = offset + block_size
        while cursor + 6 < end:
            if (data[cursor] not in (21, 22)) or data[cursor + 5] != data[cursor + 6]:
                cursor += 1
                continue
            inner_block_size = read_int32(cursor + 1) + 4
            total_inner = inner_block_size + 1
            if inner_block_size < 7 or cursor + total_inner > end:
                cursor += 1
                continue
            read_geometry(cursor, inner_block_size, 28 if data[cursor] == 22 else 0)
            cursor += total_inner

    cursor = 0
    while cursor + 5 < len(data):
        block_size = read_int32(cursor + 1) + 4
        total_block_size = block_size + 1
        if block_size < 4 or cursor + total_block_size > len(data):
            cursor += 1
            continue

        block_type = data[cursor]
        if block_type == 25 and not version_name:
            version_name = read_turkish_string(cursor + 6, data[cursor + 5])
        elif block_type == 28:
            block_name = read_turkish_string(cursor + 6, data[cursor + 5])
            if block_name == "MPROJ":
                projection = {1: "Geographic", 2: "6", 3: "3"}.get(data[cursor + 16], "Undefined")
                datum = {0: "WGS-84", 1: "ITRF", 4: "ED50", 254: "ED50-HGK"}.get(
                    data[cursor + 17], "Undefined"
                )
                projection_text = f"{datum} / {projection} / Zone {data[cursor + 21]}"
            elif block_name == "TILED_XML":
                epsg = read_epsg(cursor, min(block_size + 1, len(data) - cursor))
            elif block_name == "LEX.ST2":
                layer_count = data[cursor + 20]
                for index in range(layer_count):
                    item_offset = cursor + 23 + (index * 256) + 56
                    if item_offset + 2 >= len(data):
                        break
                    layer_colors.append(to_argb(data[item_offset], data[item_offset + 1], data[item_offset + 2]))
        elif block_type == 6:
            layer_count = data[cursor + 16] + data[cursor + 17] * 256
            for index in range(layer_count):
                item_offset = cursor + 18 + (index * 29)
                if item_offset + 29 > len(data):
                    break
                layer_name = read_turkish_string(item_offset + 5, data[item_offset + 4])
                if layer_name.strip():
                    layer_names.append(layer_name)
        elif block_type in (21, 22) and block_size >= 7:
            read_geometry(cursor, block_size, 28 if block_type == 22 else 0)
        elif block_type in EMBEDDED_GEOMETRY_CONTAINER_TYPES:
            read_embedded_geometry_blocks(cursor, block_size)

        cursor += total_block_size

    if any(entity["geometry_kind"] == "SmartObject" for entity in entities):
        entities = [
            entity
            for entity in entities
            if not (
                entity["geometry_kind"] == "Symbol"
                and entity["layer_code"] == 0
                and entity.get("label_text") == "S0"
            )
        ]

    for entity in entities:
        if not entity["layer_name"]:
            entity["layer_name"] = get_layer_name(entity["layer_code"])
        if entity["color_argb"] is None:
            entity["color_argb"] = resolve_geometry_color(entity["layer_code"], 0)

    attribute_tables = extract_attribute_tables()

    return {
        "entities": entities,
        "attribute_tables": attribute_tables,
        "layer_names": layer_names,
        "layer_colors": layer_colors,
        "version_name": version_name,
        "epsg": epsg,
        "projection_text": projection_text,
        "unsupported_geometry_types": unsupported,
    }
