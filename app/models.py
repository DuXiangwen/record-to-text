# -*- coding: utf-8 -*-
"""数据模型：项目、分段、参数。全部可 JSON 序列化，GUI 和内核共用。"""
from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

# 状态机
# pending → queued → slicing → transcribing → merging → done
#                                    ↘ partial / canceled
#         ↘ failed
STATUS_LABEL = {
    "pending": ("待开始", "gray"),
    "queued": ("排队中", "slate"),
    "slicing": ("切片中", "blue"),
    "transcribing": ("转换中", "blue"),
    "merging": ("合并中", "blue"),
    "done": ("已完成", "green"),
    "partial": ("部分失败", "amber"),
    "failed": ("失败", "red"),
    "canceled": ("已取消", "gray"),
}

RUNNING_STATUSES = ("slicing", "transcribing", "merging")
RESUMABLE_STATUSES = ("slicing", "transcribing", "merging", "partial", "failed", "canceled")

INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def fmt_ts(seconds: float) -> str:
    """秒 → 00:32:50"""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_dur(seconds: float) -> str:
    """秒 → 2:06:18，不足 1 小时省略小时位"""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def safe_name(name: str, max_len: int = 80) -> str:
    """清洗成合法文件夹名，保留中文。"""
    cleaned = INVALID_NAME_CHARS.sub("_", (name or "").strip())
    cleaned = cleaned.strip(" .")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    if not cleaned:
        cleaned = "未命名项目"
    return cleaned[:max_len]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_project_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return f"{stamp}-{random.randint(0x1000, 0xFFFF):04x}"


@dataclass
class Params:
    """一次转换的全部可调参数。项目创建后冻结，续跑时以此为准。"""

    segment_seconds: float = 90.0
    segment_mode: str = "silence"        # silence | fixed
    concurrency: int = 4
    language: str = "auto"               # auto | zh | en
    formats: list[str] = field(default_factory=lambda: ["md", "txt"])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in allowed})


@dataclass
class Segment:
    """一个切片及其转写结果。index 是唯一标识，续传只认它。"""

    index: int
    start: float          # 相对转写起点（已含选区偏移前的原始时间）
    end: float
    status: str = "pending"   # pending | done | empty | failed
    text: str = ""
    attempts: int = 0
    elapsed: float = 0.0
    error: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def chars(self) -> int:
        return len(self.text.replace("\n", ""))

    @property
    def finished(self) -> bool:
        return self.status in ("done", "empty")

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(duration=round(self.duration, 3), chars=self.chars)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class Project:
    id: str
    name: str
    source_path: str
    output_dir: str
    status: str = "pending"
    created_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    duration_sec: float = 0.0
    trim: dict | None = None             # {"start": 1970.0, "end": 4390.0}
    params: Params = field(default_factory=Params)
    progress: dict = field(default_factory=lambda: {"total": 0, "done": 0, "failed": 0, "empty": 0})
    stats: dict = field(default_factory=lambda: {"chars": 0, "elapsed_sec": 0.0})
    outputs: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def trim_start(self) -> float:
        return float((self.trim or {}).get("start", 0.0))

    @property
    def source_name(self) -> str:
        import os
        return os.path.basename(self.source_path)

    @property
    def content_duration(self) -> float:
        """实际要转写的时长（选区时是选区长度）。"""
        if self.trim:
            return max(0.0, float(self.trim["end"]) - float(self.trim["start"]))
        return self.duration_sec

    @property
    def percent(self) -> int:
        total = self.progress.get("total") or 0
        if total <= 0:
            return 0
        finished = self.progress.get("done", 0) + self.progress.get("failed", 0) + self.progress.get("empty", 0)
        return int(finished * 100 / total)

    def refresh_progress(self, segments: list[Segment]) -> None:
        self.progress = {
            "total": len(segments),
            "done": sum(1 for s in segments if s.status == "done"),
            "empty": sum(1 for s in segments if s.status == "empty"),
            "failed": sum(1 for s in segments if s.status == "failed"),
        }
        self.stats["chars"] = sum(s.chars for s in segments)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["params"] = self.params.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        data = dict(d)
        data["params"] = Params.from_dict(data.get("params") or {})
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})
