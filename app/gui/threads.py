#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后台线程模块
"""

import threading
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QPixmap, QImage

from app.scanner import FolderScanner
from app.processor import FolderProcessor


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

		# 阶段1: 扫描文件夹
		self.progress_updated.emit(0, 100, "正在扫描文件夹...")
		folders, skipped = self.scanner.scan_folders(
			self.root_dir,
			lambda current, total, message: self.progress_updated.emit(current, total, message)
		)
		total_scanned = len(folders) + skipped

		if self._stopped:
			self.scan_completed.emit({
				'groups': [],
				'tag_groups': {},
				'total_scanned': total_scanned,
				'skipped': skipped
			})
			return

		# 阶段2: 按名称分组
		self.progress_updated.emit(0, 100, "正在分组...")
		groups, tag_groups = self.scanner.classify_folders(
			folders,
			self.threshold,
			lambda current, total, message: self.progress_updated.emit(current, total, message)
		)

		self.progress_updated.emit(100, 100, "扫描完成")
		self.scan_completed.emit({
			'groups': groups,
			'tag_groups': tag_groups,
			'total_scanned': total_scanned,
			'skipped': skipped
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
		self._processor = None

	def stop(self):
		self._stop_flag = True
		if self._processor:
			self._processor.stop()

	def run(self):
		self._processor = FolderProcessor()
		content_similarity = self._processor.calculate_content_similarity_for_group(
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
		self._scanner = None
		self._stop_flag = False

	def stop(self):
		self._stop_flag = True
		if self._scanner:
			self._scanner.stop()

	def run(self):
		self._scanner = FolderScanner()
		size, size_formatted = self._scanner.get_folder_size_async(self.folder_info)
		if not self._stop_flag:
			self.size_calculated.emit(self.folder_info, size, size_formatted)


class ImageLoadThread(QThread):
	"""图片异步加载线程"""
	image_loaded = pyqtSignal(int, QPixmap, str)

	_pixmap_cache = {}
	_cache_lock = threading.Lock()
	_MAX_CACHE_SIZE = 300

	def __init__(self, image_paths, thumb_size):
		super().__init__()
		self.image_paths = image_paths
		self.thumb_size = thumb_size
		self._stop_flag = False
		self._pending_indices = set()
		self._lock = threading.Lock()

	def stop(self):
		self._stop_flag = True

	def load_indices(self, indices):
		"""添加要加载的索引到队列"""
		with self._lock:
			self._pending_indices.update(indices)

	def run(self):
		"""运行加载线程"""
		processed = set()

		while not self._stop_flag:
			indices_to_load = []
			with self._lock:
				if self._pending_indices:
					indices_to_load = sorted(self._pending_indices)
					self._pending_indices.clear()

			if not indices_to_load:
				QThread.msleep(10)
				continue

			for idx in indices_to_load:
				if self._stop_flag:
					return

				if idx in processed:
					continue
				if idx < 0 or idx >= len(self.image_paths):
					continue

				image_path = self.image_paths[idx]
				cache_key = f"{image_path}_{self.thumb_size}"

				with self._cache_lock:
					if cache_key in self._pixmap_cache:
						pixmap = self._pixmap_cache[cache_key]
						self.image_loaded.emit(idx, pixmap, image_path)
						processed.add(idx)
						continue

				try:
					image = QImage(image_path)
					if image.isNull():
						processed.add(idx)
						continue

					scaled_image = image.scaled(
						self.thumb_size, self.thumb_size,
						Qt.KeepAspectRatio, Qt.FastTransformation
					)
					pixmap = QPixmap.fromImage(scaled_image)

					with self._cache_lock:
						if len(self._pixmap_cache) >= self._MAX_CACHE_SIZE:
							keys_to_remove = list(self._pixmap_cache.keys())[:self._MAX_CACHE_SIZE // 3]
							for key in keys_to_remove:
								if key in self._pixmap_cache:
									del self._pixmap_cache[key]
						self._pixmap_cache[cache_key] = pixmap

					if not self._stop_flag:
						self.image_loaded.emit(idx, pixmap, image_path)
					processed.add(idx)
				except Exception as e:
					print(f"加载图片失败 {image_path}: {e}")
					processed.add(idx)

	@classmethod
	def clear_cache(cls):
		"""清空缓存"""
		with cls._cache_lock:
			cls._pixmap_cache.clear()
