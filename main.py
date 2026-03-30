#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主程序文件
"""

import sys
import multiprocessing
from PyQt5.QtWidgets import QApplication
from app.gui import MainWindow

if __name__ == "__main__":
	# Windows 多进程支持
	multiprocessing.freeze_support()
	
	app = QApplication(sys.argv)
	window = MainWindow()
	window.show()
	sys.exit(app.exec_())
