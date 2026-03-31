#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图像相似度计算模块
"""

import os
import time
import numpy as np
from PIL import Image
from difflib import SequenceMatcher
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count
import multiprocessing

# 禁用siphash24警告
import warnings
warnings.filterwarnings("ignore", category=UserWarning, message="Unable to import recommended hash")


def _calculate_single_hash(image_path):
	"""计算单个图像的哈希值（用于多进程，优化版）

	Args:
		image_path: 图像路径

	Returns:
		tuple: (image_path, hash_str)
	"""
	try:
		# 打开图像并转换为灰度
		with Image.open(image_path) as img:
			# 快速调整大小
			img = img.convert('L').resize((8, 8), Image.BILINEAR)  # 更快的缩放
			# 直接获取像素数据
			pixels = list(img.getdata())

		# 计算平均值
		avg_pixel = sum(pixels) / len(pixels)
		# 生成哈希
		hash_bits = ['1' if p > avg_pixel else '0' for p in pixels]
		hash_str = ''.join(hash_bits)

		return (image_path, hash_str)
	except Exception as e:
		return (image_path, None)


def _hamming_distance_batch(hash_list1, hash_list2):
	"""批量计算汉明距离（优化版）

	Args:
		hash_list1: 第一组哈希值列表
		hash_list2: 第二组哈希值列表

	Returns:
		np.ndarray: 相似度矩阵 [len(hash_list1), len(hash_list2)]
	"""
	if not hash_list1 or not hash_list2:
		return np.array([])

	# 快速路径：当其中一个列表只有一个元素时
	if len(hash_list1) == 1:
		hash1 = hash_list1[0]
		similarities = np.zeros(len(hash_list2), dtype=np.float32)
		for i, hash2 in enumerate(hash_list2):
			distance = sum(c1 != c2 for c1, c2 in zip(hash1, hash2))
			similarities[i] = 1.0 - (distance / 64.0)
		return similarities.reshape(1, -1)

	if len(hash_list2) == 1:
		hash2 = hash_list2[0]
		similarities = np.zeros(len(hash_list1), dtype=np.float32)
		for i, hash1 in enumerate(hash_list1):
			distance = sum(c1 != c2 for c1, c2 in zip(hash1, hash2))
			similarities[i] = 1.0 - (distance / 64.0)
		return similarities.reshape(-1, 1)

	# 批量处理
	# 将哈希字符串转换为NumPy数组
	def hashes_to_array(hash_list):
		return np.array([[int(c) for c in h] for h in hash_list], dtype=np.int8)

	arr1 = hashes_to_array(hash_list1)
	arr2 = hashes_to_array(hash_list2)

	# 计算汉明距离矩阵 (向量化操作)
	# arr1: [n1, 64], arr2: [n2, 64]
	# 结果: [n1, n2]
	distances = np.sum(arr1[:, np.newaxis, :] != arr2[np.newaxis, :, :], axis=2)
	similarities = 1.0 - (distances / 64.0)

	return similarities


class ImageSimilarity:
	"""图像相似度计算"""

	def __init__(self, max_workers=None):
		self.hash_size = 8
		self.max_workers = max_workers or cpu_count()
		# 哈希缓存
		self._hash_cache = {}
		# 控制标志
		self._paused = False
		self._stopped = False
		# 进程池（常驻）
		self._process_pool = None

	def _get_process_pool(self):
		"""获取或创建进程池"""
		if self._process_pool is None:
			self._process_pool = ProcessPoolExecutor(max_workers=self.max_workers)
		return self._process_pool

	def _shutdown_process_pool(self):
		"""关闭进程池"""
		if self._process_pool is not None:
			self._process_pool.shutdown(wait=False)
			self._process_pool = None

	def calculate_hash(self, image_path):
		"""计算图像的感知哈希值

		Args:
			image_path: 图像文件路径

		Returns:
			str: 哈希值字符串
		"""
		# 检查缓存
		if image_path in self._hash_cache:
			return self._hash_cache[image_path]

		result = _calculate_single_hash(image_path)
		if result[1]:
			self._hash_cache[image_path] = result[1]
		return result[1]

	def calculate_hash_batch(self, image_paths, progress_callback=None):
		"""批量计算图像哈希值（优化版）

		Args:
			image_paths: 图像路径列表
			progress_callback: 进度回调函数

		Returns:
			dict: {image_path: hash_str}
		"""
		results = {}
		total = len(image_paths)
		completed = 0

		# 过滤已缓存的
		uncached_paths = [p for p in image_paths if p not in self._hash_cache]

		if not uncached_paths:
			# 全部已缓存，直接返回
			return {p: self._hash_cache[p] for p in image_paths}

		# 限制批量处理大小，避免内存占用过大
		batch_size = 100
		batches = [uncached_paths[i:i+batch_size] for i in range(0, len(uncached_paths), batch_size)]

		# 使用常驻进程池处理
		pool = self._get_process_pool()

		for batch in batches:
			if self._stopped:
				self._shutdown_process_pool()
				break

			# 检查是否暂停
			while self._paused:
				time.sleep(0.05)
				if self._stopped:
					self._shutdown_process_pool()
					break

			# 提交当前批次
			futures = {pool.submit(_calculate_single_hash, p): p for p in batch}

			for future in as_completed(futures):
				# 检查是否已停止
				if self._stopped:
					self._shutdown_process_pool()
					break

				# 检查是否暂停
				while self._paused:
					time.sleep(0.05)
					if self._stopped:
						self._shutdown_process_pool()
						break

				path, hash_str = future.result()
				if hash_str:
					results[path] = hash_str
					self._hash_cache[path] = hash_str

				completed += 1
				if progress_callback and completed % 10 == 0:  # 减少回调频率
					progress_callback(completed, total, f"计算哈希: {completed}/{total}")

		# 合并缓存结果
		for path in image_paths:
			if path in self._hash_cache:
				results[path] = self._hash_cache[path]

		# 清理缓存，限制缓存大小
		max_cache_size = 10000
		if len(self._hash_cache) > max_cache_size:
			# 移除最旧的缓存项
			old_keys = list(self._hash_cache.keys())[:len(self._hash_cache) - max_cache_size]
			for key in old_keys:
				if key in self._hash_cache:
					del self._hash_cache[key]

		return results

	def hamming_distance(self, hash1, hash2):
		"""计算两个哈希值的汉明距离

		Args:
			hash1: 第一个哈希值
			hash2: 第二个哈希值

		Returns:
			float: 相似度 (0-1, 1表示完全相同)
		"""
		if not hash1 or not hash2:
			return 0.0

		# CPU计算
		distance = sum(c1 != c2 for c1, c2 in zip(hash1, hash2))
		similarity = 1 - (distance / len(hash1))
		return similarity

	def calculate_name_similarity(self, name1, name2):
		"""计算两个名称的相似度（快速Levenshtein算法）

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

	def calculate_name_similarity_batch(self, names, threshold=0.7, progress_callback=None):
		"""批量计算名称相似度矩阵（Jaccard快速筛选+精确计算）

		Args:
			names: 名称列表
			threshold: 相似度阈值
			progress_callback: 进度回调函数

		Returns:
			np.ndarray: 相似度矩阵 [n, n]
		"""
		n = len(names)
		if n == 0:
			return np.array([])

		# 预处理：计算所有名称的n-gram集合
		def get_ngrams(text, n=2):
			if len(text) < n:
				return set([text])
			return set(text[i:i+n] for i in range(len(text) - n + 1))

		ngram_sets = [get_ngrams(name.lower()) for name in names]

		# 计算相似度矩阵
		similarity_matrix = np.zeros((n, n), dtype=np.float32)

		for i in range(n):
			similarity_matrix[i, i] = 1.0
			for j in range(i + 1, n):
				# 使用Jaccard相似度快速筛选
				set1, set2 = ngram_sets[i], ngram_sets[j]
				if not set1 or not set2:
					continue

				intersection = len(set1 & set2)
				union = len(set1 | set2)
				jaccard = intersection / union if union > 0 else 0

				# Jaccard相似度低于阈值一半的直接跳过
				if jaccard < threshold * 0.5:
					continue

				# 使用精确算法计算
				sim = self.calculate_name_similarity(names[i], names[j])
				similarity_matrix[i, j] = sim
				similarity_matrix[j, i] = sim

			if progress_callback and i % 50 == 0:
				progress_callback(i, n, f"计算相似度: {i}/{n}")

		return similarity_matrix

	def calculate_folders_name_similarity(self, folder1, folder2):
		"""计算两个文件夹的名称相似度

		Args:
			folder1: 第一个文件夹信息
			folder2: 第二个文件夹信息

		Returns:
			float: 相似度 (0-1)
		"""
		return self.calculate_name_similarity(
			folder1['content_name'], folder2['content_name']
		)

	def calculate_content_similarity(self, images1, images2, progress_callback=None):
		"""计算两个文件夹内容的相似度（NumPy向量化优化）

		Args:
			images1: 第一个文件夹的图像列表
			images2: 第二个文件夹的图像列表
			progress_callback: 进度回调函数

		Returns:
			float: 相似度 (0-1)
		"""
		if not images1 or not images2:
			return 0.0

		# 批量计算哈希（多进程）
		all_images = list(set(images1 + images2))
		hash_map = self.calculate_hash_batch(all_images, progress_callback)

		# 获取哈希值列表
		hash_list1 = [hash_map.get(img) for img in images1 if hash_map.get(img)]
		hash_list2 = [hash_map.get(img) for img in images2 if hash_map.get(img)]

		if not hash_list1 or not hash_list2:
			return 0.0

		# 使用NumPy向量化计算相似度矩阵
		similarity_matrix = _hamming_distance_batch(hash_list1, hash_list2)

		# 计算每个图像的最大相似度
		max_similarities = np.max(similarity_matrix, axis=1)

		# 计算平均相似度
		average_similarity = float(np.mean(max_similarities))

		if progress_callback:
			progress_callback(len(images1), len(images1), f"比对完成: {len(images1)}/{len(images1)}")

		return average_similarity

	def calculate_folders_content_similarity(self, folder1, folder2, progress_callback=None):
		"""计算两个文件夹的内容相似度

		Args:
			folder1: 第一个文件夹信息
			folder2: 第二个文件夹信息
			progress_callback: 进度回调函数

		Returns:
			float: 相似度 (0-1)
		"""
		return self.calculate_content_similarity(
			folder1['images'], folder2['images'], progress_callback
		)

	def clear_cache(self):
		"""清空哈希缓存"""
		self._hash_cache.clear()

	def get_cache_size(self):
		"""获取缓存大小"""
		return len(self._hash_cache)

	def pause(self):
		"""暂停处理"""
		self._paused = True

	def resume(self):
		"""恢复处理"""
		self._paused = False

	def stop(self):
		"""停止处理"""
		self._stopped = True
		self._shutdown_process_pool()
