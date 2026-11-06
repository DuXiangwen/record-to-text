# -*- coding: utf-8 -*-
"""全局配置：config.json 的读写。原子写 + 缺字段自动补默认值。"""
from __future__ import annotations

import copy
import json
import os

from .exporter import atomic_write_json

DEFAULTS: dict = {
    "version": 3,
    "api": {
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "api_key": "",
        "model": "mimo-v2.5-asr",
        "timeout": 180,
        "max_retries": 3,
    },
    "defaults": {
        "segment_seconds": 90,
        "segment_mode": "silence",
        "concurrency": 4,
        "language": "auto",
        "formats": ["md", "txt"],
    },
    "segment": {
        "tolerance_ratio": 0.30,
        "prefetch_ratio": 0.60,
        "min_seg_ratio": 0.40,
        "silence_db": -35.0,
        "silence_min_sec": 0.4,
        "long_silence_sec": 1.5,
        "overlap_sec": 0.3,
    },
    "waveform": {
        "bucket_ms": 20,
        "sample_rate": 16000,
        "show_silence": True,
        "silence_db": -45.0,
        "silence_min_sec": 2.0,
    },
    "window": {"w": 1280, "h": 840, "x": 180, "y": 80},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.path = os.path.join(base_dir, "config.json")
        self.data = _deep_merge(DEFAULTS, self._read())

    def _read(self) -> dict:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def save(self) -> None:
        atomic_write_json(self.path, self.data)

    # 便捷访问
    def get(self, *keys, default=None):
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set(self, value, *keys) -> None:
        node = self.data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    # 常用分组
    @property
    def api(self) -> dict:
        return self.data["api"]

    @property
    def segment(self) -> dict:
        return self.data["segment"]

    @property
    def waveform(self) -> dict:
        return self.data["waveform"]

    def last_params(self) -> dict:
        """新建项目时带的参数：上次用过的值。"""
        return dict(self.data.get("defaults") or {})

    def remember_params(self, params: dict) -> None:
        """记住这次用的参数，下次新建带上。"""
        keep = {k: params[k] for k in
                ("segment_seconds", "segment_mode", "concurrency",
                 "language", "formats") if k in params}
        self.data.setdefault("defaults", {}).update(keep)
        self.save()
