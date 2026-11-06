# -*- coding: utf-8 -*-
"""程序入口。双击 run.bat 或直接 python main.py 启动。"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

_lock_holder = None       # 保持锁对象存活，防止被回收


def main() -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, False)
    app = QApplication(sys.argv)
    app.setApplicationName("RecordToText")
    app.setApplicationDisplayName("录音转文本")
    app.setQuitOnLastWindowClosed(True)

    global _lock_holder
    _lock_holder = QLockFile(os.path.join(BASE, "app.lock"))
    _lock_holder.setStaleLockTime(0)
    if not _lock_holder.tryLock(200):
        QMessageBox.information(None, "已经开着", "录音转文本已经在运行了，看看任务栏。")
        return 0

    qss_path = os.path.join(BASE, "app", "ui", "theme.qss")
    if os.path.isfile(qss_path):
        try:
            with open(qss_path, encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        except OSError:
            pass

    from app.ui.main_window import MainWindow
    win = MainWindow(BASE)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
