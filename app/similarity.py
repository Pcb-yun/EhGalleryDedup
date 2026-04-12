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
from collections import OrderedDict

# 禁用siphash24警告
import warnings
warnings.filterwarnings("ignore", category=UserWarning, message="Unable to import recommended hash")


def _calculate_single_hash(image_path, hash_type='dhash'):
	"""计算单个图像的哈希值（支持多种哈希算法）

	Args:
		image_path: 图像路径
		hash_type: 哈希类型 ('ahash'|'phash'|'dhash')，默认使用dhash(差异哈希)

	Returns:
		tuple: (image_path, hash_str)
	"""
	try:
		if hash_type == 'dhash':
			return _calculate_dhash(image_path)
		elif hash_type == 'phash':
			return _calculate_phash(image_path)
		else:
			return _calculate_ahash(image_path)
	except Exception as e:
		return (image_path, None)


def _calculate_ahash(image_path):
	"""计算平均哈希(Average Hash)

	Args:
		image_path: 图像路径

	Returns:
		tuple: (image_path, hash_str)
	"""
	try:
		with Image.open(image_path) as img:
			img = img.convert('L').resize((8, 8), Image.BILINEAR)
			pixels = list(img.getdata())
		avg_pixel = sum(pixels) / len(pixels)
		hash_bits = ['1' if p > avg_pixel else '0' for p in pixels]
		return (image_path, ''.join(hash_bits))
	except Exception as e:
		return (image_path, None)


def _calculate_dhash(image_path):
	"""计算差异哈希(Difference Hash) - 更快更准确

	Args:
		image_path: 图像路径

	Returns:
		tuple: (image_path, hash_str)
	"""
	try:
		with Image.open(image_path) as img:
			img = img.convert('L').resize((9, 8), Image.BILINEAR)
			pixels = list(img.getdata())
		hash_bits = []
		for row in range(8):
			for col in range(8):
				left = pixels[row * 9 + col]
				right = pixels[row * 9 + col + 1]
				hash_bits.append('1' if left > right else '0')
		return (image_path, ''.join(hash_bits))
	except Exception as e:
		return (image_path, None)


def _calculate_phash(image_path):
	"""计算感知哈希(Perceptual Hash) - 更准确但稍慢

	Args:
		image_path: 图像路径

	Returns:
		tuple: (image_path, hash_str)
	"""
	try:
		with Image.open(image_path) as img:
			img = img.convert('L').resize((32, 32), Image.BILINEAR)
			pixels = np.array(img.getdata(), dtype=np.float32).reshape(32, 32)
		
		dct = np.fft.fft2(pixels)
		dct_low_freq = dct[:8, :8]
		median = np.median(np.abs(dct_low_freq))
		hash_bits = ['1' if abs(val) > median else '0' for val in dct_low_freq.flatten()]
		return (image_path, ''.join(hash_bits))
	except Exception as e:
		return (image_path, None)


def _hash_to_int(hash_str):
	"""将哈希字符串转换为整数，用于快速汉明距离计算

	Args:
		hash_str: 哈希字符串

	Returns:
		int: 哈希整数
	"""
	return int(hash_str, 2)


def _hamming_distance_fast(hash_int1, hash_int2):
	"""使用位运算快速计算汉明距离

	Args:
		hash_int1: 第一个哈希整数
		hash_int2: 第二个哈希整数

	Returns:
		int: 汉明距离
	"""
	return bin(hash_int1 ^ hash_int2).count('1')


