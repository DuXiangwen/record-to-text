# -*- coding: utf-8 -*-
"""左侧新建转换表单。只管收集参数，转写逻辑交给主窗口。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..models import fmt_dur, safe_name


class FormPanel(QWidget):
    startRequested = Signal()
    fileChanged = Signal(str)
    paramsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FormPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(340)
        self._build()

    # ------------------------------------------------------------ UI
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(0)

        title = QLabel("新建转换")
        title.setObjectName("SectionTitle")
        lay.addWidget(title)
        lay.addSpacing(12)

        # 项目名
        lay.addWidget(self._label("项目名"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("给这次转换起个名字")
        self.name_edit.textChanged.connect(self._on_changed)
        lay.addWidget(self.name_edit)
        lay.addSpacing(10)

        # 录音文件
        lay.addWidget(self._label("录音文件"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("拖入文件或点右侧选择")
        self.file_edit.setReadOnly(True)
        self.file_edit.setStyleSheet("font-size:11px;color:#5F5E5A;")
        btn_browse = QPushButton("选择")
        btn_browse.setObjectName("Ghost")
        btn_browse.setFixedWidth(46)
        btn_browse.clicked.connect(self._pick_file)
        row.addWidget(self.file_edit, 1)
        row.addWidget(btn_browse)
        lay.addLayout(row)
        lay.addSpacing(10)

        # 切片长度
        lay.addWidget(self._label("切片长度"))
        seg_row = QHBoxLayout()
        seg_row.setSpacing(6)
        self.seg_spin = QSpinBox()
        self.seg_spin.setRange(5, 600)
        self.seg_spin.setValue(90)
        self.seg_spin.setSingleStep(5)
        self.seg_spin.setSuffix(" 秒")
        self.seg_spin.valueChanged.connect(self._on_changed)
        seg_row.addWidget(self.seg_spin, 1)
        lay.addLayout(seg_row)
        lay.addSpacing(10)

        # 并发 + 语言
        two = QHBoxLayout()
        two.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(self._label("并发"))
        self.conc_spin = QSpinBox()
        self.conc_spin.setRange(1, 8)
        self.conc_spin.setValue(4)
        self.conc_spin.valueChanged.connect(self._on_changed)
        left.addWidget(self.conc_spin)
        right = QVBoxLayout()
        right.setSpacing(4)
        right.addWidget(self._label("语言"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["auto", "zh", "en"])
        self.lang_combo.currentIndexChanged.connect(self._on_changed)
        right.addWidget(self.lang_combo)
        two.addLayout(left, 1)
        two.addLayout(right, 1)
        lay.addLayout(two)
        lay.addSpacing(10)

        # 切片方式
        lay.addWidget(self._label("切片方式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("静音吸附", "silence")
        self.mode_combo.addItem("定长硬切", "fixed")
        self.mode_combo.currentIndexChanged.connect(self._on_changed)
        lay.addWidget(self.mode_combo)
        lay.addSpacing(10)

        # 输出格式
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(14)
        self.chk_md = QCheckBox("Markdown")
        self.chk_md.setChecked(True)
        self.chk_txt = QCheckBox("纯文本")
        self.chk_txt.setChecked(True)
        self.chk_md.stateChanged.connect(self._on_changed)
        self.chk_txt.stateChanged.connect(self._on_changed)
        fmt_row.addWidget(self.chk_md)
        fmt_row.addWidget(self.chk_txt)
        fmt_row.addStretch(1)
        lay.addLayout(fmt_row)
        lay.addSpacing(10)

        # 输出位置预览
        self.out_label = QLabel("")
        self.out_label.setObjectName("HintLabel")
        self.out_label.setWordWrap(True)
        lay.addWidget(self.out_label)

        lay.addStretch(1)

        # 选区提示条
        self.trim_hint = QLabel("")
        self.trim_hint.setWordWrap(True)
        self.trim_hint.setVisible(False)
        self.trim_hint.setStyleSheet(
            "background:#FAEEDA;color:#854F0B;border-radius:6px;padding:6px 8px;font-size:11px;")
        lay.addWidget(self.trim_hint)
        lay.addSpacing(6)

        # 开始按钮
        self.start_btn = QPushButton("开始转换")
        self.start_btn.setObjectName("Primary")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self.startRequested.emit)
        lay.addWidget(self.start_btn)

        self.estimate = QLabel("")
        self.estimate.setObjectName("HintLabel")
        self.estimate.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(5)
        lay.addWidget(self.estimate)

    @staticmethod
    def _label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("FieldLabel")
        return lab

    # ------------------------------------------------------------ 事件
    def _on_changed(self) -> None:
        self.paramsChanged.emit()

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择录音文件", "",
            "音频文件 (*.m4a *.mp3 *.wav *.aac *.flac *.ogg *.wma *.mp4 *.amr *.opus);;所有文件 (*.*)")
        if path:
            self.set_file(path)
            self.fileChanged.emit(path)

    # ------------------------------------------------------------ 对外
    def set_file(self, path: str) -> None:
        self.file_edit.setText(path)
        self.file_edit.setToolTip(path)
        if not self.name_edit.text().strip():
            base = os.path.splitext(os.path.basename(path))[0]
            self.name_edit.setText(safe_name(base))
        self._on_changed()

    def current_file(self) -> str:
        return self.file_edit.text().strip()

    def project_name(self) -> str:
        return safe_name(self.name_edit.text().strip())

    def apply_defaults(self, d: dict) -> None:
        self.seg_spin.setValue(int(d.get("segment_seconds", 90)))
        self.mode_combo.setCurrentIndex(0 if d.get("segment_mode", "silence") == "silence" else 1)
        self.conc_spin.setValue(int(d.get("concurrency", 4)))
        lang = d.get("language", "auto")
        idx = self.lang_combo.findText(lang)
        self.lang_combo.setCurrentIndex(max(0, idx))
        formats = d.get("formats") or ["md", "txt"]
        self.chk_md.setChecked("md" in formats)
        self.chk_txt.setChecked("txt" in formats)

    def params(self) -> dict:
        formats = []
        if self.chk_md.isChecked():
            formats.append("md")
        if self.chk_txt.isChecked():
            formats.append("txt")
        return {
            "segment_seconds": float(self.seg_spin.value()),
            "segment_mode": self.mode_combo.currentData(),
            "concurrency": int(self.conc_spin.value()),
            "language": self.lang_combo.currentText(),
            "formats": formats,
        }

    def set_output_preview(self, text: str) -> None:
        self.out_label.setText(text)

    def set_trim_hint(self, sel: tuple[float, float] | None, est_segments: int = 0) -> None:
        if sel and (sel[1] - sel[0]) > 1:
            dur = fmt_dur(sel[1] - sel[0])
            extra = f" · 约 {est_segments} 段" if est_segments else ""
            self.trim_hint.setText(f"仅转写选区 {fmt_dur(sel[0])} – {fmt_dur(sel[1])} · {dur}{extra}")
            self.trim_hint.setVisible(True)
            self.start_btn.setText("开始转换（仅选区）")
        else:
            self.trim_hint.setVisible(False)
            self.start_btn.setText("开始转换")

    def set_estimate(self, text: str) -> None:
        self.estimate.setText(text)

    def set_start_enabled(self, enabled: bool, reason: str = "") -> None:
        self.start_btn.setEnabled(enabled)
        if not enabled and reason:
            self.estimate.setText(reason)
