from __future__ import annotations

import math
import logging
from collections import defaultdict
from typing import Iterable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .carousels import SidebarCarousel
from .coherence_distance import (
    AnalyzerDistancePayload,
    _distance_between_payloads,
    _distance_payload_for_vector,
)
from .coherence_geometry import (
    _layer_point,
    _sunflower_offsets,
)
from . import coherence_projection
from .coherence_math import (
    _degenerate_projection,
    _distance_matrix,
    _mds_coords,
    _normalize_coords,
    _stable_hash,
    _stable_hue,
)
from .coherence_view_model import AnalyzerPoint, analyzer_data_key, coherence_points_from_app, points_signature
from ..utils.layout_helpers import apply_layout_margins, apply_layout_spacing
from ..utils.styles import ColorPalette, apply_style, button_style, make_qcolor, scaled_px
from ..utils.widget_helpers import apply_fixed_height


class CoherenceMapWidget(QWidget):
    audioPreviewRequested = Signal(str)
    anchorRequested = Signal(str)
    findRequested = Signal(str)
    vibeRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[AnalyzerPoint] = []
        self._points_version = None
        self._projected: list[tuple[AnalyzerPoint, QPointF]] = []
        self._projected_clusters: dict[str, tuple[str, QPointF]] = {}
        self._projection_cache: dict[tuple[object, str, str], tuple[list[tuple[AnalyzerPoint, QPointF]], dict[str, tuple[str, QPointF]]]] = {}
        self._distance_payloads: dict[str, AnalyzerDistancePayload] = {}
        self._background_layer_count = 0
        self._audio_type = "Loops"
        self._category_filter = ""
        self._visible_record_ids: set[str] | None = None
        self._zoom_level = 2
        self._cached_bg_pixmap = None
        self._cached_points_pixmap = None
        self.setMinimumHeight(scaled_px(260))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_points(self, points: Iterable[AnalyzerPoint], version=None) -> None:
        self.configure(points=points, version=version)

    def set_audio_type(self, audio_type: str) -> None:
        self.configure(audio_type=audio_type)

    def set_category_filter(self, category: str) -> None:
        self.configure(category=category)

    def _invalidate_cache(self) -> None:
        self._cached_bg_pixmap = None
        self._cached_points_pixmap = None

    def set_visible_record_ids(self, record_ids: set[str] | None) -> None:
        new_ids = set(record_ids) if record_ids is not None else None
        if self._visible_record_ids == new_ids:
            return
        self._visible_record_ids = new_ids
        self._cached_points_pixmap = None  
        self.update()

    def configure(self, *, points=None, audio_type=None, category=None, version=None) -> None:
        changed = False
        if points is not None:
            new_points = list(points)
            if version is None:
                version = points_signature(new_points)
            if version != self._points_version:
                self._points = new_points
                self._points_version = version
                self._projection_cache = {}
                self._distance_payloads = _distance_payloads_for_points(new_points)
                changed = True
        if audio_type is not None:
            new_audio_type = str(audio_type or "")
            if new_audio_type != self._audio_type:
                self._audio_type = new_audio_type
                changed = True
        if category is not None:
            new_category = str(category or "")
            if new_category != self._category_filter:
                self._category_filter = new_category
                changed = True
        if changed:
            self._reproject()

    def set_zoom_level(self, level: int) -> None:
        self._zoom_level = max(1, min(4, level))
        self._invalidate_cache()
        self.update()

    def mousePressEvent(self, event):  
        point = self._point_at(event.position())
        if point is None:
            return super().mousePressEvent(event)
        if event.button() == Qt.RightButton:
            self._show_point_menu(point, event.globalPosition().toPoint())
            return
        if event.button() == Qt.LeftButton and point.source_path:
            self.audioPreviewRequested.emit(point.source_path)
            return
        return super().mousePressEvent(event)

    def _point_at(self, pos: QPointF) -> AnalyzerPoint | None:
        rect = self.rect().adjusted(scaled_px(14), scaled_px(14), -scaled_px(14), -scaled_px(14))
        zoom = _map_zoom_factor(self._zoom_level)
        best: tuple[float, AnalyzerPoint] | None = None
        for point, projected in self._projected:
            screen = self._screen_point(projected, rect, zoom)
            distance = math.hypot(screen.x() - pos.x(), screen.y() - pos.y())
            if distance <= scaled_px(7) and (best is None or distance < best[0]):
                best = (distance, point)
        return best[1] if best else None

    def _show_point_menu(self, point: AnalyzerPoint, global_pos) -> None:
        menu = QMenu(self)
        play = menu.addAction("Play Sample")
        play.setEnabled(bool(point.source_path))
        vibe = menu.addAction("Similarity Explorer")
        vibe.setEnabled(bool(point.source_path))
        filter_by = menu.addAction("Filter by Sample")
        filter_by.setEnabled(bool(point.source_path))
        anchor = menu.addAction("Add as Anchor")
        action = menu.exec(global_pos)
        if action is play and point.source_path:
            self.audioPreviewRequested.emit(point.source_path)
        elif action is vibe and point.source_path:
            self.vibeRequested.emit(point.source_path)
        elif action is filter_by and point.source_path:
            self.findRequested.emit(point.source_path)
        elif action is anchor:
            self.anchorRequested.emit(point.record_id)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._invalidate_cache()
        self.update()

    def _reproject(self) -> None:
        cache_key = (self._points_version, self._audio_type, self._category_filter)
        cached = self._projection_cache.get(cache_key)
        if cached is not None:
            self._projected, self._projected_clusters = cached
            self._background_layer_count = coherence_projection._background_layer_count(self._projected, self._category_filter)
            self._invalidate_cache()
            self.update()
            return
        self._projected, self._projected_clusters = self._project_for(self._audio_type, self._category_filter)
        self._background_layer_count = coherence_projection._background_layer_count(self._projected, self._category_filter)
        self._projection_cache[cache_key] = (self._projected, self._projected_clusters)
        self._invalidate_cache()
        self.update()

    def prewarm_projection(self, audio_type: str, category: str = "") -> None:
        cache_key = (self._points_version, (audio_type or ""), (category or ""))
        if cache_key not in self._projection_cache:
            self._projection_cache[cache_key] = self._project_for((audio_type or ""), (category or ""))

    def _project_for(self, audio_type: str, category: str) -> tuple[list[tuple[AnalyzerPoint, QPointF]], dict[str, tuple[str, QPointF]]]:
        selected = [
            point
            for point in self._points
            if (not audio_type or point.audio_type == audio_type)
            and (not category or point.category == category)
        ]
        if not selected:
            return [], {}
        projected = coherence_projection._continuous_acoustic_projection(
            selected,
            self._point_distance,
            vector_distance_fn=self._vector_distance,
            category_layers=not bool(category),
        )
        projected_clusters: dict[str, tuple[str, QPointF]] = {}
        by_cluster: dict[str, list[QPointF]] = defaultdict(list)
        category_by_cluster: dict[str, str] = {}
        for point, pos in projected:
            by_cluster[point.cluster_id].append(pos)
            category_by_cluster.setdefault(point.cluster_id, point.category)
        for cluster_id, positions in by_cluster.items():
            if not positions:
                continue
            projected_clusters[cluster_id] = (
                category_by_cluster.get(cluster_id, ""),
                QPointF(
                    sum(pos.x() for pos in positions) / len(positions),
                    sum(pos.y() for pos in positions) / len(positions),
                ),
            )
        return projected, projected_clusters

    def _cluster_representative(self, points: list[AnalyzerPoint]) -> list[float]:
        if not points:
            return []
        if len(points) == 1:
            return points[0].vector
        return coherence_projection._mean_vector(points)

    def _project_cluster_centers(self, cluster_vectors: list[list[float]]) -> list[QPointF]:
        count = len(cluster_vectors)
        if count <= 0:
            return []
        if count == 1:
            return [QPointF(0.5, 0.5)]
        if count == 2:
            return [QPointF(0.38, 0.5), QPointF(0.62, 0.5)]
        coords = _mds_coords(_distance_matrix(cluster_vectors, self._vector_distance))
        if coords.shape[1] < 2:
            coords = np.pad(coords, ((0, 0), (0, 2 - coords.shape[1])))
        if _degenerate_projection(coords):
            angles = np.linspace(0, math.tau, count, endpoint=False)
            coords = np.column_stack((np.cos(angles), np.sin(angles)))
        coords = _normalize_coords(coords, margin=0.12)
        return [QPointF(float(x), float(y)) for x, y in coords]

    def _local_cluster_offsets(self, points: list[AnalyzerPoint], representative: list[float]) -> list[QPointF]:
        count = len(points)
        if count <= 1:
            return [QPointF(0.0, 0.0)]
        if count <= 180:
            coords = _mds_coords(coherence_projection._distance_matrix_for_points(points, self._point_distance))
            if coords.shape[1] >= 2 and not _degenerate_projection(coords):
                coords = _normalize_coords(coords, margin=-1.0)
                coords = (coords - 0.5) * 2.0
                return [QPointF(float(x), float(y)) for x, y in coords]
        landmark_offsets = coherence_projection._landmark_cluster_offsets(
            points,
            representative,
            self._point_distance,
            vector_distance_fn=self._vector_distance,
            sunflower_shell=True,
        )
        if landmark_offsets:
            return landmark_offsets
        offsets = _sunflower_offsets(count)
        ordered_indexes = sorted(
            range(count),
            key=lambda idx: (coherence_projection._squared_vector_distance(points[idx].vector, representative), str(points[idx].record_id)),
        )
        by_original_index = [QPointF(0.0, 0.0) for _ in range(count)]
        for offset, original_index in zip(offsets, ordered_indexes):
            by_original_index[original_index] = offset
        return by_original_index

    def _point_distance(self, left: AnalyzerPoint, right: AnalyzerPoint) -> float:
        distance = _distance_between_payloads(
            self._distance_payloads.get(left.record_id),
            self._distance_payloads.get(right.record_id),
        )
        if not math.isfinite(distance):
            return 10.0
        return max(0.0, distance)

    def _vector_distance(self, left: list[float], right: list[float]) -> float:
        distance = _distance_between_payloads(
            _distance_payload_for_vector(left or []),
            _distance_payload_for_vector(right or []),
        )
        if not math.isfinite(distance):
            return 10.0
        return max(0.0, distance)

    def paintEvent(self, _event): 
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), make_qcolor(ColorPalette.BG_LIST))
        rect = self.rect().adjusted(scaled_px(14), scaled_px(14), -scaled_px(14), -scaled_px(14))
        if not self._projected:
            painter.setPen(make_qcolor(ColorPalette.TEXT_MUTED))
            scope = f"{self._audio_type.lower()} " if self._audio_type else ""
            painter.drawText(rect, Qt.AlignCenter, f"No {scope}sound-map points yet.")
            return

        zoom = _map_zoom_factor(self._zoom_level)

        # 1. Concentric backgrounds caching
        if self._cached_bg_pixmap is None or self._cached_bg_pixmap.size() != self.size():
            from PySide6.QtGui import QPixmap
            self._cached_bg_pixmap = QPixmap(self.size())
            self._cached_bg_pixmap.fill(Qt.transparent)
            bg_painter = QPainter(self._cached_bg_pixmap)
            bg_painter.setRenderHint(QPainter.Antialiasing)
            self._paint_layer_backgrounds(bg_painter, QRectF(rect), zoom)
            bg_painter.end()

        painter.drawPixmap(0, 0, self._cached_bg_pixmap)

        # 2. Points caching
        if self._cached_points_pixmap is None or self._cached_points_pixmap.size() != self.size():
            from PySide6.QtGui import QPixmap
            self._cached_points_pixmap = QPixmap(self.size())
            self._cached_points_pixmap.fill(Qt.transparent)
            pts_painter = QPainter(self._cached_points_pixmap)
            pts_painter.setRenderHint(QPainter.Antialiasing)
            
            rect_w = float(rect.width())
            rect_h = float(rect.height())
            side = min(rect_w, rect_h)
            left = float(rect.left()) + (rect_w - side) / 2.0
            top = float(rect.top()) + (rect_h - side) / 2.0
            
            points = []
            for point, pos in self._projected:
                cx = (pos.x() - 0.5) * zoom + 0.5
                cy = (pos.y() - 0.5) * zoom + 0.5
                points.append((point, QPointF(left + cx * side, top + cy * side)))
                
            self._paint_points(pts_painter, points)
            pts_painter.end()

        painter.drawPixmap(0, 0, self._cached_points_pixmap)
        painter.setPen(make_qcolor(ColorPalette.TEXT_MUTED))

    def _screen_point(self, pos: QPointF, rect, zoom: float) -> QPointF:
        centered_x = (pos.x() - 0.5) * zoom + 0.5
        centered_y = (pos.y() - 0.5) * zoom + 0.5
        side = min(float(rect.width()), float(rect.height()))
        left = float(rect.left()) + (float(rect.width()) - side) / 2.0
        top = float(rect.top()) + (float(rect.height()) - side) / 2.0
        return QPointF(left + centered_x * side, top + centered_y * side)

    def _paint_points(self, painter: QPainter, points: list[tuple[AnalyzerPoint, QPointF]]) -> None:
        from collections import defaultdict
        visible_ids = self._visible_record_ids
        
        dimmed_color = make_qcolor(ColorPalette.TEXT_MUTED)
        dimmed_color.setAlpha(70)
        
        dimmed_points = []
        normal_by_cluster = defaultdict(list)
        
        for point, pos in points:
            if visible_ids is not None and point.record_id not in visible_ids:
                dimmed_points.append(pos)
            else:
                normal_by_cluster[(point.cluster_id, point.category)].append(pos)
                
        painter.setPen(Qt.NoPen)
        
        if dimmed_points:
            painter.setBrush(dimmed_color)
            rx_dim = scaled_px(1.5)
            for pos in dimmed_points:
                painter.drawEllipse(pos, rx_dim, rx_dim)
                
        rx_norm = scaled_px(1.7)
        for (cluster_id, category), positions in normal_by_cluster.items():
            painter.setBrush(_cluster_color(cluster_id, category))
            for pos in positions:
                painter.drawEllipse(pos, rx_norm, rx_norm)

    def _paint_layer_backgrounds(self, painter: QPainter, rect: QRectF, zoom: float) -> None:
        layer_count = self._background_layer_count or coherence_projection._background_layer_count(self._projected, self._category_filter)
        if layer_count <= 0:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        for layer in range(layer_count):
            inner, outer = coherence_projection._layer_band_bounds(layer, layer_count)
            color = _alternating_window_band_color(layer, layer_count)
            path = QPainterPath()
            steps = 96
            for step in range(steps + 1):
                t = step / steps
                pos = _layer_point(t, outer)
                screen = self._screen_point(pos, rect, zoom)
                if step == 0:
                    path.moveTo(screen)
                else:
                    path.lineTo(screen)
            for step in range(steps, -1, -1):
                t = step / steps
                pos = _layer_point(t, inner)
                screen = self._screen_point(pos, rect, zoom)
                path.lineTo(screen)
            path.closeSubpath()
            painter.setBrush(color)
            painter.drawPath(path)
        painter.restore()

    def refresh_theme(self) -> None:
        self._invalidate_cache()
        self.update()


