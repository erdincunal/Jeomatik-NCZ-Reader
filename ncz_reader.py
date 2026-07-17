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
import os
import re
from dataclasses import dataclass, field

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)

from .ncz_binary import NCZBinaryReader


@dataclass
class LayerBucket:
    display_name: str
    geometry_type: str
    entities: list = field(default_factory=list)


@dataclass
class LayerGroup:
    name: str
    layers: list = field(default_factory=list)


class NCZReaderPlugin:
    FIELD_DEFINITIONS = [
        QgsField("source_file", QVariant.String),
        QgsField("layer_code", QVariant.Int),
        QgsField("layer_name", QVariant.String),
        QgsField("entity_type", QVariant.String),
        QgsField("name", QVariant.String),
        QgsField("label", QVariant.String),
        QgsField("color_argb", QVariant.String),
        QgsField("radius", QVariant.Double),
        QgsField("start_ang", QVariant.Double),
        QgsField("end_ang", QVariant.Double),
        QgsField("text_h", QVariant.Double),
        QgsField("rotation", QVariant.Double),
        QgsField("box_width", QVariant.Double),
        QgsField("box_height", QVariant.Double),
        QgsField("scale", QVariant.Double),
        QgsField("grid_x", QVariant.Double),
        QgsField("grid_y", QVariant.Double),
    ]

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu_name = "Jeomatik"

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.action = QAction(
            QIcon(icon_path),
            "Jeomatik NCZ Reader",
            self.iface.mainWindow(),
        )
        self.action.setObjectName("JeomatikNCZReader")
        self.action.setToolTip("Jeomatik NCZ Reader")
        self.action.setStatusTip("NetCAD NCZ dosyalarını QGIS'e aktar")
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(f"&{self.menu_name}", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu(f"&{self.menu_name}", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def run(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            "Open NCZ File",
            "",
            "NCZ files (*.ncz);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            result = NCZBinaryReader(file_path).parse()
            layer_groups = self._build_layers(file_path, result)
            self._add_layer_groups_to_project(layer_groups)

            message = (
                f"{len(result.entities)} nesne içe aktarıldı"
                f" | ayrıştırıcı: {result.parser_backend}"
            )
            if result.attribute_tables:
                attribute_row_count = sum(
                    len(table.rows)
                    for table in result.attribute_tables
                )
                message += (
                    f" | öznitelikler: {len(result.attribute_tables)} tablo,"
                    f" {attribute_row_count} satır"
                )
            if result.unsupported_geometry_types:
                unsupported = ", ".join(
                    f"{code}:{count}"
                    for code, count in sorted(result.unsupported_geometry_types.items())
                )
                message += f" | desteklenmeyen türler: {unsupported}"

            self.iface.messageBar().pushSuccess("NCZ Reader", message)
        except Exception as exc:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "NCZ Reader",
                str(exc),
            )

    def _build_layers(self, file_path, result):
        if not result.entities and not result.attribute_tables:
            raise ValueError("NCZ içinde okunabilir geometri veya öznitelik bulunamadı.")

        base_name = self._sanitize_name(os.path.splitext(os.path.basename(file_path))[0])
        source_file_name = os.path.splitext(os.path.basename(file_path))[0]
        crs = QgsProject.instance().crs()
        grouped_entities = {}

        for entity in result.entities:
            geometry_family, geometry_type = self._geometry_family(entity.geometry_kind)
            if geometry_family is None:
                continue

            source_name = self._sanitize_name(entity.layer_name or f"LAYER_{entity.layer_code}")
            group_name = f"{base_name}_{geometry_family}"
            layer_key = (
                entity.layer_code,
                entity.layer_name or "",
                geometry_family,
            )
            display_name = f"{source_name}_{geometry_family}"
            grouped_entities.setdefault(group_name, {}).setdefault(
                layer_key,
                LayerBucket(display_name=display_name, geometry_type=geometry_type),
            ).entities.append(entity)

        layer_groups = []
        for group_name in sorted(grouped_entities.keys()):
            layers = []
            used_names = {}
            for layer_key in sorted(grouped_entities[group_name].keys()):
                item = grouped_entities[group_name][layer_key]
                layer_name = self._unique_layer_name(item.display_name, layer_key[0], used_names)
                layers.append(
                    self._create_layer(
                        layer_name,
                        item.geometry_type,
                        item.entities,
                        crs,
                        source_file_name,
                    )
                )
            if layers:
                layer_groups.append(LayerGroup(name=group_name, layers=layers))

        if result.attribute_tables:
            attribute_group_name = f"{base_name}_ATTRIBUTES"
            attribute_layers = []
            for table in sorted(result.attribute_tables, key=lambda item: item.table_ref):
                table_name = self._sanitize_name(table.table_ref or "ATTRIBUTE_TABLE")
                attribute_layers.append(self._create_attribute_table_layer(table_name, table, source_file_name))
            if attribute_layers:
                layer_groups.append(LayerGroup(name=attribute_group_name, layers=attribute_layers))

        return layer_groups

    def _add_layer_groups_to_project(self, layer_groups):
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        for item in layer_groups:
            group = root.addGroup(self._unique_group_name(root, item.name))
            for layer in item.layers:
                project.addMapLayer(layer, False)
                group.addLayer(layer)

    def _unique_group_name(self, root, base_name):
        """Return a group name without replacing an earlier NCZ import."""
        if root.findGroup(base_name) is None:
            return base_name

        suffix = 2
        while root.findGroup(f"{base_name}_{suffix}") is not None:
            suffix += 1
        return f"{base_name}_{suffix}"

    def _geometry_family(self, geometry_kind):
        if geometry_kind in ("Point", "Text", "Symbol", "Block"):
            return "POINT", "Point"
        if geometry_kind in ("Line", "Polyline", "Arc"):
            return "LINE", "LineString"
        if geometry_kind in ("Polygon", "Box", "Circle", "Triangle", "MapSheet", "SmartObject"):
            return "POLYGON", "Polygon"
        return None, None

    def _sanitize_name(self, value):
        text = re.sub(r"\W+", "_", str(value).strip(), flags=re.UNICODE)
        text = text.strip("_")
        return text.upper() or "UNNAMED"

    def _unique_layer_name(self, base_name, layer_code, used_names):
        if base_name not in used_names:
            used_names[base_name] = 1
            return base_name

        candidate = f"{base_name}_L{layer_code}"
        if candidate not in used_names:
            used_names[candidate] = 1
            return candidate

        suffix = used_names[base_name] + 1
        while f"{candidate}_{suffix}" in used_names:
            suffix += 1
        final_name = f"{candidate}_{suffix}"
        used_names[base_name] = suffix
        used_names[final_name] = 1
        return final_name

    def _create_layer(self, layer_name, geometry_type, entities, crs, source_file_name):
        uri = geometry_type
        if crs is not None and crs.isValid():
            uri += f"?crs={crs.authid()}"
        layer = QgsVectorLayer(uri, layer_name, "memory")
        provider = layer.dataProvider()
        provider.addAttributes(list(self.FIELD_DEFINITIONS))
        layer.updateFields()

        features = []
        for entity in entities:
            geometry = self._entity_to_geometry(entity, geometry_type)
            if geometry is None:
                continue
            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            feature.setAttributes(
                [
                    source_file_name,
                    entity.layer_code,
                    entity.layer_name,
                    entity.geometry_kind,
                    entity.name,
                    entity.label_text,
                    "" if entity.color_argb is None else str(entity.color_argb),
                    entity.radius,
                    entity.start_angle,
                    entity.end_angle,
                    entity.text_height,
                    entity.rotation_degrees,
                    entity.box_width,
                    entity.box_height,
                    entity.scale,
                    entity.grid_x,
                    entity.grid_y,
                ]
            )
            features.append(feature)

        provider.addFeatures(features)
        layer.updateExtents()
        if crs is not None and crs.isValid():
            layer.setCrs(crs)
        return layer

    def _create_attribute_table_layer(self, layer_name, table, source_file_name):
        layer = QgsVectorLayer("None", f"{layer_name}_ATTRIBUTES", "memory")
        provider = layer.dataProvider()

        field_names = {"source_file", "table_ref", "row_index"}
        column_types = {}
        for row in table.rows:
            for key, value in row.columns.items():
                field_names.add(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    column_types.setdefault(key, QVariant.Int)
                elif isinstance(value, float):
                    column_types.setdefault(key, QVariant.Double)
                else:
                    column_types.setdefault(key, QVariant.String)

        fixed_field_names = {"source_file", "table_ref", "row_index"}
        ordered_dynamic_names = sorted(
            name for name in field_names if name not in fixed_field_names
        )
        fields = [
            QgsField("source_file", QVariant.String),
            QgsField("table_ref", QVariant.String),
            QgsField("row_index", QVariant.Int),
        ]
        for name in ordered_dynamic_names:
            fields.append(QgsField(name, column_types.get(name, QVariant.String)))

        provider.addAttributes(fields)
        layer.updateFields()

        features = []
        for row in table.rows:
            feature = QgsFeature(layer.fields())
            values = [row.columns.get(name) for name in ordered_dynamic_names]
            feature.setAttributes(
                [
                    source_file_name,
                    table.table_ref,
                    row.row_index,
                    *values,
                ]
            )
            features.append(feature)

        provider.addFeatures(features)
        layer.updateExtents()
        return layer

    def _entity_to_geometry(self, entity, geometry_type):
        if geometry_type == "Point":
            if not entity.coordinates:
                return None
            point = entity.coordinates[0]
            return QgsGeometry.fromPointXY(QgsPointXY(point.x, point.y))

        if geometry_type == "LineString":
            points = self._line_points(entity)
            if len(points) < 2:
                return None
            return QgsGeometry.fromPolylineXY(points)

        if geometry_type == "Polygon":
            ring = self._polygon_points(entity)
            if len(ring) < 4:
                return None
            return QgsGeometry.fromPolygonXY([ring])

        return None

    def _line_points(self, entity):
        if entity.geometry_kind == "Arc":
            return self._approximate_arc(entity)
        return [QgsPointXY(coord.x, coord.y) for coord in entity.coordinates]

    def _polygon_points(self, entity):
        if entity.geometry_kind == "Circle":
            return self._approximate_circle(entity)

        ring = [QgsPointXY(coord.x, coord.y) for coord in entity.coordinates]
        if ring and (ring[0].x() != ring[-1].x() or ring[0].y() != ring[-1].y()):
            ring.append(QgsPointXY(ring[0]))
        return ring

    def _approximate_circle(self, entity, segments=72):
        if not entity.coordinates or entity.radius <= 0:
            return []
        center = entity.coordinates[0]
        points = []
        for index in range(segments):
            angle = (2.0 * 3.141592653589793 * index) / segments
            points.append(
                QgsPointXY(
                    center.x + math.cos(angle) * entity.radius,
                    center.y + math.sin(angle) * entity.radius,
                )
            )
        points.append(QgsPointXY(points[0]))
        return points

    def _approximate_arc(self, entity, segments=48):
        if not entity.coordinates or entity.radius <= 0:
            return []
        center = entity.coordinates[0]
        start = entity.start_angle
        end = entity.end_angle

        # Some NCZ files appear to store arc angles in radians, others behave like degrees.
        # Normalize to degrees before generating the arc polyline.
        if abs(start) <= (2.0 * math.pi + 0.001) and abs(end) <= (2.0 * math.pi + 0.001):
            start = math.degrees(start)
            end = math.degrees(end)

        if not all(math.isfinite(value) for value in (center.x, center.y, entity.radius, start, end)):
            return []

        while end < start:
            end += 360.0
        sweep = end - start
        if sweep <= 0 or sweep > 3600:
            return []
        steps = max(8, int((segments * max(sweep, 1.0)) / 360.0))
        points = []
        for index in range(steps + 1):
            angle_deg = start + (sweep * index / steps)
            angle = math.radians(angle_deg)
            points.append(
                QgsPointXY(
                    center.x + math.cos(angle) * entity.radius,
                    center.y + math.sin(angle) * entity.radius,
                )
            )
        if len(points) >= 2:
            dx = points[-1].x() - points[0].x()
            dy = points[-1].y() - points[0].y()
            chord = math.hypot(dx, dy)
            if chord < 1e-9:
                return []
        return points
