#!/usr/bin/env python
"""DSH 本地门禁（① 段 + 证据校验）。

替代 codebuddy-sop 的 gate.ps1：Python 实现，跨平台、无 BOM、可用单元测试覆盖。
规则唯一出处：本文件。

语义（继承 codebuddy-sop 踩出来的三条，来自真实假绿事故）：
1. skip = FAIL：命令缺失不等于通过。
2. warn 只表示「项目本来就没配这一项」，必须打印，且不能撑起「有代码检查」。
3. ran == 0（一步都没执行）必须 FAIL，不能打印 GATE PASS。

用法：
    python scripts/gate.py                 # 完整门禁（项目命令 + 一致性检查）
    python scripts/gate.py --checks-only   # 只跑文档/结构/证据/模型分离（本地用）
    python scripts/gate.py --ci            # 同上去掉「本地证据」（CI 跑，证据只在本地）
    python scripts/gate.py --root DIR      # 指定项目根（自检用）

环境变量：
    REVIEW_SKIP=1  由 .githooks/pre-commit 在「只有普通文档改动」时设置，
                   只跳过「CI 前独立审核证据」一步，其余照跑。

退出码：0 全绿；1 有失败；5 自检失败；6 项目根不可读。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

DEFAULT_ROOT = _common.ROOT
CHECK_NAMES = ("lint", "typecheck", "test", "build")
CODE_CHECK_NAMES = ("lint", "typecheck", "test")
STEP_TIMEOUT = 900


class Gate:
    def __init__(self, root: Path):
        self.root = root
        self.total = 0
        self.fail = 0
        self.ran = 0

    # ---------- 记账 ----------
    def _header(self, label: str) -> None:
        self.total += 1
        print(f"\n=== [{self.total}] {label} ===")

    def warn(self, label: str, hint: str) -> None:
        self._header(label)
        print(f"  -> WARN: 未配置（{hint}）")

    def skip(self, label: str, hint: str) -> None:
        self._header(label)
        self.fail += 1
        print(f"  -> FAIL: 未执行（{hint}）。缺命令不等于通过。")

    def run(self, label: str, argv: list[str] | None, hint: str = "") -> None:
        if not argv:
            self.skip(label, hint or "没有可执行的命令")
            return
        exe = argv[0]
        resolved = shutil.which(exe)
        if resolved is None and Path(exe).is_file():
            resolved = str(Path(exe).resolve())
        if resolved is None:
            self.skip(label, f"命令 '{exe}' 无法解析")
            return
        self._header(label)
        self.ran += 1
        argv = [resolved, *argv[1:]]
        try:
            result = subprocess.run(argv, cwd=self.root, check=False, timeout=STEP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.fail += 1
            print(f"  -> FAIL: {label} 超过 {STEP_TIMEOUT} 秒未结束")
            return
        except OSError as exc:
            self.fail += 1
            print(f"  -> FAIL: {label} 无法启动：{exc}")
            return
        if result.returncode != 0:
            self.fail += 1
            print(f"  -> FAIL: {label}（退出码 {result.returncode}）")

    # ---------- 项目命令 ----------
    def load_commands(self) -> dict[str, list[str]] | None:
        """返回 {步骤: argv}；None 表示项目形态无法识别。"""
        configured = self._configured_commands()
        if configured is not None:
            return configured
        pkg = self.root / "package.json"
        if pkg.is_file():
            return self._node_commands(pkg)
        if (self.root / "pyproject.toml").is_file() or (self.root / "requirements.txt").is_file():
            return self._python_commands()
        return None

    def _configured_commands(self) -> dict[str, list[str]] | None:
        sop = self.root / "sop.json"
        if not sop.is_file():
            return None
        try:
            data = json.loads(_common.read_text(sop))
        except (OSError, ValueError) as exc:
            print(f"\nGATE FAIL: sop.json 不可读：{exc}")
            raise SystemExit(1)
        commands = data.get("commands") if isinstance(data, dict) else None
        if not isinstance(commands, dict) or not commands:
            return None
        out: dict[str, list[str]] = {}
        for name in CHECK_NAMES:
            value = commands.get(name)
            if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
                out[name] = value
        return out

    def _node_commands(self, pkg_path: Path) -> dict[str, list[str]]:
        try:
            pkg = json.loads(_common.read_text(pkg_path))
        except (OSError, ValueError) as exc:
            print(f"\nGATE FAIL: package.json 不可读：{exc}")
            raise SystemExit(1)
        scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
        script_names = list(scripts) if isinstance(scripts, dict) else []
        manager = "npm"
        if (self.root / "pnpm-lock.yaml").is_file():
            manager = "pnpm"
        elif (self.root / "yarn.lock").is_file():
            manager = "yarn"
        if shutil.which(manager) is None:
            manager = "npm" if shutil.which("npm") else manager

        out: dict[str, list[str]] = {}
        for name in CHECK_NAMES:
            if name in script_names:
                out[name] = [manager, "run", name]
        return out

    def _python_commands(self) -> dict[str, list[str]]:
        python = self._python_executable()
        out: dict[str, list[str]] = {}
        if python:
            out["test"] = [python, "-m", "pytest", "-q"]
        return out

    def _python_executable(self) -> str | None:
        for candidate in (
            self.root / ".venv" / "Scripts" / "python.exe",
            self.root / "venv" / "Scripts" / "python.exe",
            self.root / ".venv" / "bin" / "python",
            self.root / "venv" / "bin" / "python",
        ):
            if candidate.is_file():
                return str(candidate)
        for name in ("python", "python3", "py"):
            found = shutil.which(name)
            if found:
                return found
        return None

    # ---------- 门禁主体 ----------
    def project_steps(self) -> int:
        commands = self.load_commands()
        if commands is None:
            print("\nGATE FAIL: 没检测到 package.json / pyproject.toml，sop.json 也没有 commands")
            return 1
        for name in CHECK_NAMES:
            if name in commands:
                self.run(name, commands[name])
            else:
                self.warn(name, f"项目没有配置 {name}")
        if not any(name in commands for name in CODE_CHECK_NAMES):
            self._header("代码检查")
            self.fail += 1
            print("  -> FAIL: lint / typecheck / test 一个都没有，代码没有任何检查（build 不算）")
        return 0

    def check_steps(self, include_pre_ci: bool = True) -> None:
        scripts = self.root / "scripts"
        self.run("文档一致性", self._script("check-docs.py", scripts))
        self.run("结构契约", self._script("check-structure.py", scripts))
        if not include_pre_ci:
            print("\n=== [skip] CI 前独立审核 —— 证据只在本地，CI 无法验证（已知限制，见 README） ===")
        elif os.environ.get("REVIEW_SKIP") == "1":
            print("\n=== [skip] CI 前独立审核 —— 本次只有普通文档改动 ===")
        else:
            self.run("CI 前独立审核", self._script("pre-ci-review.py", scripts, "check"))
        self.run("模型分离", self._script("check-model-separation.py", scripts))

    def _script(self, name: str, scripts: Path, *args: str) -> list[str] | None:
        path = scripts / name
        if not path.is_file():
            return None
        return [sys.executable, str(path), *args]

    def verdict(self) -> int:
        print()
        if self.ran == 0:
            print("GATE FAIL: 没有任何一步真正执行（全部跳过或未配置，不是通过）")
            return 1
        if self.fail == 0:
            print(f"GATE PASS: {self.total}/{self.total}")
            return 0
        print(f"GATE FAIL: {self.fail} of {self.total} failed")
        return 1


def gate(root: Path, mode: str = "full") -> int:
    instance = Gate(root)
    if mode == "full":
        code = instance.project_steps()
        if code != 0:
            return code
    instance.check_steps(include_pre_ci=mode != "ci")
    return instance.verdict()


def self_test() -> int:
    """反例优先：先证明「没检查时必须红」，再证明「检查存在时会跑」。"""
    results: list[tuple[str, int, int]] = []
    scripts = Path(__file__).resolve().parent

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def write(relative: str, text: str) -> None:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        # 1) 空目录：必须红
        results.append(("空目录", gate(root), 1))

        # 2) Node 项目没有任何代码检查：必须红
        write("package.json", json.dumps({"scripts": {"dev": "node -e \"\""}}))
        results.append(("无代码检查", gate(root), 1))

        # 3) 显式的检查命令不存在：必须红（在临时项目内调用本脚本会缺 check-docs 等，同样红）
        write("sop.json", json.dumps({"commands": {"lint": ["definitely-not-a-real-binary"]}}))
        results.append(("显式命令无法解析", gate(root), 1))

        # 4) 模板原样：必须红（占位符未填）
        template = Path(__file__).resolve().parent.parent
        copy_root = root / "template-copy"
        copy_root.mkdir()
        for relative, source in _walk_template(template):
            target = copy_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        results.append(("模板原样", gate(copy_root), 1))
        results.append(("模板原样 --checks-only", gate(copy_root, mode="checks"), 1))
        results.append(("模板原样 --ci", gate(copy_root, mode="ci"), 1))

    failures = [
        f"{label}: 期望 {expected}，实际 {actual}"
        for label, actual, expected in results
        if actual != expected
    ]
    if failures:
        print("[gate] self-test FAILED:")
        for failure in failures:
            print("  - " + failure)
        return 5
    print(f"[gate] self-test OK ({len(results)} cases)")
    return 0


def _walk_template(template: Path):
    for relative, path in _common.iter_files(template):
        if relative.startswith("tests/"):
            continue
        yield relative, path


def main(argv: list[str]) -> int:
    if argv and argv[0] == "self-test":
        return self_test()
    root = DEFAULT_ROOT
    mode = "full"
    index = 0
    while index < len(argv):
        if argv[index] == "--root" and index + 1 < len(argv):
            root = Path(argv[index + 1])
            index += 2
        elif argv[index] == "--checks-only":
            mode = "checks"
            index += 1
        elif argv[index] == "--ci":
            mode = "ci"
            index += 1
        else:
            print(f"[gate] FAIL: 未知参数 {argv[index]}")
            return 6
    if not root.is_dir():
        print(f"[gate] FAIL: 项目根不存在：{root}")
        return 6
    return gate(root, mode)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
