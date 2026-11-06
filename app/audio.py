# -*- coding: utf-8 -*-
"""音频处理：探测、流式解码算 RMS、静音检测、切片规划、切割。

设计要点：
- 解码一律流式，不把整段 PCM 读进内存（5 小时录音会到 GB 级）
- 切片直接从源文件切，不做全片预转码
- 所有子进程参数走 list，不经过 shell，中文路径安全
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import numpy as np

SR = 16000                 # 统一采样率
BUCKET_MS = 20             # RMS 分块粒度
CHUNK = 1 << 20            # 每次从 ffmpeg 读 1MB

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_FFMPEG: str | None = None


def get_ffmpeg() -> str:
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    try:
        import imageio_ffmpeg
        _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        _FFMPEG = "ffmpeg"
    return _FFMPEG


def _popen(args: list[str], **kw) -> subprocess.Popen:
    return subprocess.Popen(
        args, creationflags=CREATE_NO_WINDOW,
        stdout=kw.pop("stdout", subprocess.PIPE),
        stderr=kw.pop("stderr", subprocess.PIPE), **kw)


# ---------------------------------------------------------------- 探测

def probe_info(path: str) -> dict:
    """读时长、采样率、声道数、编码。解析 ffmpeg 的 stderr。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    proc = _popen([get_ffmpeg(), "-hide_banner", "-i", path],
                  stdout=subprocess.DEVNULL)
    _, err = proc.communicate(timeout=120)
    text = err.decode("utf-8", errors="replace")

    info = {"duration": 0.0, "sample_rate": 0, "channels": 0, "codec": ""}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", text)
    if m:
        h, mi, s = m.groups()
        info["duration"] = int(h) * 3600 + int(mi) * 60 + float(s)
    m = re.search(r"Audio:\s*([\w\-]+).*?(\d+)\s*Hz,\s*([\w\.]+)", text)
    if m:
        info["codec"] = m.group(1)
        info["sample_rate"] = int(m.group(2))
        layout = m.group(3)
        info["channels"] = 1 if layout.startswith("mono") else 2
    if info["duration"] <= 0:
        raise RuntimeError(f"无法读取音频时长: {path}\n{text[-600:]}")
    return info


# ---------------------------------------------------------------- RMS / 静音

def compute_rms(path: str, sr: int = SR, bucket_ms: int = BUCKET_MS) -> tuple[np.ndarray, float]:
    """流式解码并计算每 bucket_ms 的 RMS 电平（dBFS）。

    返回 (db 数组, 时长秒)。内存峰值只有一个 1MB 块。
    """
    n = max(1, int(sr * bucket_ms / 1000))
    proc = _popen([get_ffmpeg(), "-hide_banner", "-v", "error",
                   "-i", path, "-ac", "1", "-ar", str(sr),
                   "-f", "s16le", "-"])
    chunks: list[np.ndarray] = []
    carry = b""
    total_blocks = 0
    try:
        while True:
            raw = proc.stdout.read(CHUNK)
            if not raw:
                break
            buf = carry + raw
            n_samples = len(buf) // 2
            n_blocks = n_samples // n
            if n_blocks == 0:
                carry = buf
                continue
            take = n_blocks * n * 2
            arr = np.frombuffer(buf[:take], dtype=np.int16).astype(np.float32) / 32768.0
            blocks = arr.reshape(n_blocks, n)
            chunks.append(np.sqrt((blocks ** 2).mean(axis=1)))
            total_blocks += n_blocks
            carry = buf[take:]
    finally:
        err = proc.stderr.read()
        proc.wait()

    if proc.returncode not in (0, None) and total_blocks == 0:
        raise RuntimeError(f"解码失败: {path}\n{err.decode('utf-8', 'replace')[-600:]}")

    if not chunks:
        return np.array([], dtype=np.float32), 0.0

    rms = np.concatenate(chunks)
    db = 20.0 * np.log10(np.maximum(rms, 1e-7))
    db = np.clip(db, -80.0, 0.0)
    duration = total_blocks * bucket_ms / 1000.0
    return db.astype(np.float32), duration


def find_silences(db: np.ndarray, bucket_ms: int = BUCKET_MS,
                  threshold_db: float = -35.0,
                  min_sec: float = 0.4) -> list[tuple[float, float]]:
    """把 dB 曲线里连续低于阈值的段挑出来，返回 [(start, end)] 秒。"""
    if db.size == 0:
        return []
    silent = db < threshold_db
    min_blocks = max(1, int(round(min_sec * 1000 / bucket_ms)))
    out: list[tuple[float, float]] = []
    i, n = 0, silent.size
    while i < n:
        if not silent[i]:
            i += 1
            continue
        j = i
        while j < n and silent[j]:
            j += 1
        if (j - i) >= min_blocks:
            out.append((i * bucket_ms / 1000.0, j * bucket_ms / 1000.0))
        i = j
    return out


# ---------------------------------------------------------------- 切片规划

