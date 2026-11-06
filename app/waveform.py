# -*- coding: utf-8 -*-
"""波形数据：带磁盘缓存的 RMS 曲线。

同一次解码的结果同时供两处使用：
  - 界面画波形（阈值 -45dB / 2s 的静音底纹）
  - 切片规划（阈值 -35dB / 0.4s 的静音区间）
"""
from __future__ import annotations

import hashlib
import os

import numpy as np

from . import audio

CACHE_VERSION = 1


def cache_key(source_path: str, bucket_ms: int, sr: int) -> str:
    st = os.stat(source_path)
    raw = f"{os.path.abspath(source_path)}|{st.st_size}|{int(st.st_mtime)}|{bucket_ms}|{sr}|v{CACHE_VERSION}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def peaks_path(cache_dir: str, key: str) -> str:
    return os.path.join(cache_dir, "peaks", f"{key}.npz")


def load_peaks(source_path: str, cache_dir: str,
               bucket_ms: int = audio.BUCKET_MS,
               sr: int = audio.SR,
               progress=None) -> tuple[np.ndarray, float]:
    """返回 (db 数组, 时长秒)。优先读缓存，没有再解码。"""
    key = cache_key(source_path, bucket_ms, sr)
    path = peaks_path(cache_dir, key)

    if os.path.isfile(path):
        try:
            with np.load(path) as data:
                return data["db"].astype(np.float32), float(data["duration"])
        except (OSError, ValueError, KeyError):
            pass

    if progress:
        progress("正在解析波形…")
    db, duration = audio.compute_rms(source_path, sr=sr, bucket_ms=bucket_ms)

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        np.savez_compressed(path, db=db.astype(np.float16),
                            duration=np.float64(duration))
    except OSError:
        pass
    return db, duration


def slice_view(db: np.ndarray, start_sec: float, end_sec: float,
               bucket_ms: int = audio.BUCKET_MS) -> np.ndarray:
    """按时间取子数组，用于选区内的切片规划。"""
    i0 = max(0, int(round(start_sec * 1000.0 / bucket_ms)))
    i1 = min(db.size, int(round(end_sec * 1000.0 / bucket_ms)))
    if i1 <= i0:
        return db[:0]
    return db[i0:i1]


def clear_cache(cache_dir: str) -> int:
    """清空波形缓存，返回删除的文件数。"""
    folder = os.path.join(cache_dir, "peaks")
    if not os.path.isdir(folder):
        return 0
    count = 0
    for name in os.listdir(folder):
        try:
            os.remove(os.path.join(folder, name))
            count += 1
        except OSError:
            pass
    return count


def cache_size(cache_dir: str) -> int:
    folder = os.path.join(cache_dir, "peaks")
    if not os.path.isdir(folder):
        return 0
    total = 0
    for name in os.listdir(folder):
        try:
            total += os.path.getsize(os.path.join(folder, name))
        except OSError:
            pass
    return total
