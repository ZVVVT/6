"""人工头部校准的高性能 QGraphicsView 画布。"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from core.analysis_v2.opencv_compat import cv2


class ImageCanvas(QGraphicsView):
    imageClicked = Signal(float, float)
    ellipseRequested = Signal(float, float, float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setBackgroundBrush(QColor(24, 24, 24))
        self._image_item = QGraphicsPixmapItem()
        self.scene().addItem(self._image_item)
        self._outline_item = QGraphicsPathItem()
        self._outline_item.setZValue(10)
        white_pen = QPen(QColor(255, 255, 255), 1.4)
        white_pen.setCosmetic(True)
        self._outline_item.setPen(white_pen)
        self.scene().addItem(self._outline_item)
        self._selected_item = QGraphicsPathItem()
        self._selected_item.setZValue(11)
        yellow_pen = QPen(QColor(255, 220, 0), 2.2)
        yellow_pen.setCosmetic(True)
        self._selected_item.setPen(yellow_pen)
        self.scene().addItem(self._selected_item)
        self._preview_item = None  # type: Optional[QGraphicsEllipseItem]
        self._labels = None  # type: Optional[np.ndarray]
        self._mode = "select"
        self._space_pressed = False
        self._panning = False
        self._pan_position = QPoint()
        self._press_scene_position = None  # type: Optional[QPointF]
        self._fit_on_next_resize = False
        self.setFocusPolicy(Qt.StrongFocus)

    @staticmethod
    def _to_pixmap(bgr_image: np.ndarray) -> QPixmap:
        rgb = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        image = QImage(
            rgb.data,
            width,
            height,
            channels * width,
            QImage.Format_RGB888,
        )
        return QPixmap.fromImage(image.copy())

    @staticmethod
    def _contours_path(mask: np.ndarray) -> QPainterPath:
        contours, _hierarchy = cv2.findContours(
            mask.astype(np.uint8),
            cv2.RETR_LIST,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        path = QPainterPath()
        for contour in contours:
            points = contour.reshape(-1, 2)
            if points.size == 0:
                continue
            path.moveTo(float(points[0, 0]), float(points[0, 1]))
            for point in points[1:]:
                path.lineTo(float(point[0]), float(point[1]))
            path.closeSubpath()
        return path

    def set_image(self, bgr_image: np.ndarray, fit: bool = False) -> None:
        pixmap = self._to_pixmap(bgr_image)
        self._image_item.setPixmap(pixmap)
        self.scene().setSceneRect(QRectF(pixmap.rect()))
        if fit:
            self.fit_to_window()

    def set_labels(self, labels: np.ndarray, selected_object_id: int = 0) -> None:
        self._labels = labels
        self._outline_item.setPath(self._contours_path(labels > 0))
        self.set_selected_object(selected_object_id)

    def set_selected_object(self, object_id: int) -> None:
        if self._labels is None or int(object_id) <= 0:
            self._selected_item.setPath(QPainterPath())
        else:
            self._selected_item.setPath(
                self._contours_path(self._labels == int(object_id))
            )

    def set_mode(self, mode: str) -> None:
        if mode not in ("select", "add"):
            raise ValueError("未知画布模式：{}".format(mode))
        self._mode = mode
        self.viewport().setCursor(Qt.CrossCursor if mode == "add" else Qt.ArrowCursor)
        self._remove_preview()

    def fit_to_window(self) -> None:
        if not self._image_item.pixmap().isNull():
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def actual_size(self) -> None:
        self.resetTransform()

    def zoom_in(self) -> None:
        self.scale(1.25, 1.25)

    def zoom_out(self) -> None:
        self.scale(0.8, 0.8)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale(factor, factor)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pressed = True
            self.viewport().setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pressed = False
            if not self._panning:
                self.viewport().setCursor(Qt.CrossCursor if self._mode == "add" else Qt.ArrowCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        pan_requested = event.button() == Qt.MiddleButton or (
            event.button() == Qt.LeftButton and self._space_pressed
        )
        if pan_requested:
            self._panning = True
            self._pan_position = event.position().toPoint()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            scene_position = self.mapToScene(event.position().toPoint())
            self._press_scene_position = scene_position
            if self._mode == "add":
                self._preview_item = QGraphicsEllipseItem()
                preview_pen = QPen(QColor(255, 220, 0), 1.5, Qt.DashLine)
                preview_pen.setCosmetic(True)
                self._preview_item.setPen(preview_pen)
                self._preview_item.setZValue(20)
                self.scene().addItem(self._preview_item)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            current = event.position().toPoint()
            delta = current - self._pan_position
            self._pan_position = current
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._mode == "add" and self._preview_item is not None and self._press_scene_position is not None:
            current = self.mapToScene(event.position().toPoint())
            self._preview_item.setRect(QRectF(self._press_scene_position, current).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning and event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._panning = False
            self.viewport().setCursor(
                Qt.OpenHandCursor if self._space_pressed
                else (Qt.CrossCursor if self._mode == "add" else Qt.ArrowCursor)
            )
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._press_scene_position is not None:
            released = self.mapToScene(event.position().toPoint())
            pressed = self._press_scene_position
            self._press_scene_position = None
            if self._mode == "add":
                self._remove_preview()
                self.ellipseRequested.emit(pressed.x(), pressed.y(), released.x(), released.y())
            else:
                self.imageClicked.emit(released.x(), released.y())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _remove_preview(self) -> None:
        if self._preview_item is not None:
            self.scene().removeItem(self._preview_item)
            self._preview_item = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_on_next_resize:
            self.fit_to_window()
            self._fit_on_next_resize = False
