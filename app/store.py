# -*- coding: utf-8 -*-
"""项目索引 projects.json + 单项目 project.json 的读写。"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes

from .exporter import atomic_write_json
from .models import Project

PROJECT_FILE = "project.json"
LOCK_FILE = "app.lock"


# ---------------------------------------------------------------- 回收站
class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def send_to_recycle_bin(path: str) -> bool:
    """删文件夹走回收站，不直接抹掉。非 Windows 直接返回 False 交给上层处理。"""
    if sys.platform != "win32" or not os.path.exists(path):
        return False
    from_path = os.path.abspath(path) + "\0\0"
    op = _SHFILEOPSTRUCTW(
        None, 3, from_path, None,          # FO_DELETE
        0x0040 | 0x0010 | 0x0004 | 0x0400,  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
        False, None, None)
    try:
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 存储
class ProjectStore:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.path = os.path.join(base_dir, "projects.json")
        self.projects: list[Project] = []
        self.load()

    def load(self) -> None:
        if not os.path.isfile(self.path):
            self.projects = []
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            self.projects = []
            return
        items = data.get("projects", []) if isinstance(data, dict) else []
        self.projects = []
        for item in items:
            try:
                self.projects.append(Project.from_dict(item))
            except TypeError:
                continue
        self._sort()

    def save(self) -> None:
        atomic_write_json(self.path, {
            "version": 3,
            "projects": [p.to_dict() for p in self.projects],
        })

    def _sort(self) -> None:
        self.projects.sort(key=lambda p: p.created_at, reverse=True)

    # ------------------------------------------------------------ CRUD
    def add(self, project: Project) -> Project:
        self.projects.insert(0, project)
        self.save()
        return project

    def find(self, project_id: str) -> Project | None:
        for p in self.projects:
            if p.id == project_id:
                return p
        return None

    def update(self, project: Project) -> None:
        for i, p in enumerate(self.projects):
            if p.id == project.id:
                self.projects[i] = project
                break
        else:
            self.projects.insert(0, project)
        self._sort()
        self.save()

    def remove(self, project_id: str, delete_files: bool = False) -> tuple[bool, str]:
        """移除卡片。delete_files=True 时把整个项目文件夹送进回收站。"""
        project = self.find(project_id)
        if not project:
            return False, "找不到这个项目"
        if delete_files and os.path.isdir(project.output_dir):
            if not send_to_recycle_bin(project.output_dir):
                return False, "移入回收站失败，文件未删除"
        self.projects = [p for p in self.projects if p.id != project_id]
        self.save()
        return True, "已移除"

    # ------------------------------------------------------------ 单项目文件
    @staticmethod
    def save_project_file(project: Project) -> str:
        path = os.path.join(project.output_dir, PROJECT_FILE)
        atomic_write_json(path, project.to_dict())
        return path

    @staticmethod
    def load_project_file(output_dir: str) -> Project | None:
        path = os.path.join(output_dir, PROJECT_FILE)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return Project.from_dict(json.load(f) or {})
        except (OSError, ValueError, TypeError):
            return None


# ---------------------------------------------------------------- 目录规划
def unique_dir(parent: str, name: str) -> str:
    """重名自动加 _2、_3。"""
    candidate = os.path.join(parent, name)
    if not os.path.exists(candidate):
        return candidate
    i = 2
    while True:
        candidate = os.path.join(parent, f"{name}_{i}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def writable_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False
