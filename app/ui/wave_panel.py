# -*- coding: utf-8 -*-
"""波形控件：柱状 RMS 曲线 + 双游标选区 + 播放头 + 滚轮缩放。

只负责画和交互，不碰播放和转写。外部通过信号接收选区变化。
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

PAD_LEFT = 42
PAD_RIGHT = 10
PAD_TOP = 10
PAD_BOTTOM = 18

COL_WAVE = QColor("#B5D4F4")
COL_WAVE_SEL = QColor("#185FA5")
COL_SILENCE = QColor("#F1F1EC")
COL_GRID = QColor("#E8E8E4")
COL_TEXT = QColor("#A0A09A")
COL_CURSOR = QColor("#D85A30")
COL_PLAYHEAD = QColor("#3B6D11")

DB_FLOOR = -60.0          # 低于这个当作 0 高度
MIN_VIEW = 5.0            # 最小可视时长（秒）
HIT_MARGIN = 7            # 游标命中范围（像素）

MODE_NONE = 0
MODE_DRAG_A = 1
MODE_DRAG_B = 2
MODE_NEW_SEL = 3


class WavePanel(QWidget):
    selectionChanged = Signal(float, float)   # A, B（秒）
    selectionCleared = Signal()
    seekRequested = Signal(float)             # 单击波形，请求播放头跳转
    dragStarted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(96)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._db = np.zeros(0, dtype=np.float32)
        self._duration = 0.0
        self._bucket_ms = 20
        self._silences: list[tuple[float, float]] = []

        self._view_start = 0.0
        self._view_end = 1.0
        self._sel_a: float | None = None
        self._sel_b: float | None = None
        self._playhead: float | None = None

        self._mode = MODE_NONE
        self._anchor = 0.0
        self._hover_cursor: int | None = None
        self._px_cache: np.ndarray | None = None
        self._px_cache_key: tuple | None = None

    # ------------------------------------------------------------ 数据
    def set_data(self, db: np.ndarray, duration: float, bucket_ms: int = 20) -> None:
        self._db = np.asarray(db, dtype=np.float32)
        self._duration = max(0.001, float(duration))
        self._bucket_ms = max(1, int(bucket_ms))
        self._view_start = 0.0
        self._view_end = self._duration
        self._sel_a = self._sel_b = None
        self._playhead = None
        self._invalidate()
        self.update()

    def set_silences(self, ranges: list[tuple[float, float]]) -> None:
        self._silences = list(ranges or [])
        self.update()

    def has_data(self) -> bool:
        return self._db.size > 0

    def clear(self) -> None:
        self._db = np.zeros(0, dtype=np.float32)
        self._duration = 0.0
        self._silences = []
        self._sel_a = self._sel_b = None
        self._playhead = None
        self._invalidate()
        self.update()

    @property
    def duration(self) -> float:
        return self._duration

    # ------------------------------------------------------------ 选区
    def selection(self) -> tuple[float, float] | None:
        if self._sel_a is None or self._sel_b is None:
            return None
        return (min(self._sel_a, self._sel_b), max(self._sel_a, self._sel_b))

    def set_selection(self, a: float, b: float) -> None:
        a, b = max(0.0, min(a, b)), min(self._duration, max(a, b))
        if b - a < 0.2:
            return
        self._sel_a, self._sel_b = a, b
        self.update()
        self.selectionChanged.emit(a, b)

    def clear_selection(self) -> None:
        self._sel_a = self._sel_b = None
        self.update()
        self.selectionCleared.emit()

    def set_playhead(self, seconds: float | None) -> None:
        self._playhead = seconds
        self.update()

    # ------------------------------------------------------------ 坐标换算
    def _plot_rect(self) -> QRectF:
        w = max(1, self.width() - PAD_LEFT - PAD_RIGHT)
        h = max(1, self.height() - PAD_TOP - PAD_BOTTOM)
        return QRectF(PAD_LEFT, PAD_TOP, w, h)

    def _time_to_x(self, t: float) -> float:
        r = self._plot_rect()
        span = max(1e-6, self._view_end - self._view_start)
        return r.left() + (t - self._view_start) / span * r.width()

    def _x_to_time(self, x: float) -> float:
        r = self._plot_rect()
        ratio = (x - r.left()) / max(1.0, r.width())
        t = self._view_start + ratio * (self._view_end - self._view_start)
        return max(0.0, min(self._duration, t))

    # ------------------------------------------------------------ 每像素电平
    def _invalidate(self) -> None:
        self._px_cache_key = None

    def _pixel_levels(self, width: int) -> np.ndarray:
        key = (width, round(self._view_start, 4), round(self._view_end, 4), self._db.size)
        if self._px_cache_key == key and self._px_cache is not None:
            return self._px_cache
        if self._db.size == 0 or width <= 0:
            return np.zeros(0, dtype=np.float32)

        ms_per = self._bucket_ms / 1000.0
        span = max(1e-6, self._view_end - self._view_start)
        t = self._view_start + np.arange(width + 1) * (span / width)
        idx = np.clip((t / ms_per).astype(np.int64), 0, self._db.size)
        out = np.zeros(width, dtype=np.float32)
        for x in range(width):
            lo, hi = idx[x], max(idx[x] + 1, idx[x + 1])
            out[x] = self._db[lo:hi].max() if hi > lo else DB_FLOOR

        self._px_cache = out
        self._px_cache_key = key
        return out

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if self._db.size == 0:
            self._paint_empty(painter)
            return

        rect = self._plot_rect()
        painter.fillRect(rect, QColor("#FCFCFA"))

        # 静音底纹
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COL_SILENCE)
        for s, e in self._silences:
            if e < self._view_start or s > self._view_end:
                continue
            x0 = self._time_to_x(max(s, self._view_start))
            x1 = self._time_to_x(min(e, self._view_end))
            painter.drawRect(QRectF(x0, rect.top(), max(1.0, x1 - x0), rect.height()))

        # dB 网格
        painter.setPen(QPen(COL_GRID, 1, Qt.PenStyle.DotLine))
        for db_val in (-20.0, -40.0):
            y = rect.bottom() - self._db_to_h(db_val, rect)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        # 波形
        sel = self.selection()
        levels = self._pixel_levels(int(rect.width()))
        mid = rect.center().y()
        pen_normal = QPen(COL_WAVE, 1)
        pen_sel = QPen(COL_WAVE_SEL, 1)
        sel_px = None
        if sel:
            sel_px = (self._time_to_x(sel[0]), self._time_to_x(sel[1]))

        painter.setPen(Qt.PenStyle.NoPen)
        for x, db_val in enumerate(levels):
            h = self._db_to_h(float(db_val), rect)
            if h <= 0:
                continue
            px = rect.left() + x
            in_sel = sel_px is not None and sel_px[0] <= px <= sel_px[1]
            painter.setPen(pen_sel if in_sel else pen_normal)
            painter.drawLine(QPointF(px, mid - h), QPointF(px, mid + h))

        self._paint_scale(painter, rect)

        if sel:
            self._paint_selection_overlay(painter, rect, sel)
        if self._playhead is not None:
            self._paint_playhead(painter, rect)

        painter.end()

    def _db_to_h(self, db_val: float, rect: QRectF) -> float:
        norm = (max(DB_FLOOR, min(0.0, db_val)) - DB_FLOOR) / -DB_FLOOR
        return norm * (rect.height() / 2.0 - 1)

    def _paint_empty(self, painter: QPainter) -> None:
        rect = self._plot_rect()
        painter.setPen(QPen(QColor("#DDDDD8"), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)
        painter.setPen(QColor("#A0A09A"))
        f = QFont(self.font())
        f.setPointSize(12)
        painter.setFont(f)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                         "把录音文件拖到这里，或点「选择文件」")

    def _paint_scale(self, painter: QPainter, rect: QRectF) -> None:
        f = QFont(self.font())
        f.setPointSize(9)
        painter.setFont(f)
        painter.setPen(COL_TEXT)

        # 左侧 dB 刻度
        for db_val, label in ((0.0, "0"), (-20.0, "-20"), (-40.0, "-40")):
            y = rect.bottom() - self._db_to_h(db_val, rect)
            painter.drawText(QRectF(0, y - 6, PAD_LEFT - 6, 12),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        span = self._view_end - self._view_start
        step = self._nice_step(span)
        t = np.ceil(self._view_start / step) * step
        while t <= self._view_end + 1e-6:
            x = self._time_to_x(t)
            painter.setPen(QPen(COL_GRID, 1))
            painter.drawLine(QPointF(x, rect.bottom()), QPointF(x, rect.bottom() + 3))
            painter.setPen(COL_TEXT)
            painter.drawText(QRectF(x - 30, rect.bottom() + 3, 60, 14),
                             Qt.AlignmentFlag.AlignCenter, self._fmt(t, step))
            t += step

    @staticmethod
    def _nice_step(span: float) -> float:
        for step in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800):
            if span / step <= 8:
                return float(step)
        return 3600.0

    @staticmethod
    def _fmt(t: float, step: float) -> str:
        t = max(0, int(round(t)))
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        if step < 60:
            return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        return f"{h}:{m:02d}" if h else f"{m}分"

    def _paint_selection_overlay(self, painter: QPainter, rect: QRectF,
                                 sel: tuple[float, float]) -> None:
        a, b = sel
        xa, xb = self._time_to_x(a), self._time_to_x(b)
        painter.setPen(QPen(COL_CURSOR, 1.5))
        painter.drawLine(QPointF(xa, rect.top()), QPointF(xa, rect.bottom()))
        painter.drawLine(QPointF(xb, rect.top()), QPointF(xb, rect.bottom()))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COL_CURSOR)
        for x in (xa, xb):
            painter.drawPolygon(QPolygonF([QPointF(x - 5, rect.top() - 7),
                                           QPointF(x + 5, rect.top() - 7),
                                           QPointF(x, rect.top())]))

        f = QFont(self.font())
        f.setPointSize(9)
        painter.setFont(f)
        for x, t in ((xa, a), (xb, b)):
            label = self._fmt(t, 1)
            w = painter.fontMetrics().horizontalAdvance(label) + 10
            box = QRectF(min(max(0, x - w / 2), self.width() - w), rect.top() + 2, w, 15)
            painter.setBrush(COL_CURSOR)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_playhead(self, painter: QPainter, rect: QRectF) -> None:
        x = self._time_to_x(self._playhead)
        if x < rect.left() or x > rect.right():
            return
        painter.setPen(QPen(COL_PLAYHEAD, 1))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.setBrush(COL_PLAYHEAD)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x, rect.top() + 3), 2.5, 2.5)

    # ------------------------------------------------------------ 交互
    def _hit_cursor(self, x: float) -> int | None:
        sel = self.selection()
        if not sel:
            return None
        if abs(x - self._time_to_x(sel[0])) <= HIT_MARGIN:
            return 0
        if abs(x - self._time_to_x(sel[1])) <= HIT_MARGIN:
            return 1
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._db.size == 0 or event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        rect = self._plot_rect()
        if not (rect.left() <= x <= rect.right()):
            return

        hit = self._hit_cursor(x)
        if hit == 0:
            self._mode = MODE_DRAG_A
        elif hit == 1:
            self._mode = MODE_DRAG_B
        else:
            self._mode = MODE_NEW_SEL
            self._anchor = self._x_to_time(x)
            self._sel_a = self._sel_b = self._anchor
        self.dragStarted.emit()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        rect = self._plot_rect()
        if self._mode == MODE_NONE:
            hit = self._hit_cursor(x)
            self.setCursor(Qt.CursorShape.SizeHorCursor if hit is not None
                           else Qt.CursorShape.CrossCursor)
            return

        t = self._x_to_time(min(max(x, rect.left()), rect.right()))
        if self._mode == MODE_DRAG_A:
            b = self._sel_b if self._sel_b is not None else self._duration
            self._sel_a = min(t, b - 0.2)
        elif self._mode == MODE_DRAG_B:
            a = self._sel_a if self._sel_a is not None else 0.0
            self._sel_b = max(t, a + 0.2)
        else:
            if t >= self._anchor:
                self._sel_a, self._sel_b = self._anchor, t
            else:
                self._sel_a, self._sel_b = t, self._anchor
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._mode == MODE_NONE:
            return
        was_new = self._mode == MODE_NEW_SEL
        self._mode = MODE_NONE
        sel = self.selection()
        if sel and (sel[1] - sel[0]) >= 1.0:
            self.selectionChanged.emit(sel[0], sel[1])
            if was_new:
                self.seekRequested.emit(sel[0])
        elif was_new:
            self._sel_a = self._sel_b = None
            self.selectionCleared.emit()
            self.seekRequested.emit(self._x_to_time(event.position().x()))
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.selection():
            self.clear_selection()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._db.size == 0:
            return
        rect = self._plot_rect()
        span = self._view_end - self._view_start
        factor = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        new_span = max(MIN_VIEW, min(self._duration, span / factor))
        if abs(new_span - span) < 1e-6:
            return

        ratio = min(1.0, max(0.0, (event.position().x() - rect.left()) / rect.width()))
        anchor = self._view_start + span * ratio
        start = anchor - new_span * ratio
        start = max(0.0, min(start, self._duration - new_span))
        self._view_start = start
        self._view_end = start + new_span
        self._invalidate()
        self.update()

    # ------------------------------------------------------------ 外部操作
    def zoom_fit(self) -> None:
        if self._duration <= 0:
            return
        self._view_start, self._view_end = 0.0, self._duration
        self._invalidate()
        self.update()

    def zoom_to_selection(self) -> None:
        sel = self.selection()
        if not sel:
            return
        pad = (sel[1] - sel[0]) * 0.1
        self._view_start = max(0.0, sel[0] - pad)
        self._view_end = min(self._duration, sel[1] + pad)
        self._invalidate()
        self.update()

    def shrink_view(self, keep_center: bool = True) -> None:
        if self._duration <= 0:
            return
        span = self._view_end - self._view_start
        if keep_center:
            center = (self._view_start + self._view_end) / 2
            step = 0.5
            new_start = max(0.0, center - span * step)
            self._view_start = max(0.0, new_start)
            self._view_end = self._view_start + span
            self._invalidate()
            self.update()
