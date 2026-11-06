# -*- coding: utf-8 -*-
"""底部日志面板：横跨全宽，默认 5 行高，可折叠。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

COLLAPSED_H = 26
EXPANDED_H = 26 + 88          # 标题 + 5 行正文
MAX_LINES = 600


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LogPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._expanded = True
        self._build()
        self.setFixedHeight(EXPANDED_H)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QWidget()
        head.setFixedHeight(COLLAPSED_H)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 0, 12, 0)
        hl.setSpacing(8)

        title = QLabel("日志")
        title.setObjectName("LogTitle")
        self.count = QLabel("")
        self.count.setObjectName("LogTitle")
        self.toggle_btn = QPushButton("收起")
        self.toggle_btn.setObjectName("LinkBtn")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggle)

        hl.addWidget(title)
        hl.addWidget(self.count)
        hl.addStretch(1)
        hl.addWidget(self.toggle_btn)
        lay.addWidget(head)

        self.view = QPlainTextEdit()
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        f = QFont("Consolas", 9)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.view.setFont(f)
        lay.addWidget(self.view, 1)

    # ------------------------------------------------------------ 操作
    def append(self, text: str, timestamp: bool = True) -> None:
        line = text.rstrip("\n")
        if timestamp and not line.startswith("["):
            line = f"{datetime.now():%H:%M:%S}  {line}"
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4

        self.view.appendPlainText(line)
        if at_bottom:
            self.view.moveCursor(QTextCursor.MoveOperation.End)
            bar.setValue(bar.maximum())

        total = self.view.blockCount()
        self.count.setText(f"{total} 行" if total > 1 else "")

    def clear(self) -> None:
        self.view.clear()
        self.count.setText("")

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self.view.setVisible(self._expanded)
        self.toggle_btn.setText("收起" if self._expanded else "展开")
        self.setFixedHeight(EXPANDED_H if self._expanded else COLLAPSED_H)

    def is_expanded(self) -> bool:
        return self._expanded
