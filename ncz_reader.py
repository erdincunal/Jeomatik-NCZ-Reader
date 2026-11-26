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
from urllib.parse import quote

from qgis.PyQt.QtCore import QSettings, QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsProjectionSelectionWidget

from .ncz_binary import NCZBinaryReader
from .ncz_crs import (
    compute_entity_bounds,
    detect_ncz_crs,
    format_crs_label,
)

_DIALOG_ACCEPTED = getattr(QDialog, "DialogCode", QDialog).Accepted
_STANDARD_BUTTON = getattr(QDialogButtonBox, "StandardButton", QDialogButtonBox)


class NCZCoordinateSystemDialog(QDialog):
    """Dialog prompting the user to confirm or select the CRS for an NCZ import."""

    def __init__(
        self,
        file_path,
        detected_crs,
        initial_crs,
        initial_source_label,
        resolved_detected_epsg="",
        bounds=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Koordinat Sistemi Seçimi - Jeomatik NCZ Reader")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        intro_label = QLabel(
            "İçe aktarılacak NCZ katmanları için kaynak koordinat sistemini (CRS) seçin.\n"
            "Mevcut QGIS proje koordinat sistemi değiştirilmez."
        )
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        form = QFormLayout()

        file_label = QLabel(os.path.basename(file_path))
        file_label.setWordWrap(True)
        form.addRow("Dosya:", file_label)

        detected_summary = self._build_detected_summary(
            detected_crs,
            resolved_detected_epsg,
        )
        detected_label = QLabel(detected_summary)
        detected_label.setWordWrap(True)
        form.addRow("NCZ Projeksiyonu:", detected_label)

        if bounds is not None:
            min_x, min_y, max_x, max_y = bounds
            bounds_label = QLabel(
                f"X: [{min_x:,.3f} .. {max_x:,.3f}]   Y: [{min_y:,.3f} .. {max_y:,.3f}]"
            )
            bounds_label.setWordWrap(True)
            form.addRow("Koordinat Aralığı:", bounds_label)

        self.crs_widget = QgsProjectionSelectionWidget(self)
        crs_option = getattr(
            QgsProjectionSelectionWidget,
            "CrsOption",
            QgsProjectionSelectionWidget,
        )
        for option_name in ("ProjectCrs", "CurrentCrs", "RecentCrs"):
            option = getattr(crs_option, option_name, None)
            if option is not None:
                self.crs_widget.setOptionVisible(option, True)
        if initial_crs is not None and initial_crs.isValid():
            self.crs_widget.setCrs(initial_crs)
        form.addRow("Koordinat Sistemi:", self.crs_widget)

        if initial_source_label:
            source_label = QLabel(initial_source_label)
            source_label.setWordWrap(True)
            form.addRow("Ön Seçim Kaynağı:", source_label)

        layout.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.button_box = QDialogButtonBox(
            _STANDARD_BUTTON.Ok | _STANDARD_BUTTON.Cancel,
            self,
        )
        self.ok_button = self.button_box.button(_STANDARD_BUTTON.Ok)
        cancel_button = self.button_box.button(_STANDARD_BUTTON.Cancel)
        if self.ok_button is not None:
            self.ok_button.setText("İçe Aktar")
        if cancel_button is not None:
            cancel_button.setText("İptal")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.crs_widget.crsChanged.connect(self._on_crs_changed)
        self._on_crs_changed(self.crs_widget.crs())

    def _build_detected_summary(self, detected_crs, resolved_detected_epsg):
        parts = []
        if detected_crs.projection_text:
            parts.append(detected_crs.projection_text)
        if resolved_detected_epsg:
            crs_obj = QgsCoordinateReferenceSystem(resolved_detected_epsg)
            if crs_obj.isValid():
                parts.append(
                    f"Önerilen: {format_crs_label(crs_obj.authid(), crs_obj.description())}"
                )
            else:
                parts.append(f"Önerilen: {resolved_detected_epsg}")
        elif detected_crs.tiled_epsg and detected_crs.tiled_epsg != resolved_detected_epsg:
            parts.append(f"TILED_XML: {detected_crs.tiled_epsg} (geçerli CRS bulunamadı)")

        if parts:
            if not resolved_detected_epsg and detected_crs.projection_text:
                parts.append("(otomatik EPSG eşleştirilemedi)")
            return " | ".join(parts)
        return "Dosyada projeksiyon bilgisi bulunamadı"

    def _on_crs_changed(self, crs):
        is_valid = crs is not None and crs.isValid()
        if self.ok_button is not None:
            self.ok_button.setEnabled(is_valid)
        if is_valid:
            label = format_crs_label(crs.authid(), crs.description())
            self.status_label.setStyleSheet("")
            self.status_label.setText(f"Seçili CRS: {label}")
        else:
            self.status_label.setStyleSheet("color: #b00020;")
            self.status_label.setText("Lütfen geçerli bir koordinat sistemi (CRS) seçin.")

    def selected_crs(self):
        return self.crs_widget.crs()


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
    SETTINGS_LAST_CRS_KEY = "JeomatikNCZReader/last_crs"

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
            if not result.entities and not result.attribute_tables:
                raise ValueError(
                    "NCZ içinde okunabilir geometri veya öznitelik bulunamadı."
                )

            selected_crs = None
            if result.entities:
                selected_crs = self._prompt_for_crs(file_path, result)
                if selected_crs is None:
                    return
                self._save_last_crs(selected_crs)

            layer_groups = self._build_layers(file_path, result, crs=selected_crs)
            self._add_layer_groups_to_project(layer_groups)
            if selected_crs is not None:
                self._zoom_to_layer_groups(layer_groups)

            message = (
                f"{len(result.entities)} nesne içe aktarıldı"
                f" | ayrıştırıcı: {result.parser_backend}"
            )
            if selected_crs is not None and selected_crs.isValid():
                crs_label = format_crs_label(
                    selected_crs.authid(),
                    selected_crs.description(),
                )
                message += f" | CRS: {crs_label}"
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

    def _resolve_initial_crs(self, detected_crs):
        for candidate_code in detected_crs.candidate_epsgs:
            candidate_crs = QgsCoordinateReferenceSystem(candidate_code)
            if candidate_crs.isValid():
                source_tag = (
                    "MPROJ"
                    if candidate_code == detected_crs.epsg and detected_crs.source == "MPROJ"
                    else "TILED_XML"
                )
                return (
                    candidate_crs,
                    f"NCZ dosyasından algılandı ({source_tag})",
                    candidate_code,
                )

        saved_value = QSettings().value(self.SETTINGS_LAST_CRS_KEY, "")
        saved_text = str(saved_value).strip() if saved_value else ""
        if saved_text:
            saved_crs = QgsCoordinateReferenceSystem(saved_text)
            if saved_crs.isValid():
                return saved_crs, "Son seçilen koordinat sistemi", ""

        project_crs = QgsProject.instance().crs()
        if project_crs is not None and project_crs.isValid():
            return project_crs, "Aktif QGIS proje koordinat sistemi", ""

        return (
            QgsCoordinateReferenceSystem("EPSG:4326"),
            "Varsayılan koordinat sistemi",
            "",
        )

    def _save_last_crs(self, crs):
        if crs is None or not crs.isValid():
            return
        serialized = crs.authid() or crs.toWkt()
        if serialized:
            QSettings().setValue(self.SETTINGS_LAST_CRS_KEY, serialized)

    def _prompt_for_crs(self, file_path, result):
        detected_crs = detect_ncz_crs(result)
        initial_crs, source_label, resolved_detected_epsg = self._resolve_initial_crs(
            detected_crs
        )
        bounds = compute_entity_bounds(result.entities)
        dialog = NCZCoordinateSystemDialog(
            file_path=file_path,
            detected_crs=detected_crs,
            initial_crs=initial_crs,
            initial_source_label=source_label,
            resolved_detected_epsg=resolved_detected_epsg,
            bounds=bounds,
            parent=self.iface.mainWindow(),
        )
        if dialog.exec() != _DIALOG_ACCEPTED:
            return None
        selected_crs = dialog.selected_crs()
        if selected_crs is None or not selected_crs.isValid():
            return None
        return selected_crs

    def _zoom_to_layer_groups(self, layer_groups):
        canvas = self.iface.mapCanvas() if hasattr(self.iface, "mapCanvas") else None
        if canvas is None:
            return

        project = QgsProject.instance()
        target_crs = canvas.mapSettings().destinationCrs()
        if target_crs is None or not target_crs.isValid():
            target_crs = project.crs()

        combined_extent = None
        for group in layer_groups:
            for layer in group.layers:
                if not layer.isSpatial() or layer.featureCount() == 0:
                    continue
                extent = layer.extent()
                if extent is None or extent.isNull():
                    continue
                if (
                    target_crs is not None
                    and target_crs.isValid()
                    and layer.crs().isValid()
                    and layer.crs() != target_crs
                ):
                    try:
                        transform = QgsCoordinateTransform(
                            layer.crs(),
                            target_crs,
                            project,
                        )
                        extent = transform.transformBoundingBox(extent)
                    except Exception:
                        continue
                if extent.isNull():
                    continue
                if combined_extent is None:
                    combined_extent = QgsRectangle(extent)
                else:
                    combined_extent.combineExtentWith(extent)

        if combined_extent is None or combined_extent.isNull():
            return

        if combined_extent.width() == 0 and combined_extent.height() == 0:
            is_geographic = (
                target_crs is not None
                and target_crs.isValid()
                and target_crs.isGeographic()
            )
            combined_extent.grow(0.001 if is_geographic else 50.0)
        else:
            combined_extent.scale(1.05)

        canvas.setExtent(combined_extent)
        canvas.refresh()

    def _build_layers(self, file_path, result, crs=None):
        if not result.entities and not result.attribute_tables:
            raise ValueError("NCZ içinde okunabilir geometri veya öznitelik bulunamadı.")

        base_name = self._sanitize_name(os.path.splitext(os.path.basename(file_path))[0])
        source_file_name = os.path.splitext(os.path.basename(file_path))[0]
        if crs is None:
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
            authid = crs.authid()
            if authid:
                uri += f"?crs={authid}"
            else:
                wkt = crs.toWkt()
                if wkt:
                    uri += f"?crs=wkt:{quote(wkt, safe='')}"
        layer = QgsVectorLayer(uri, layer_name, "memory")
        if not layer.isValid():
            layer = QgsVectorLayer(geometry_type, layer_name, "memory")
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