class CoherenceAnalyzerPage(QFrame):
    runCoherenceRequested = Signal()
    continuousRefinementRequested = Signal()
    autoCheckChanged = Signal(bool)
    audioPreviewRequested = Signal(str)
    anchorRequested = Signal(str)
    findRequested = Signal(str)
    vibeRequested = Signal(str)

    def __init__(
        self,
        parent=None,
        *,
        show_header: bool = True,
        show_filters: bool = True,
        show_zoom: bool = True,
        default_zoom: int = 2,
    ):
        super().__init__(parent)
        self._show_header = show_header
        self._show_filters = show_filters
        self._show_zoom = show_zoom
        self._default_zoom = max(1, min(4, (default_zoom or 2)))
        self._records: list[AnalyzerPoint] = []
        self._results: list[dict] = []
        self._data_key = None
        self._selected_audio_type = "Loops"
        self._selected_category = ""
        self._settings = None
        self._loading_state = False
        self._is_loading = False
        self.header_label = None
        self.category_carousel = None
        self.type_buttons = {}
        self.zoom_label = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        apply_layout_margins(layout, (12, 12, 12, 12))
        apply_layout_spacing(layout, 10)
        if self._show_header:
            title_row = QHBoxLayout()
            self.header_label = _section_header("Sound Map")
            title_row.addWidget(self.header_label, 1)
            layout.addLayout(title_row)

        if self._show_filters:
            map_toolbar = QHBoxLayout()
            map_toolbar.addWidget(QLabel("View"))
            self.type_group = QButtonGroup(self)
            for label, value in (("Loops", "Loops"), ("Oneshots", "Oneshots")):
                button = QPushButton(label)
                button.setCheckable(True)
                button.clicked.connect(lambda checked=False, v=value: self._set_audio_type(v))
                self.type_group.addButton(button)
                self.type_buttons[value] = button
                map_toolbar.addWidget(button)
                if value == "Loops":
                    button.setChecked(True)
            map_toolbar.addSpacing(scaled_px(12))
            self.category_carousel = SidebarCarousel("Category", [], self, compact=True)
            self.category_carousel.valueSelected.connect(self._set_category_filter)
            self.category_carousel.activeChanged.connect(self._on_category_active_changed)
            map_toolbar.addWidget(self.category_carousel)
            map_toolbar.addStretch(1)
            layout.addLayout(map_toolbar)

        self.map = CoherenceMapWidget()
        self.map.audioPreviewRequested.connect(self.audioPreviewRequested.emit)
        self.map.anchorRequested.connect(self.anchorRequested.emit)
        self.map.findRequested.connect(self.findRequested.emit)
        self.map.vibeRequested.connect(self.vibeRequested.emit)
        self.map_stage = QFrame()
        self.map_stage.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        stage_layout = QGridLayout(self.map_stage)
        apply_layout_margins(stage_layout, (0, 0, 0, 0))
        apply_layout_spacing(stage_layout, 0)
        stage_layout.addWidget(self.map, 0, 0)
        self.loading_panel = QFrame()
        self.loading_panel.setObjectName("AnalyzerLoadingPanel")
        loading_layout = QVBoxLayout(self.loading_panel)
        apply_layout_margins(loading_layout, (24, 24, 24, 24))
        apply_layout_spacing(loading_layout, 10)
        loading_layout.addStretch(1)
        self.loading_label = QLabel("Preparing view...")
        self.loading_label.setAlignment(Qt.AlignCenter)
        loading_layout.addWidget(self.loading_label)
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedHeight(scaled_px(6))
        self.loading_bar.setMaximumWidth(scaled_px(260))
        loading_layout.addWidget(self.loading_bar, 0, Qt.AlignCenter)
        loading_layout.addStretch(1)
        stage_layout.addWidget(self.loading_panel, 0, 0)
        self.loading_panel.hide()
        layout.addWidget(self.map_stage, 1)
        self.audio_reserve = QWidget()
        self.audio_reserve.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.audio_reserve.setMinimumHeight(0)
        self.audio_reserve.setMaximumHeight(scaled_px(48))
        layout.addWidget(self.audio_reserve, 0)
        self.status = QLabel("")
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.status, 1)
        self.zoom_label = QLabel("Zoom")
        bottom_row.addWidget(self.zoom_label)
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["1", "2", "3", "4"])
        self.zoom_combo.setCurrentText(str(self._default_zoom))
        self.map.set_zoom_level(self._default_zoom)
        self.zoom_combo.currentTextChanged.connect(self._set_zoom_level)
        bottom_row.addWidget(self.zoom_combo)
        self.zoom_label.setVisible(self._show_zoom)
        self.zoom_combo.setVisible(self._show_zoom)
        layout.addLayout(bottom_row)
        self.refresh_theme()

    def set_loading(self, loading: bool, message: str = "Preparing view...") -> None:
        self._is_loading = loading
        self.loading_label.setText(message or "Preparing view...")
        self.loading_panel.setVisible(self._is_loading)
        self.loading_panel.raise_()
        for widget in (self.map, self.zoom_combo):
            widget.setEnabled(not self._is_loading)

    def set_auto_checked(self, checked: bool) -> None:
        return

    def refresh_from_app(self, app, *, force: bool = False, audio_type: str | None = None, category: str | None = None) -> None:
        try:
            try:
                self._settings = getattr(app, "settings", None)
                if self._show_filters:
                    self._load_state()
                if audio_type is not None:
                    self._selected_audio_type = (audio_type or "")
                if category is not None:
                    self._selected_category = (category or "")
                records, results = coherence_points_from_app(app)
                data_key = analyzer_data_key(app, records, results)
                if not force and data_key == self._data_key:
                    self._refresh_category_options()
                    self._sync_analyzer_data()
                    self.status.setText(f"{len(results)} sound-map results loaded.")
                    return
                self._records = records
                self._results = results
                self._data_key = data_key
                self._refresh_category_options()
                self._sync_analyzer_data()
                if not self._show_filters:
                    self.status.setText(f"{len(results)} sound-map results loaded.")
                    return
                types = {point.audio_type for point in records}
                if self._selected_audio_type not in types and "Loops" not in types and "Oneshots" in types:
                    self.type_buttons["Oneshots"].setChecked(True)
                    self._set_audio_type("Oneshots")
                elif self._selected_audio_type not in types and "Loops" in types:
                    self.type_buttons["Loops"].setChecked(True)
                    self._set_audio_type("Loops")
                elif self._selected_audio_type in self.type_buttons:
                    self.type_buttons[self._selected_audio_type].setChecked(True)
                    self._set_audio_type(self._selected_audio_type)
                self.status.setText(f"{len(results)} sound-map results loaded.")
            except Exception:
                logging.exception("Sound map refresh failed.")
                self._records = []
                self._results = []
                self._data_key = None
                self.map.set_points([], version=None)
                if self.category_carousel is not None:
                    self.category_carousel.set_options([("Global", "")])
                self.status.setText("Sound map could not be refreshed.")
        finally:
            self.set_loading(False)

    def _refresh_category_options(self) -> None:
        if self.category_carousel is None:
            return
        categories = sorted(
            {
                point.category
                for point in self._records
                if point.category and (not self._selected_audio_type or point.audio_type == self._selected_audio_type)
            }
        )
        options = [("Global", "")] + [(category, category) for category in categories]
        self.category_carousel.set_options(options)
        if self._selected_category and self._selected_category not in categories:
            self._selected_category = ""
        self.category_carousel.set_active_values({self._selected_category} if self._selected_category else set())
        self.category_carousel.set_current_value(self._selected_category)

    def _set_category_filter(self, category: str) -> None:
        self._selected_category = (category or "")
        if self.category_carousel is not None:
            self.category_carousel.set_active_values({self._selected_category} if self._selected_category else set())
            self.category_carousel.set_current_value(self._selected_category)
        self.map.set_category_filter(self._selected_category)
        self._save_state()

    def _set_audio_type(self, audio_type: str) -> None:
        audio_type = (audio_type or "")
        self._selected_audio_type = audio_type
        self._refresh_category_options()
        self.map.set_audio_type(audio_type)
        self.map.set_category_filter(self._selected_category)
        self._save_state()

    def _on_category_active_changed(self, category: str, active: bool) -> None:
        if self.category_carousel is None:
            return
        if active:
            self._set_category_filter((category or ""))
            return
        self._selected_category = ""
        self.category_carousel.set_current_value("")
        self.map.set_category_filter("")
        self._save_state()

    def _set_zoom_level(self, value: str) -> None:
        try:
            level = int(value or 2)
        except ValueError:
            level = 2
        self.map.set_zoom_level(level)
        self._save_state()

    def _load_state(self) -> None:
        if self._settings is None:
            return
        self._loading_state = True
        try:
            audio_type = str(self._settings.value("coherence_analyzer/audio_type", self._selected_audio_type) or "Loops")
            if audio_type in {"", "Loops", "Oneshots"}:
                self._selected_audio_type = audio_type
            self._selected_category = str(self._settings.value("coherence_analyzer/category", self._selected_category) or "")
            if not self._show_zoom:
                self.zoom_combo.setCurrentText(str(self._default_zoom))
                self.map.set_zoom_level(self._default_zoom)
                return
            zoom = str(self._settings.value("coherence_analyzer/zoom", self.zoom_combo.currentText()) or str(self._default_zoom))
            if zoom in {"1", "2", "3", "4"}:
                self.zoom_combo.blockSignals(True)
                self.zoom_combo.setCurrentText(zoom)
                self.zoom_combo.blockSignals(False)
                self.map.set_zoom_level(int(zoom))
        finally:
            self._loading_state = False

    def _save_state(self) -> None:
        if self._loading_state or self._settings is None:
            return
        if not self._show_zoom:
            self.map.set_zoom_level(self._default_zoom)
        self._settings.setValue("coherence_analyzer/audio_type", self._selected_audio_type)
        self._settings.setValue("coherence_analyzer/category", self._selected_category)
        if self._show_zoom:
            self._settings.setValue("coherence_analyzer/zoom", self.zoom_combo.currentText())

    def _sync_analyzer_data(self) -> None:
        self.map.configure(
            points=self._records,
            audio_type=self._selected_audio_type,
            category=self._selected_category,
            version=self._data_key,
        )

    def prewarm_library_projections(self, *, frontload: bool = False) -> None:
        if frontload:
            for audio_type in ("Loops", "Oneshots"):
                self.map.prewarm_projection(audio_type, "")
            return

        categories_by_type: dict[str, set[str]] = {"Loops": set(), "Oneshots": set()}
        for point in self._records:
            audio_type = str(getattr(point, "audio_type", "") or "")
            category = str(getattr(point, "category", "") or "")
            if audio_type in categories_by_type and category:
                categories_by_type[audio_type].add(category)
        for audio_type in ("Loops", "Oneshots"):
            self.map.prewarm_projection(audio_type, "")
            for category in sorted(categories_by_type[audio_type]):
                self.map.prewarm_projection(audio_type, category)

    def set_library_filters(self, audio_type: str, category: str, visible_record_ids: set[str] | None = None) -> None:
        """Mirror the Library sidebar filters when embedded as a Library view."""
        new_audio_type = (audio_type or "")
        new_category = (category or "")
        if new_audio_type == self._selected_audio_type and new_category == self._selected_category:
            self.map.set_visible_record_ids(visible_record_ids)
            return
        self._selected_audio_type = new_audio_type
        self._selected_category = new_category
        self._sync_analyzer_data()
        self.map.set_visible_record_ids(visible_record_ids)
        self._save_state()

    def refresh_theme(self) -> None:
        apply_style(self, f"QFrame {{ background: {ColorPalette.BG_LIST}; border: none; border-radius: {scaled_px(8)}px; }}")
        apply_style(
            self.loading_panel,
            (
                f"QFrame#AnalyzerLoadingPanel {{ background: {ColorPalette.BG_LIST}; border: none; }}"
                f"QLabel {{ color: {ColorPalette.TEXT_LIGHT}; }}"
                f"QProgressBar {{ background: {ColorPalette.BG_DARK}; border: none; border-radius: {scaled_px(3)}px; }}"
                f"QProgressBar::chunk {{ background: {ColorPalette.BG_ACCENT}; border-radius: {scaled_px(3)}px; }}"
            ),
        )
        if self.header_label is not None:
            _apply_section_header_style(self.header_label)
        for button in self.findChildren(QPushButton):
            apply_style(
                button,
                (
                    f"{button_style('secondary', size='normal')}"
                    f"QPushButton:checked {{ background: {ColorPalette.PRIMARY}; color: {ColorPalette.TEXT_INVERSE}; }}"
                ),
            )
        apply_style(
            self.zoom_combo,
            (
                f"QComboBox {{ background: {ColorPalette.BG_HOVER}; color: {ColorPalette.TEXT_LIGHT}; border: none; "
                f"border-radius: {scaled_px(4)}px; padding: 0 {scaled_px(10)}px; min-height: {scaled_px(30)}px; font-weight: bold; }}"
                f"QComboBox QAbstractItemView {{ background: {ColorPalette.BG_DROPDOWN}; color: {ColorPalette.TEXT_MAIN}; "
                f"selection-background-color: {ColorPalette.PRIMARY}; outline: none; }}"
            ),
        )
        apply_style(self.status, f"color: {ColorPalette.TEXT_MUTED}; background: transparent;")
        if self.category_carousel is not None:
            self.category_carousel.refresh_theme()
        self.map.refresh_theme()


