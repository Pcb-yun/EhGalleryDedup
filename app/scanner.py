#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件夹扫描器模块
"""

import os
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count

from app.similarity import ImageSimilarity


class FolderScanner:
	"""文件夹扫描器类"""

	def __init__(self):
		"""初始化文件夹扫描器"""
		# 控制标志
		self._stop_flag = False
		self._pause_flag = False
		# 线程数 - IO密集型使用更多线程
		self.max_workers = min(64, cpu_count() * 8)
		# 相似度计算对象
		self.similarity = ImageSimilarity()

	def stop(self):
		"""停止扫描"""
		self._stop_flag = True

	def pause(self):
		"""暂停扫描"""
		self._pause_flag = True

	def resume(self):
		"""恢复扫描"""
		self._pause_flag = False

	def _parse_folder_name(self, folder_name):
		"""解析文件夹名称

		Args:
			folder_name: 文件夹名称

		Returns:
			tuple: (serial, clean_name, original_name) 或 None
		"""
		# 匹配数字-名称格式
		match = re.match(r'^(\d+)(?:\s*-\s*|\s*[-_]\s*)(.+)$', folder_name)
		if match:
			serial = match.group(1)
			original_name = match.group(2)
			# 清理名称
			clean_name = re.sub(r'\[.*?\]', '', original_name).strip()
			return serial, clean_name, original_name
		return None

	def _get_folder_mtime(self, folder_path):
		"""获取文件夹最后修改时间

		Args:
			folder_path: 文件夹路径

		Returns:
			str: 格式化的时间字符串
		"""
		try:
			mtime = os.path.getmtime(folder_path)
			return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
		except Exception as e:
			print(f"获取文件夹修改时间失败 {folder_path}: {e}")
			return '未知'

	def _extract_extension_tag(self, folder_name):
		"""提取文件夹名称中的标签

		Args:
			folder_name: 文件夹名称

		Returns:
			str: 标签或空字符串
		"""
		match = re.search(r'\[(.*?)\]', folder_name)
		if match:
			return match.group(1)
		return ''

	def _calculate_name_similarity_fast(self, name1, name2):
		"""快速计算名称相似度

		Args:
			name1: 第一个名称
			name2: 第二个名称

		Returns:
			float: 相似度 (0-1)
		"""
		if not name1 or not name2:
			return 0.0
		if name1 == name2:
			return 1.0

		len1, len2 = len(name1), len(name2)
		if len1 == 0:
			return 0.0 if len2 > 0 else 1.0
		if len2 == 0:
			return 0.0

		# 优化：只保留两行
		prev_row = list(range(len2 + 1))
		for i, c1 in enumerate(name1):
			curr_row = [i + 1]
			for j, c2 in enumerate(name2):
				insertions = prev_row[j + 1] + 1
				deletions = curr_row[j] + 1
				substitutions = prev_row[j] + (c1 != c2)
				curr_row.append(min(insertions, deletions, substitutions))
			prev_row = curr_row

		distance = prev_row[-1]
		max_len = max(len1, len2)
		return 1.0 - (distance / max_len)

	def _process_folder_entry(self, entry):
		"""处理单个文件夹条目

		Args:
			entry: os.DirEntry对象

		Returns:
			dict or None: 文件夹信息或None
		"""
		if self._stop_flag:
			return None

		while self._pause_flag and not self._stop_flag:
			time.sleep(0.05)

		if self._stop_flag:
			return None

		try:
			if entry.is_dir():
				parsed = self._parse_folder_name(entry.name)
				if parsed:
					serial, clean_name, original_name = parsed

					folder_info = {
						'path': entry.path,
						'name': entry.name,
						'serial': serial,
						'content_name': clean_name,
						'original_name': original_name,
						'size': 0,
						'size_formatted': '-',
						'mtime': self._get_folder_mtime(entry.path),
						'images': []
					}

					return folder_info
				else:
					return 'skipped'
		except Exception as e:
			print(f"处理文件夹失败 {entry.path}: {e}")
			return 'skipped'

	def scan_folders(self, root_dir, progress_callback=None):
		"""扫描文件夹（仅获取基本信息，不分类）

		Args:
			root_dir: 根目录路径
			progress_callback: 进度回调函数

		Returns:
			list: 文件夹信息列表
		"""
		self._stop_flag = False
		self._pause_flag = False

		folders = []
		skipped = 0

		try:
			with os.scandir(root_dir) as entries:
				entry_list = list(entries)
			total_count = len(entry_list)
		except Exception as e:
			print(f"读取目录失败: {e}")
			return [], 0

		if total_count == 0:
			return [], 0

		processed_count = 0

		# 使用多线程并行处理
		with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
			# 提交所有任务
			future_to_entry = {
				executor.submit(self._process_folder_entry, entry): entry
				for entry in entry_list
			}

			# 收集结果
			for future in as_completed(future_to_entry):
				if self._stop_flag:
					executor.shutdown(wait=False)
					break

				result = future.result()
				processed_count += 1

				if progress_callback and (processed_count % 50 == 0 or processed_count == total_count):
					entry = future_to_entry[future]
					progress_callback(processed_count, total_count, f"正在扫描: {entry.name}")

				if result == 'skipped':
					skipped += 1
				elif result:
					folders.append(result)

		return folders, skipped

	def _process_char_group(self, char_folder, threshold):
		"""处理单个首字符组的分类

		Args:
			char_folder: 同一首字符的文件夹列表
			threshold: 相似度阈值

		Returns:
			list: 分类后的组列表
		"""
		if not char_folder:
			return []

		# 按名称长度排序，减少比较次数
		char_folder.sort(key=lambda x: len(x['content_name']))

		groups = []
		# 直接使用列表存储组，避免字典查找开销
		for folder in char_folder:
			content_name = folder['content_name']
			matched_group = None
			best_similarity = 0

			# 只与已有的组进行比较
			for group in groups:
				if group['folders']:
					ref_folder = group['folders'][0]
					# 快速筛选：长度差异过大的直接跳过
					len_diff = abs(len(content_name) - len(ref_folder['content_name']))
					if len_diff > 3:
						continue
					# 计算相似度
					similarity = self._calculate_name_similarity_fast(
						content_name, ref_folder['content_name']
					)
					if similarity >= threshold and similarity > best_similarity:
						matched_group = group
						best_similarity = similarity

			if matched_group:
				matched_group['folders'].append(folder)
				if best_similarity < matched_group['similarity']:
					matched_group['similarity'] = best_similarity
			else:
				new_group = {
					'folders': [folder],
					'similarity': 1.0,
					'content_similarity': None
				}
				groups.append(new_group)

		return groups

	def classify_folders(self, folders, threshold=0.7, progress_callback=None):
		"""对文件夹进行分类（首字符索引，优化版）"""
		name_groups = []  # 按名称相似分组
		tag_groups = {}   # 按标签分组
		total = len(folders)
		processed = 0

		if total == 0:
			return name_groups, tag_groups

		# 快速路径：文件夹数量较少时使用单线程
		if total < 100:
			for folder in folders:
				processed += 1
				if progress_callback and (processed % 10 == 0 or processed == total):
					progress_callback(processed, total, f"分类中: {folder['name'][:30]}")

				# 提取标签
				tag = self._extract_extension_tag(folder['original_name'])
				if tag:
					if tag not in tag_groups:
						tag_groups[tag] = []
					tag_groups[tag].append(folder)

				# 查找相似组
				matched_group = None
				best_similarity = 0
				content_name = folder['content_name']

				for group in name_groups:
					if group['folders']:
						ref_folder = group['folders'][0]
						# 快速筛选：长度差异过大的直接跳过
						len_diff = abs(len(content_name) - len(ref_folder['content_name']))
						if len_diff > 3:
							continue
						# 计算相似度
						similarity = self._calculate_name_similarity_fast(
							content_name, ref_folder['content_name']
						)
						if similarity >= threshold and similarity > best_similarity:
							matched_group = group
							best_similarity = similarity

				if matched_group:
					matched_group['folders'].append(folder)
					if best_similarity < matched_group['similarity']:
						matched_group['similarity'] = best_similarity
				else:
					new_group = {
						'folders': [folder],
						'similarity': 1.0,
						'content_similarity': None
					}
					name_groups.append(new_group)

			return name_groups, tag_groups

		# 文件夹数量较多时使用并行处理
		# 预处理：按首字符分组
		char_groups = {}
		grouped = 0
		for folder in folders:
			first_char = folder['content_name'][0] if folder['content_name'] else ''
			if first_char not in char_groups:
				char_groups[first_char] = []
			char_groups[first_char].append(folder)
			grouped += 1
			if progress_callback and (grouped % 10 == 0 or grouped == total):
				progress_callback(grouped, total, f"分组中: {grouped}/{total}")

		# 限制线程数，避免过多线程导致的开销
		thread_count = min(self.max_workers, len(char_groups))
		if thread_count > 1:
			with ThreadPoolExecutor(max_workers=thread_count) as executor:
				# 提交所有任务
				future_to_char = {}
				for char, char_folder in char_groups.items():
					if len(char_folder) > 0:
						future_to_char[executor.submit(self._process_char_group, char_folder, threshold)] = char

				# 收集结果
				for future in as_completed(future_to_char):
					char_group_result = future.result()
					name_groups.extend(char_group_result)

					# 更新进度
					char = future_to_char[future]
					processed += len(char_groups[char])
					if progress_callback:
						# 每次完成一个字符组就更新进度
						progress_percent = int((processed / total) * 100)
						progress_callback(processed, total, f"分类中: {processed}/{total}")
		else:
			# 单线程处理
			for char, char_folder in char_groups.items():
				if len(char_folder) > 0:
					char_group_result = self._process_char_group(char_folder, threshold)
					name_groups.extend(char_group_result)
					processed += len(char_folder)
					if progress_callback:
						# 每次完成一个字符组就更新进度
						progress_percent = int((processed / total) * 100)
						progress_callback(processed, total, f"分类中: {processed}/{total}")

		# 构建标签分组
		for group in name_groups:
			for folder in group['folders']:
				tag = self._extract_extension_tag(folder['original_name'])
				if tag:
					if tag not in tag_groups:
						tag_groups[tag] = []
					tag_groups[tag].append(folder)

		return name_groups, tag_groups

	def _scan_directory(self, directory, image_extensions):
		"""扫描单个目录获取图像文件（非递归优化版）

		Args:
			directory: 目录路径
			image_extensions: 图像扩展名集合

		Returns:
			list: 图像文件路径列表
		"""
		images = []
		dirs_to_scan = [directory]

		try:
			while dirs_to_scan:
				current_dir = dirs_to_scan.pop()
				with os.scandir(current_dir) as entries:
					for item in entries:
						if self._stop_flag:
							return []
						while self._pause_flag and not self._stop_flag:
							time.sleep(0.05)
						if item.is_dir():
							dirs_to_scan.append(item.path)
						elif item.is_file():
							ext = os.path.splitext(item.name)[1].lower()
							if ext in image_extensions:
								images.append(item.path)
		except Exception as e:
			print(f"扫描目录失败 {directory}: {e}")
		return images

	def get_folder_images(self, folder_path):
		"""获取文件夹中的图像文件

		Args:
			folder_path: 文件夹路径

		Returns:
			list: 图像文件路径列表
		"""
		image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
		return self._scan_directory(folder_path, image_extensions)

	def get_folder_size_async(self, folder_info):
		"""异步获取文件夹大小（优化版）

		Args:
			folder_info: 文件夹信息

		Returns:
			tuple: (size, size_formatted)
		"""
		folder_path = folder_info['path']
		total_size = 0
		dirs_to_scan = [folder_path]

		try:
			while dirs_to_scan:
				current_dir = dirs_to_scan.pop()
				with os.scandir(current_dir) as entries:
					for item in entries:
						if self._stop_flag:
							return 0, "-"
						if item.is_dir():
							dirs_to_scan.append(item.path)
						elif item.is_file():
							total_size += item.stat().st_size
		except Exception as e:
			print(f"获取文件夹大小失败: {e}")

		# 格式化大小
		if total_size < 1024:
			size_formatted = f"{total_size} B"
		elif total_size < 1024 * 1024:
			size_formatted = f"{total_size / 1024:.2f} KB"
		elif total_size < 1024 * 1024 * 1024:
			size_formatted = f"{total_size / (1024 * 1024):.2f} MB"
		else:
			size_formatted = f"{total_size / (1024 * 1024 * 1024):.2f} GB"

		return total_size, size_formatted
