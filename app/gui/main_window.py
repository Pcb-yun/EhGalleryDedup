#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主窗口模块
"""

import os
import json
import shutil
from datetime import datetime

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QLabel, QPushButton, QFileDialog,
    QMessageBox, QSplitter, QScrollArea, QGridLayout, QProgressBar,
    QMenuBar, QMenu, QAction, QActionGroup, QSizePolicy, QLineEdit
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QFontMetrics

from app.scanner import FolderScanner
from app.processor import FolderProcessor
from app.gui.widgets import ClickableImageLabel
from app.gui.dialogs import ImageViewerDialog
from app.gui.threads import (
    ScanThread, ContentCompareThread, FolderSizeThread, ImageLoadThread
)


class MainWindow(QMainWindow):
    """主窗口类"""

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("Eh画廊查重")
        self.setGeometry(100, 100, 900, 650)

        # 先加载配置
        self.config_file = os.path.join(os.path.dirname(__file__), '..', 'config.json')
        self._load_config()

        # 创建菜单栏
        self._create_menu_bar()

        # 主布局
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # 顶部工具栏
        self._create_toolbar(main_layout)

        # 进度条和状态
        self._create_progress_section(main_layout)

        # 中央分割器
        self._create_main_content(main_layout)

        # 设置中央部件
        self.setCentralWidget(main_widget)

        # 初始化变量
        self._init_variables()

        # 连接信号
        self._connect_signals()

    def _create_menu_bar(self):
        """创建菜单栏"""
        menubar = self.menuBar()

        # 打开文件夹
        open_action = QAction("打开文件夹", self)
        open_action.triggered.connect(self.select_folder)
        menubar.addAction(open_action)

        # 相似度阈值菜单
        threshold_menu = menubar.addMenu("相似度阈值")
        threshold_group = QActionGroup(self)
        self.threshold_actions = {}
        for threshold in [50, 60, 70, 80, 90]:
            action = QAction(f"{threshold}%", self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, t=threshold: self._on_threshold_menu(t))
            threshold_group.addAction(action)
            threshold_menu.addAction(action)
            self.threshold_actions[threshold] = action

        # 缩略图大小菜单
        thumbnail_menu = menubar.addMenu("缩略图大小")
        thumbnail_group = QActionGroup(self)
        self.thumbnail_actions = {}
        size_map = {"特大": 300, "大": 200, "中": 150, "小": 100}
        for name, size in size_map.items():
            action = QAction(name, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, s=size: self._on_thumbnail_menu(s))
            thumbnail_group.addAction(action)
            thumbnail_menu.addAction(action)
            self.thumbnail_actions[size] = action

        # 分类方式菜单
        view_mode_menu = menubar.addMenu("分类方式")
        view_mode_group = QActionGroup(self)
        self.view_mode_actions = {}
        for mode, label in [('name', '按名称'), ('tag', '按标签')]:
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, m=mode: self._on_view_mode_menu(m))
            view_mode_group.addAction(action)
            view_mode_menu.addAction(action)
            self.view_mode_actions[mode] = action

        # 自动扫描菜单
        auto_scan_menu = menubar.addMenu("自动扫描")
        auto_scan_group = QActionGroup(self)
        self.auto_scan_actions = {}
        for value, label in [(True, '开启'), (False, '关闭')]:
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, v=value: self._on_auto_scan_menu(v))
            auto_scan_group.addAction(action)
            auto_scan_menu.addAction(action)
            self.auto_scan_actions[value] = action

        # 排序方式菜单
        sort_menu = menubar.addMenu("排序方式")
        sort_group = QActionGroup(self)
        self.sort_actions = {}
        for value, label in [('asc', '时间升序'), ('desc', '时间降序')]:
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, v=value: self._on_sort_menu(v))
            sort_group.addAction(action)
            sort_menu.addAction(action)
            self.sort_actions[value] = action

        # 搜索框
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索文件夹...")
        self.search_input.setMaximumWidth(200)
        self.search_input.textChanged.connect(self._on_search_changed)
        menubar.setCornerWidget(self.search_input, Qt.TopRightCorner)

        self._update_menu_selections()

    def _create_toolbar(self, main_layout):
        """创建工具栏"""
        top_layout = QHBoxLayout()

        # 左侧按钮
        left_layout = QHBoxLayout()
        self.scan_btn = QPushButton("开始扫描")
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setEnabled(False)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)

        left_layout.addWidget(self.scan_btn)
        left_layout.addWidget(self.pause_btn)
        left_layout.addWidget(self.stop_btn)

        # 右侧按钮
        right_layout = QHBoxLayout()
        self.hide_preview_btn = QPushButton("隐藏")
        self.compare_content_btn = QPushButton("内容比对")
        self.delete_folder_btn = QPushButton("删除文件夹")

        right_layout.addWidget(self.hide_preview_btn)
        right_layout.addWidget(self.compare_content_btn)
        right_layout.addWidget(self.delete_folder_btn)

        top_layout.addLayout(left_layout)
        top_layout.addStretch()
        top_layout.addLayout(right_layout)
        main_layout.addLayout(top_layout)

    def _create_progress_section(self, main_layout):
        """创建进度条和状态区域"""
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximumHeight(20)
        main_layout.addWidget(self.progress_bar)

        status_layout = QHBoxLayout()
        self.progress_label = QLabel("就绪")
        self.progress_label.setMinimumWidth(200)
        self.folder_count_label = QLabel("文件夹: 0")
        self.folder_count_label.setMinimumWidth(150)

        status_layout.addWidget(self.progress_label)
        status_layout.addStretch()
        status_layout.addWidget(self.folder_count_label)
        main_layout.addLayout(status_layout)

    def _create_main_content(self, main_layout):
        """创建主内容区域"""
        splitter = QSplitter(Qt.Horizontal)

        # 左侧树形列表
        self.group_tree = QTreeWidget()
        self.group_tree.setHeaderLabel("文件夹组")
        splitter.addWidget(self.group_tree)

        # 右侧信息和预览
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(5)

        # 文件夹名称
        self.folder_name_label = QLineEdit("未选择文件夹")
        self.folder_name_label.setReadOnly(True)
        self.folder_name_label.setAlignment(Qt.AlignLeft)
        self.folder_name_label.setFont(QFont("Arial", 12, QFont.Bold))
        self.folder_name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.folder_name_label.setStyleSheet("border: none; background: transparent;")
        right_layout.addWidget(self.folder_name_label)

        # 详细信息
        detail_layout = QHBoxLayout()
        self.folder_info_label = QLabel("图像: 0 | 大小: - | 修改: -")
        self.name_similarity_label = QLabel("名称: -")
        self.content_similarity_label = QLabel("内容: -")

        detail_layout.addWidget(self.folder_info_label)
        detail_layout.addStretch()
        detail_layout.addWidget(self.name_similarity_label)
        detail_layout.addWidget(self.content_similarity_label)
        right_layout.addLayout(detail_layout)

        # 预览区域
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        preview_widget = QWidget()
        self.preview_grid_layout = QGridLayout(preview_widget)
        self.preview_grid_layout.setSpacing(5)
        self.preview_scroll.setWidget(preview_widget)

        # 遮罩
        self.preview_mask = QLabel("预览已隐藏")
        self.preview_mask.setAlignment(Qt.AlignCenter)
        self.preview_mask.setStyleSheet("background-color: #808080; color: #333; font-size: 16px;")
        self.preview_mask.setVisible(False)
        self.preview_mask.setParent(self.preview_scroll.viewport())

        right_layout.addWidget(self.preview_scroll)
        splitter.addWidget(right_widget)

        splitter.setSizes([210, 720])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter, 1)
        self.splitter = splitter

    def _init_variables(self):
        """初始化变量"""
        self.selected_folder = None
        self.similar_groups = []
        self.tag_groups = {}
        self.current_group = None
        self.current_folder = None
        self.current_display_count = 0
        self.image_labels = {}
        self.image_containers = {}
        self.scan_thread = None
        self.content_compare_thread = None
        self.image_load_thread = None
        self.size_thread = None

        # 定时器
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._on_refresh_timer)

        self.scroll_timer = QTimer()
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.timeout.connect(self._on_scroll_timer_timeout)

        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_resize_timeout)

    def _connect_signals(self):
        """连接信号"""
        self.scan_btn.clicked.connect(self.start_scan)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn.clicked.connect(self.stop_scan)
        self.hide_preview_btn.clicked.connect(self.toggle_preview)
        self.compare_content_btn.clicked.connect(self.start_content_compare)
        self.delete_folder_btn.clicked.connect(self.delete_selected_folder)
        self.group_tree.itemClicked.connect(self.on_tree_item_clicked)
        self.preview_scroll.verticalScrollBar().valueChanged.connect(self.on_preview_scroll)
        self.splitter.splitterMoved.connect(self.on_splitter_moved)

    def _load_config(self):
        """加载配置文件"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.threshold = config.get('threshold', 70)
                    self.thumbnail_size = config.get('thumbnail_size', 150)
                    self.current_view_mode = config.get('view_mode', 'name')
                    self.auto_scan = config.get('auto_scan', False)
                    self.sort_order = config.get('sort_order', 'asc')
                    return
        except Exception as e:
            print(f"加载配置文件失败: {e}")

        # 默认值
        self.threshold = 70
        self.thumbnail_size = 150
        self.current_view_mode = 'name'
        self.auto_scan = False
        self.sort_order = 'asc'

    def _save_config(self):
        """保存配置文件"""
        try:
            config = {
                'threshold': self.threshold,
                'view_mode': self.current_view_mode,
                'thumbnail_size': self.thumbnail_size,
                'auto_scan': self.auto_scan,
                'sort_order': self.sort_order
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置文件失败: {e}")

    def _update_menu_selections(self):
        """更新菜单选中状态"""
        if hasattr(self, 'threshold_actions') and self.threshold in self.threshold_actions:
            self.threshold_actions[self.threshold].setChecked(True)
        if hasattr(self, 'thumbnail_actions') and self.thumbnail_size in self.thumbnail_actions:
            self.thumbnail_actions[self.thumbnail_size].setChecked(True)
        if hasattr(self, 'view_mode_actions') and self.current_view_mode in self.view_mode_actions:
            self.view_mode_actions[self.current_view_mode].setChecked(True)
        if hasattr(self, 'auto_scan_actions') and self.auto_scan in self.auto_scan_actions:
            self.auto_scan_actions[self.auto_scan].setChecked(True)
        if hasattr(self, 'sort_actions') and self.sort_order in self.sort_actions:
            self.sort_actions[self.sort_order].setChecked(True)

    def _on_threshold_menu(self, threshold):
        """阈值菜单选择"""
        self.threshold = threshold
        self._save_config()

    def _on_thumbnail_menu(self, size):
        """缩略图大小菜单选择"""
        self.thumbnail_size = size
        self._save_config()
        if self.current_folder:
            self.show_folder_preview(self.current_folder)

    def _on_view_mode_menu(self, mode):
        """分类方式菜单选择"""
        self.current_view_mode = mode
        self.refresh_tree_view()

    def _on_auto_scan_menu(self, value):
        """自动扫描菜单选择"""
        self.auto_scan = value
        self._save_config()

    def _on_sort_menu(self, value):
        """排序方式菜单选择"""
        self.sort_order = value
        self._save_config()
        self.refresh_tree_view()

    def select_folder(self):
        """选择文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择扫描目录")
        if folder:
            self.selected_folder = folder
            self.setWindowTitle(f"Eh画廊查重 - {folder}")
            if self.auto_scan:
                self.start_scan()

    def start_scan(self):
        """开始扫描"""
        if not hasattr(self, 'selected_folder') or not self.selected_folder:
            QMessageBox.warning(self, "警告", "请先选择扫描目录")
            return

        threshold = self.threshold / 100.0

        self.scan_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("扫描中...")
        self.group_tree.clear()
        self.similar_groups = []
        self.tag_groups = {}

        self.scan_thread = ScanThread(self.selected_folder, threshold)
        self.scan_thread.progress_updated.connect(self.update_progress)
        self.scan_thread.scan_completed.connect(self.on_scan_completed)
        self.scan_thread.start()

        self.refresh_timer.start(500)

    def toggle_pause(self):
        """切换暂停/继续"""
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
        if self.scan_thread:
            self.scan_thread.stop()
            self.progress_label.setText("停止中...")
        if self.content_compare_thread and self.content_compare_thread.isRunning():
            self.content_compare_thread.stop()

    def update_progress(self, current, total, message):
        """更新进度"""
        if total > 0:
            self.progress_bar.setValue(int(current / total * 100))
        self.progress_label.setText(message)

    def _on_refresh_timer(self):
        """定时刷新UI"""
        if self.scan_thread and hasattr(self.scan_thread, 'groups') and self.scan_thread.groups:
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
        """完整刷新树形列表"""
        expanded_groups = set()
        selected_folder_path = None

        if self.current_folder:
            selected_folder_path = self.current_folder['path']

        # 保存展开状态：使用组中第一个文件夹的路径作为标识
        for i in range(self.group_tree.topLevelItemCount()):
            top_item = self.group_tree.topLevelItem(i)
            if top_item.isExpanded():
                expanded_groups.add(top_item.text(0))
            for j in range(top_item.childCount()):
                child = top_item.child(j)
                if child.isExpanded():
                    data = child.data(0, Qt.UserRole)
                    if data and data[0] == 'group' and data[1]['folders']:
                        # 使用组中第一个文件夹的路径作为标识
                        group_id = data[1]['folders'][0]['path']
                        expanded_groups.add(group_id)
                    else:
                        expanded_groups.add(child.text(0))

        self.group_tree.clear()

        if self.current_view_mode == 'name':
            self._refresh_tree_view_by_name(expanded_groups, selected_folder_path)
        else:
            self._refresh_tree_view_by_tag(expanded_groups, selected_folder_path)

    def _get_sorted_folders(self, folders):
        """根据排序设置对文件夹列表进行排序"""
        reverse = self.sort_order == 'desc'
        return sorted(folders, key=lambda f: f.get('mtime', ''), reverse=reverse)

    def _filter_folders(self, folders):
        """根据搜索关键词过滤文件夹"""
        keyword = self.search_input.text().strip().lower()
        if not keyword:
            return folders

        return [f for f in folders if keyword in f['name'].lower()]

    def _filter_groups(self, groups):
        """根据搜索关键词过滤文件夹组"""
        keyword = self.search_input.text().strip().lower()
        if not keyword:
            return groups

        filtered_groups = []
        for group in groups:
            matched_folders = [f for f in group['folders'] if keyword in f['name'].lower()]
            if matched_folders:
                new_group = group.copy()
                new_group['folders'] = matched_folders
                filtered_groups.append(new_group)

        return filtered_groups

    def _on_search_changed(self):
        """搜索关键词改变时刷新列表"""
        self.refresh_tree_view()

    def _refresh_tree_view_by_name(self, expanded_groups=None, selected_folder_path=None):
        """按名称刷新树形列表"""
        if expanded_groups is None:
            expanded_groups = set()

        filtered_groups = self._filter_groups(self.similar_groups)

        similar_count = sum(1 for g in filtered_groups if len(g['folders']) > 1)
        single_count = sum(1 for g in filtered_groups if len(g['folders']) == 1)
        total_folders = sum(len(g['folders']) for g in filtered_groups)

        self.folder_count_label.setText(f"文件夹: {total_folders} (相似:{similar_count} 单独:{single_count})")

        if similar_count > 0:
            similar_root = QTreeWidgetItem(self.group_tree)
            similar_root.setText(0, f"相似文件夹组 ({similar_count}组)")
            similar_root.setExpanded(f"相似文件夹组 ({similar_count}组)" in expanded_groups or similar_count <= 5)

            for i, group in enumerate(filtered_groups):
                if len(group['folders']) > 1:
                    group_item = QTreeWidgetItem(similar_root)
                    group_text = f"组 {i+1} (相似度: {group['similarity']:.2f}"
                    if group.get('content_similarity') is not None:
                        group_text += f", 内容: {group['content_similarity']:.2f}"
                    group_text += ")"
                    group_item.setText(0, group_text)
                    group_item.setData(0, Qt.UserRole, ('group', group))
                    # 使用组中第一个文件夹的路径作为标识来判断是否展开
                    group_id = group['folders'][0]['path']
                    group_item.setExpanded(group_id in expanded_groups or group_text in expanded_groups)

                    for folder in self._get_sorted_folders(group['folders']):
                        folder_item = QTreeWidgetItem(group_item)
                        display_name = folder['name'][:40] + '...' if len(folder['name']) > 40 else folder['name']
                        folder_item.setText(0, display_name)
                        folder_item.setToolTip(0, folder['name'])
                        folder_item.setData(0, Qt.UserRole, ('folder', folder, group))
                        if selected_folder_path and folder['path'] == selected_folder_path:
                            self.group_tree.setCurrentItem(folder_item)

        if single_count > 0:
            single_root = QTreeWidgetItem(self.group_tree)
            single_root.setText(0, f"单独文件夹 ({single_count}个)")
            single_root.setExpanded(f"单独文件夹 ({single_count}个)" in expanded_groups)

            single_folders = [(g['folders'][0], g) for g in filtered_groups if len(g['folders']) == 1]
            reverse = self.sort_order == 'desc'
            for folder, group in sorted(single_folders, key=lambda x: x[0].get('mtime', ''), reverse=reverse):
                folder_item = QTreeWidgetItem(single_root)
                display_name = folder['name'][:40] + '...' if len(folder['name']) > 40 else folder['name']
                folder_item.setText(0, display_name)
                folder_item.setToolTip(0, folder['name'])
                folder_item.setData(0, Qt.UserRole, ('folder', folder, group))
                if selected_folder_path and folder['path'] == selected_folder_path:
                    self.group_tree.setCurrentItem(folder_item)

    def _refresh_tree_view_by_tag(self, expanded_groups=None, selected_folder_path=None):
        """按标签刷新树形列表"""
        if expanded_groups is None:
            expanded_groups = set()

        keyword = self.search_input.text().strip().lower()

        total_count = 0
        tag_count = 0

        for tag, folders in sorted(self.tag_groups.items()):
            if keyword:
                filtered = [f for f in folders if keyword in f['name'].lower()]
            else:
                filtered = folders

            if not filtered:
                continue

            tag_count += 1
            total_count += len(filtered)

            tag_root = QTreeWidgetItem(self.group_tree)
            tag_root.setText(0, f"[{tag}] ({len(filtered)}个)")
            tag_root.setExpanded(f"[{tag}] ({len(filtered)}个)" in expanded_groups or len(filtered) <= 5)

            for folder in self._get_sorted_folders(filtered):
                folder_item = QTreeWidgetItem(tag_root)
                display_name = folder['name'][:40] + '...' if len(folder['name']) > 40 else folder['name']
                folder_item.setText(0, display_name)
                folder_item.setToolTip(0, folder['name'])
                folder_item.setData(0, Qt.UserRole, ('folder', folder, None))
                if selected_folder_path and folder['path'] == selected_folder_path:
                    self.group_tree.setCurrentItem(folder_item)

        self.folder_count_label.setText(f"文件夹: {total_count} (标签:{tag_count})")

    def on_scan_completed(self, result):
        """扫描完成"""
        self.refresh_timer.stop()

        self.similar_groups = result['groups']
        self.tag_groups = result.get('tag_groups', {})
        total_scanned = result['total_scanned']
        skipped = result['skipped']

        self.scan_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setText("暂停")
        self.progress_bar.setValue(100)

        self.refresh_tree_view()
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
            self.folder_name_label.setText(self._truncate_text(self.current_folder['name']))
            info_text = f"图像: {len(self.current_folder['images']) if 'images' in self.current_folder else 0}"
            if 'size_formatted' in self.current_folder and self.current_folder['size_formatted'] != '-':
                info_text += f" | 大小: {self.current_folder['size_formatted']}"
            else:
                info_text += " | 大小: 计算中..."
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
                self.name_similarity_label.setText("名称: -")
                self.content_similarity_label.setText("内容: -")

            self.show_folder_preview(self.current_folder)

    def on_folder_size_calculated(self, folder_info, size, size_formatted):
        """文件夹大小计算完成"""
        folder_info['size'] = size
        folder_info['size_formatted'] = size_formatted
        if self.current_folder == folder_info:
            info_text = f"图像: {len(self.current_folder['images']) if 'images' in self.current_folder else 0}"
            info_text += f" | 大小: {size_formatted}"
            if 'mtime' in self.current_folder:
                info_text += f" | 修改: {self.current_folder['mtime']}"
            self.folder_info_label.setText(info_text)

    def clear_preview(self):
        """清空预览"""
        if hasattr(self, 'image_load_thread') and self.image_load_thread:
            self.image_load_thread.stop()
            self.image_load_thread.wait(1000)
            self.image_load_thread = None

        self.image_labels = {}
        self.image_containers = {}

        while self.preview_grid_layout.count():
            item = self.preview_grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def on_preview_scroll(self, value):
        """滚动时延迟加载可见区域"""
        self.scroll_timer.start(50)

    def _on_scroll_timer_timeout(self):
        """滚动定时器超时"""
        self._load_visible_area()

    def _load_visible_area(self):
        """加载可见区域"""
        if not hasattr(self, 'image_load_thread') or not self.image_load_thread:
            return
        if not hasattr(self, 'image_containers') or not self.image_containers:
            return

        scrollbar = self.preview_scroll.verticalScrollBar()
        scroll_value = scrollbar.value()
        viewport_height = self.preview_scroll.viewport().height()

        visible_top = scroll_value
        visible_bottom = scroll_value + viewport_height

        visible_indices = []
        for idx, container in self.image_containers.items():
            if container and not container.isHidden():
                rect = container.geometry()
                container_top = rect.top()
                container_bottom = rect.bottom()

                if container_bottom >= visible_top and container_top <= visible_bottom:
                    visible_indices.append(idx)

        if visible_indices:
            self.image_load_thread.load_indices(visible_indices)

    def show_folder_preview(self, folder):
        """显示文件夹预览"""
        self.clear_preview()

        if not folder.get('images'):
            self.folder_info_label.setText("图像: 加载中... | 大小: - | 修改: -")
            # 后台加载图像列表
            from PyQt5.QtCore import QThread, pyqtSignal

            class ImageLoadTask(QThread):
                images_loaded = pyqtSignal(list)

                def __init__(self, folder_path):
                    super().__init__()
                    self.folder_path = folder_path
                    self._stop_flag = False
                    self._scanner = None

                def stop(self):
                    self._stop_flag = True
                    if self._scanner:
                        self._scanner.stop()

                def run(self):
                    self._scanner = FolderScanner()
                    images = self._scanner.get_folder_images(self.folder_path)
                    if not self._stop_flag:
                        self.images_loaded.emit(images)

            if hasattr(self, 'image_list_thread') and self.image_list_thread:
                self.image_list_thread.stop()
                self.image_list_thread.wait(500)

            load_task = ImageLoadTask(folder['path'])
            load_task.images_loaded.connect(lambda images: self._on_images_loaded(folder, images))
            load_task.start()
            self.image_list_thread = load_task
            return
        else:
            info_text = f"图像: {len(folder['images'])}"
            if 'size_formatted' in folder:
                info_text += f" | 大小: {folder['size_formatted']}"
            if 'mtime' in folder:
                info_text += f" | 修改: {folder['mtime']}"
            self.folder_info_label.setText(info_text)

        thumb_size = self.thumbnail_size
        scroll_width = self.preview_scroll.width() - 30
        cols = max(1, scroll_width // (thumb_size + 10))

        display_images = folder['images'][:1000]

        self.current_thumb_size = thumb_size
        self.current_cols = cols
        self.current_preview_folder = folder
        self.current_display_count = len(display_images)
        self.image_labels = {}
        self.image_containers = {}

        for idx in range(len(display_images)):
            image_path = display_images[idx]
            self._create_image_placeholder(idx, image_path, thumb_size, cols)

        self.preview_scroll.verticalScrollBar().setValue(0)

        self.image_load_thread = ImageLoadThread(display_images, thumb_size)
        self.image_load_thread.image_loaded.connect(self.on_image_loaded)
        self.image_load_thread.start()

        QTimer.singleShot(50, self._load_visible_area)

    def _on_images_loaded(self, folder, images):
        """图像列表加载完成"""
        folder['images'] = images
        info_text = f"图像: {len(images)}"
        if 'size_formatted' in folder:
            info_text += f" | 大小: {folder['size_formatted']}"
        if 'mtime' in folder:
            info_text += f" | 修改: {folder['mtime']}"
        self.folder_info_label.setText(info_text)
        if self.current_folder == folder:
            self.show_folder_preview(folder)

    def _create_image_placeholder(self, idx, image_path, thumb_size, cols):
        """创建图片占位框"""
        from PyQt5.QtWidgets import QWidget, QVBoxLayout

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(1, 0, 1, 0)
        container_layout.setSpacing(0)

        img_label = ClickableImageLabel(image_path)
        img_label.setFixedSize(thumb_size, thumb_size)
        img_label.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ddd;")
        img_label.double_clicked.connect(self._on_image_double_clicked)
        container_layout.addWidget(img_label)

        index_label = QLabel(str(idx + 1))
        index_label.setAlignment(Qt.AlignCenter)
        index_label.setStyleSheet("color: #666; font-size: 9px; padding: 0px; margin: 0px;")
        index_label.setFixedHeight(12)
        container_layout.addWidget(index_label)

        row = idx // cols
        col = idx % cols
        self.preview_grid_layout.addWidget(container, row, col)

        self.image_labels[idx] = img_label
        self.image_containers[idx] = container

        cache_key = f"{image_path}_{thumb_size}"
        with ImageLoadThread._cache_lock:
            if cache_key in ImageLoadThread._pixmap_cache:
                from PyQt5.QtGui import QPixmap
                pixmap = ImageLoadThread._pixmap_cache[cache_key]
                img_label.setPixmap(pixmap)
                img_label.setStyleSheet("")
                img_label.setToolTip(image_path)

    def _on_image_double_clicked(self, image_path):
        """双击图片"""
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

    def start_content_compare(self):
        """开始内容比对"""
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
        self.compare_content_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.progress_label.setText("比对完成")

        if group.get('content_similarity') is not None:
            self.content_similarity_label.setText(f"内容: {group['content_similarity']:.2f}")

        self.refresh_tree_view()

    def delete_selected_folder(self):
        """删除选中的文件夹"""
        if not self.current_folder:
            QMessageBox.warning(self, "警告", "请先选择要删除的文件夹")
            return

        try:
            import send2trash
            has_send2trash = True
        except ImportError:
            has_send2trash = False

        if has_send2trash:
            message = f"确定要将以下文件夹放入回收站吗？\n{self.current_folder['name']}"
            title = "确认放入回收站"
        else:
            message = f"确定要删除以下文件夹吗？\n{self.current_folder['name']}"
            title = "确认删除"

        reply = QMessageBox.question(
            self, title, message,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                folder_path = self.current_folder['path']
                # 统一路径分隔符为Windows格式
                folder_path = folder_path.replace('/', '\\')
                # 移除UNC扩展前缀
                if folder_path.startswith('\\\\?\\'):
                    folder_path = folder_path[4:]
                elif folder_path.startswith('\\\\?\\UNC\\'):
                    folder_path = '\\\\' + folder_path[8:]

                if not os.path.exists(folder_path):
                    QMessageBox.warning(self, "错误", "文件夹不存在")
                    return
                if not os.path.isdir(folder_path):
                    QMessageBox.warning(self, "错误", "指定路径不是文件夹")
                    return

                if has_send2trash:
                    send2trash.send2trash(folder_path)
                else:
                    shutil.rmtree(folder_path)

                # 从数据结构中移除文件夹
                deleted_folder = self.current_folder

                # 从相似组中移除
                if self.current_group and deleted_folder in self.current_group['folders']:
                    self.current_group['folders'].remove(deleted_folder)

                # 从标签组中移除
                for tag in list(self.tag_groups.keys()):
                    if deleted_folder in self.tag_groups[tag]:
                        self.tag_groups[tag].remove(deleted_folder)
                        if not self.tag_groups[tag]:
                            del self.tag_groups[tag]

                # 移除空的相似组
                self.similar_groups = [g for g in self.similar_groups if len(g['folders']) > 0]

                self.current_folder = None
                self.current_group = None
                self.folder_name_label.setText("未选择文件夹")
                self.folder_info_label.setText("图像: 0 | 大小: - | 修改: -")
                self.clear_preview()
                self.refresh_tree_view()

            except PermissionError:
                QMessageBox.warning(self, "错误", "权限不足，无法删除文件夹")
            except FileNotFoundError:
                QMessageBox.warning(self, "错误", "文件夹不存在")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"删除文件夹失败: {str(e)}")

    def toggle_preview(self):
        """切换预览显示/隐藏"""
        if self.preview_mask.isVisible():
            self.preview_mask.setVisible(False)
            self.hide_preview_btn.setText("隐藏")
        else:
            self.preview_mask.resize(self.preview_scroll.viewport().size())
            self.preview_mask.move(0, 0)
            self.preview_mask.setVisible(True)
            self.hide_preview_btn.setText("显示")

    def _truncate_text(self, text):
        """根据标签宽度动态截断文本（从尾部开始）"""
        if not hasattr(self, 'folder_name_label'):
            return text

        label_width = self.folder_name_label.width()
        if label_width <= 0:
            return text

        font = self.folder_name_label.font()
        fm = QFontMetrics(font)
        full_text_width = fm.width(text)

        if full_text_width > label_width:
            available_width = label_width - fm.width("...")
            low, high = 0, len(text)
            best_length = 0
            while low <= high:
                mid = (low + high) // 2
                if fm.width("..." + text[-mid:]) <= label_width:
                    best_length = mid
                    low = mid + 1
                else:
                    high = mid - 1
            if best_length > 0:
                return "..." + text[-best_length:]
        return text

    def resizeEvent(self, event):
        """窗口大小改变事件"""
        if hasattr(self, 'preview_mask') and self.preview_mask.isVisible():
            self.preview_mask.resize(self.preview_scroll.viewport().size())
        self._resize_timer.start(200)
        if self.current_folder:
            self.folder_name_label.setText(self._truncate_text(self.current_folder['name']))
        super().resizeEvent(event)

    def _on_resize_timeout(self):
        """窗口大小改变超时"""
        if hasattr(self, 'preview_mask') and self.preview_mask.isVisible():
            self.preview_mask.resize(self.preview_scroll.viewport().size())
        if self.current_folder:
            self.show_folder_preview(self.current_folder)
        if self.current_folder:
            self.folder_name_label.setText(self._truncate_text(self.current_folder['name']))

    def on_splitter_moved(self, pos, index):
        """分割器位置改变事件"""
        if hasattr(self, 'preview_mask') and self.preview_mask.isVisible():
            self.preview_mask.resize(self.preview_scroll.viewport().size())
        if hasattr(self, 'folder_name_label'):
            self.folder_name_label.adjustSize()
            self.folder_name_label.parent().layout().update()
        if self.current_folder:
            self.folder_name_label.setText(self._truncate_text(self.current_folder['name']))
        self._resize_timer.start(200)

    def closeEvent(self, event):
        """窗口关闭时清理资源"""
        threads_to_stop = []

        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.stop()
            threads_to_stop.append(('scan', self.scan_thread))

        if self.content_compare_thread and self.content_compare_thread.isRunning():
            self.content_compare_thread.stop()
            threads_to_stop.append(('compare', self.content_compare_thread))

        if self.image_load_thread and self.image_load_thread.isRunning():
            self.image_load_thread.stop()
            threads_to_stop.append(('image', self.image_load_thread))

        if hasattr(self, 'image_list_thread') and self.image_list_thread and self.image_list_thread.isRunning():
            self.image_list_thread.stop()
            threads_to_stop.append(('image_list', self.image_list_thread))

        if hasattr(self, 'size_thread') and self.size_thread and self.size_thread.isRunning():
            self.size_thread.stop()
            threads_to_stop.append(('size', self.size_thread))

        for name, thread in threads_to_stop:
            if not thread.wait(500):
                print(f"警告: {name}线程未能正常停止")
                thread.terminate()
                thread.wait(100)

        ImageLoadThread.clear_cache()
        event.accept()
