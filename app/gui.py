#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用户界面模块
"""

import os
import shutil
import threading
import time
import json
from PyQt5.QtWidgets import (
	QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
	QLabel, QFileDialog, QProgressBar, QListWidget, QListWidgetItem,
	QSplitter, QGroupBox, QScrollArea, QMessageBox, QCheckBox, QTreeWidget, QTreeWidgetItem,
	QSlider, QSpinBox, QMenu, QAction, QToolTip, QComboBox, QGridLayout, QSizePolicy, QDialog
)
from PyQt5.QtGui import QPixmap, QImage, QMouseEvent
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from app.scanner import FolderScanner
from app.processor import FolderProcessor


class ClickableImageLabel(QLabel):
	"""支持双击的图片标签"""
	double_clicked = pyqtSignal(str)  # 发射图片路径

	def __init__(self, image_path, parent=None):
		super().__init__(parent)
		self.image_path = image_path
		self.setAlignment(Qt.AlignCenter)
		self.setCursor(Qt.PointingHandCursor)

	def mouseDoubleClickEvent(self, event):
		"""双击事件"""
		if event.button() == Qt.LeftButton:
			self.double_clicked.emit(self.image_path)
		super().mouseDoubleClickEvent(event)


class ImageViewerDialog(QDialog):
	"""图片查看器对话框"""

	def __init__(self, image_path, parent=None):
		super().__init__(parent)
		self.image_path = image_path
		self.original_pixmap = None
		self.current_scale = 1.0
		self.init_ui()

	def init_ui(self):
		self.setWindowTitle(os.path.basename(self.image_path))
		self.setMinimumSize(800, 600)

		layout = QVBoxLayout(self)
		layout.setContentsMargins(0, 0, 0, 0)

		# 滚动区域
		scroll = QScrollArea()
		scroll.setWidgetResizable(True)
		scroll.setAlignment(Qt.AlignCenter)

		# 图片标签
		self.image_label = QLabel()
		self.image_label.setAlignment(Qt.AlignCenter)
		scroll.setWidget(self.image_label)

		layout.addWidget(scroll)

		# 加载图片
		self.load_image()

	def load_image(self):
		"""加载图片"""
		pixmap = QPixmap(self.image_path)
		if not pixmap.isNull():
			self.original_pixmap = pixmap
			self.scale_image()

	def scale_image(self):
		"""缩放图片以适应窗口"""
		if self.original_pixmap:
			# 计算缩放比例，让图片适应窗口但不超过原始大小
			available_size = self.size() - QSize(40, 40)  # 留一些边距
			scaled_pixmap = self.original_pixmap.scaled(
				available_size,
				Qt.KeepAspectRatio,
				Qt.SmoothTransformation
			)
			self.image_label.setPixmap(scaled_pixmap)

	def resizeEvent(self, event):
		"""窗口大小改变时重新缩放图片"""
		self.scale_image()
		super().resizeEvent(event)

class ScanThread(QThread):
	"""扫描线程"""
	progress_updated = pyqtSignal(int, int, str)
	scan_completed = pyqtSignal(dict)

	def __init__(self, root_dir, threshold=0.7):
		super().__init__()
		self.root_dir = root_dir
		self.threshold = threshold
		self.scanner = None
		self.groups = []
		self.tag_groups = {}
		self._paused = False
		self._stopped = False

	def run(self):
		self.groups = []
		self.tag_groups = {}
		self._paused = False
		self._stopped = False
		self.scanner = FolderScanner()

		# 阶段1: 扫描文件夹（多线程加速）
		self.progress_updated.emit(0, 100, "正在扫描文件夹...")
		scan_result = self.scanner.scan_folders(
			self.root_dir,
			lambda current, total, message: self.progress_updated.emit(current, total, message)
		)

		if self._stopped:
			self.scan_completed.emit({
				'groups': [],
				'tag_groups': {},
				'total_scanned': scan_result['total_scanned'],
				'skipped': scan_result['skipped']
			})
			return

		# 阶段2: 按名称分组（使用scanner的方法）
		folders = scan_result['folders']
		self.progress_updated.emit(0, 100, "正在分组...")
		groups, tag_groups = self.scanner.group_folders_by_name(
			folders,
			lambda current, total, message: self.progress_updated.emit(current, total, message)
		)

		self.progress_updated.emit(100, 100, "扫描完成")
		self.scan_completed.emit({
			'groups': groups,
			'tag_groups': tag_groups,
			'total_scanned': scan_result['total_scanned'],
			'skipped': scan_result['skipped']
		})

	def pause(self):
		self._paused = True
		if self.scanner:
			self.scanner.pause()

	def resume(self):
		self._paused = False
		if self.scanner:
			self.scanner.resume()

	def stop(self):
		self._stopped = True
		if self.scanner:
			self.scanner.stop()

class ContentCompareThread(QThread):
	"""内容比对线程"""
	progress_updated = pyqtSignal(int, int, str)
	compare_completed = pyqtSignal(dict)

	def __init__(self, group):
		super().__init__()
		self.group = group
		self._stop_flag = False

	def stop(self):
		self._stop_flag = True

	def run(self):
		processor = FolderProcessor()
		content_similarity = processor.calculate_content_similarity_for_group(
			self.group,
			lambda current, total, message: self.progress_updated.emit(current, total, message)
		)
		if not self._stop_flag:
			self.compare_completed.emit(self.group)

class FolderSizeThread(QThread):
	"""文件夹大小计算线程"""
	size_calculated = pyqtSignal(dict, int, str)

	def __init__(self, folder_info):
		super().__init__()
		self.folder_info = folder_info

	def run(self):
		scanner = FolderScanner()
		size, size_formatted = scanner.get_folder_size_async(self.folder_info)
		self.size_calculated.emit(self.folder_info, size, size_formatted)

class ImageLoadThread(QThread):
	"""图片异步加载线程"""
	image_loaded = pyqtSignal(int, QPixmap, str)  # idx, pixmap, path
	all_loaded = pyqtSignal()

	# 类级别缓存，所有线程共享
	_pixmap_cache = {}
	_cache_lock = threading.Lock()
	_MAX_CACHE_SIZE = 500  # 增大缓存到500张图片

	def __init__(self, image_paths, thumb_size):
		super().__init__()
		self.image_paths = image_paths
		self.thumb_size = thumb_size
		self._stop_flag = False
		self._pending_indices = []
		self._lock = threading.Lock()

	def stop(self):
		self._stop_flag = True

	def load_indices(self, indices):
		"""添加要加载的索引到队列"""
		with self._lock:
			self._pending_indices = []
			for idx in indices:
				if 0 <= idx < len(self.image_paths):
					self._pending_indices.append(idx)

	@classmethod
	def _trim_cache(cls):
		"""裁剪缓存到最大大小"""
		if len(cls._pixmap_cache) > cls._MAX_CACHE_SIZE:
			keys_to_remove = list(cls._pixmap_cache.keys())[:len(cls._pixmap_cache) - cls._MAX_CACHE_SIZE]
			for key in keys_to_remove:
				del cls._pixmap_cache[key]

	def run(self):
		"""按需加载，从队列中获取并加载图片"""
		while not self._stop_flag:
			idx = None
			with self._lock:
				if self._pending_indices:
					idx = self._pending_indices.pop(0)

			if idx is not None:
				try:
					image_path = self.image_paths[idx]
					cache_key = f"{image_path}_{self.thumb_size}"

					# 先检查缓存
					with self._cache_lock:
						if cache_key in self._pixmap_cache:
							self.image_loaded.emit(idx, self._pixmap_cache[cache_key], image_path)
							continue

					# 缓存未命中，加载图片
					image = QImage()
					if image.load(image_path):
						pixmap = QPixmap.fromImage(image)
						if not pixmap.isNull():
							scaled_pixmap = pixmap.scaled(
								self.thumb_size, self.thumb_size,
								Qt.KeepAspectRatio,
								Qt.FastTransformation
							)
							with self._cache_lock:
								self._pixmap_cache[cache_key] = scaled_pixmap
								self._trim_cache()
							self.image_loaded.emit(idx, scaled_pixmap, image_path)
				except Exception as e:
					print(f"加载图片失败: {e}")
			else:
				self.msleep(10)

		self.all_loaded.emit()

	@classmethod
	def clear_cache(cls):
		"""清空图片缓存"""
		with cls._cache_lock:
			cls._pixmap_cache.clear()

class MainWindow(QMainWindow):
	"""主窗口"""

	def __init__(self):
		super().__init__()
		self.setWindowTitle("Eh下载文件查重")
		self.setGeometry(100, 100, 1200, 800)
		self.setMinimumSize(800, 600)

		# 配置文件路径
		self.config_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

		# 加载配置
		self.config = self._load_config()

		self.selected_folder = None
		self.folders = []
		self.similar_groups = []
		self.tag_groups = {}
		self.current_group = None
		self.current_folder = None
		self.current_view_mode = self.config.get('view_mode', 'name')  # 'name' 或 'tag'

		# 滚动延迟加载相关
		self.scroll_timer = QTimer()
		self.scroll_timer.setSingleShot(True)
		self.scroll_timer.timeout.connect(self._on_scroll_timer_timeout)

		# 窗口大小改变相关
		self._resize_timer = QTimer()
		self._resize_timer.setSingleShot(True)
		self._resize_timer.timeout.connect(self._on_resize_timeout)

		central_widget = QWidget()
		main_layout = QVBoxLayout(central_widget)
		main_layout.setSpacing(5)
		main_layout.setContentsMargins(5, 5, 5, 5)

		# 控制区域 - 合并为一行
		control_layout = QHBoxLayout()

		self.select_folder_btn = QPushButton("选择文件夹")
		self.select_folder_btn.setToolTip("选择要扫描的根目录")

		self.scan_btn = QPushButton("开始扫描")
		self.scan_btn.setToolTip("开始扫描并分类文件夹")
		self.pause_btn = QPushButton("暂停")
		self.pause_btn.setToolTip("暂停/继续扫描")
		self.stop_btn = QPushButton("停止")
		self.stop_btn.setToolTip("停止当前扫描")

		self.pause_btn.setEnabled(False)
		self.stop_btn.setEnabled(False)

		control_layout.addWidget(self.select_folder_btn)
		control_layout.addWidget(self.scan_btn)
		control_layout.addWidget(self.pause_btn)
		control_layout.addWidget(self.stop_btn)
		control_layout.addStretch()

		main_layout.addLayout(control_layout)

		# 进度区域 - 分两行
		progress_container = QVBoxLayout()
		progress_container.setSpacing(2)

		# 第一行：进度条
		self.progress_bar = QProgressBar()
		self.progress_bar.setFixedHeight(20)
		self.progress_bar.setTextVisible(False)
		progress_container.addWidget(self.progress_bar)

		# 第二行：状态信息和文件夹计数
		status_layout = QHBoxLayout()
		self.progress_label = QLabel("准备就绪")
		self.progress_label.setMinimumWidth(600)  # 增加宽度以显示更长的文件夹名
		self.folder_count_label = QLabel("文件夹: 0")
		status_layout.addWidget(self.progress_label)
		status_layout.addStretch()
		status_layout.addWidget(self.folder_count_label)
		progress_container.addLayout(status_layout)

		main_layout.addLayout(progress_container)

		# 分割器 - 主要区域
		splitter = QSplitter(Qt.Horizontal)
		splitter.setCollapsible(0, False)  # 左侧栏不可折叠
		splitter.setStretchFactor(0, 0)     # 左侧栏不拉伸
		splitter.setStretchFactor(1, 1)     # 右侧栏拉伸填充

		# 左侧树形列表
		self.group_tree = QTreeWidget()
		self.group_tree.setHeaderLabel("文件夹分组")
		self.group_tree.setMinimumWidth(200)
		self.group_tree.setMaximumWidth(350)
		self.group_tree.setToolTip("双击展开/折叠\n点击查看详情")
		splitter.addWidget(self.group_tree)

		# 右侧面板
		right_widget = QWidget()
		right_layout = QVBoxLayout(right_widget)
		right_layout.setSpacing(5)

		# 当前文件夹信息 - 分两行显示
		info_container = QWidget()
		info_container_layout = QVBoxLayout(info_container)
		info_container_layout.setSpacing(2)
		info_container_layout.setContentsMargins(0, 0, 0, 0)

		# 第一行：文件夹名称和图片总数
		name_row_layout = QHBoxLayout()
		self.folder_name_label = QLabel("未选择文件夹")
		self.folder_name_label.setWordWrap(False)
		self.folder_name_label.setStyleSheet("font-weight: bold; font-size: 14px;")
		self.folder_name_label.setToolTip("点击查看完整名称")
		self.image_count_label = QLabel("共 0 张图片")
		self.image_count_label.setStyleSheet("color: #666;")
		name_row_layout.addWidget(self.folder_name_label)
		name_row_layout.addStretch()
		name_row_layout.addWidget(self.image_count_label)
		info_container_layout.addLayout(name_row_layout)

		# 第二行：文件夹信息和按钮
		info_row_layout = QHBoxLayout()
		self.folder_info_label = QLabel("图像: 0 | 大小: - | 修改: -")
		self.name_similarity_label = QLabel("名称相似度: -")
		self.content_similarity_label = QLabel("内容相似度: -")
		self.compare_content_btn = QPushButton("内容比对")
		self.compare_content_btn.setToolTip("对选中的相似组进行图片内容比对")
		self.delete_btn = QPushButton("删除")
		self.delete_btn.setToolTip("删除选中的文件夹")

		info_row_layout.addWidget(self.folder_info_label)
		info_row_layout.addWidget(self.name_similarity_label)
		info_row_layout.addWidget(self.content_similarity_label)
		info_row_layout.addWidget(self.compare_content_btn)
		info_row_layout.addWidget(self.delete_btn)
		info_container_layout.addLayout(info_row_layout)

		right_layout.addWidget(info_container)

		# 图像预览 - 主要区域（使用网格布局）
		self.preview_group = QGroupBox("图像预览")
		preview_outer_layout = QVBoxLayout()

		# 预览控制栏
		preview_control_layout = QHBoxLayout()
		self.hide_preview_btn = QPushButton("隐藏")
		self.hide_preview_btn.setToolTip("隐藏图片缩略图")
		self.hide_preview_btn.clicked.connect(self.toggle_preview)
		preview_control_layout.addWidget(self.hide_preview_btn)
		preview_control_layout.addStretch()
		preview_outer_layout.addLayout(preview_control_layout)

		self.preview_scroll = QScrollArea()
		self.preview_scroll.setWidgetResizable(True)
		self.preview_content = QWidget()
		self.preview_grid_layout = QGridLayout(self.preview_content)
		self.preview_grid_layout.setSpacing(10)
		self.preview_grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
		self.preview_scroll.setWidget(self.preview_content)
		# 监听滚动事件
		self.preview_scroll.verticalScrollBar().valueChanged.connect(self.on_preview_scroll)
		preview_outer_layout.addWidget(self.preview_scroll)
		self.preview_group.setLayout(preview_outer_layout)
		right_layout.addWidget(self.preview_group, 1)

		# 移除未使用的分批加载定时器

		splitter.addWidget(right_widget)
		splitter.setSizes([250, 950])
		main_layout.addWidget(splitter, 1)

		self.setCentralWidget(central_widget)

		# 添加菜单栏
		self._create_menu_bar()

		# 连接信号
		self.select_folder_btn.clicked.connect(self.select_folder)
		self.scan_btn.clicked.connect(self.start_scan)
		self.pause_btn.clicked.connect(self.toggle_pause)
		self.stop_btn.clicked.connect(self.stop_scan)
		self.group_tree.itemClicked.connect(self.on_tree_item_clicked)
		self.compare_content_btn.clicked.connect(self.start_content_compare)
		self.delete_btn.clicked.connect(self.delete_selected_folder)


		self.scan_thread = None
		self.content_compare_thread = None

		# 定时器用于定期刷新UI
		self.refresh_timer = QTimer()
		self.refresh_timer.timeout.connect(self._on_refresh_timer)

	def _create_menu_bar(self):
		"""创建菜单栏"""
		menu_bar = self.menuBar()

		# 分类方式菜单
		view_mode_menu = menu_bar.addMenu("分类方式")

		# 存储当前选中的动作
		self.current_view_mode_action = None

		view_modes = [
			("按名称相似", "name"),
			("按标签分组", "tag")
		]

		for mode_name, mode_value in view_modes:
			action = QAction(mode_name, self)
			action.setCheckable(True)
			if mode_value == self.current_view_mode:
				action.setChecked(True)
				self.current_view_mode_action = action
			action.triggered.connect(lambda checked, m=mode_value, a=action: self._set_view_mode(m, a))
			view_mode_menu.addAction(action)

		# 缩略图大小菜单
		thumb_size_menu = menu_bar.addMenu("缩略图大小")

		# 存储当前选中的动作
		self.current_thumb_size_action = None

		thumb_sizes = [
			("小", 100),
			("中", 150),
			("大", 200),
			("超大", 300)
		]

		for size_name, size_value in thumb_sizes:
			action = QAction(size_name, self)
			action.setCheckable(True)
			if size_value == self.thumbnail_size:
				action.setChecked(True)
				self.current_thumb_size_action = action
			action.triggered.connect(lambda checked, s=size_value, a=action: self._set_thumbnail_size(s, a))
			thumb_size_menu.addAction(action)

		# 相似度阈值菜单
		threshold_menu = menu_bar.addMenu("相似度阈值")

		# 存储当前选中的动作
		self.current_threshold_action = None

		thresholds = [
			("50%", 50),
			("60%", 60),
			("70%", 70),
			("80%", 80),
			("90%", 90)
		]

		for threshold_name, threshold_value in thresholds:
			action = QAction(threshold_name, self)
			action.setCheckable(True)
			if threshold_value == self.threshold:
				action.setChecked(True)
				self.current_threshold_action = action
			action.triggered.connect(lambda checked, t=threshold_value, a=action: self._set_threshold(t, a))
			threshold_menu.addAction(action)

	def _set_view_mode(self, mode, action):
		"""设置视图模式"""
		# 如果点击的是当前已选中的选项，保持选中状态
		if action == self.current_view_mode_action:
			action.setChecked(True)
			return

		# 取消之前选中的动作
		if self.current_view_mode_action:
			self.current_view_mode_action.setChecked(False)

		# 设置新的选中动作
		self.current_view_mode_action = action
		action.setChecked(True)

		# 更新值
		self.current_view_mode = mode
		self._save_config()
		self.refresh_tree_view()

	def _set_thumbnail_size(self, size, action):
		"""设置缩略图大小"""
		# 如果点击的是当前已选中的选项，保持选中状态
		if action == self.current_thumb_size_action:
			action.setChecked(True)
			return

		# 取消之前选中的动作
		if self.current_thumb_size_action:
			self.current_thumb_size_action.setChecked(False)

		# 设置新的选中动作
		self.current_thumb_size_action = action
		action.setChecked(True)

		# 更新值
		self.thumbnail_size = size
		self._save_config()
		if self.current_folder:
			self.show_folder_preview(self.current_folder)

	def _set_threshold(self, threshold, action):
		"""设置相似度阈值"""
		# 如果点击的是当前已选中的选项，保持选中状态
		if action == self.current_threshold_action:
			action.setChecked(True)
			return

		# 取消之前选中的动作
		if self.current_threshold_action:
			self.current_threshold_action.setChecked(False)

		# 设置新的选中动作
		self.current_threshold_action = action
		action.setChecked(True)

		# 更新值
		self.threshold = threshold
		self._save_config()

	def _load_config(self):
		"""加载配置文件"""
		try:
			if os.path.exists(self.config_file):
				with open(self.config_file, 'r', encoding='utf-8') as f:
					config = json.load(f)
					# 加载相似度阈值
					self.threshold = config.get('threshold', 70)
					# 加载缩略图大小
					self.thumbnail_size = config.get('thumbnail_size', 150)
					return config
		except Exception as e:
			print(f"加载配置文件失败: {e}")
		# 默认配置
		default_config = {
			'threshold': 70,
			'view_mode': 'name',
			'thumbnail_size': 150
		}
		# 设置默认阈值
		self.threshold = default_config['threshold']
		# 设置默认缩略图大小
		self.thumbnail_size = default_config['thumbnail_size']
		return default_config

	def _save_config(self):
		"""保存配置文件"""
		try:
			config = {
				'threshold': self.threshold,
				'view_mode': self.current_view_mode,
				'thumbnail_size': self.thumbnail_size
			}
			with open(self.config_file, 'w', encoding='utf-8') as f:
				json.dump(config, f, indent=2, ensure_ascii=False)
		except Exception as e:
			print(f"保存配置文件失败: {e}")

	def select_folder(self):
		"""选择文件夹"""
		import time
		start_time = time.time()
		folder = QFileDialog.getExistingDirectory(self, "选择扫描目录")
		if folder:
			self.selected_folder = folder
			# 将路径添加到窗口标题
			self.setWindowTitle(f"Eh下载文件查重 - {folder}")
		else:
			print("取消选择文件夹")

	def toggle_preview(self):
		"""切换预览显示/隐藏"""
		import time
		start_time = time.time()
		current_state = self.preview_scroll.isVisible()
		self.preview_scroll.setVisible(not current_state)
		if self.preview_scroll.isVisible():
			self.hide_preview_btn.setText("隐藏")
		else:
			self.hide_preview_btn.setText("显示")

	def start_scan(self):
		"""开始扫描"""
		import time
		start_time = time.time()
		if not hasattr(self, 'selected_folder') or not self.selected_folder:
			QMessageBox.warning(self, "警告", "请先选择扫描目录")
			return
		folder = self.selected_folder

		threshold = self.threshold / 100.0

		self.scan_btn.setEnabled(False)
		self.pause_btn.setEnabled(True)
		self.stop_btn.setEnabled(True)
		self.progress_bar.setValue(0)
		self.progress_label.setText("扫描中...")
		self.group_tree.clear()
		self.similar_groups = []
		self.tag_groups = {}

		self.scan_thread = ScanThread(folder, threshold)
		self.scan_thread.progress_updated.connect(self.update_progress)
		self.scan_thread.scan_completed.connect(self.on_scan_completed)
		self.scan_thread.start()

		self.refresh_timer.start(500)

	def toggle_pause(self):
		"""切换暂停/继续"""
		import time
		start_time = time.time()
		if self.scan_thread:
			if self.pause_btn.text() == "暂停":
				self.scan_thread.pause()
				self.pause_btn.setText("继续")
				self.progress_label.setText("已暂停")
			else:
				self.scan_thread.resume()
				self.pause_btn.setText("暂停")
				self.progress_label.setText("扫描中...")

	def stop_scan(self):
		"""停止扫描"""
		import time
		start_time = time.time()
		if self.scan_thread:
			self.scan_thread.stop()
			self.progress_label.setText("停止中...")

		if self.content_compare_thread and self.content_compare_thread.isRunning():
			self.content_compare_thread.stop()

	def update_progress(self, current, total, message):
		"""更新进度"""
		import time
		start_time = time.time()
		if total > 0:
			self.progress_bar.setValue(int(current / total * 100))
		# 截断长消息
		if len(message) > 50:
			message = message[:47] + '...'
		self.progress_label.setText(message)

	def _on_refresh_timer(self):
		"""定时刷新UI"""
		if self.scan_thread and self.scan_thread.groups:
			self.similar_groups = self.scan_thread.groups
			self._refresh_tree_view_fast()

	def _refresh_tree_view_fast(self):
		"""快速刷新树形列表"""
		if self.current_view_mode == 'name':
			total_folders = sum(len(g['folders']) for g in self.similar_groups)
			similar_count = sum(1 for g in self.similar_groups if len(g['folders']) > 1)
			single_count = sum(1 for g in self.similar_groups if len(g['folders']) == 1)
			self.folder_count_label.setText(f"文件夹: {total_folders} (相似:{similar_count} 单独:{single_count})")
		else:
			total_folders = sum(len(folders) for folders in self.tag_groups.values())
			tag_count = len(self.tag_groups)
			self.folder_count_label.setText(f"文件夹: {total_folders} (标签:{tag_count})")

	def refresh_tree_view(self):
		"""完整刷新树形列表视图"""
		# 保存当前展开状态和选中项
		expanded_groups = set()
		selected_folder_path = None

		if self.current_folder:
			selected_folder_path = self.current_folder['path']

		# 遍历保存展开状态
		for i in range(self.group_tree.topLevelItemCount()):
			top_item = self.group_tree.topLevelItem(i)
			if top_item.isExpanded():
				expanded_groups.add(top_item.text(0))
			# 遍历子项
			for j in range(top_item.childCount()):
				child = top_item.child(j)
				if child.isExpanded():
					expanded_groups.add(child.text(0))

		self.group_tree.clear()

		if self.current_view_mode == 'name':
			self._refresh_tree_view_by_name(expanded_groups, selected_folder_path)
		else:
			self._refresh_tree_view_by_tag()

	def _refresh_tree_view_by_name(self, expanded_groups=None, selected_folder_path=None):
		"""按名称相似刷新树形列表"""
		if expanded_groups is None:
			expanded_groups = set()

		similar_count = sum(1 for g in self.similar_groups if len(g['folders']) > 1)
		single_count = sum(1 for g in self.similar_groups if len(g['folders']) == 1)
		total_folders = sum(len(g['folders']) for g in self.similar_groups)

		self.folder_count_label.setText(f"文件夹: {total_folders} (相似:{similar_count} 单独:{single_count})")

		if similar_count > 0:
			similar_root = QTreeWidgetItem(self.group_tree)
			similar_root.setText(0, f"相似文件夹组 ({similar_count}组)")
			similar_root.setExpanded(f"相似文件夹组 ({similar_count}组)" in expanded_groups or similar_count <= 5)

			for i, group in enumerate(self.similar_groups):
				if len(group['folders']) > 1:
					group_item = QTreeWidgetItem(similar_root)
					group_text = f"组 {i+1} (相似度: {group['similarity']:.2f}"
					if group.get('content_similarity') is not None:
						group_text += f", 内容: {group['content_similarity']:.2f}"
					group_text += ")"
					group_item.setText(0, group_text)
					group_item.setData(0, Qt.UserRole, ('group', group))
					# 恢复展开状态
					group_item.setExpanded(group_text in expanded_groups)

					for folder in group['folders']:
						folder_item = QTreeWidgetItem(group_item)
						display_name = folder['name'][:40] + '...' if len(folder['name']) > 40 else folder['name']
						folder_item.setText(0, display_name)
						folder_item.setToolTip(0, folder['name'])
						folder_item.setData(0, Qt.UserRole, ('folder', folder, group))
						# 恢复选中状态
						if selected_folder_path and folder['path'] == selected_folder_path:
							self.group_tree.setCurrentItem(folder_item)

		if single_count > 0:
			single_root = QTreeWidgetItem(self.group_tree)
			single_root.setText(0, f"单独文件夹 ({single_count}个)")
			single_root.setExpanded(f"单独文件夹 ({single_count}个)" in expanded_groups)

			for i, group in enumerate(self.similar_groups):
				if len(group['folders']) == 1:
					folder = group['folders'][0]
					folder_item = QTreeWidgetItem(single_root)
					display_name = folder['name'][:40] + '...' if len(folder['name']) > 40 else folder['name']
					folder_item.setText(0, display_name)
					folder_item.setToolTip(0, folder['name'])
					folder_item.setData(0, Qt.UserRole, ('folder', folder, group))
					# 恢复选中状态
					if selected_folder_path and folder['path'] == selected_folder_path:
						self.group_tree.setCurrentItem(folder_item)

	def _refresh_tree_view_by_tag(self):
		"""按标签刷新树形列表"""
		total_folders = sum(len(folders) for folders in self.tag_groups.values())
		tag_count = len(self.tag_groups)

		self.folder_count_label.setText(f"文件夹: {total_folders} (标签:{tag_count})")

		for tag, folders in sorted(self.tag_groups.items()):
			tag_root = QTreeWidgetItem(self.group_tree)
			tag_root.setText(0, f"[{tag}] ({len(folders)}个)")
			tag_root.setExpanded(True)

			for folder in folders:
				folder_item = QTreeWidgetItem(tag_root)
				display_name = folder['name'][:40] + '...' if len(folder['name']) > 40 else folder['name']
				folder_item.setText(0, display_name)
				folder_item.setToolTip(0, folder['name'])
				folder_item.setData(0, Qt.UserRole, ('folder', folder, None))

	def on_scan_completed(self, result):
		"""扫描完成"""
		self.refresh_timer.stop()

		self.similar_groups = result['groups']
		self.tag_groups = result.get('tag_groups', {})
		total_scanned = result['total_scanned']
		skipped = result['skipped']
		stopped = result.get('stopped', False)

		self.scan_btn.setEnabled(True)
		self.pause_btn.setEnabled(False)
		self.stop_btn.setEnabled(False)
		self.pause_btn.setText("暂停")

		# 完成进度条
		self.progress_bar.setValue(100)

		self.refresh_tree_view()

		if stopped:
			self.progress_label.setText("已停止")
		else:
			self.progress_label.setText(f"完成: {len(self.similar_groups)}组")

		if len(self.similar_groups) == 0 and skipped > 0:
			QMessageBox.information(self, "提示", f"扫描到 {total_scanned} 个项目，但都不符合命名格式\n\n格式要求: 纯数字序列号-名称\n例如: 123456-文件夹名")

	def on_tree_item_clicked(self, item, column):
		"""点击树形列表项"""
		data = item.data(0, Qt.UserRole)

		if data is None:
			return

		item_type = data[0]

		if item_type == 'group':
			self.current_group = data[1]
			self.current_folder = None

			self.folder_name_label.setText(f"组内 {len(self.current_group['folders'])} 个文件夹")
			self.folder_info_label.setText("")
			self.name_similarity_label.setText(f"名称: {self.current_group['similarity']:.2f}")

			if self.current_group.get('content_similarity') is not None:
				self.content_similarity_label.setText(f"内容: {self.current_group['content_similarity']:.2f}")
			else:
				self.content_similarity_label.setText("内容: 未比对")

			self.clear_preview()

		elif item_type == 'folder':
			self.current_folder = data[1]
			self.current_group = data[2]

			self.folder_name_label.setText(self.current_folder['name'])
			info_text = f"图像: {len(self.current_folder['images'])}"
			if 'size_formatted' in self.current_folder and self.current_folder['size_formatted'] != '-':
				info_text += f" | 大小: {self.current_folder['size_formatted']}"
			else:
				info_text += " | 大小: 计算中..."
				# 异步计算文件夹大小
				self.size_thread = FolderSizeThread(self.current_folder)
				self.size_thread.size_calculated.connect(self.on_folder_size_calculated)
				self.size_thread.start()
			if 'mtime' in self.current_folder:
				info_text += f" | 修改: {self.current_folder['mtime']}"
			self.folder_info_label.setText(info_text)

			if self.current_group:
				self.name_similarity_label.setText(f"名称: {self.current_group['similarity']:.2f}")

				if self.current_group.get('content_similarity') is not None:
					self.content_similarity_label.setText(f"内容: {self.current_group['content_similarity']:.2f}")
				else:
					self.content_similarity_label.setText("内容: 未比对")
			else:
				self.name_similarity_label.setText("名称相似度: -")
				self.content_similarity_label.setText("内容相似度: -")

			self.show_folder_preview(self.current_folder)

	def on_folder_size_calculated(self, folder_info, size, size_formatted):
		"""文件夹大小计算完成"""
		folder_info['size'] = size
		folder_info['size_formatted'] = size_formatted

		# 更新当前显示
		if self.current_folder == folder_info:
			info_text = f"图像: {len(self.current_folder['images'])}"
			info_text += f" | 大小: {size_formatted}"
			if 'mtime' in self.current_folder:
				info_text += f" | 修改: {self.current_folder['mtime']}"
			self.folder_info_label.setText(info_text)

	def clear_preview(self):
		"""清空预览"""
		# 停止正在加载的线程
		if hasattr(self, 'image_load_thread') and self.image_load_thread:
			self.image_load_thread.stop()
			self.image_load_thread = None

		# 清空图片标签缓存
		self.image_labels = {}
		self.image_containers = {}

		while self.preview_grid_layout.count():
			item = self.preview_grid_layout.takeAt(0)
			if item.widget():
				item.widget().deleteLater()

	def on_preview_scroll(self, value):
		"""滚动时延迟加载可见图片"""
		self.scroll_timer.start(100)

	def _on_scroll_timer_timeout(self):
		"""滚动定时器超时，加载可见图片"""
		self._load_visible_area()

	def _load_visible_area(self):
		"""加载可见区域的图片"""
		if not hasattr(self, 'image_load_thread') or not self.image_load_thread:
			return

		# 获取可见区域信息
		scrollbar = self.preview_scroll.verticalScrollBar()
		viewport_height = self.preview_scroll.viewport().height()
		scroll_value = scrollbar.value()

		# 计算可见行范围
		item_height = self.current_thumb_size + 30
		top_row = scroll_value // item_height
		bottom_row = (scroll_value + viewport_height) // item_height

		# 扩展预加载缓冲
		top_row = max(0, top_row - 3)
		bottom_row += 3

		# 计算需要加载的图片索引
		start_idx = top_row * self.current_cols
		end_idx = min((bottom_row + 2) * self.current_cols, len(self.current_preview_folder['images']))

		indices = list(range(start_idx, end_idx))

		# 加载图片
		if indices and hasattr(self, 'image_load_thread') and self.image_load_thread:
			self.image_load_thread.load_indices(indices)

	def show_folder_preview(self, folder):
		"""显示文件夹预览"""
		self.clear_preview()

		if not folder['images']:
			scanner = FolderScanner()
			folder['images'] = scanner.get_folder_images(folder['path'])
			info_text = f"图像: {len(folder['images'])}"
			if 'size_formatted' in folder:
				info_text += f" | 大小: {folder['size_formatted']}"
			if 'mtime' in folder:
				info_text += f" | 修改: {folder['mtime']}"
			self.folder_info_label.setText(info_text)

		# 更新图片计数
		self.image_count_label.setText(f"共 {len(folder['images'])} 张图片")

		# 获取缩略图大小
		thumb_size = self.thumbnail_size

		# 计算每行显示的图片数量
		scroll_width = self.preview_scroll.width() - 30
		cols = max(1, scroll_width // (thumb_size + 10))

		# 保存当前预览参数
		self.current_thumb_size = thumb_size
		self.current_cols = cols
		self.current_preview_folder = folder
		self.image_labels = {}
		self.image_containers = {}

		# 一次性创建所有占位框（确保快速滚动时有占位框）
		for idx in range(len(folder['images'])):
			image_path = folder['images'][idx]
			self._create_image_placeholder(idx, image_path, thumb_size, cols)

		# 重置滚动位置到顶部
		self.preview_scroll.verticalScrollBar().setValue(0)

		# 启动异步加载线程
		self.image_load_thread = ImageLoadThread(folder['images'], thumb_size)
		self.image_load_thread.image_loaded.connect(self.on_image_loaded)
		self.image_load_thread.all_loaded.connect(self.on_all_images_loaded)
		self.image_load_thread.start()

		# 立即加载可见区域图片
		self._load_visible_area()

	def _create_image_placeholder(self, idx, image_path, thumb_size, cols):
		"""创建单个图片占位框，支持双击打开大图"""
		# 创建图片容器
		container = QWidget()
		container_layout = QVBoxLayout(container)
		container_layout.setContentsMargins(1, 0, 1, 0)  # 减小边距
		container_layout.setSpacing(0)

		# 图片标签（可点击）
		img_label = ClickableImageLabel(image_path)
		img_label.setFixedSize(thumb_size, thumb_size)
		img_label.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ddd;")
		img_label.double_clicked.connect(self._on_image_double_clicked)
		container_layout.addWidget(img_label)

		# 序号标签
		index_label = QLabel(str(idx + 1))
		index_label.setAlignment(Qt.AlignCenter)
		index_label.setStyleSheet("color: #666; font-size: 9px; padding: 0px; margin: 0px;")
		index_label.setFixedHeight(12)  # 减小序号高度
		container_layout.addWidget(index_label)

		# 计算网格位置
		row = idx // cols
		col = idx % cols
		self.preview_grid_layout.addWidget(container, row, col)

		self.image_labels[idx] = img_label
		self.image_containers[idx] = container

		# 立即检查缓存，如果有就直接显示
		cache_key = f"{image_path}_{thumb_size}"
		with ImageLoadThread._cache_lock:
			if cache_key in ImageLoadThread._pixmap_cache:
				pixmap = ImageLoadThread._pixmap_cache[cache_key]
				img_label.setPixmap(pixmap)
				img_label.setStyleSheet("")
				img_label.setToolTip(image_path)

	def _on_image_double_clicked(self, image_path):
		"""双击图片打开大图查看器"""
		dialog = ImageViewerDialog(image_path, self)
		dialog.exec_()

	def on_image_loaded(self, idx, pixmap, path):
		"""单张图片加载完成"""
		if idx in self.image_labels:
			label = self.image_labels[idx]
			label.setPixmap(pixmap)
			label.setStyleSheet("")
			label.setText("")
			label.setToolTip(path)
		# 无论如何，只要加载完成就记录到缓存（已经在加载线程中处理了）

	def on_all_images_loaded(self):
		"""所有图片加载完成"""
		pass

	def start_content_compare(self):
		"""开始内容比对"""
		import time
		start_time = time.time()
		if not self.current_group:
			QMessageBox.warning(self, "警告", "请先选择一个文件夹组")
			return

		if len(self.current_group['folders']) < 2:
			QMessageBox.information(self, "提示", "单独文件夹无需进行内容比对")
			return

		self.compare_content_btn.setEnabled(False)
		self.pause_btn.setEnabled(True)
		self.stop_btn.setEnabled(True)
		self.progress_bar.setValue(0)
		self.progress_label.setText("内容比对中...")

		self.content_compare_thread = ContentCompareThread(self.current_group)
		self.content_compare_thread.progress_updated.connect(self.update_progress)
		self.content_compare_thread.compare_completed.connect(self.on_content_compare_completed)
		self.content_compare_thread.start()

	def on_content_compare_completed(self, group):
		"""内容比对完成"""
		import time
		start_time = time.time()
		self.compare_content_btn.setEnabled(True)
		self.pause_btn.setEnabled(False)
		self.stop_btn.setEnabled(False)
		self.progress_label.setText("比对完成")

		if group.get('content_similarity') is not None:
			self.content_similarity_label.setText(f"内容: {group['content_similarity']:.2f}")

		self.refresh_tree_view()

	def delete_selected_folder(self):
		"""删除选中的文件夹"""
		import time
		start_time = time.time()
		if not self.current_folder:
			QMessageBox.warning(self, "警告", "请先选择要删除的文件夹")
			return

		message = f"确定要删除以下文件夹吗？\n{self.current_folder['name']}"
		reply = QMessageBox.question(
			self, "确认删除", message,
			QMessageBox.Yes | QMessageBox.No, QMessageBox.No
		)

		if reply == QMessageBox.Yes:
			try:
				folder_path = self.current_folder['path']
				shutil.rmtree(folder_path)
				QMessageBox.information(self, "成功", "文件夹已删除")

				if self.current_group and self.current_folder in self.current_group['folders']:
					self.current_group['folders'].remove(self.current_folder)

				self.current_folder = None
				self.folder_name_label.setText("未选择文件夹")
				self.folder_info_label.setText("图像: 0 | 大小: - | 修改: -")
				self.clear_preview()
				self.refresh_tree_view()

			except Exception as e:
				QMessageBox.warning(self, "错误", f"删除文件夹失败: {e}")
		else:
			pass

	def resizeEvent(self, event):
		"""窗口大小改变事件"""
		# 启动延迟重新排列定时器
		self._resize_timer.start(200)
		super().resizeEvent(event)

	def _on_resize_timeout(self):
		"""窗口大小改变超时，重新排列图片预览"""
		if self.current_folder:
			# 保存当前滚动位置
			scroll_value = self.preview_scroll.verticalScrollBar().value()
			# 重新显示预览
			self.show_folder_preview(self.current_folder)
			# 尝试恢复滚动位置
			self.preview_scroll.verticalScrollBar().setValue(scroll_value)

	def closeEvent(self, event):
		"""窗口关闭时清理资源"""
		# 停止扫描线程
		if hasattr(self, 'scan_thread') and self.scan_thread:
			self.scan_thread.stop()
			self.scan_thread.wait()

		# 停止内容比对线程
		if hasattr(self, 'content_compare_thread') and self.content_compare_thread:
			self.content_compare_thread.quit()
			self.content_compare_thread.wait()

		# 清空图片缓存
		ImageLoadThread.clear_cache()

		event.accept()
