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
	"""计算单个图像的哈希值（用于多进程）

	Args:
		image_path: 图像路径

	Returns:
		tuple: (image_path, hash_str)
	"""
	try:
		img = Image.open(image_path).convert('L')
		img = img.resize((8, 8), Image.LANCZOS)
		pixels = np.array(img)

		# CPU计算
		avg_pixel = np.mean(pixels)
		hash_str = ''
		for i in range(8):
			for j in range(8):
				hash_str += '1' if pixels[i, j] > avg_pixel else '0'

		return (image_path, hash_str)
	except Exception as e:
		return (image_path, None)


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
		"""批量计算图像哈希值

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

		# 使用多进程处理
		with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
			futures = {executor.submit(_calculate_single_hash, p): p for p in uncached_paths}

			for future in as_completed(futures):
				# 检查是否已停止
				if self._stopped:
					executor.shutdown(wait=False)
					break

				# 检查是否暂停
				while self._paused:
					time.sleep(0.1)
					if self._stopped:
						executor.shutdown(wait=False)
						break

				path, hash_str = future.result()
				if hash_str:
					results[path] = hash_str
					self._hash_cache[path] = hash_str

				completed += 1
				if progress_callback and completed % 5 == 0:
					progress_callback(completed, total, f"计算哈希: {completed}/{total}")

		# 合并缓存结果
		for path in image_paths:
			if path in self._hash_cache:
				results[path] = self._hash_cache[path]

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
		"""计算两个名称的相似度

		Args:
			name1: 第一个名称
			name2: 第二个名称

		Returns:
			float: 相似度 (0-1)
		"""
		matcher = SequenceMatcher(None, name1, name2)
		return matcher.ratio()

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
		"""计算两个文件夹内容的相似度（多进程优化）

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

		# 计算相似度
		similarities = []
		total_images = len(images1)

		# CPU计算
		for idx, img1 in enumerate(images1):
			# 检查是否已停止
			if self._stopped:
				return 0.0

			# 检查是否暂停
			while self._paused:
				time.sleep(0.1)
				if self._stopped:
					return 0.0

			if progress_callback:
				progress_callback(idx + 1, total_images, f"比对图像: {idx + 1}/{total_images}")

			hash1 = hash_map.get(img1)
			if not hash1:
				continue

			max_similarity = 0
			for img2 in images2:
				hash2 = hash_map.get(img2)
				if not hash2:
					continue

				similarity = self.hamming_distance(hash1, hash2)
				max_similarity = max(max_similarity, similarity)

			similarities.append(max_similarity)

		if not similarities:
			return 0.0

		average_similarity = sum(similarities) / len(similarities)
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
