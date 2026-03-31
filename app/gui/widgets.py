#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自定义UI组件模块
"""

from PyQt5.QtWidgets import QLabel
from PyQt5.QtCore import Qt, pyqtSignal


class ClickableImageLabel(QLabel):
	"""可点击的图像标签"""
	double_clicked = pyqtSignal(str)

	def __init__(self, image_path):
		super().__init__()
		self.image_path = image_path

	def mouseDoubleClickEvent(self, event):
		"""双击事件"""
		self.double_clicked.emit(self.image_path)
