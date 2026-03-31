#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件夹处理器模块
"""

import os
import json
import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count

from app.similarity import ImageSimilarity, _hamming_distance_batch
from app.scanner import FolderScanner


class FolderProcessor:
	"""文件夹处理器类"""

	def __init__(self):
		"""初始化文件夹处理器"""
		self.similarity = ImageSimilarity()
		self.scanner = FolderScanner()
		self.max_workers = cpu_count() * 2

	def _load_folder_images_parallel(self, folders, progress_callback=None):
		"""并行加载多个文件夹的图像列表（优化版）

		Args:
			folders: 文件夹列表
			progress_callback: 进度回调函数

		Returns:
			dict: {folder_path: images_list}
		"""
		results = {}
		total = len(folders)
		completed = 0

		# 过滤已加载的文件夹
		folders_to_load = [folder for folder in folders if not folder.get('images')]
		if not folders_to_load:
			# 所有文件夹都已加载
			for folder in folders:
				results[folder['path']] = folder['images']
			if progress_callback:
				progress_callback(total, total, "加载文件夹: 完成")
			return results

		def load_single(folder):
			# 检查是否已停止
			if self.similarity._stopped:
				return folder['path'], []
			# 检查是否暂停
			while self.similarity._paused and not self.similarity._stopped:
				time.sleep(0.05)  # 减少等待时间
			if self.similarity._stopped:
				return folder['path'], []
			images = self.scanner.get_folder_images(folder['path'])
			folder['images'] = images
			return folder['path'], images

		# 动态调整线程数
		thread_count = min(self.max_workers, len(folders_to_load))
		if thread_count == 0:
			return results

		with ThreadPoolExecutor(max_workers=thread_count) as executor:
			futures = {executor.submit(load_single, folder): folder for folder in folders_to_load}

			for future in as_completed(futures):
				# 检查是否已停止
				if self.similarity._stopped:
					executor.shutdown(wait=False)
					break
				# 检查是否暂停
				while self.similarity._paused and not self.similarity._stopped:
					time.sleep(0.05)
				if self.similarity._stopped:
					executor.shutdown(wait=False)
					break
				path, images = future.result()
				results[path] = images
				completed += 1
				if progress_callback and completed % 5 == 0:
					progress_callback(completed, total, f"加载文件夹: {completed}/{total}")

		# 合并已加载的结果
		for folder in folders:
			if folder['path'] not in results and folder.get('images'):
				results[folder['path']] = folder['images']

		return results

	def process_folders(self, folder_path, threshold=0.7, progress_callback=None):
		"""处理文件夹，找出相似的文件夹组

		Args:
			folder_path: str, 要扫描的文件夹路径
			threshold: float, 相似度阈值
			progress_callback: function, 进度回调函数

		Returns:
			dict: 包含扫描结果的字典
		"""
		start_time = time.time()

		# 阶段1: 扫描文件夹
		if progress_callback:
			progress_callback(0, 100, "阶段1/2: 扫描文件夹...")

		folders, skipped = self.scanner.scan_folders(folder_path, progress_callback)
		total_scanned = len(folders) + skipped

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

		# 使用scanner的classify_folders方法进行分类
		groups, tag_groups = self.scanner.classify_folders(
			folders, threshold,
			lambda cur, tot, msg: progress_callback(int((cur/tot)*100), 100, msg) if progress_callback else None
		)

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

	def _calculate_folder_pair_similarity(self, folder1, folder2, folder_hashes):
		"""计算两个文件夹的相似度

		Args:
			folder1: 第一个文件夹
			folder2: 第二个文件夹
			folder_hashes: 文件夹哈希映射

		Returns:
			float: 相似度
		"""
		hash_list1 = folder_hashes.get(folder1['path'], [])
		hash_list2 = folder_hashes.get(folder2['path'], [])

		if not hash_list1 or not hash_list2:
			return 0.0

		# 使用NumPy计算相似度
		similarity_matrix = _hamming_distance_batch(hash_list1, hash_list2)

		if similarity_matrix.size > 0:
			max_similarities = np.max(similarity_matrix, axis=1)
			return float(np.mean(max_similarities))
		return 0.0

	def calculate_content_similarity_for_group(self, group, progress_callback=None):
		"""计算指定组的内容相似度（多线程+NumPy优化）

		Args:
			group: dict, 文件夹组
			progress_callback: function, 进度回调函数

		Returns:
			float: 内容相似度
		"""
		if len(group['folders']) < 2:
			group['content_similarity'] = 1.0
			return 1.0

		folders = group['folders']

		# 阶段1: 并行加载所有文件夹的图像列表
		if progress_callback:
			progress_callback(0, 100, "正在加载图像列表...")

		folder_images_map = self._load_folder_images_parallel(folders, progress_callback)

		# 收集所有图像路径
		all_images = []
		for images in folder_images_map.values():
			all_images.extend(images)
		all_images = list(set(all_images))

		if not all_images:
			group['content_similarity'] = 0.0
			return 0.0

		# 阶段2: 批量计算所有图像的哈希值（多进程）
		if progress_callback:
			progress_callback(30, 100, "正在计算图像哈希...")

		hash_map = self.similarity.calculate_hash_batch(all_images, progress_callback)

		# 阶段3: 批量计算所有文件夹对的相似度
		if progress_callback:
			progress_callback(70, 100, "正在比对文件夹内容...")

		# 为每个文件夹构建哈希列表
		folder_hashes = {}
		valid_folders = []
		for folder in folders:
			images = folder_images_map.get(folder['path'], [])
			hashes = [hash_map.get(img) for img in images if hash_map.get(img)]
			if hashes:  # 只保留有有效哈希的文件夹
				folder_hashes[folder['path']] = hashes
				valid_folders.append(folder)

		if len(valid_folders) < 2:
			group['content_similarity'] = 0.0
			return 0.0

		# 计算所有文件夹对的相似度（多线程）
		total_pairs = len(valid_folders) * (len(valid_folders) - 1) // 2
		if total_pairs == 0:
			group['content_similarity'] = 0.0
			return 0.0

		current_pair = 0
		similarities = []

		# 生成所有文件夹对
		folder_pairs = []
		for i in range(len(valid_folders)):
			for j in range(i + 1, len(valid_folders)):
				folder_pairs.append((valid_folders[i], valid_folders[j]))

		# 动态调整线程数
		thread_count = min(self.max_workers, total_pairs)
		if thread_count > 0:
			with ThreadPoolExecutor(max_workers=thread_count) as executor:
				# 提交所有任务
				future_to_pair = {
					executor.submit(self._calculate_folder_pair_similarity, pair[0], pair[1], folder_hashes): pair
					for pair in folder_pairs
				}

				# 收集结果
				for future in as_completed(future_to_pair):
					# 检查是否已停止
					if self.similarity._stopped:
						executor.shutdown(wait=False)
						break

					# 检查是否暂停
					while self.similarity._paused:
						time.sleep(0.05)
						if self.similarity._stopped:
							executor.shutdown(wait=False)
							break

					similarity = future.result()
					if similarity > 0:
						similarities.append(similarity)

					current_pair += 1
					if progress_callback and current_pair % 5 == 0:
						progress_percent = 70 + int((current_pair / total_pairs) * 30)
						progress_callback(progress_percent, 100, f"比对进度: {current_pair}/{total_pairs}")
		else:
			# 单线程处理
			for pair in folder_pairs:
				if self.similarity._stopped:
					break
				while self.similarity._paused:
					time.sleep(0.05)
					if self.similarity._stopped:
						break
				similarity = self._calculate_folder_pair_similarity(pair[0], pair[1], folder_hashes)
				if similarity > 0:
					similarities.append(similarity)
				current_pair += 1
				if progress_callback and current_pair % 5 == 0:
					progress_percent = 70 + int((current_pair / total_pairs) * 30)
					progress_callback(progress_percent, 100, f"比对进度: {current_pair}/{total_pairs}")

		# 计算整体平均相似度
		if similarities:
			content_similarity = sum(similarities) / len(similarities)
		else:
			content_similarity = 0.0

		group['content_similarity'] = content_similarity

		if progress_callback:
			progress_callback(100, 100, "比对完成")

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