def _clamp_cut(sil: tuple[float, float], target: float, margin: float = 0.2) -> float:
    """在静音区间内取最靠近 target 的位置，两端各让出 margin。"""
    s, e = sil
    lo, hi = s + margin, e - margin
    if hi <= lo:
        mid = (s + e) / 2.0
        return mid
    return min(max(target, lo), hi)


def plan_segments(db: np.ndarray, duration: float, seg_len: float,
                  mode: str = "silence", bucket_ms: int = BUCKET_MS,
                  tol_ratio: float = 0.30,
                  silence_db: float = -35.0,
                  silence_min_sec: float = 0.4,
                  long_silence_sec: float = 1.5,
                  prefetch_ratio: float = 0.60,
                  min_seg_ratio: float = 0.40) -> list[dict]:
    """规划切片，返回 [{"index", "start", "end"}]。

    优先级：
      1. 目标点正好落在静音里 → 原地切
      2. 容差窗口内（±seg_len×tol_ratio）有静音 → 吸附到最长的那个
      3. 窗口内没有，往前 prefetch 范围内有长静音 → 提前吸附
      4. 都没有 → 硬切
    """
    if duration <= 0:
        return []
    if duration <= seg_len or mode == "fixed" and duration <= seg_len:
        return [{"index": 0, "start": 0.0, "end": duration}]

    if mode == "fixed":
        segs, pos, idx = [], 0.0, 0
        while pos < duration - 1e-6:
            end = min(pos + seg_len, duration)
            segs.append({"index": idx, "start": pos, "end": end})
            idx += 1
            pos = end
        return segs

    all_sil = find_silences(db, bucket_ms, silence_db, silence_min_sec)
    long_sil = [s for s in all_sil if (s[1] - s[0]) >= long_silence_sec]

    delta = seg_len * tol_ratio
    prefetch = seg_len * prefetch_ratio
    min_seg = seg_len * min_seg_ratio

    cuts = [0.0]
    pos = 0.0
    guard = 0
    while pos < duration - 1e-6:
        guard += 1
        if guard > 100000:
            break
        target = pos + seg_len
        if target >= duration:
            cuts.append(duration)
            break

        cut = None

        # 优先级 2：容差窗口内的静音，选最长
        win = [s for s in all_sil if s[1] > target - delta and s[0] < target + delta]
        if win:
            best = max(win, key=lambda s: s[1] - s[0])
            cand = _clamp_cut(best, target)
            if cand - pos >= min_seg:
                cut = cand

        # 优先级 3：往前找长静音
        if cut is None and prefetch > 0:
            ahead = [s for s in long_sil
                     if s[1] <= target + 1e-6 and s[1] >= target - prefetch]
            if ahead:
                best = max(ahead, key=lambda s: s[1])
                cand = _clamp_cut(best, target)
                if cand - pos >= min_seg:
                    cut = cand

        # 优先级 4：硬切
        if cut is None:
            cut = target

        if cut <= pos + 0.5:
            cut = pos + seg_len

        cuts.append(cut)
        pos = cut

    if cuts[-1] < duration - 1e-6:
        cuts.append(duration)

    return [{"index": i, "start": cuts[i], "end": cuts[i + 1]}
            for i in range(len(cuts) - 1)]


def summarize_plan(segs: list[dict]) -> str:
    if not segs:
        return "无切片"
    durs = [s["end"] - s["start"] for s in segs]
    return (f"{len(segs)} 段，段长 {min(durs):.1f}–{max(durs):.1f} 秒，"
            f"平均 {sum(durs)/len(durs):.1f} 秒")


# ---------------------------------------------------------------- 切割

def cut_segment(src: str, out_path: str, start: float, dur: float,
                sr: int = SR, bitrate: str = "32k") -> str:
    """从源文件切一段并编码成 mp3。一次编码，不经过中间文件。"""
    if dur <= 0:
        raise ValueError(f"切片长度非法: {dur}")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    args = [get_ffmpeg(), "-y", "-hide_banner", "-v", "error",
            "-ss", f"{start:.3f}", "-i", src,
            "-t", f"{dur:.3f}", "-ar", str(sr), "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", bitrate, out_path]
    proc = _popen(args)
    _, err = proc.communicate(timeout=600)
    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError(f"切片失败 {start:.1f}+{dur:.1f}s: "
                           f"{err.decode('utf-8', 'replace')[-500:]}")
    return out_path


def make_silence_mp3(out_path: str, seconds: float = 2.0, sr: int = SR) -> str:
    """生成一段静音 mp3，用于测试 API 连通性。"""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    args = [get_ffmpeg(), "-y", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=mono",
            "-t", f"{seconds:.2f}", "-c:a", "libmp3lame", "-b:a", "32k", out_path]
    proc = _popen(args)
    _, err = proc.communicate(timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"生成测试音频失败: {err.decode('utf-8', 'replace')[-400:]}")
    return out_path
