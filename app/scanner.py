#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件夹扫描器模块 - 多线程/多进程加速版本
只对文件夹名进行扫描和比较
"""

import os
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count, Manager
from collections import defaultdict


class FolderScanner:
	"""文件夹扫描器类"""

	def __init__(self):
		"""初始化文件夹扫描器"""
		# 控制标志
		self._paused = False
		self._stopped = False
		# 线程数 - IO密集型使用更多线程
		self.max_workers = min(64, cpu_count() * 8)

	def pause(self):
		"""暂停扫描"""
		self._paused = True

	def resume(self):
		"""恢复扫描"""
		self._paused = False

	def stop(self):
		"""停止扫描"""
		self._stopped = True

	def scan_folders(self, root_path, progress_callback=None):
		"""扫描文件夹，只对文件名进行快速扫描（多线程加速）

		Args:
			root_path: str, 根目录路径
			progress_callback: function, 进度回调函数

		Returns:
			dict: 包含扫描结果的字典
		"""
		start_time = time.time()

		# 第一步：快速获取所有文件夹名称
		folder_items = self._list_folders_fast(root_path)
		total_folders = len(folder_items)

		if progress_callback:
			progress_callback(0, total_folders, f"发现 {total_folders} 个文件夹")

		if self._stopped or total_folders == 0:
			return {
				'folders': [],
				'total_scanned': total_folders,
				'skipped': 0
			}

		# 第二步：使用多线程并行获取文件夹基本信息
		folders = self._get_folders_info_parallel(folder_items, progress_callback)

		elapsed_time = time.time() - start_time
		if progress_callback:
			progress_callback(total_folders, total_folders,
				f"扫描完成: {len(folders)} 个文件夹 ({elapsed_time:.2f}s)")

		return {
			'folders': folders,
			'total_scanned': total_folders,
			'skipped': 0
		}

	def _list_folders_fast(self, root_path):
		"""快速列出所有符合条件的文件夹

		Args:
			root_path: str, 根目录路径

		Returns:
			list: [(folder_path, number, name), ...]
		"""
		folder_items = []
		try:
			items = os.listdir(root_path)
			for item in items:
				if self._stopped:
					break

				item_path = os.path.join(root_path, item)
				if os.path.isdir(item_path):
					# 检查文件夹名称是否符合格式：数字-名称
					match = re.match(r'^(\d+)-(.*)$', item)
					if match:
						folder_items.append((item_path, match.group(1), match.group(2).strip()))
		except Exception as e:
			print(f"列出文件夹失败: {e}")

		return folder_items

	def _get_folders_info_parallel(self, folder_items, progress_callback=None):
		"""并行获取文件夹基本信息

		Args:
			folder_items: list, 文件夹项目列表
			progress_callback: function, 进度回调函数

		Returns:
			list: 文件夹信息列表
		"""
		folders = []
		total = len(folder_items)
		completed = 0

		with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
			# 提交所有任务
			future_to_item = {
				executor.submit(self._get_folder_info_basic, item): item
				for item in folder_items
			}

			# 收集结果
			for future in as_completed(future_to_item):
				if self._stopped:
					executor.shutdown(wait=False)
					break

				# 检查暂停
				while self._paused and not self._stopped:
					time.sleep(0.05)

				folder_info = future.result()
				if folder_info:
					folders.append(folder_info)

				completed += 1
				if progress_callback and completed % 50 == 0:
					progress_callback(completed, total, f"扫描进度: {completed}/{total}")

		return folders

	def _get_folder_info_basic(self, folder_item):
		"""获取文件夹基本信息（快速，不遍历内部文件）

		Args:
			folder_item: tuple, (folder_path, number, name)

		Returns:
			dict: 文件夹信息
		"""
		try:
			folder_path, number, name = folder_item

			# 获取最后修改时间
			mtime = os.path.getmtime(folder_path)
			modified_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

			# 提取标签
			tags = re.findall(r'\[(.*?)\]', name)

			# 清理名称
			content_name = re.sub(r'\[.*?\]', '', name).strip()

			return {
				'path': folder_path,
				'name': os.path.basename(folder_path),
				'number': number,
				'content_name': content_name,
				'tags': tags,
				'modified_time': modified_time,
				# 以下字段延迟加载
				'size': '-',
				'size_bytes': 0,
				'images': [],
				'image_count': 0
			}
		except Exception as e:
			return None

	def group_folders_by_name(self, folders, progress_callback=None):
		"""按名称对文件夹进行分组（多进程加速）

		Args:
			folders: list, 文件夹列表
			progress_callback: function, 进度回调函数

		Returns:
			tuple: (groups, tag_groups)
		"""
		if not folders:
			return [], {}

		total = len(folders)
		if progress_callback:
			progress_callback(0, total, "开始分组...")

		# 使用字典进行快速分组
		name_groups = defaultdict(list)
		for i, folder in enumerate(folders):
			if self._stopped:
				break

			while self._paused and not self._stopped:
				time.sleep(0.05)

			name = folder['content_name']
			name_groups[name].append(folder)

			if progress_callback and i % 100 == 0:
				progress_callback(i, total, f"分组进度: {i}/{total}")

		if progress_callback:
			progress_callback(total, total, "分组完成")

		# 构建结果
		groups = []
		tag_groups = defaultdict(list)

		for name, folder_list in name_groups.items():
			group = {
				'name': name,
				'folders': folder_list,
				'similarity': 1.0,
				'content_similarity': None
			}
			groups.append(group)

			# 按标签分组
			for folder in folder_list:
				for tag in folder.get('tags', []):
					tag_groups[tag].append(folder)

		return groups, dict(tag_groups)

	def get_folder_images(self, folder_path):
		"""获取文件夹中的图像文件（按需调用）

		Args:
			folder_path: str, 文件夹路径

		Returns:
			list: 图像文件路径列表
		"""
		image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
		images = []
		try:
			for root, dirs, files in os.walk(folder_path):
				for file in files:
					ext = os.path.splitext(file)[1].lower()
					if ext in image_extensions:
						images.append(os.path.join(root, file))
		except Exception as e:
			print(f"获取图像列表失败 {folder_path}: {e}")
		return images

	def get_folder_size_async(self, folder_info):
		"""异步获取文件夹大小（按需调用）

		Args:
			folder_info: dict, 文件夹信息字典

		Returns:
			tuple: (size_bytes, size_formatted)
		"""
		folder_path = folder_info['path']
		total_size = 0
		try:
			for root, dirs, files in os.walk(folder_path):
				for file in files:
					file_path = os.path.join(root, file)
					if os.path.exists(file_path):
						total_size += os.path.getsize(file_path)
		except Exception as e:
			print(f"获取文件夹大小失败 {folder_path}: {e}")

		return total_size, self._format_size(total_size)

	def _format_size(self, size_bytes):
		"""格式化文件大小

		Args:
			size_bytes: int, 字节大小

		Returns:
			str: 格式化后的大小
		"""
		if size_bytes < 1024:
			return f"{size_bytes} B"
		elif size_bytes < 1024 * 1024:
			return f"{size_bytes / 1024:.2f} KB"
		elif size_bytes < 1024 * 1024 * 1024:
			return f"{size_bytes / (1024 * 1024):.2f} MB"
		else:
			return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