def _hamming_distance_batch(hash_list1, hash_list2):
	"""批量计算汉明距离（优化版 - 位运算加速）

	Args:
		hash_list1: 第一组哈希值列表
		hash_list2: 第二组哈希值列表

	Returns:
		np.ndarray: 相似度矩阵 [len(hash_list1), len(hash_list2)]
	"""
	if not hash_list1 or not hash_list2:
		return np.array([])

	# 将哈希字符串转换为整数
	hash_ints1 = [_hash_to_int(h) for h in hash_list1]
	hash_ints2 = [_hash_to_int(h) for h in hash_list2]

	# 快速路径：当其中一个列表只有一个元素时
	if len(hash_list1) == 1:
		hash1 = hash_ints1[0]
		similarities = np.zeros(len(hash_list2), dtype=np.float32)
		for i, hash2 in enumerate(hash_ints2):
			distance = _hamming_distance_fast(hash1, hash2)
			similarities[i] = 1.0 - (distance / 64.0)
		return similarities.reshape(1, -1)

	if len(hash_list2) == 1:
		hash2 = hash_ints2[0]
		similarities = np.zeros(len(hash_list1), dtype=np.float32)
		for i, hash1 in enumerate(hash_ints1):
			distance = _hamming_distance_fast(hash1, hash2)
			similarities[i] = 1.0 - (distance / 64.0)
		return similarities.reshape(-1, 1)

	# 批量处理 - 使用NumPy向量化
	def hashes_to_array(hash_list):
		return np.array([[int(c) for c in h] for h in hash_list], dtype=np.int8)

	arr1 = hashes_to_array(hash_list1)
	arr2 = hashes_to_array(hash_list2)

	distances = np.sum(arr1[:, np.newaxis, :] != arr2[np.newaxis, :, :], axis=2)
	similarities = 1.0 - (distances / 64.0)

	return similarities


def _calculate_jaro_winkler(s1, s2, prefix_scale=0.1):
	"""计算Jaro-Winkler相似度 - 对短字符串更准确

	Args:
		s1: 第一个字符串
		s2: 第二个字符串
		prefix_scale: 前缀权重

	Returns:
		float: Jaro-Winkler相似度 (0-1)
	"""
	if s1 == s2:
		return 1.0

	len1, len2 = len(s1), len(s2)
	if len1 == 0 or len2 == 0:
		return 0.0

	# 计算匹配窗口大小
	match_distance = max(len1, len2) // 2 - 1

	# 标记匹配的字符
	matches1 = [False] * len1
	matches2 = [False] * len2
	match_count = 0

	for i in range(len1):
		start = max(0, i - match_distance)
		end = min(i + match_distance + 1, len2)
		for j in range(start, end):
			if not matches2[j] and s1[i] == s2[j]:
				matches1[i] = True
				matches2[j] = True
				match_count += 1
				break

	if match_count == 0:
		return 0.0

	# 计算转置次数
	transpositions = 0
	k = 0
	for i in range(len1):
		if matches1[i]:
			while k < len2 and not matches2[k]:
				k += 1
			if s1[i] != s2[k]:
				transpositions += 1
			k += 1
	transpositions //= 2

	# 计算Jaro相似度
	jaro = ((match_count / len1) + (match_count / len2) + 
	        ((match_count - transpositions) / match_count)) / 3.0

	# 计算共同前缀长度
	prefix_len = 0
	max_prefix = 4
	while prefix_len < min(max_prefix, len1, len2) and s1[prefix_len] == s2[prefix_len]:
		prefix_len += 1

	# 计算Jaro-Winkler相似度
	jaro_winkler = jaro + (prefix_len * prefix_scale * (1 - jaro))
	return jaro_winkler


class LRUCache:
	"""LRU缓存实现，用于高效管理哈希缓存"""

	def __init__(self, max_size=10000):
		self.cache = OrderedDict()
		self.max_size = max_size

	def get(self, key):
		if key not in self.cache:
			return None
		self.cache.move_to_end(key)
		return self.cache[key]

	def put(self, key, value):
		if key in self.cache:
			self.cache.move_to_end(key)
		self.cache[key] = value
		if len(self.cache) > self.max_size:
			self.cache.popitem(last=False)

	def clear(self):
		self.cache.clear()

	def __len__(self):
		return len(self.cache)

	def __contains__(self, key):
		return key in self.cache


