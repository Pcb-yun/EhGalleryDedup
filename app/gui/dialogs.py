#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对话框模块
"""

import os
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QScrollArea, QLabel
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QPixmap, QImage, QCursor


class NoWheelScrollArea(QScrollArea):
	"""禁用滚轮滚动的滚动区域"""

	def wheelEvent(self, event):
		"""忽略滚轮事件"""
		event.ignore()


class ImageViewerDialog(QDialog):
	"""图像查看器对话框，支持滚轮缩放和拖拽平移"""

	def __init__(self, image_path, parent=None):
		super().__init__(parent)
		self.image_path = image_path
		self.setWindowTitle(f"图像查看 - {os.path.basename(image_path)}")
		self.resize(1000, 700)
		self.setMouseTracking(True)

		self._original_pixmap = None
		self._current_scale = 1.0
		self._min_scale = 1.0
		self._max_scale = 10.0
		self._dragging = False
		self._last_pos = QPoint()

		layout = QVBoxLayout(self)
		layout.setContentsMargins(0, 0, 0, 0)

		self.scroll_area = NoWheelScrollArea()
		self.scroll_area.setWidgetResizable(False)
		self.scroll_area.setAlignment(Qt.AlignCenter)
		self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
		self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
		layout.addWidget(self.scroll_area)

		self.image_label = QLabel()
		self.image_label.setAlignment(Qt.AlignCenter)
		self.image_label.setMouseTracking(True)
		self.scroll_area.setWidget(self.image_label)

		self._load_image()
		self.setFocusPolicy(Qt.StrongFocus)

	def _load_image(self):
		"""加载原始图像"""
		try:
			image = QImage(self.image_path)
			if not image.isNull():
				self._original_pixmap = QPixmap.fromImage(image)
				self._fit_to_window()
			else:
				self.image_label.setText("无法加载图像")
		except Exception as e:
			print(f"加载图像失败 {self.image_path}: {e}")
			self.image_label.setText(f"加载失败: {str(e)}")

	def _fit_to_window(self):
		"""将图像适应窗口大小"""
		if not self._original_pixmap:
			return

		viewport_size = self.scroll_area.viewport().size()
		pixmap_size = self._original_pixmap.size()

		scale_w = viewport_size.width() / pixmap_size.width()
		scale_h = viewport_size.height() / pixmap_size.height()
		self._min_scale = min(scale_w, scale_h, 1.0)
		self._current_scale = self._min_scale

		self._update_display()

	def _update_display(self):
		"""更新显示的图像"""
		if not self._original_pixmap:
			return

		new_size = self._original_pixmap.size() * self._current_scale
		scaled_pixmap = self._original_pixmap.scaled(
			new_size,
			Qt.KeepAspectRatio,
			Qt.SmoothTransformation
		)
		self.image_label.setPixmap(scaled_pixmap)
		self.image_label.resize(scaled_pixmap.size())

	def wheelEvent(self, event):
		"""滚轮事件：仅缩放图像"""
		if not self._original_pixmap:
			return

		delta = event.angleDelta().y()
		if delta == 0:
			return

		old_scale = self._current_scale

		if delta > 0:
			self._current_scale *= 1.1
		else:
			self._current_scale /= 1.1

		self._current_scale = max(self._min_scale, min(self._max_scale, self._current_scale))

		if old_scale != self._current_scale:
			self._update_display()

			if delta > 0:
				cursor_pos = self.image_label.mapFromGlobal(QCursor.pos())
				self._center_on_point(cursor_pos)

		event.accept()

	def _center_on_point(self, point):
		"""将指定点居中显示"""
		h_bar = self.scroll_area.horizontalScrollBar()
		v_bar = self.scroll_area.verticalScrollBar()

		target_x = int(point.x() - self.scroll_area.viewport().width() / 2)
		target_y = int(point.y() - self.scroll_area.viewport().height() / 2)

		h_bar.setValue(max(h_bar.minimum(), min(h_bar.maximum(), target_x)))
		v_bar.setValue(max(v_bar.minimum(), min(v_bar.maximum(), target_y)))

	def keyPressEvent(self, event):
		"""键盘事件"""
		key = event.key()
		if key == Qt.Key_Escape:
			self.close()
		elif key == Qt.Key_R:
			self._fit_to_window()
		else:
			super().keyPressEvent(event)

	def mousePressEvent(self, event):
		"""鼠标按下：开始拖拽"""
		if event.button() == Qt.LeftButton:
			self._dragging = True
			self._last_pos = event.pos()
			self.setCursor(Qt.ClosedHandCursor)

	def mouseMoveEvent(self, event):
		"""鼠标移动：拖拽视图"""
		if self._dragging:
			delta = event.pos() - self._last_pos
			self._last_pos = event.pos()

			h_bar = self.scroll_area.horizontalScrollBar()
			v_bar = self.scroll_area.verticalScrollBar()

			h_bar.setValue(h_bar.value() - delta.x())
			v_bar.setValue(v_bar.value() - delta.y())

	def mouseReleaseEvent(self, event):
		"""鼠标释放：结束拖拽"""
		if event.button() == Qt.LeftButton:
			self._dragging = False
			self.setCursor(Qt.ArrowCursor)

	def resizeEvent(self, event):
		"""窗口大小改变时重新计算最小缩放"""
		super().resizeEvent(event)
		if self._original_pixmap and self._current_scale == self._min_scale:
			self._fit_to_window()
