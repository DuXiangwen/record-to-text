# -*- coding: utf-8 -*-
"""实时预览窗口：只读，按 index 就位，滚动位置不动。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout)


class PreviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("实时预览")
        self.resize(520, 640)
        self.setModal(False)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        self.title = QLabel("实时预览")
        self.title.setObjectName("DialogTitle")
        self.progress = QLabel("")
        self.progress.setObjectName("HintLabel")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.progress)
        lay.addLayout(head)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        f = QFont("Microsoft YaHei UI", 10)
        self.text.setFont(f)
        self.text.setStyleSheet(
            "QPlainTextEdit{background:#FCFCFA;border:1px solid #ECECEA;"
            "border-radius:8px;padding:10px;font-size:12px;line-height:160%;}")
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        lay.addWidget(self.text, 1)

        foot = QHBoxLayout()
        btn_copy = QPushButton("复制已转内容")
        btn_copy.setObjectName("Ghost")
        btn_copy.clicked.connect(self._copy)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(self.close)
        foot.addWidget(btn_copy)
        foot.addStretch(1)
        foot.addWidget(btn_close)
        lay.addLayout(foot)

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_progress(self, finished: int, total: int) -> None:
        if total <= 0:
            self.progress.setText("")
            return
        pct = int(finished * 100 / total) if total else 0
        self.progress.setText(f"{finished} / {total} · {pct}%")

    def set_text(self, text: str) -> None:
        """整段替换，但保持滚动条位置，避免看着看着跳走。"""
        bar = self.text.verticalScrollBar()
        pos = bar.value()
        at_bottom = pos >= bar.maximum() - 4
        self.text.setPlainText(text)
        if at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(pos, bar.maximum()))

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.text.toPlainText())
