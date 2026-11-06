# -*- coding: utf-8 -*-
"""输出：片段文件、合并 md/txt、manifest。

时间一律用原始录音时间轴（选区已加过偏移），所以这里不需要再算偏移。
"""
from __future__ import annotations

import json
import os

from .models import Segment, fmt_dur, fmt_ts

EMPTY_PLACEHOLDER = "（本段无识别内容，疑似静音）"
SEG_TIME_FMT = "%03d_%s"


def atomic_write(path: str, text: str, encoding: str = "utf-8") -> None:
    """先写临时文件再替换，避免中途崩溃留下半个文件。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_json(path: str, obj) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2))


def seg_basename(seg: Segment) -> str:
    """001_00-32-50 —— 时间只用于给人看，续传认 index。"""
    stamp = fmt_ts(seg.start).replace(":", "-")
    return SEG_TIME_FMT % (seg.index + 1, stamp)


def write_segment_files(seg_dir: str, seg: Segment) -> None:
    """每段写两个文件：给人看的 txt + 续传用的 json。"""
    os.makedirs(seg_dir, exist_ok=True)
    base = os.path.join(seg_dir, seg_basename(seg))
    body = seg.text if seg.text else EMPTY_PLACEHOLDER
    if seg.status == "failed":
        body = f"（本段转写失败：{seg.error or '未知错误'}）"
    atomic_write(base + ".txt", body + "\n")
    atomic_write_json(base + ".json", seg.to_dict())


def remove_segment_files(seg_dir: str, seg: Segment) -> None:
    base = os.path.join(seg_dir, seg_basename(seg))
    for ext in (".txt", ".json"):
        try:
            os.remove(base + ext)
        except OSError:
            pass


def _header(project, segments: list[Segment], completed_all: bool) -> list[str]:
    p = project.params
    mode = "静音吸附" if p.segment_mode == "silence" else "定长硬切"
    if project.trim:
        span = f"{fmt_ts(project.trim_start)} – {fmt_ts(project.trim['end'])}（选区）"
    else:
        span = f"全片 {fmt_ts(0)} – {fmt_ts(project.duration_sec)}"

    lines = [
        f"# {project.name}",
        "",
        f"- 源文件：{project.source_name}",
        f"- 转写范围：{span}",
        f"- 转写时长：{fmt_dur(project.content_duration)}",
        f"- 分段：{len(segments)} 段（{mode}，目标 {p.segment_seconds:g} 秒）",
        f"- 转写：MiMo-V2.5-ASR / language={p.language}",
        f"- 生成时间：{project.created_at[:19].replace('T', ' ')}",
    ]
    if not completed_all:
        finished = sum(1 for s in segments if s.finished)
        lines.insert(1, f"> 本文件未完成：{len(segments)} 段中已完成 {finished} 段，"
                        f"可右键卡片「继续」补跑。")
        lines.insert(2, "")
    lines += ["", "---", ""]
    return lines


def build_md(project, segments: list[Segment]) -> str:
    completed_all = all(s.finished for s in segments) if segments else False
    lines = _header(project, segments, completed_all)
    for seg in segments:
        lines.append(f"## 第 {seg.index + 1} 段 · {fmt_ts(seg.start)} – {fmt_ts(seg.end)}")
        lines.append("")
        if seg.status == "failed":
            lines.append(f"_（本段转写失败：{seg.error or '未知错误'}）_")
        elif seg.status in ("done", "empty"):
            lines.append(seg.text if seg.text else f"_{EMPTY_PLACEHOLDER}_")
        else:
            lines.append("_（尚未转写）_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_txt(project, segments: list[Segment]) -> str:
    lines: list[str] = []
    for seg in segments:
        lines.append(f"[{fmt_ts(seg.start)} – {fmt_ts(seg.end)}]")
        if seg.status == "failed":
            lines.append(f"（本段转写失败：{seg.error or '未知错误'}）")
        elif seg.status in ("done", "empty"):
            lines.append(seg.text if seg.text else EMPTY_PLACEHOLDER)
        else:
            lines.append("（尚未转写）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(project, segments: list[Segment]) -> list[str]:
    """写合并产物，返回相对路径列表。可以反复调用（增量刷新）。"""
    written: list[str] = []
    if "md" in project.params.formats:
        name = f"{project.name}_全文.md"
        atomic_write(os.path.join(project.output_dir, name), build_md(project, segments))
        written.append(name)
    if "txt" in project.params.formats:
        name = f"{project.name}_全文.txt"
        atomic_write(os.path.join(project.output_dir, name), build_txt(project, segments))
        written.append(name)
    return written


def write_manifest(project, segments: list[Segment]) -> str:
    payload = {
        "project": project.id,
        "name": project.name,
        "source": project.source_path,
        "trim": project.trim,
        "params": project.params.to_dict(),
        "duration_sec": round(project.duration_sec, 3),
        "total": len(segments),
        "segments": [s.to_dict() for s in segments],
    }
    path = os.path.join(project.output_dir, "manifest.json")
    atomic_write_json(path, payload)
    return path


# ---------------------------------------------------------------- 续传索引

def scan_segments(seg_dir: str) -> dict[int, Segment]:
    """扫描 segments 目录，按 index 建立已完成段的索引。"""
    found: dict[int, Segment] = {}
    if not os.path.isdir(seg_dir):
        return found
    for name in os.listdir(seg_dir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(seg_dir, name), encoding="utf-8") as f:
                data = json.load(f)
            seg = Segment.from_dict(data)
        except (OSError, ValueError, TypeError):
            continue
        if seg.status in ("done", "empty"):
            found[seg.index] = seg
    return found


def clean_segments_dir(seg_dir: str) -> None:
    """重新切片前清空旧的片段文件（参数变了，旧结果没意义）。"""
    if not os.path.isdir(seg_dir):
        return
    for name in os.listdir(seg_dir):
        if name.endswith((".txt", ".json")):
            try:
                os.remove(os.path.join(seg_dir, name))
            except OSError:
                pass


def load_manifest_segments(project) -> list[Segment]:
    """从 manifest.json 读回完整分段列表（含失败段），供预览窗口使用。"""
    path = os.path.join(project.output_dir, "manifest.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return []
    out: list[Segment] = []
    for item in data.get("segments", []):
        try:
            out.append(Segment.from_dict(item))
        except TypeError:
            continue
    out.sort(key=lambda s: s.index)
    return out
