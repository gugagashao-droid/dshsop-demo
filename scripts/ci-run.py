#!/usr/bin/env python
"""CI 阶段编排（平台无关）。

为什么存在：GitHub Actions workflow 只管阶段顺序，命令放在 ci.json。
换技术栈不改 workflow，换 CI 平台不改本脚本——两边都不用重做。

规则唯一出处：本文件。命令清单里任何一项失败即停，整段才算通过；
留有占位符时直接判不可读，不让「没填」伪装成通过。

@相对路径 参数：执行前替换成该文件内容（strip）。CI 里的 --round / --important
是上一步算出来的，写死会让下一轮记账失败；文件缺失或为空直接判不可读。

退出码：0 通过；1 命令失败；5 自检失败；6 清单不可读。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

STAGES = ("machine", "platform", "ai")
PLACEHOLDER = re.compile(r"<[^>]*>")


class Unreadable(Exception):
    pass


def load(root: Path) -> dict:
    path = root / "ci.json"
    if not path.is_file():
        raise Unreadable("ci.json 不存在：项目差异没有登记")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Unreadable(f"ci.json 不可读：{exc}") from exc
    if not isinstance(data, dict):
        raise Unreadable("ci.json 顶层必须是对象")
    return data


def expand_args(cmd: list, root: Path) -> list[str]:
    argv = []
    for part in cmd:
        text = str(part)
        if not text.startswith("@"):
            argv.append(text)
            continue
        # 不让空值伪装成 0：缺文件、空文件都必须是硬失败。
        path = root / text[1:]
        if not path.is_file():
            raise Unreadable(f"参数文件 {text[1:]} 不存在")
        value = path.read_text(encoding="utf-8-sig").strip()
        if not value:
            raise Unreadable(f"参数文件 {text[1:]} 是空的")
        argv.append(value)
    return argv


def run_stage(root: Path, data: dict, stage: str) -> int:
    items = data.get(stage)
    if not isinstance(items, list) or not items:
        raise Unreadable(f"ci.json 的 {stage} 阶段没有登记命令")

    for item in items:
        if not isinstance(item, dict):
            raise Unreadable(f"{stage} 阶段有不是对象的条目")
        name = item.get("name", "(未命名)")
        cmd = item.get("cmd")
        # 占位符不执行：让「没填」明确变红，而不是报一个看不懂的命令不存在。
        if not isinstance(cmd, list) or not cmd or any(PLACEHOLDER.search(str(x)) for x in cmd):
            raise Unreadable(f"{name} 的命令仍是占位符或未填")

        argv = expand_args(cmd, root)
        print(f"[ci] === {name} ===")
        print(f"[ci] $ {' '.join(argv)}")
        result = subprocess.run(argv, cwd=root, check=False)
        if result.returncode != 0:
            print(f"[ci] FAIL: {name} 退出码 {result.returncode}")
            return 1
    print(f"[ci] OK: {stage} 阶段 {len(items)} 项全过")
    return 0


def run(root: Path, stage: str) -> int:
    try:
        data = load(root)
        if stage == "all":
            for name in STAGES:
                code = run_stage(root, data, name)
                if code != 0:
                    return code
            return 0
        if stage not in STAGES:
            raise Unreadable(f"未知阶段 {stage}")
        return run_stage(root, data, stage)
    except (Unreadable, OSError) as exc:
        print(f"[ci] FAIL: {exc}")
        return 6


def self_test() -> int:
    cases: list[tuple[str, int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cases.append(("缺 ci.json", run(root, "machine"), 6))

        def write(data):
            (root / "ci.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        write({"machine": [{"name": "未填", "cmd": ["<填写：命令>"]}]})
        cases.append(("命令仍是占位符", run(root, "machine"), 6))

        write({"machine": []})
        cases.append(("阶段没有条目", run(root, "machine"), 6))

        write({"machine": [{"name": "失败项", "cmd": [sys.executable, "-c", "raise SystemExit(3)"]}]})
        cases.append(("命令失败", run(root, "machine"), 1))

        (root / "count.txt").write_text("7", encoding="utf-8")
        write({"machine": [{"name": "展开参数", "cmd": [
            sys.executable, "-c", "import sys; raise SystemExit(0 if sys.argv[1]=='7' else 3)", "@count.txt",
        ]}]})
        cases.append(("@file 展开正确", run(root, "machine"), 0))

        write({"machine": [{"name": "参数文件缺失", "cmd": [sys.executable, "-c", "pass", "@missing.txt"]}]})
        cases.append(("参数文件缺失", run(root, "machine"), 6))

        (root / "empty.txt").write_text("  \n", encoding="utf-8")
        write({"machine": [{"name": "参数文件为空", "cmd": [sys.executable, "-c", "pass", "@empty.txt"]}]})
        cases.append(("参数文件为空", run(root, "machine"), 6))

        write({
            "machine": [{"name": "通过项", "cmd": [sys.executable, "-c", "print('OK')"]}],
            "platform": [{"name": "通过项", "cmd": [sys.executable, "-c", "print('OK')"]}],
            "ai": [{"name": "通过项", "cmd": [sys.executable, "-c", "print('OK')"]}],
        })
        cases.append(("三项全绿", run(root, "all"), 0))
        cases.append(("未知阶段", run(root, "nope"), 6))

    failures = [f"{label}: 期望 {expected}，实际 {actual}" for label, actual, expected in cases if actual != expected]
    if failures:
        print("[ci] self-test FAILED:")
        for failure in failures:
            print("  - " + failure)
        return 5
    print(f"[ci] self-test OK ({len(cases)} cases)")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "self-test":
        return self_test()

    root = Path(__file__).resolve().parent.parent
    stage = "all"
    i = 0
    while i < len(argv):
        if argv[i] == "--stage" and i + 1 < len(argv):
            stage = argv[i + 1]
            i += 2
        elif argv[i] == "--root" and i + 1 < len(argv):
            root = Path(argv[i + 1])
            i += 2
        else:
            print(f"[ci] FAIL: 未知参数 {argv[i]}")
            return 6
    return run(root, stage)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