def _section_header(title: str) -> QLabel:
    label = QLabel(title)
    apply_fixed_height(label, scaled_px(38))
    _apply_section_header_style(label)
    return label


def _apply_section_header_style(label: QLabel) -> None:
    apply_style(
        label,
        f"QLabel {{ background: {ColorPalette.BG_HOVER}; color: {ColorPalette.TEXT_HEADER}; "
        f"font-weight: bold; border-radius: {scaled_px(4)}px; padding: {scaled_px(8)}px {scaled_px(12)}px; }}",
    )


from functools import lru_cache

@lru_cache(maxsize=8192)
def _cluster_color(cluster_id: str, category: str = "") -> QColor:
    base_hue = _stable_hue(category or cluster_id)
    offset = (_stable_hash(cluster_id) % 31) - 15
    return QColor.fromHsv((base_hue + offset) % 360, 165, 218)


def _map_zoom_factor(level: int) -> float:
    return {1: 0.76, 2: 0.84, 3: 0.92, 4: 1.0}.get((level or 2), 0.84)


def _distance_payloads_for_points(points: list[AnalyzerPoint]) -> dict[str, AnalyzerDistancePayload]:
    return {
        point.record_id: payload
        for point in points
        if (payload := _distance_payload_for_vector(point.vector)) is not None
    }


def _alternating_window_band_color(layer: int, layer_count: int) -> QColor:
    if (layer_count - layer) % 2 == 0:
        return make_qcolor(ColorPalette.BG_LIST)
    base = make_qcolor(ColorPalette.BG_LIST)
    if base.lightness() >= 128:
        target = make_qcolor(ColorPalette.TEXT_MAIN)
        amount = 0.085
        return QColor(
            int(base.red() + (target.red() - base.red()) * amount),
            int(base.green() + (target.green() - base.green()) * amount),
            int(base.blue() + (target.blue() - base.blue()) * amount),
        )
    return make_qcolor(ColorPalette.TABLE_HOVER)
