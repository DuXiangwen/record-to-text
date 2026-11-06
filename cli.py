# -*- coding: utf-8 -*-
"""命令行入口：不开界面直接跑一次转写，用于验证内核。

    python cli.py "录音.m4a" --name 测试 --segment 90
    python cli.py "录音.m4a" --name 测试 --start 1970 --end 4390
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from app.config import Config
from app.models import Params, Project, fmt_dur, fmt_ts, new_project_id, safe_name
from app.pipeline import Callbacks, TranscribeJob
from app.store import ProjectStore, unique_dir

BASE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser(description="录音转文本 · 命令行")
    ap.add_argument("source", help="录音文件路径")
    ap.add_argument("--name", help="项目名，默认取文件名")
    ap.add_argument("--segment", type=float, help="切片长度（秒）")
    ap.add_argument("--mode", choices=["silence", "fixed"], help="切片方式")
    ap.add_argument("--concurrency", type=int, help="并发数")
    ap.add_argument("--language", choices=["auto", "zh", "en"], help="语言")
    ap.add_argument("--start", type=float, help="转写起点（秒）")
    ap.add_argument("--end", type=float, help="转写终点（秒）")
    ap.add_argument("--out", help="输出根目录，默认建在录音同级")
    ap.add_argument("--quiet", action="store_true", help="只打关键行")
    args = ap.parse_args()

    cfg = Config(BASE)
    if not cfg.api.get("api_key"):
        print("× 还没配置 API Key。先编辑 config.json 里的 api.api_key，或用界面设置。")
        return 2

    src = os.path.abspath(args.source)
    if not os.path.isfile(src):
        print(f"× 文件不存在：{src}")
        return 2

    defaults = cfg.last_params()
    params = Params(
        segment_seconds=float(args.segment or defaults.get("segment_seconds", 90)),
        segment_mode=args.mode or defaults.get("segment_mode", "silence"),
        concurrency=int(args.concurrency or defaults.get("concurrency", 4)),
        language=args.language or defaults.get("language", "auto"),
        formats=list(defaults.get("formats", ["md", "txt"])),
    )

    name = safe_name(args.name or os.path.splitext(os.path.basename(src))[0])
    parent = os.path.abspath(args.out) if args.out else os.path.dirname(src)
    out_dir = unique_dir(parent, name)

    project = Project(
        id=new_project_id(), name=name, source_path=src, output_dir=out_dir,
        params=params, status="pending")
    if args.start is not None and args.end is not None:
        project.trim = {"start": float(args.start), "end": float(args.end)}

    print(f"项目    {name}")
    print(f"源文件  {src}")
    print(f"输出    {out_dir}")
    print(f"参数    {params.segment_seconds:g}s / {params.segment_mode} / "
          f"并发 {params.concurrency} / {params.language}")
    print("-" * 60)

    def on_status(status: str) -> None:
        print(f"[状态] {status}")

    def on_segment(seg, finished: int, total: int) -> None:
        tail = f"{seg.text[:28]}…" if len(seg.text) > 28 else (seg.text or "(空)")
        tail = tail.replace("\n", " ")
        print(f"[{finished:>3}/{total}] {seg.index + 1:>3}  "
              f"{fmt_ts(seg.start)}–{fmt_ts(seg.end)}  "
              f"{seg.status:<6} {seg.elapsed:>5.1f}s  {tail}")

    cb = Callbacks(on_status=None if args.quiet else on_status,
                   on_segment=on_segment,
                   on_log=None if args.quiet else print)
    job = TranscribeJob(project, cfg, cb)

    t0 = time.time()
    try:
        ok = job.run()
    except Exception as exc:  # noqa: BLE001
        print(f"\n× 失败：{exc}")
        return 1

    print("-" * 60)
    p = project.progress
    print(f"完成    {p.get('done', 0)} 段成功 / {p.get('empty', 0)} 段静音 / "
          f"{p.get('failed', 0)} 段失败，共 {p.get('total', 0)} 段")
    print(f"字数    {project.stats.get('chars', 0)}")
    print(f"耗时    {time.time() - t0:.1f} 秒")
    print(f"内容    {fmt_dur(project.content_duration)}")
    for f in project.outputs:
        print(f"输出    {os.path.join(out_dir, f)}")

    ProjectStore.save_project_file(project)
    cfg.remember_params(params.to_dict())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
