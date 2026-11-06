# -*- coding: utf-8 -*-
"""设置弹窗：API / 默认参数 / 高级。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import waveform
from ..asr import AsrClient


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("设置")
        self.setMinimumSize(560, 520)
        self._build()
        self._load()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        title = QLabel("设置")
        title.setObjectName("DialogTitle")
        lay.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._tab_api(), "API")
        tabs.addTab(self._tab_defaults(), "默认参数")
        tabs.addTab(self._tab_advanced(), "高级")
        lay.addWidget(tabs, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("Ghost")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("保存")
        btn_ok.setObjectName("Primary")
        btn_ok.setFixedWidth(90)
        btn_ok.clicked.connect(self._save)
        foot.addWidget(btn_cancel)
        foot.addWidget(btn_ok)
        lay.addLayout(foot)

    # ------------------------------------------------------------ API
    def _tab_api(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(4, 14, 4, 4)
        form.setSpacing(10)

        self.url_edit = QLineEdit()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_edit = QLineEdit()

        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        btn_eye = QPushButton("显示")
        btn_eye.setObjectName("Ghost")
        btn_eye.setFixedWidth(48)
        btn_eye.setCheckable(True)
        btn_eye.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(btn_eye)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(30, 900)
        self.timeout_spin.setSuffix(" 秒")
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(1, 10)

        form.addRow("接口地址", self.url_edit)
        form.addRow("API Key", key_row)
        form.addRow("模型", self.model_edit)
        form.addRow("单段超时", self.timeout_spin)
        form.addRow("最大重试", self.retry_spin)

        btn_test = QPushButton("测试连接")
        btn_test.setObjectName("Ghost")
        btn_test.clicked.connect(self._test_api)
        self.test_label = QLabel("")
        self.test_label.setObjectName("HintLabel")
        self.test_label.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(btn_test)
        row.addWidget(self.test_label, 1)
        form.addRow("", row)

        hint = QLabel("tp- 开头的订阅 key 必须配 token-plan-cn 专属端点，用公共端点会 401。")
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return w

    def _test_api(self) -> None:
        self.test_label.setText("测试中…")
        self.test_label.setStyleSheet("color:#6B6B66;")
        self.repaint()
        client = AsrClient(
            api_key=self.key_edit.text().strip(),
            base_url=self.url_edit.text().strip(),
            model=self.model_edit.text().strip() or "mimo-v2.5-asr",
            timeout=int(self.timeout_spin.value()),
            max_retries=1)
        ok, msg = client.test_connection(os.path.join(self.cfg.base_dir, "cache"))
        self.test_label.setText(("✓ " if ok else "× ") + msg)
        self.test_label.setStyleSheet("color:#3B6D11;" if ok else "color:#993C1D;")

    # ------------------------------------------------------------ 默认参数
    def _tab_defaults(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(4, 14, 4, 4)
        form.setSpacing(10)

        self.def_seg = QSpinBox()
        self.def_seg.setRange(5, 600)
        self.def_seg.setSuffix(" 秒")
        self.def_mode = QComboBox()
        self.def_mode.addItem("静音吸附", "silence")
        self.def_mode.addItem("定长硬切", "fixed")
        self.def_conc = QSpinBox()
        self.def_conc.setRange(1, 8)
        self.def_lang = QComboBox()
        self.def_lang.addItems(["auto", "zh", "en"])

        self.tol_spin = QSpinBox()
        self.tol_spin.setRange(10, 50)
        self.tol_spin.setSuffix(" %")
        self.sil_db = QSpinBox()
        self.sil_db.setRange(-60, -20)
        self.sil_db.setSuffix(" dB")
        self.long_sil = QDoubleSpinBox()
        self.long_sil.setRange(0.5, 5.0)
        self.long_sil.setSingleStep(0.5)
        self.long_sil.setSuffix(" 秒")

        form.addRow("默认切片长度", self.def_seg)
        form.addRow("默认切片方式", self.def_mode)
        form.addRow("默认并发", self.def_conc)
        form.addRow("默认语言", self.def_lang)
        form.addRow("吸附容差", self.tol_spin)
        form.addRow("静音判定", self.sil_db)
        form.addRow("长静音阈值", self.long_sil)

        hint = QLabel("吸附容差 = 切片长度的百分比，90 秒 × 30% 就是 ±27 秒。")
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return w

    # ------------------------------------------------------------ 高级
    def _tab_advanced(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 14, 4, 4)
        lay.setSpacing(10)

        self.chk_silence = QCheckBox("在波形上标出静音区间")
        lay.addWidget(self.chk_silence)

        self.wave_db = QSpinBox()
        self.wave_db.setRange(-60, -20)
        self.wave_db.setSuffix(" dB")
        self.wave_db.setMaximumWidth(120)
        row = QHBoxLayout()
        row.addWidget(QLabel("底纹阈值"))
        row.addWidget(self.wave_db)
        row.addStretch(1)
        lay.addLayout(row)

        size_kb = waveform.cache_size(os.path.join(self.cfg.base_dir, "cache")) / 1024
        self.cache_label = QLabel(f"波形缓存：{size_kb:.0f} KB")
        self.cache_label.setObjectName("HintLabel")
        lay.addWidget(self.cache_label)

        btn_clear = QPushButton("清空缓存")
        btn_clear.setObjectName("Ghost")
        btn_clear.clicked.connect(self._clear_cache)
        lay.addWidget(btn_clear)

        lay.addSpacing(6)
        btn_reset = QPushButton("恢复出厂默认")
        btn_reset.setObjectName("Ghost")
        btn_reset.clicked.connect(self._reset_defaults)
        lay.addWidget(btn_reset)
        lay.addStretch(1)
        return w

    def _clear_cache(self) -> None:
        n = waveform.clear_cache(os.path.join(self.cfg.base_dir, "cache"))
        self.cache_label.setText(f"已清除 {n} 个缓存文件")
        self.cache_label.setStyleSheet("color:#3B6D11;")

    def _reset_defaults(self) -> None:
        ret = QMessageBox.question(self, "恢复默认", "所有设置会回到出厂值，包括 API Key。继续？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        from ..config import DEFAULTS
        import copy
        self.cfg.data = copy.deepcopy(DEFAULTS)
        self._load()

    # ------------------------------------------------------------ 读写
    def _load(self) -> None:
        api = self.cfg.api
        self.url_edit.setText(api.get("base_url", ""))
        self.key_edit.setText(api.get("api_key", ""))
        self.model_edit.setText(api.get("model", "mimo-v2.5-asr"))
        self.timeout_spin.setValue(int(api.get("timeout", 180)))
        self.retry_spin.setValue(int(api.get("max_retries", 3)))

        d = self.cfg.data.get("defaults", {})
        self.def_seg.setValue(int(d.get("segment_seconds", 90)))
        self.def_mode.setCurrentIndex(0 if d.get("segment_mode", "silence") == "silence" else 1)
        self.def_conc.setValue(int(d.get("concurrency", 4)))
        idx = self.def_lang.findText(d.get("language", "auto"))
        self.def_lang.setCurrentIndex(max(0, idx))

        s = self.cfg.segment
        self.tol_spin.setValue(int(float(s.get("tolerance_ratio", 0.3)) * 100))
        self.sil_db.setValue(int(float(s.get("silence_db", -35))))
        self.long_sil.setValue(float(s.get("long_silence_sec", 1.5)))

        wv = self.cfg.waveform
        self.chk_silence.setChecked(bool(wv.get("show_silence", True)))
        self.wave_db.setValue(int(float(wv.get("silence_db", -45))))

    def _save(self) -> None:
        key = self.key_edit.text().strip()
        if key and not key.startswith(("tp-", "sk-")):
            ret = QMessageBox.question(
                self, "Key 格式存疑",
                "这个 Key 不以 tp- 或 sk- 开头，可能填错了。仍然保存？")
            if ret != QMessageBox.StandardButton.Yes:
                return

        self.cfg.data["api"].update({
            "base_url": self.url_edit.text().strip(),
            "api_key": key,
            "model": self.model_edit.text().strip() or "mimo-v2.5-asr",
            "timeout": int(self.timeout_spin.value()),
            "max_retries": int(self.retry_spin.value()),
        })
        self.cfg.data["defaults"].update({
            "segment_seconds": int(self.def_seg.value()),
            "segment_mode": self.def_mode.currentData(),
            "concurrency": int(self.def_conc.value()),
            "language": self.def_lang.currentText(),
        })
        self.cfg.data["segment"].update({
            "tolerance_ratio": self.tol_spin.value() / 100.0,
            "silence_db": float(self.sil_db.value()),
            "long_silence_sec": float(self.long_sil.value()),
        })
        self.cfg.data["waveform"].update({
            "show_silence": self.chk_silence.isChecked(),
            "silence_db": float(self.wave_db.value()),
        })
        self.cfg.save()
        self.accept()
