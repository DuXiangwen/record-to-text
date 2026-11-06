# -*- coding: utf-8 -*-
"""主窗口：顶栏 + 波形区 + 表单 + 卡片列表 + 状态栏。"""
from __future__ import annotations

import os
import shutil
import time

from PySide6.QtCore import QLockFile, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import audio, exporter, waveform
from ..config import Config
from ..models import (Params, Project, STATUS_LABEL, fmt_dur, fmt_ts,
                      new_project_id, safe_name)
from ..pipeline import Callbacks, TranscribeJob
from ..store import PROJECT_FILE, ProjectStore, unique_dir, writable_dir
from .form_panel import FormPanel
from .log_panel import LogPanel
from .project_card import ProjectCard
from .settings_dialog import SettingsDialog
from .wave_panel import WavePanel

AUDIO_EXT = (".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma",
             ".mp4", ".mov", ".amr", ".opus", ".m4b")


# ---------------------------------------------------------------- 后台线程
class WaveLoader(QThread):
    loaded = Signal(object, float, object)
    failed = Signal(str)

    def __init__(self, path: str, cfg: Config, parent=None):
        super().__init__(parent)
        self.path = path
        self.cfg = cfg

    def run(self) -> None:
        try:
            cache = os.path.join(self.cfg.base_dir, "cache")
            bucket = int(self.cfg.waveform.get("bucket_ms", 20))
            db, dur = waveform.load_peaks(
                self.path, cache, bucket_ms=bucket,
                sr=int(self.cfg.waveform.get("sample_rate", 16000)))
            sil = audio.find_silences(
                db, bucket,
                float(self.cfg.waveform.get("silence_db", -45.0)),
                float(self.cfg.waveform.get("silence_min_sec", 2.0)))
            self.loaded.emit(db, dur, sil)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class JobWorker(QThread):
    statusChanged = Signal(str)
    segmentDone = Signal(object, int, int)
    logMessage = Signal(str)
    jobFailed = Signal(str)
    jobFinished = Signal(bool)

    def __init__(self, project: Project, cfg: Config, parent=None):
        super().__init__(parent)
        self.project = project
        self.cfg = cfg
        self.job = TranscribeJob(project, cfg, Callbacks(
            on_status=self.statusChanged.emit,
            on_segment=self.segmentDone.emit,
            on_log=self.logMessage.emit,
        ))

    def run(self) -> None:
        try:
            ok = self.job.run()
            self.jobFinished.emit(ok)
        except Exception as exc:  # noqa: BLE001
            self.jobFailed.emit(str(exc))
            self.jobFinished.emit(False)

    def stop(self) -> None:
        self.job.stop()