class ImageSimilarity:
	"""图像相似度计算"""

	def __init__(self, max_workers=None, hash_type='dhash', cache_size=10000):
		self.hash_size = 8
		self.max_workers = max_workers or cpu_count()
		self.hash_type = hash_type  # 'ahash'|'phash'|'dhash'
		# 使用LRU缓存提高效率
		self._hash_cache = LRUCache(max_size=cache_size)
		# 名称相似度缓存
		self._name_similarity_cache = LRUCache(max_size=5000)
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
		"""计算图像的哈希值（支持多种哈希算法）

		Args:
			image_path: 图像文件路径

		Returns:
			str: 哈希值字符串
		"""
		cached = self._hash_cache.get(image_path)
		if cached is not None:
			return cached

		result = _calculate_single_hash(image_path, self.hash_type)
		if result[1]:
			self._hash_cache.put(image_path, result[1])
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
		uncached_paths = []
		for p in image_paths:
			cached = self._hash_cache.get(p)
			if cached is not None:
				results[p] = cached
			else:
				uncached_paths.append(p)

		if not uncached_paths:
			return results

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
			futures = {
				pool.submit(_calculate_single_hash, p, self.hash_type): p 
				for p in batch
			}

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
					self._hash_cache.put(path, hash_str)

				completed += 1
				if progress_callback and completed % 10 == 0:
					progress_callback(completed, total, f"计算哈希: {completed}/{total}")

		return results

	def hamming_distance(self, hash1, hash2):
		"""计算两个哈希值的汉明距离（优化版 - 位运算加速）

		Args:
			hash1: 第一个哈希值
			hash2: 第二个哈希值

		Returns:
			float: 相似度 (0-1, 1表示完全相同)
		"""
		if not hash1 or not hash2:
			return 0.0
		if hash1 == hash2:
			return 1.0

		try:
			hash_int1 = _hash_to_int(hash1)
			hash_int2 = _hash_to_int(hash2)
			distance = _hamming_distance_fast(hash_int1, hash_int2)
		except:
			distance = sum(c1 != c2 for c1, c2 in zip(hash1, hash2))

		similarity = 1.0 - (distance / len(hash1))
		return similarity

	def calculate_name_similarity(self, name1, name2, use_jaro_winkler=True):
		"""计算两个名称的相似度（支持Jaro-Winkler和Levenshtein算法）

		Args:
			name1: 第一个名称
			name2: 第二个名称
			use_jaro_winkler: 是否使用Jaro-Winkler算法（默认True）

		Returns:
			float: 相似度 (0-1)
		"""
		if not name1 or not name2:
			return 0.0
		if name1 == name2:
			return 1.0

		# 检查缓存
		cache_key = tuple(sorted([name1, name2]))
		cached = self._name_similarity_cache.get(cache_key)
		if cached is not None:
			return cached

		if use_jaro_winkler:
			similarity = _calculate_jaro_winkler(name1, name2)
		else:
			similarity = self._calculate_levenshtein(name1, name2)

		# 缓存结果
		self._name_similarity_cache.put(cache_key, similarity)
		return similarity

	def _calculate_levenshtein(self, name1, name2):
		"""计算Levenshtein距离

		Args:
			name1: 第一个名称
			name2: 第二个名称

		Returns:
			float: 相似度 (0-1)
		"""
		len1, len2 = len(name1), len(name2)
		if len1 == 0:
			return 0.0 if len2 > 0 else 1.0
		if len2 == 0:
			return 0.0

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
		"""清空所有缓存"""
		self._hash_cache.clear()
		self._name_similarity_cache.clear()

	def get_cache_size(self):
		"""获取总缓存大小"""
		return len(self._hash_cache) + len(self._name_similarity_cache)

	def get_hash_cache_size(self):
		"""获取哈希缓存大小"""
		return len(self._hash_cache)

	def get_name_similarity_cache_size(self):
		"""获取名称相似度缓存大小"""
		return len(self._name_similarity_cache)

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
