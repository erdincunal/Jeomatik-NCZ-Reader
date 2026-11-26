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

import math
import re
from dataclasses import dataclass, field
from typing import Optional

_ITRF_TM3_EPSG = {
    27: "EPSG:5253",
    30: "EPSG:5254",
    33: "EPSG:5255",
    36: "EPSG:5256",
    39: "EPSG:5257",
    42: "EPSG:5258",
    45: "EPSG:5259",
}

_ED50_TM3_EPSG = {
    27: "EPSG:2319",
    30: "EPSG:2320",
    33: "EPSG:2321",
    36: "EPSG:2322",
    39: "EPSG:2323",
    42: "EPSG:2324",
    45: "EPSG:2325",
}

_MPROJ_PATTERN = re.compile(
    r"^\s*(?P<datum>[^/]+?)\s*/\s*(?P<projection>[^/]+?)\s*/\s*Zone\s*(?P<zone>\d+)\s*$",
    re.IGNORECASE,
)


@dataclass
class DetectedCRS:
    epsg: str = ""
    source: str = ""
    projection_text: str = ""
    tiled_epsg: str = ""
    candidate_epsgs: list[str] = field(default_factory=list)


def parse_epsg_code(raw_value: str) -> str:
    """Normalize an EPSG string extracted from NCZ metadata into 'EPSG:<code>'."""
    if not raw_value:
        return ""

    text = str(raw_value).strip()
    if not text:
        return ""

    match = re.search(r"EPSG\s*:\s*(\d+)", text, re.IGNORECASE)
    if match:
        code = int(match.group(1))
        return f"EPSG:{code}" if code > 0 else ""

    match = re.search(r"(?:^|\bSRS\s*[=:]\s*)(\d+)\b", text, re.IGNORECASE)
    if match:
        code = int(match.group(1))
        return f"EPSG:{code}" if code > 0 else ""

    if text.isdigit():
        code = int(text)
        return f"EPSG:{code}" if code > 0 else ""

    return ""


def resolve_mproj_epsg(projection_text: str) -> str:
    """Resolve NetCAD MPROJ metadata ('Datum / Projection / Zone N') to an EPSG code."""
    if not projection_text:
        return ""

    match = _MPROJ_PATTERN.match(str(projection_text))
    if not match:
        return ""

    datum = match.group("datum").strip().upper()
    projection = match.group("projection").strip().lower()
    zone = int(match.group("zone"))

    if datum == "UNDEFINED" or projection == "undefined":
        return ""

    if projection == "geographic":
        if datum in ("WGS-84", "WGS84"):
            return "EPSG:4326"
        if datum in ("ITRF", "ITRF96", "TUREF"):
            return "EPSG:5252"
        if datum in ("ED50", "ED50-HGK"):
            return "EPSG:4230"
        return ""

    if projection == "3":
        if zone in _ITRF_TM3_EPSG:
            central_meridian = zone
        elif 9 <= zone <= 15:
            central_meridian = zone * 3
        else:
            return ""

        if datum in ("ITRF", "ITRF96", "TUREF"):
            return _ITRF_TM3_EPSG.get(central_meridian, "")
        if datum in ("ED50", "ED50-HGK"):
            return _ED50_TM3_EPSG.get(central_meridian, "")
        return ""

    if projection == "6":
        if not (1 <= zone <= 60):
            return ""
        if datum in ("WGS-84", "WGS84", "ITRF", "ITRF96", "TUREF"):
            return f"EPSG:{32600 + zone}"
        if datum in ("ED50", "ED50-HGK") and 28 <= zone <= 38:
            return f"EPSG:{23000 + zone}"
        return ""

    return ""


def detect_ncz_crs(result) -> DetectedCRS:
    """Inspect NCZParseResult metadata and return ordered CRS candidates."""
    projection_text = (getattr(result, "projection_text", "") or "").strip()
    raw_epsg = (getattr(result, "epsg", "") or "").strip()

    mproj_epsg = resolve_mproj_epsg(projection_text)
    tiled_epsg = parse_epsg_code(raw_epsg)

    candidates: list[str] = []
    if mproj_epsg:
        candidates.append(mproj_epsg)
    if tiled_epsg and tiled_epsg not in candidates:
        candidates.append(tiled_epsg)

    if mproj_epsg:
        epsg = mproj_epsg
        source = "MPROJ"
    elif tiled_epsg:
        epsg = tiled_epsg
        source = "TILED_XML"
    else:
        epsg = ""
        source = ""

    return DetectedCRS(
        epsg=epsg,
        source=source,
        projection_text=projection_text,
        tiled_epsg=tiled_epsg,
        candidate_epsgs=candidates,
    )


def compute_entity_bounds(entities) -> Optional[tuple[float, float, float, float]]:
    """Compute (min_x, min_y, max_x, max_y) across all finite entity coordinates."""
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf

    for entity in entities:
        coords = getattr(entity, "coordinates", None) or []
        radius = getattr(entity, "radius", 0.0) or 0.0
        is_circle = getattr(entity, "geometry_kind", "") == "Circle" and radius > 0.0

        for coord in coords:
            x = getattr(coord, "x", None)
            y = getattr(coord, "y", None)
            if x is None or y is None:
                continue
            if not (math.isfinite(x) and math.isfinite(y)):
                continue

            if is_circle and math.isfinite(radius):
                min_x = min(min_x, x - radius)
                max_x = max(max_x, x + radius)
                min_y = min(min_y, y - radius)
                max_y = max(max_y, y + radius)
            else:
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)

    if not all(math.isfinite(v) for v in (min_x, min_y, max_x, max_y)):
        return None
    return (min_x, min_y, max_x, max_y)


def format_crs_label(authid: str, description: str) -> str:
    """Format a user-friendly CRS label from authid and description."""
    authid = (authid or "").strip()
    description = (description or "").strip()
    if authid and description:
        return f"{authid} ({description})"
    if authid:
        return authid
    if description:
        return description
    return "Özel CRS"
