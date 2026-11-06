# -*- coding: utf-8 -*-
"""历史项目卡片：四行信息 + 状态徽章 + 动作按钮 + 右键菜单。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from ..models import STATUS_LABEL, Project, fmt_dur

DOT_COLOR = {
    "pending": "#B4B2A9", "queued": "#888780",
    "slicing": "#378ADD", "transcribing": "#378ADD", "merging": "#378ADD",
    "done": "#639922", "partial": "#EF9F27", "failed": "#E24B4A",
    "canceled": "#B4B2A9",
}
BADGE_STYLE = {
    "pending": ("#F1F1EC", "#6B6B66"), "queued": ("#F1F1EC", "#6B6B66"),
    "slicing": ("#E6F1FB", "#185FA5"), "transcribing": ("#E6F1FB", "#185FA5"),
    "merging": ("#E6F1FB", "#185FA5"),
    "done": ("#EAF3DE", "#3B6D11"), "partial": ("#FAEEDA", "#854F0B"),
    "failed": ("#FCEBEB", "#A32D2D"), "canceled": ("#F1F1EC", "#6B6B66"),
}
BAR_COLOR = {
    "slicing": "#378ADD", "transcribing": "#378ADD", "merging": "#378ADD",
    "done": "#639922", "partial": "#EF9F27", "failed": "#E24B4A",
}
ACTION_TEXT = {
    "pending": "开始", "queued": "取消", "slicing": "停止",
    "transcribing": "停止", "merging": "停止",
    "done": "打开", "partial": "继续", "failed": "重试", "canceled": "继续",
}


class ProjectCard(QWidget):
    openRequested = Signal(str)
    previewRequested = Signal(str)
    startRequested = Signal(str)
    stopRequested = Signal(str)
    cancelQueueRequested = Signal(str)
    continueRequested = Signal(str)
    deleteRequested = Signal(str, bool)     # (project_id, delete_files)

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setObjectName("Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._build()
        self.update_project(project)

    # ------------------------------------------------------------ UI
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(11, 9, 11, 9)
        outer.setSpacing(3)

        # 第一行
        row1 = QHBoxLayout()
        row1.setSpacing(7)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(9)
        self.name = QLabel()
        self.name.setObjectName("CardName")
        self.badge = QLabel()
        self.badge.setObjectName("Badge")
        self.action = QPushButton()
        self.action.setObjectName("LinkBtn")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.clicked.connect(self._on_action)
        row1.addWidget(self.dot)
        row1.addWidget(self.name)
        row1.addWidget(self.badge)
        row1.addStretch(1)
        row1.addWidget(self.action)
        outer.addLayout(row1)

        # 第二行
        self.meta = QLabel()
        self.meta.setObjectName("CardMeta")
        outer.addWidget(self.meta)

        # 第三行
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self.bar = QProgressBar()
        self.bar.setObjectName("CardBar")
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.num = QLabel()
        self.num.setObjectName("CardMeta")
        self.num.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row3.addWidget(self.bar, 1)
        row3.addWidget(self.num)
        outer.addLayout(row3)

        # 第四行
        self.param = QLabel()
        self.param.setObjectName("CardParam")
        outer.addWidget(self.param)

    # ------------------------------------------------------------ 数据刷新
    def update_project(self, project: Project) -> None:
        self.project = project
        p = project
        status = p.status
        label, _ = STATUS_LABEL.get(status, ("未知", "gray"))
        color = DOT_COLOR.get(status, "#B4B2A9")

        self.dot.setStyleSheet(f"color:{color};font-size:10px;")
        self.name.setText(p.name)
        self.badge.setText(label)
        bg, fg = BADGE_STYLE.get(status, ("#F1F1EC", "#6B6B66"))
        self.badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:4px;padding:1px 6px;font-size:11px;")

        dur = p.content_duration or p.duration_sec
        self.meta.setText(f"{p.source_name} · {fmt_dur(dur)}")

        total = p.progress.get("total", 0) or 0
        done = (p.progress.get("done", 0) + p.progress.get("empty", 0)
                + p.progress.get("failed", 0))
        pct = int(done * 100 / total) if total else 0
        self.bar.setValue(pct)
        bar_color = BAR_COLOR.get(status, "#C8C8C2")
        self.bar.setStyleSheet(
            f"QProgressBar#CardBar::chunk{{background:{bar_color};border-radius:2px;}}"
            f"QProgressBar#CardBar{{background:#EEEEEA;border:none;border-radius:2px;}}")

        if status in ("transcribing", "slicing", "merging"):
            self.num.setText(f"{done}/{total} · {pct}%")
        elif status == "done":
            self.num.setText(f"{total}/{total} · {p.stats.get('chars', 0):,} 字")
        elif status == "partial":
            self.num.setText(f"{done}/{total} · {p.progress.get('failed', 0)} 段失败")
        elif status == "failed":
            self.num.setText("中断")
        elif total:
            self.num.setText(f"{done}/{total}")
        else:
            self.num.setText("等待中")

        self.param.setText(self._param_text(p))
        self.action.setText(ACTION_TEXT.get(status, "打开"))
        self.action.setVisible(True)

    @staticmethod
    def _param_text(p: Project) -> str:
        parts = [f"{p.params.segment_seconds:g}s/段",
                 f"并发 {p.params.concurrency}",
                 p.params.language]
        if p.trim:
            parts.append(f"选区 {fmt_dur(p.content_duration)}")
        if p.status == "transcribing" and p.progress.get("total"):
            elapsed = p.stats.get("elapsed_sec", 0.0)
            done = (p.progress.get("done", 0) + p.progress.get("empty", 0))
            remain = p.progress["total"] - done
            if done > 0 and elapsed > 0:
                eta = elapsed / done * remain
                parts.append(f"剩 {fmt_dur(eta)}")
        elif p.stats.get("elapsed_sec"):
            parts.append(f"用时 {fmt_dur(p.stats['elapsed_sec'])}")
        if p.error and p.status == "failed":
            parts.append(p.error[:24])
        return " · ".join(parts)

    # ------------------------------------------------------------ 事件
    def _on_action(self) -> None:
        status = self.project.status
        pid = self.project.id
        if status in ("transcribing", "slicing", "merging"):
            self.stopRequested.emit(pid)
        elif status == "queued":
            self.cancelQueueRequested.emit(pid)
        elif status == "pending":
            self.startRequested.emit(pid)
        elif status in ("partial", "failed", "canceled"):
            self.continueRequested.emit(pid)
        else:
            self.openRequested.emit(pid)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.project.status in ("transcribing", "slicing", "merging"):
            self.previewRequested.emit(self.project.id)
        else:
            self.openRequested.emit(self.project.id)

    def _show_menu(self, pos) -> None:
        p = self.project
        menu = QMenu(self)
        act_open = QAction("打开输出文件夹", menu)
        act_open.triggered.connect(lambda: self.openRequested.emit(p.id))
        menu.addAction(act_open)

        if p.status in ("transcribing", "slicing", "merging"):
            act_prev = QAction("实时预览", menu)
        else:
            act_prev = QAction("查看分段文本", menu)
        act_prev.triggered.connect(lambda: self.previewRequested.emit(p.id))
        menu.addAction(act_prev)

        if p.status in ("partial", "failed", "canceled"):
            act_cont = QAction("继续（跳过已完成）", menu)
            act_cont.triggered.connect(lambda: self.continueRequested.emit(p.id))
            menu.addAction(act_cont)

        if p.status == "queued":
            act_cancel = QAction("取消排队", menu)
            act_cancel.triggered.connect(lambda: self.cancelQueueRequested.emit(p.id))
            menu.addAction(act_cancel)

        act_copy = QAction("复制全文", menu)
        act_copy.triggered.connect(lambda: self._copy_text())
        menu.addAction(act_copy)

        if os.path.isfile(os.path.join(p.output_dir, "run.log")):
            act_log = QAction("查看日志", menu)
            act_log.triggered.connect(lambda: self._open_log())
            menu.addAction(act_log)

        menu.addSeparator()
        act_rm = QAction("移除卡片（保留文件）", menu)
        act_rm.triggered.connect(lambda: self.deleteRequested.emit(p.id, False))
        menu.addAction(act_rm)
        act_del = QAction("连文件夹一起删除", menu)
        act_del.triggered.connect(lambda: self.deleteRequested.emit(p.id, True))
        menu.addAction(act_del)

        menu.exec(self.mapToGlobal(pos))

    def _copy_text(self) -> None:
        from PySide6.QtWidgets import QApplication
        folder = self.project.output_dir
        if not os.path.isdir(folder):
            return
        for name in os.listdir(folder):
            if name.endswith(("_全文.txt", "_全文.md")):
                try:
                    with open(os.path.join(folder, name), encoding="utf-8") as f:
                        QApplication.clipboard().setText(f.read())
                except OSError:
                    pass
                return

    def _open_log(self) -> None:
        path = os.path.join(self.project.output_dir, "run.log")
        try:
            os.startfile(path)  # noqa: S606
        except OSError:
            pass
