#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件夹处理器模块
"""

import os
import json
import time
from app.similarity import ImageSimilarity
from app.scanner import FolderScanner

class FolderProcessor:
	"""文件夹处理器类"""

	def __init__(self):
		"""初始化文件夹处理器"""
		self.similarity = ImageSimilarity()
		self.scanner = FolderScanner()

	def process_folders(self, folder_path, threshold=0.7, progress_callback=None):
		"""处理文件夹，找出相似的文件夹组

		Args:
			folder_path: str, 要扫描的文件夹路径
			threshold: float, 相似度阈值
			progress_callback: function, 进度回调函数

		Returns:
			dict: 包含扫描结果的字典
		"""
		import time
		start_time = time.time()

		# 阶段1: 扫描文件夹
		if progress_callback:
			progress_callback(0, 100, "阶段1/2: 扫描文件夹...")

		scan_result = self.scanner.scan_folders(folder_path, progress_callback)
		folders = scan_result['folders']
		total_scanned = scan_result['total_scanned']
		skipped = scan_result['skipped']

		# 检查是否已停止
		if self.similarity._stopped:
			return {
				'groups': [],
				'tag_groups': {},
				'total_scanned': total_scanned,
				'skipped': skipped
			}

		# 阶段2: 比对文件夹名称
		if progress_callback:
			progress_callback(0, 100, "阶段2/2: 比对文件夹名称...")

		groups = []
		tag_groups = {}
		processed = set()

		total_folders = len(folders)
		current_folder = 0

		for i, folder1 in enumerate(folders):
			# 检查是否已停止
			if self.similarity._stopped:
				break

			# 检查是否暂停
			while self.similarity._paused:
				time.sleep(0.1)
				if self.similarity._stopped:
					break

			if i in processed:
				continue

			current_folder += 1
			if progress_callback:
				progress_percent = int((current_folder / total_folders) * 100) if total_folders > 0 else 0
				progress_callback(progress_percent, 100, f"阶段2/2: 比对名称 {current_folder}/{total_folders}")

			# 查找与当前文件夹相似的其他文件夹
			similar_folders = [folder1]
			processed.add(i)

			for j, folder2 in enumerate(folders):
				# 检查是否已停止
				if self.similarity._stopped:
					break

				# 检查是否暂停
				while self.similarity._paused:
					time.sleep(0.1)
					if self.similarity._stopped:
						break

				if j in processed or i == j:
					continue

				# 计算名称相似度
				name_similarity = self.similarity.calculate_folders_name_similarity(folder1, folder2)
				if name_similarity >= threshold:
					similar_folders.append(folder2)
					processed.add(j)

			if len(similar_folders) > 1:
				# 为组生成一个名称
				group_name = self._generate_group_name(similar_folders)

				# 计算组内文件夹的平均相似度
				average_similarity = self._calculate_group_average_similarity(similar_folders)

				group = {
					'name': group_name,
					'folders': similar_folders,
					'similarity': average_similarity,
					'content_similarity': None  # 将在内容比对时计算
				}
				groups.append(group)

				# 按标签分组
				for folder in similar_folders:
					for tag in folder.get('tags', []):
						if tag not in tag_groups:
							tag_groups[tag] = []
						tag_groups[tag].append(folder)

		if progress_callback:
			progress_callback(100, 100, "扫描完成")

		return {
			'groups': groups,
			'tag_groups': tag_groups,
			'total_scanned': total_scanned,
			'skipped': skipped
		}

	def _generate_group_name(self, folders):
		"""为相似文件夹组生成名称

		Args:
			folders: list, 文件夹列表

		Returns:
			str: 组名称
		"""
		# 提取所有文件夹的名称
		names = [folder['content_name'] for folder in folders]

		# 找出最长的公共前缀
		if not names:
			return "未命名组"

		prefix = os.path.commonprefix(names)
		if prefix:
			return f"{prefix.strip()}"
		else:
			# 如果没有公共前缀，使用第一个文件夹的名称
			return f"{names[0][:20]}..."

	def _calculate_group_average_similarity(self, folders):
		"""计算组内文件夹的平均相似度

		Args:
			folders: list, 文件夹列表

		Returns:
			float: 平均相似度
		"""
		if len(folders) < 2:
			return 1.0

		total_similarity = 0
		count = 0

		for i in range(len(folders)):
			for j in range(i + 1, len(folders)):
				similarity = self.similarity.calculate_folders_name_similarity(folders[i], folders[j])
				total_similarity += similarity
				count += 1

		return total_similarity / count if count > 0 else 1.0

	def calculate_content_similarity_for_group(self, group, progress_callback=None):
		"""计算指定组的内容相似度（多进程优化）

		Args:
			group: dict, 文件夹组
			progress_callback: function, 进度回调函数

		Returns:
			float: 内容相似度
		"""
		start_time = time.time()

		if len(group['folders']) < 2:
			group['content_similarity'] = 1.0
			return 1.0

		folder1 = group['folders'][0]
		folder2 = group['folders'][1]

		# 确保图像列表已加载
		if not folder1.get('images'):
			folder1['images'] = self.scanner.get_folder_images(folder1['path'])
		if not folder2.get('images'):
			folder2['images'] = self.scanner.get_folder_images(folder2['path'])

		content_similarity = self.similarity.calculate_folders_content_similarity(
			folder1, folder2, progress_callback
		)

		group['content_similarity'] = content_similarity

		return content_similarity

	def clear_cache(self):
		"""清空缓存"""
		self.similarity.clear_cache()

	def pause(self):
		"""暂停处理"""
		self.scanner.pause()
		self.similarity.pause()

	def resume(self):
		"""恢复处理"""
		self.scanner.resume()
		self.similarity.resume()

	def stop(self):
		"""停止处理"""
		self.scanner.stop()
		self.similarity.stop()