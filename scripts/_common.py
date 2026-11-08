#!/usr/bin/env python
"""DSHSOP 共享约定：项目根、忽略清单、文本读取。

规则唯一出处：本文件。pre-ci-review.py 与 review-state.py 都从这里取
「什么算改动」的口径——codebuddy-sop 里两套口径打架过一次：pytest 缓存、
coverage.xml、build.log 让「CI 前审核证据」误判为过期（审计 P1-1）。
这里只保留一份 ignore 清单，两个指纹器共用。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 工具产物与缓存：不是代码改动，不应让证据或轮次状态失效。
IGNORED_DIRS = {
    ".git",
    ".review",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    "out",
    "target",
    "coverage",
    "htmlcov",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".next",
    ".turbo",
    ".cache",
    ".idea",
    ".vscode",
    "site-packages",
}

IGNORED_FILES = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    ".coverage",
    "coverage.xml",
    "junit.xml",
}

IGNORED_SUFFIXES = {
    ".log",
    ".tmp",
    ".temp",
    ".swp",
    ".swo",
    ".pyc",
    ".pyo",
    ".class",
    ".o",
    ".obj",
}


def read_text(path) -> str:
    """BOM 容忍读取。

    Windows 编辑器与 PowerShell 默认给文本加 UTF-8 BOM；codebuddy-sop 的 agent
    解析器因此把合法文件判成「配置不可读」（审计 P1-4）。文档、配置一律走这里。
    """
    return Path(path).read_text(encoding="utf-8-sig")


def iter_files(root=ROOT, include_markdown: bool = True):
    """按稳定顺序遍历项目文件，产出 (相对路径 posix, Path)。"""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if name in IGNORED_FILES:
                continue
            suffix = path.suffix.lower()
            if suffix in IGNORED_SUFFIXES:
                continue
            if not include_markdown and suffix == ".md":
                continue
            yield path.relative_to(root).as_posix(), path


def fingerprint(root=ROOT) -> str:
    """审核覆盖面（全部非忽略文件，含 markdown）的内容指纹。"""
    root = Path(root)
    digest = hashlib.sha256()
    for relative, path in iter_files(root, include_markdown=True):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
