# -*- coding: utf-8 -*-
"""转写任务内核：与界面无关，命令行和 GUI 共用。

一次 run() = 校验 → 规划切片 → 恢复断点 → 并发切片转写 → 汇总输出。
所有进度通过 Callbacks 回调出去，调用方决定怎么展示。
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from . import audio, exporter, waveform
from .asr import AsrClient, AsrError
from .models import Project, Segment, fmt_ts

MAX_CONSECUTIVE_FAILURES = 5      # 连续这么多段失败就判定服务异常
FLUSH_EVERY = 10                  # 每完成这么多段刷新一次合并文件
AUDIO_EXT = (".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma",
             ".mp4", ".mov", ".amr", ".opus", ".m4b")


@dataclass
class Callbacks:
    on_status: Callable[[str], None] | None = None
    on_segment: Callable[[Segment, int, int], None] | None = None
    on_log: Callable[[str], None] | None = None
    should_stop: Callable[[], bool] | None = None


class TranscribeJob:
    def __init__(self, project: Project, cfg, callbacks: Callbacks | None = None):
        self.project = project
        self.cfg = cfg
        self.cb = callbacks or Callbacks()
        self.segments: list[Segment] = []
        self.client = AsrClient(
            api_key=cfg.api.get("api_key", ""),
            base_url=cfg.api.get("base_url", ""),
            model=cfg.api.get("model", "mimo-v2.5-asr"),
            timeout=int(cfg.api.get("timeout", 180)),
            max_retries=int(cfg.api.get("max_retries", 3)),
            rate=float(project.params.concurrency),
        )
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._consecutive_fail = 0
        self._aborted_reason = ""
        self._flush_mark = 0
        self._t0 = 0.0

    # ------------------------------------------------------------ 基础
    @property
    def audio_dir(self) -> str:
        return os.path.join(self.project.output_dir, "audio")

    @property
    def seg_dir(self) -> str:
        return os.path.join(self.project.output_dir, "segments")

    @property
    def log_path(self) -> str:
        return os.path.join(self.project.output_dir, "run.log")

    def stop(self) -> None:
        self._stop.set()

    @property
    def elapsed(self) -> float:
        return time.time() - self._t0 if self._t0 else 0.0

    def _stopped(self) -> bool:
        if self._stop.is_set():
            return True
        fn = self.cb.should_stop
        return bool(fn and fn())

    def _log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        try:
            os.makedirs(self.project.output_dir, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        if self.cb.on_log:
            self.cb.on_log(line)

    def _set_status(self, status: str) -> None:
        self.project.status = status
        if self.cb.on_status:
            self.cb.on_status(status)

    # ------------------------------------------------------------ 主流程
    def run(self) -> bool:
        """跑完返回 True；用户中断返回 False；致命错误抛异常。"""
        self._t0 = time.time()
        try:
            self._validate()
            self._prepare_dirs()
            self._set_status("slicing")
            self._plan()
            self._restore()
            self._set_status("transcribing")
            self._process_all()
            self._finalize()
        except Exception as exc:  # noqa: BLE001
            self.project.status = "failed"
            self.project.error = str(exc)
            self.project.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
            self._log(f"失败：{exc}")
            self._persist()
            raise
        return self.project.status in ("done", "partial")

    def _validate(self) -> None:
        src = self.project.source_path
        if not os.path.isfile(src):
            raise FileNotFoundError(f"录音文件不存在：{src}")
        if not src.lower().endswith(AUDIO_EXT):
            raise ValueError(f"不支持的音频格式：{os.path.splitext(src)[1]}")
        if not self.cfg.api.get("api_key"):
            raise RuntimeError("还没配置 API Key，请在设置里填好再开始")
        if not os.path.isfile(audio.get_ffmpeg()) and audio.get_ffmpeg() == "ffmpeg":
            raise RuntimeError("找不到 ffmpeg")

    def _prepare_dirs(self) -> None:
        for path in (self.project.output_dir, self.audio_dir, self.seg_dir):
            os.makedirs(path, exist_ok=True)

    def _plan(self) -> None:
        """探测 + 规划切片。已有规划则沿用（续跑）。"""
        p = self.project.params
        info = audio.probe_info(self.project.source_path)
        if not self.project.duration_sec:
            self.project.duration_sec = info["duration"]

        trim = self.project.trim
        if trim:
            t_start = float(trim["start"])
            t_end = float(trim["end"])
        else:
            t_start, t_end = 0.0, self.project.duration_sec
        t_start = max(0.0, min(t_start, self.project.duration_sec))
        t_end = max(t_start, min(t_end, self.project.duration_sec))
        content_len = t_end - t_start
        if content_len <= 0:
            raise ValueError("转写范围为空")

        if p.segment_mode == "silence":
            bucket_ms = int(self.cfg.waveform.get("bucket_ms", 20))
            db, _ = waveform.load_peaks(
                self.project.source_path,
                os.path.join(self.cfg.base_dir, "cache"),
                bucket_ms=bucket_ms,
                sr=int(self.cfg.waveform.get("sample_rate", 16000)),
                progress=lambda msg: self._log(msg))
            sub = waveform.slice_view(db, t_start, t_end, bucket_ms)
            seg_cfg = self.cfg.segment
            plan = audio.plan_segments(
                sub, content_len, p.segment_seconds, mode="silence",
                bucket_ms=bucket_ms,
                tol_ratio=float(seg_cfg.get("tolerance_ratio", 0.30)),
                silence_db=float(seg_cfg.get("silence_db", -35.0)),
                silence_min_sec=float(seg_cfg.get("silence_min_sec", 0.4)),
                long_silence_sec=float(seg_cfg.get("long_silence_sec", 1.5)),
                prefetch_ratio=float(seg_cfg.get("prefetch_ratio", 0.60)),
                min_seg_ratio=float(seg_cfg.get("min_seg_ratio", 0.40)),
            )
        else:
            plan = audio.plan_segments([], content_len, p.segment_seconds, mode="fixed")

        self.segments = [
            Segment(index=item["index"],
                    start=round(t_start + item["start"], 3),
                    end=round(t_start + item["end"], 3))
            for item in plan
        ]
        self.project.refresh_progress(self.segments)
        self._log(f"规划完成：{audio.summarize_plan(plan)}")

    def _restore(self) -> None:
        """把已完成的段从磁盘捡回来，前提是切点能对上。"""
        done = exporter.scan_segments(self.seg_dir)
        if not done:
            return
        matched = 0
        for seg in self.segments:
            old = done.get(seg.index)
            if old and abs(old.start - seg.start) < 1.0 and abs(old.end - seg.end) < 1.0:
                seg.status = old.status
                seg.text = old.text
                seg.attempts = old.attempts
                seg.elapsed = old.elapsed
                matched += 1
        if matched == 0 and done:
            self._log("切点与旧结果对不上，清空旧片段重新开始")
            exporter.clean_segments_dir(self.seg_dir)
            return
        self.project.refresh_progress(self.segments)
        if matched:
            self._log(f"断点续传：跳过已完成的 {matched} 段")

    # ------------------------------------------------------------ 逐段处理
    def _cut(self, seg: Segment) -> str:
        path = os.path.join(self.audio_dir, f"seg_{seg.index:03d}.mp3")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        return audio.cut_segment(self.project.source_path, path,
                                 seg.start, max(0.1, seg.duration))

    def _process_one(self, seg: Segment) -> Segment:
        t0 = time.time()
        seg.attempts += 1
        try:
            path = self._cut(seg)
            text = self.client.transcribe(path, language=self.project.params.language)
            seg.text = text
            seg.status = "done" if text else "empty"
            seg.error = ""
        except AsrError as exc:
            seg.status = "failed"
            seg.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            seg.status = "failed"
            seg.error = f"{type(exc).__name__}: {exc}"
        seg.elapsed = round(time.time() - t0, 2)

        exporter.write_segment_files(self.seg_dir, seg)
        with self._lock:
            if seg.status == "failed":
                self._consecutive_fail += 1
                if self._consecutive_fail >= MAX_CONSECUTIVE_FAILURES and not self._aborted_reason:
                    self._aborted_reason = f"连续 {self._consecutive_fail} 段失败，已暂停"
                    self._stop.set()
            else:
                self._consecutive_fail = 0
            self.project.refresh_progress(self.segments)

        total = len(self.segments)
        if seg.status == "failed":
            self._log(f"第 {seg.index + 1}/{total} 段失败 · {seg.error[:60]}")
        elif seg.status == "empty":
            self._log(f"第 {seg.index + 1}/{total} 段完成 · 静音 · {seg.elapsed:.1f}s")
        else:
            self._log(f"第 {seg.index + 1}/{total} 段完成 · {seg.chars} 字 · {seg.elapsed:.1f}s")
        return seg

    def _process_all(self) -> None:
        pending = [s for s in self.segments if not s.finished]
        total = len(self.segments)
        if not pending:
            self._log("所有段都已完成，直接汇总")
            return

        self._log(f"开始转写：{len(pending)}/{total} 段待处理，并发 {self.project.params.concurrency}")
        workers = max(1, min(8, int(self.project.params.concurrency)))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for seg in pending:
                if self._stopped():
                    break
                futures[pool.submit(self._process_one, seg)] = seg

            for fut in as_completed(futures):
                try:
                    seg = fut.result()
                except Exception as exc:  # noqa: BLE001
                    seg = futures[fut]
                    seg.status = "failed"
                    seg.error = f"{type(exc).__name__}: {exc}"
                finished = sum(1 for s in self.segments if s.finished)
                if self.cb.on_segment:
                    self.cb.on_segment(seg, finished, total)
                self._maybe_flush()
                if self._aborted_reason:
                    self._log(self._aborted_reason)
                    break

    def _maybe_flush(self) -> None:
        finished = sum(1 for s in self.segments if s.finished)
        if finished - self._flush_mark >= FLUSH_EVERY:
            self._flush_mark = finished
            self._flush_outputs()

    def _flush_outputs(self) -> None:
        try:
            self.project.outputs = exporter.write_outputs(self.project, self.segments)
            exporter.write_manifest(self.project, self.segments)
            self._persist()
        except OSError as exc:
            self._log(f"写输出失败：{exc}")

    # ------------------------------------------------------------ 收尾
    def _finalize(self) -> None:
        self._set_status("merging")
        self.project.refresh_progress(self.segments)
        self.project.stats["elapsed_sec"] = round(time.time() - self._t0, 1)
        self.project.outputs = exporter.write_outputs(self.project, self.segments)
        exporter.write_manifest(self.project, self.segments)

        failed = self.project.progress.get("failed", 0)
        stopped = self._stop.is_set()
        if stopped and failed < len(self.segments):
            self.project.status = "canceled"
        elif failed:
            self.project.status = "partial"
        else:
            self.project.status = "done"

        self.project.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._persist()
        self._set_status(self.project.status)
        summary = (f"{self.project.status}：{self.project.progress.get('done', 0)} 段成功，"
                   f"{self.project.progress.get('empty', 0)} 段静音，"
                   f"{failed} 段失败，{self.project.stats['chars']} 字，"
                   f"用时 {self.project.stats['elapsed_sec']:.0f} 秒")
        self._log(summary)

    def _persist(self) -> None:
        try:
            os.makedirs(self.project.output_dir, exist_ok=True)
            from .store import ProjectStore
            ProjectStore.save_project_file(self.project)
        except OSError:
            pass

    # ------------------------------------------------------------ 供界面用
    def preview_text(self) -> str:
        """实时预览窗口的内容。"""
        lines: list[str] = []
        for seg in self.segments:
            lines.append(f"[{fmt_ts(seg.start)} – {fmt_ts(seg.end)}]")
            if seg.status == "failed":
                lines.append(f"（转写失败：{seg.error or '未知错误'}）")
            elif seg.status in ("done", "empty"):
                lines.append(seg.text if seg.text else exporter.EMPTY_PLACEHOLDER)
            else:
                lines.append("正在转写…")
            lines.append("")
        return "\n".join(lines)