# ---------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):
    def __init__(self, base_dir: str):
        super().__init__()
        self.base_dir = base_dir
        self.cfg = Config(base_dir)
        self.store = ProjectStore(base_dir)

        self.setWindowTitle("录音转文本")
        self.resize(int(self.cfg.get("window", "w", default=1280)),
                    int(self.cfg.get("window", "h", default=840)))
        self.setMinimumSize(1040, 700)
        self.setAcceptDrops(True)

        self._db = None
        self._duration = 0.0
        self._silences: list = []
        self._current_path = ""
        self._loader: WaveLoader | None = None
        self._worker: JobWorker | None = None
        self._running: Project | None = None
        self._queue: list[Project] = []
        self._cards: dict[str, ProjectCard] = {}
        self._preview_win = None

        self._build()
        self._setup_player()
        self._refresh_cards()
        self._refresh_api_state()
        self._on_params_changed()
        self.log_panel.append("就绪，拖入录音文件开始")

    # ------------------------------------------------------------ 界面
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._build_topbar())
        lay.addWidget(self._build_wave_section())
        lay.addWidget(self._build_body(), 1)
        self.log_panel = LogPanel()
        lay.addWidget(self.log_panel)
        lay.addWidget(self._build_statusbar())

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(50)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(9)

        icon = QLabel()
        icon.setPixmap(self._make_icon())
        icon.setFixedSize(26, 26)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background:#E6F1FB;border-radius:7px;")

        title = QLabel("录音转文本")
        title.setObjectName("AppTitle")

        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addStretch(1)

        self.api_chip = QPushButton("检查中…")
        self.api_chip.setObjectName("Ghost")
        self.api_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.api_chip.clicked.connect(self._open_settings)
        lay.addWidget(self.api_chip)

        self.collapse_btn = QPushButton("收起波形")
        self.collapse_btn.setObjectName("Ghost")
        self.collapse_btn.clicked.connect(self._toggle_wave)
        lay.addWidget(self.collapse_btn)

        btn_settings = QPushButton("设置")
        btn_settings.setObjectName("Ghost")
        btn_settings.clicked.connect(self._open_settings)
        lay.addWidget(btn_settings)
        return bar

    @staticmethod
    def _make_icon() -> QPixmap:
        pm = QPixmap(22, 22)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#185FA5"))
        heights = (6, 12, 8, 15, 10)
        for i, h in enumerate(heights):
            p.drawRoundedRect(2 + i * 4, 11 - h // 2, 2, h, 1, 1)
        p.end()
        return pm

    def _build_wave_section(self) -> QWidget:
        self.wave_section = QWidget()
        self.wave_section.setObjectName("WaveSection")
        self.wave_section.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self.wave_section)
        lay.setContentsMargins(16, 11, 16, 9)
        lay.setSpacing(7)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.wave_title = QLabel("还没有选择录音")
        self.wave_title.setObjectName("WaveMeta")
        self.wave_sub = QLabel("把文件拖到窗口任意位置，或点左侧「选择」")
        self.wave_sub.setObjectName("WaveMetaSub")
        self.zoom_btn = QPushButton("适应全宽")
        self.zoom_btn.setObjectName("Ghost")
        self.zoom_btn.clicked.connect(lambda: self.wave.zoom_fit())
        self.zoom_btn.setEnabled(False)
        head.addWidget(self.wave_title)
        head.addWidget(self.wave_sub)
        head.addStretch(1)
        head.addWidget(self.zoom_btn)
        lay.addLayout(head)

        self.wave = WavePanel()
        self.wave.selectionChanged.connect(self._on_selection)
        self.wave.selectionCleared.connect(self._on_selection_cleared)
        self.wave.seekRequested.connect(self._on_seek)
        self.wave.dragStarted.connect(self._stop_play)
        lay.addWidget(self.wave, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.sel_label = QLabel("未选择区间 · 默认处理全片")
        self.sel_label.setObjectName("SelInfo")
        self.play_btn = QPushButton("▶ 试听")
        self.play_btn.setObjectName("Ghost")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._toggle_play)
        self.stop_btn = QPushButton("■")
        self.stop_btn.setObjectName("Ghost")
        self.stop_btn.setFixedWidth(30)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_play)
        self.speed_btn = QPushButton("1×")
        self.speed_btn.setObjectName("Ghost")
        self.speed_btn.setFixedWidth(44)
        self.speed_btn.setEnabled(False)
        self.speed_btn.clicked.connect(self._cycle_speed)
        self.clear_btn = QPushButton("清除选区")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self.wave.clear_selection)

        foot.addWidget(self.sel_label)
        foot.addStretch(1)
        foot.addWidget(self.speed_btn)
        foot.addWidget(self.play_btn)
        foot.addWidget(self.stop_btn)
        foot.addWidget(self.clear_btn)
        lay.addLayout(foot)
        return self.wave_section

    def _build_body(self) -> QWidget:
        body = QWidget()
        lay = QHBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.form = FormPanel()
        self.form.startRequested.connect(self._start_from_form)
        self.form.fileChanged.connect(self._load_file)
        self.form.paramsChanged.connect(self._on_params_changed)
        self.form.apply_defaults(self.cfg.last_params())
        lay.addWidget(self.form)

        panel = QWidget()
        panel.setObjectName("ListPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(16, 13, 16, 13)
        pl.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(9)
        title = QLabel("历史项目")
        title.setObjectName("SectionTitle")
        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText("搜索项目名…")
        self.search.setFixedHeight(26)
        self.search.textChanged.connect(self._filter_cards)
        self.count_label = QLabel("0 个")
        self.count_label.setObjectName("CountLabel")
        head.addWidget(title)
        head.addWidget(self.search, 1)
        head.addWidget(self.count_label)
        pl.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        self.card_layout = QVBoxLayout(holder)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(8)
        self.card_layout.addStretch(1)
        self.scroll.setWidget(holder)
        pl.addWidget(self.scroll, 1)

        lay.addWidget(panel, 1)
        return body

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(26)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        self.status_left = QLabel("就绪")
        self.status_right = QLabel("内核 v0.8")
        lay.addWidget(self.status_left)
        lay.addStretch(1)
        lay.addWidget(self.status_right)
        return bar

    # ------------------------------------------------------------ 播放
    def _setup_player(self) -> None:
        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.9)
        self.player.setAudioOutput(self.audio_out)
        self.player.positionChanged.connect(self._on_player_pos)
        self.player.playbackStateChanged.connect(self._on_player_state)
        self._play_start = 0.0
        self._play_end: float | None = None
        self._speed = 1.0

    def _toggle_play(self) -> None:
        if not self._current_path:
            return
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        if state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
            return
        sel = self.wave.selection()
        start, end = (sel if sel else (0.0, self._duration))
        self._play_start = start
        self._play_end = end if sel else None
        self.player.setSource(QUrl.fromLocalFile(self._current_path))
        self.player.setPlaybackRate(self._speed)
        self.player.setPosition(int(start * 1000))
        self.player.play()

    def _stop_play(self) -> None:
        self.player.stop()
        self.wave.set_playhead(None)

    def _on_player_pos(self, pos: int) -> None:
        t = pos / 1000.0
        if self._play_end is not None and t >= self._play_end:
            self.player.pause()
            self.player.setPosition(int(self._play_start * 1000))
            self.wave.set_playhead(self._play_start)
            return
        self.wave.set_playhead(t)

    def _on_player_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸ 暂停" if playing else "▶ 试听")

    def _cycle_speed(self) -> None:
        seq = [1.0, 1.5, 2.0, 0.5]
        i = seq.index(self._speed) if self._speed in seq else 0
        self._speed = seq[(i + 1) % len(seq)]
        self.speed_btn.setText(f"{self._speed:g}×")
        self.player.setPlaybackRate(self._speed)

    def _on_seek(self, t: float) -> None:
        if self._play_end is None:
            self.wave.set_playhead(t)

    # ------------------------------------------------------------ 加载文件
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        urls = [u for u in event.mimeData().urls() if u.isLocalFile()]
        if not urls:
            return
        path = urls[0].toLocalFile()
        if not path.lower().endswith(AUDIO_EXT):
            self._toast("这个格式不支持")
            return
        if len(urls) > 1:
            self._toast("一次只能处理一个文件，用了第一个")
        self.form.set_file(path)
        self._load_file(path)

    def _load_file(self, path: str) -> None:
        if not path or not os.path.isfile(path):
            return
        self._stop_play()
        self._current_path = path
        self._db = None
        self._duration = 0.0
        self.wave.clear()
        self.wave_title.setText(os.path.basename(path))
        self.wave_sub.setText("正在解析波形…")
        self.form.set_start_enabled(False, "解析中…")
        for w in (self.play_btn, self.stop_btn, self.speed_btn, self.zoom_btn):
            w.setEnabled(False)

        if self._loader and self._loader.isRunning():
            self._loader.terminate()
        self._loader = WaveLoader(path, self.cfg)
        self._loader.loaded.connect(self._on_wave_loaded)
        self._loader.failed.connect(self._on_wave_failed)
        self._loader.start()

    def _on_wave_loaded(self, db, duration: float, silences) -> None:
        self._db, self._duration, self._silences = db, duration, silences
        self.wave.set_data(db, duration, int(self.cfg.waveform.get("bucket_ms", 20)))
        if self.cfg.waveform.get("show_silence", True):
            self.wave.set_silences(silences)
        size = os.path.getsize(self._current_path) / 1024 / 1024
        self.wave_sub.setText(f"{fmt_dur(duration)} · {size:.1f} MB · "
                              f"{len(silences)} 段静音")
        for w in (self.play_btn, self.stop_btn, self.speed_btn, self.zoom_btn):
            w.setEnabled(True)
        self._on_params_changed()
        self._update_output_preview()
        self.wave.setFocus()

    def _on_wave_failed(self, msg: str) -> None:
        self.wave_sub.setText("解析失败")
        self.form.set_start_enabled(False, "无法读取这个文件")
        QMessageBox.warning(self, "读取失败", msg[:500])

    # ------------------------------------------------------------ 参数 / 预估
    def _on_params_changed(self) -> None:
        self._update_output_preview()
        est = self._estimate_segments()
        if self._db is None or self._db.size == 0:
            self.form.set_estimate("")
            return
        minutes = est * 10 / max(1, self.form.params()["concurrency"]) / 60
        self.form.set_estimate(f"预计 {est} 段 · 约 {max(1, round(minutes))} 分钟")
        sel = self.wave.selection()
        self.form.set_trim_hint(sel, est)
        self.form.set_start_enabled(self._can_start(), "")

    def _estimate_segments(self) -> int:
        if self._duration <= 0:
            return 0
        p = self.form.params()
        sel = self.wave.selection()
        length = (sel[1] - sel[0]) if sel else self._duration
        return max(1, int(round(length / max(1.0, p["segment_seconds"]))))

    def _can_start(self) -> bool:
        return bool(self._current_path
                    and self._db is not None and self._db.size > 0
                    and self.form.project_name()
                    and self.form.params()["formats"])

    def _update_output_preview(self) -> None:
        if not self._current_path:
            self.form.set_output_preview("")
            return
        name = self.form.project_name() or "未命名"
        parent = os.path.dirname(self._current_path)
        cand = os.path.join(parent, name)
        suffix = "" if not os.path.exists(cand) else "  (已存在，将自动加 _2)"
        self.form.set_output_preview(f"将创建：{cand}{suffix}")

    def _on_selection(self, a: float, b: float) -> None:
        dur = b - a
        est = max(1, int(round(dur / max(1.0, self.form.params()["segment_seconds"]))))
        self.sel_label.setText(
            f"选区 {fmt_ts(a)} – {fmt_ts(b)} · {fmt_dur(dur)} · 约 {est} 段")
        self.clear_btn.setEnabled(True)
        self._on_params_changed()
        self.wave.zoom_to_selection()

    def _on_selection_cleared(self) -> None:
        self.sel_label.setText("未选择区间 · 默认处理全片")
        self.clear_btn.setEnabled(False)
        self._on_params_changed()

    # ------------------------------------------------------------ 开始转换
    def _start_from_form(self) -> None:
        if not self.cfg.api.get("api_key"):
            self._open_settings()
            if not self.cfg.api.get("api_key"):
                QMessageBox.information(self, "还没配置", "先在设置里填好 API Key 才能开始。")
                return
        if not self._can_start():
            self._toast("先把项目和录音文件填好")
            return

        path = self._current_path
        name = self.form.project_name()
        params = Params(**self.form.params())
        parent = os.path.dirname(path)
        out_dir = unique_dir(parent, name)

        if not writable_dir(parent):
            fallback = os.path.join(os.path.expanduser("~"), "Documents", "RecordToText")
            ret = QMessageBox.question(
                self, "换个地方存",
                f"录音所在目录不能写入。\n改存到 {fallback} 吗？")
            if ret != QMessageBox.StandardButton.Yes:
                return
            os.makedirs(fallback, exist_ok=True)
            out_dir = unique_dir(fallback, name)

        project = Project(id=new_project_id(), name=name, source_path=path,
                          output_dir=out_dir, params=params, status="queued")
        sel = self.wave.selection()
        if sel:
            project.trim = {"start": round(sel[0], 3), "end": round(sel[1], 3)}
            project.duration_sec = self._duration

        self.store.add(project)
        self.cfg.remember_params(params.to_dict())
        self._queue.append(project)
        if self._running is not None:
            self.log_panel.append(f"已排队：{name}")
        self._refresh_cards()
        self._pump_queue()
        self.form.name_edit.clear()

    def _pump_queue(self) -> None:
        if self._running is not None:
            return
        while self._queue:
            nxt = self._queue.pop(0)
            if self.store.find(nxt.id) is None:
                continue
            self._begin(nxt)
            return

    def _begin(self, project: Project) -> None:
        self._running = project
        project.status = "slicing"
        self.store.update(project)
        self._refresh_cards()

        self._worker = JobWorker(project, self.cfg, self)
        self._worker.statusChanged.connect(self._on_job_status)
        self._worker.segmentDone.connect(self._on_segment_done)
        self._worker.logMessage.connect(self.log_panel.append)
        self._worker.jobFinished.connect(self._on_job_finished)
        self._worker.jobFailed.connect(self._on_job_failed)
        self._worker.start()
        self.status_left.setText(f"正在处理 {project.name}")
        self.log_panel.append(
            f"开始处理「{project.name}」· {fmt_dur(project.content_duration)} · "
            f"输出到 {project.output_dir}")

    def _on_job_status(self, status: str) -> None:
        if not self._running:
            return
        self._running.status = status
        self.store.update(self._running)
        self._refresh_one(self._running)
        if status == "slicing":
            self.status_left.setText(f"正在切片 {self._running.name}")

    def _on_segment_done(self, seg, finished: int, total: int) -> None:
        if not self._running or not self._worker:
            return
        job = self._worker.job
        self._running.stats["elapsed_sec"] = round(job.elapsed, 1)
        self._refresh_one(self._running)
        if self._preview_win is not None and self._preview_win.isVisible():
            self._preview_win.set_text(job.preview_text())
            self._preview_win.set_progress(finished, total)

    def _on_job_finished(self, ok: bool) -> None:
        project = self._running
        self._running = None
        self._worker = None
        if project:
            self.store.update(project)
            self._refresh_one(project)
            status, _ = STATUS_LABEL.get(project.status, ("完成", ""))
            self.status_left.setText(f"{project.name}：{status}")
        self._pump_queue()

    def _on_job_failed(self, msg: str) -> None:
        self.status_left.setText(f"失败：{msg[:60]}")

    # ------------------------------------------------------------ 卡片
    def _refresh_cards(self) -> None:
        for card in self._cards.values():
            self.card_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        for project in self.store.projects:
            card = ProjectCard(project)
            card.openRequested.connect(self._open_folder)
            card.previewRequested.connect(self._show_preview)
            card.startRequested.connect(self._start_card)
            card.stopRequested.connect(self._stop_card)
            card.cancelQueueRequested.connect(self._cancel_queue)
            card.continueRequested.connect(self._continue_card)
            card.deleteRequested.connect(self._delete_card)
            self.card_layout.insertWidget(self.card_layout.count() - 1, card)
            self._cards[project.id] = card
        self.count_label.setText(f"{len(self.store.projects)} 个")
        self._filter_cards(self.search.text())

    def _refresh_one(self, project: Project) -> None:
        card = self._cards.get(project.id)
        if card:
            card.update_project(project)

    def _filter_cards(self, text: str) -> None:
        key = (text or "").strip().lower()
        for pid, card in self._cards.items():
            p = card.project
            visible = (not key) or key in p.name.lower() or key in p.source_name.lower()
            card.setVisible(visible)

    def _open_folder(self, pid: str) -> None:
        p = self.store.find(pid)
        if p and os.path.isdir(p.output_dir):
            os.startfile(p.output_dir)  # noqa: S606
        else:
            self._toast("输出文件夹不存在，可能已被删除")

    def _start_card(self, pid: str) -> None:
        p = self.store.find(pid)
        if p:
            p.status = "queued"
            self.store.update(p)
            self._queue.append(p)
            self._refresh_one(p)
            self._pump_queue()

    def _continue_card(self, pid: str) -> None:
        p = self.store.find(pid)
        if not p:
            return
        if not os.path.isfile(p.source_path):
            QMessageBox.warning(self, "源文件不在", f"找不到原始录音：\n{p.source_path}")
            return
        p.status = "queued"
        p.error = None
        self.store.update(p)
        self._queue.append(p)
        self._refresh_one(p)
        self._pump_queue()

    def _stop_card(self, pid: str) -> None:
        if self._worker and self._running and self._running.id == pid:
            self._worker.stop()
            self.status_left.setText("正在停止…")
        else:
            self._cancel_queue(pid)

    def _cancel_queue(self, pid: str) -> None:
        self._queue = [p for p in self._queue if p.id != pid]
        p = self.store.find(pid)
        if p:
            p.status = "canceled"
            self.store.update(p)
            self._refresh_one(p)

    def _delete_card(self, pid: str, delete_files: bool) -> None:
        p = self.store.find(pid)
        if not p:
            return
        if delete_files:
            ret = QMessageBox.warning(
                self, "确认删除",
                f"会把整个项目文件夹移进回收站：\n{p.output_dir}\n\n继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            if ret != QMessageBox.StandardButton.Yes:
                return
        ok, msg = self.store.remove(pid, delete_files=delete_files)
        if not ok:
            QMessageBox.warning(self, "删除失败", msg)
            return
        self._refresh_cards()
        self.status_left.setText(f"已移除 {p.name}")
        self.log_panel.append(f"已移除卡片：{p.name}" + ("（文件已移入回收站）" if delete_files else "（文件保留）"))

    def _show_preview(self, pid: str) -> None:
        from .preview_dialog import PreviewDialog
        if self._preview_win is None:
            self._preview_win = PreviewDialog(self)

        if self._worker and self._running and self._running.id == pid:
            job = self._worker.job
            name = self._running.name
            self._preview_win.setWindowTitle(f"{name} · 实时预览")
            self._preview_win.set_title(f"{name} · 实时预览")
            self._preview_win.set_text(job.preview_text())
            done = sum(1 for s in job.segments if s.finished)
            self._preview_win.set_progress(done, len(job.segments))
        else:
            p = self.store.find(pid)
            name = p.name if p else "分段文本"
            self._preview_win.setWindowTitle(f"{name} · 分段文本")
            self._preview_win.set_title(f"{name} · 分段文本")
            self._preview_win.set_progress(0, 0)
            if p:
                segs = exporter.load_manifest_segments(p)
                if not segs:
                    segs = sorted(
                        exporter.scan_segments(os.path.join(p.output_dir, "segments")).values(),
                        key=lambda s: s.index)
                self._preview_win.set_text(
                    exporter.build_txt(p, segs) if segs else "这个项目还没有转写结果。")
        self._preview_win.show()
        self._preview_win.raise_()

    # ------------------------------------------------------------ 其它
    def _toggle_wave(self) -> None:
        visible = self.wave_section.isVisible()
        self.wave_section.setVisible(not visible)
        self.collapse_btn.setText("展开波形" if visible else "收起波形")

    def _refresh_api_state(self) -> None:
        has_key = bool(self.cfg.api.get("api_key"))
        if has_key:
            self.api_chip.setText("● API 已配置")
            self.api_chip.setStyleSheet(
                "background:#EAF3DE;color:#3B6D11;border:none;border-radius:10px;"
                "padding:3px 10px;font-size:11px;")
        else:
            self.api_chip.setText("● API 未配置")
            self.api_chip.setStyleSheet(
                "background:#FAEEDA;color:#854F0B;border:none;border-radius:10px;"
                "padding:3px 10px;font-size:11px;")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            self._refresh_api_state()
            self._on_params_changed()

    def _toast(self, msg: str) -> None:
        self.status_left.setText(msg)
        QTimer.singleShot(4000, lambda: self.status_left.setText("就绪"))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker and self._worker.isRunning():
            ret = QMessageBox.question(
                self, "还在转写",
                "现在退出会中断当前任务。\n已完成的段会保留，下次可以继续。\n\n确定退出？")
            if ret != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._worker.stop()
            self._worker.wait(8000)
        self._stop_play()
        geo = self.geometry()
        self.cfg.data["window"] = {"w": geo.width(), "h": geo.height(),
                                   "x": geo.x(), "y": geo.y()}
        self.cfg.save()
        self.store.save()
        event.accept()
