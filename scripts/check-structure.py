#!/usr/bin/env python
"""通用代码结构门禁。

架构契约在 ARCHITECTURE.md；本脚本不预设语言或目录，
只检查契约是否完整、模块路径是否真实存在、依赖是否闭合，
并执行项目自己登记的结构检查命令。

退出码：0 通过；1 契约或结构检查失败。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

try:
    CHECK_DEPTH = int(os.environ.get("STRUCTURE_CHECK_DEPTH", "0"))
except ValueError:
    CHECK_DEPTH = 2

if CHECK_DEPTH >= 2:
    print("[structure] 递归调用已阻止；结构检查不得通过 gate 或包装脚本反复嵌套")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE = ROOT / "ARCHITECTURE.md"
ANGLE = re.compile(r"(?<![\w>])<([^<>\n]{1,160})>")
VAGUE_VALUES = {
    "n/a",
    "na",
    "none",
    "null",
    "无",
    "暂无",
    "待定",
    "tbd",
    "todo",
}
REQUIRED_SECTIONS = (
    "项目环境",
    "模块",
    "依赖",
    "边界",
    "扩展点",
    "架构不变量",
    "结构检查",
)

problems: list[str] = []


def section_of(text: str, title: str) -> str | None:
    pattern = re.compile(
        r"^##\s+" + re.escape(title) + r"\s*$(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1) if match else None


def split_row(line: str) -> list[str]:
    # 表格单元格里允许反引号代码包含 |（grep 交替、正则等），也允许 \| 转义。
    content = line.strip()
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|") and not content.endswith("\\|"):
        content = content[:-1]

    cells: list[str] = []
    buffer: list[str] = []
    in_code = False
    escaped = False
    for char in content:
        if escaped:
            if char in {"|", "\\"}:
                buffer.append(char)
            else:
                buffer.extend(("\\", char))
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "`":
            in_code = not in_code
            buffer.append(char)
            continue
        if char == "|" and not in_code:
            cells.append("".join(buffer).strip())
            buffer = []
            continue
        buffer.append(char)
    if escaped:
        buffer.append("\\")
    cells.append("".join(buffer).strip())
    return cells


def table_of(section: str) -> tuple[list[str], list[list[str]]]:
    # 只解析本栏目的第一张表；空行或列数变化都表示下一张表开始。
    header: list[str] | None = None
    rows: list[list[str]] = []
    seen_separator = False
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            if header is not None:
                break
            continue
        cells = split_row(line)
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            if header is None:
                continue
            if seen_separator:
                break
            seen_separator = True
            continue
        if header is None:
            header = cells
            continue
        if len(cells) != len(header):
            break
        rows.append(cells)
    if header is None:
        return [], []
    return header, rows


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).strip("`")


def index_of(headers: list[str], title: str) -> int | None:
    wanted = normalize(title)
    for index, header in enumerate(headers):
        if normalize(header) == wanted:
            return index
    return None


def is_placeholder(value: str) -> bool:
    # 只把“独立的 <...> 标记”视为占位符；Foo<T>、Promise<void> 的 < 前面是文字，不误伤。
    for match in ANGLE.finditer(value):
        if match.group(1).strip():
            return True
    stripped = value.strip().strip("。.;；")
    lowered = stripped.casefold()
    if lowered in {"todo", "tbd", "待填写", "未填写"}:
        return True
    if re.match(r"^(?:todo|tbd|待填写|未填写)\s*[:：]", stripped, re.IGNORECASE):
        return True
    return False


def is_vague(value: str) -> bool:
    return value.strip().strip("。.;；").casefold() in VAGUE_VALUES


def check_cell(value: str, where: str) -> bool:
    cleaned = value.strip()
    if not cleaned or is_placeholder(cleaned) or is_vague(cleaned):
        problems.append(f"{where}: 未填写、仍是占位符或使用了空泛值")
        return False
    return True


def path_candidates(value: str) -> list[str]:
    cleaned = value.replace("`", "").strip()
    candidates: list[str] = []
    for part in re.split(r"[,，;；]", cleaned):
        part = part.strip()
        if not part:
            continue
        part = re.sub(r"/\*+$", "", part).rstrip("/\\")
        candidates.append(part)
    return candidates


def path_error(value: str) -> str | None:
    outside = False
    root = ROOT.resolve()
    for candidate in path_candidates(value):
        path = Path(candidate)
        resolved = (path if path.is_absolute() else ROOT / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            outside = True
            continue
        if resolved.exists():
            return None
    if outside:
        return f"路径必须位于项目内: {value}"
    return f"路径不存在: {value}"


def check_environment(text: str) -> None:
    section = section_of(text, "项目环境")
    if section is None:
        return
    headers, rows = table_of(section)
    required = ("语言/运行时", "主要入口", "运行/部署形态")
    values: dict[str, str] = {}
    for row in rows:
        if len(row) >= 2:
            values[normalize(row[0])] = row[1]
    for label in required:
        value = values.get(label)
        if value is None:
            problems.append(f"项目环境: 缺少「{label}」行")
        elif check_cell(value, f"项目环境/{label}") and label == "主要入口":
            error = path_error(value)
            if error:
                problems.append(f"项目环境/主要入口: {error}")


def check_modules(text: str) -> dict[str, str]:
    section = section_of(text, "模块")
    if section is None:
        return {}
    headers, rows = table_of(section)
    required = ("模块", "根路径/入口", "拥有的职责/数据", "对外接口")
    indexes = {title: index_of(headers, title) for title in required}
    for title, index in indexes.items():
        if index is None:
            problems.append(f"模块: 缺少表头「{title}」")
    if any(index is None for index in indexes.values()):
        return {}
    if not rows:
        problems.append("模块: 至少声明一个模块")
        return {}

    modules: dict[str, str] = {}
    module_index = indexes["模块"]
    path_index = indexes["根路径/入口"]
    for row in rows:
        if len(row) <= max(indexes.values()):
            problems.append("模块: 表格列数不足")
            continue
        name = row[module_index].strip()
        key = name.strip("`").strip().casefold()
        path = row[path_index].strip()
        if not check_cell(name, "模块/模块名"):
            continue
        if key in modules:
            problems.append(f"模块: 模块名重复: {name}")
            continue
        for title, index in indexes.items():
            check_cell(row[index], f"模块/{name}/{title}")
        modules[key] = path
        if not is_placeholder(path):
            error = path_error(path)
            if error:
                problems.append(f"模块/{name}: {error}")
    return modules


def check_dependencies(text: str, modules: dict[str, str]) -> None:
    section = section_of(text, "依赖")
    if section is None:
        return
    headers, rows = table_of(section)
    required = ("依赖方", "被依赖方", "允许方向与原因")
    indexes = {title: index_of(headers, title) for title in required}
    for title, index in indexes.items():
        if index is None:
            problems.append(f"依赖: 缺少表头「{title}」")
    if any(index is None for index in indexes.values()):
        return

    for row in rows:
        if len(row) <= max(indexes.values()):
            problems.append("依赖: 表格列数不足")
            continue
        source = row[indexes["依赖方"]].strip()
        target = row[indexes["被依赖方"]].strip()
        source_key = source.strip("`").strip().casefold()
        target_key = target.strip("`").strip().casefold()
        for title, index in indexes.items():
            check_cell(row[index], f"依赖/{source or '?'}/{title}")
        if source_key and source_key not in modules:
            problems.append(f"依赖: 依赖方未在模块表声明: {source}")
        if target_key and target_key not in modules:
            problems.append(f"依赖: 被依赖方未在模块表声明: {target}")
        if source_key and source_key == target_key:
            problems.append(f"依赖: 禁止自依赖: {source}")


def check_boundaries(text: str, modules: dict[str, str]) -> None:
    section = section_of(text, "边界")
    if section is None:
        return
    headers, rows = table_of(section)
    required = ("边界", "不可信输入/外部副作用", "负责模块", "失败时的可观察行为")
    indexes = {title: index_of(headers, title) for title in required}
    for title, index in indexes.items():
        if index is None:
            problems.append(f"边界: 缺少表头「{title}」")
    if any(index is None for index in indexes.values()):
        return
    if not rows:
        problems.append("边界: 至少声明一条输入或副作用边界")
        return
    for row in rows:
        if len(row) <= max(indexes.values()):
            problems.append("边界: 表格列数不足")
            continue
        for title, index in indexes.items():
            check_cell(row[index], f"边界/{title}")
        owner = row[indexes["负责模块"]].strip()
        owner_key = owner.strip("`").strip().casefold()
        if owner_key and not is_placeholder(owner) and owner_key not in modules:
            problems.append(f"边界: 负责模块未在模块表声明: {owner}")


def check_extension_points(text: str) -> None:
    section = section_of(text, "扩展点")
    if section is None:
        return
    conclusion = re.search(r"\*\*结论[：:]\*\*\s*`([^`\n]+)`", section)
    if conclusion is None:
        problems.append("扩展点: 缺少 **结论：** `...`，不能拿说明文字当成已填写")
        return
    value = conclusion.group(1).strip()
    if not value or is_placeholder(value):
        problems.append("扩展点: 结论未填写或仍是占位符")


def check_invariants(text: str) -> None:
    section = section_of(text, "架构不变量")
    if section is None:
        return
    headers, rows = table_of(section)
    required = ("不变量", "反例", "可执行检查")
    indexes = {title: index_of(headers, title) for title in required}
    for title, index in indexes.items():
        if index is None:
            problems.append(f"架构不变量: 缺少表头「{title}」")
    if any(index is None for index in indexes.values()):
        return
    if not rows:
        problems.append("架构不变量: 至少声明一条可检查的不变量")
        return
    for row in rows:
        if len(row) <= max(indexes.values()):
            problems.append("架构不变量: 表格列数不足")
            continue
        for title, index in indexes.items():
            check_cell(row[index], f"架构不变量/{title}")
        check = row[indexes["可执行检查"]]
        code_spans = re.findall(r"`([^`]+)`", check)
        meaningful = [
            code.strip()
            for code in code_spans
            if code.strip() and not is_vague(code) and not is_placeholder(code)
        ]
        if not meaningful:
            problems.append("架构不变量: 可执行检查必须包含真实命令或测试名，不能是 none / n/a")


def is_obviously_noop(command: list[str]) -> bool:
    first = Path(command[0]).name.casefold()
    base = first[:-4] if first.endswith(".exe") else first
    if base in {"true", ":", "echo", "printf", "yes", "rem"}:
        return True
    interpreter_prefixes = (
        "python",
        "pypy",
        "node",
        "ruby",
        "perl",
        "php",
        "powershell",
        "pwsh",
        "bash",
        "sh",
    )
    if any(base.startswith(prefix) for prefix in interpreter_prefixes):
        if len(command) == 1:
            return True
        if any(
            argument.strip().casefold() in {"-c", "-e", "--eval", "-command"}
            for argument in command[1:]
        ):
            # 结构检查必须是可版本化、可审查的脚本/工具，不接受匿名内联代码。
            return True
    if base == "cmd" and "/c" in {argument.casefold() for argument in command}:
        if command[-1].strip().casefold() in {"rem", "exit", "exit 0", "0", ""}:
            return True
    return False


def parse_structure_command(text: str) -> list[str] | None:
    section = section_of(text, "结构检查")
    if section is None:
        return None
    blocks = re.findall(r"```json\s*(.*?)```", section, re.IGNORECASE | re.DOTALL)
    if len(blocks) != 1:
        problems.append("结构检查: 必须且只能包含一个 ```json 命令块")
        return None
    try:
        command = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        problems.append(f"结构检查: JSON 无法解析: {exc.msg}")
        return None
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item.strip() for item in command)
    ):
        problems.append("结构检查: 命令必须是非空字符串数组")
        return None
    if any(is_placeholder(item) for item in command):
        problems.append("结构检查: 命令仍是占位符")
        return None
    if any(
        Path(item).name.casefold() in {"check-structure.py", "gate.ps1", "gate.py"}
        for item in command
    ):
        problems.append("结构检查: 禁止递归调用 check-structure.py 或 gate.ps1")
        return None
    if is_obviously_noop(command):
        problems.append(
            "结构检查: 命令是明显空操作或匿名内联代码，没有可审查的结构检查"
        )
        return None
    return command


def read_capture(stream) -> str:
    stream.seek(0)
    return stream.read().decode("utf-8", errors="replace")


def run_structure_command(command: list[str]) -> None:
    print("[structure] running:", " ".join(command))
    environment = os.environ.copy()
    # 包装脚本、包管理器 script 或测试会继承深度；允许一层嵌套，阻止无限套娃。
    environment["STRUCTURE_CHECK_DEPTH"] = str(CHECK_DEPTH + 1)

    # 用临时文件而不是 PIPE：超时杀掉直接子进程后，不会被仍持有管道的孙进程拖住。
    stdout_file = tempfile.TemporaryFile()
    stderr_file = tempfile.TemporaryFile()
    try:
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=120,
                check=False,
                env=environment,
            )
        except FileNotFoundError:
            problems.append(f"结构检查: 命令不存在: {command[0]}")
            return
        except subprocess.TimeoutExpired:
            stdout_text = read_capture(stdout_file)
            stderr_text = read_capture(stderr_file)
            if stdout_text.strip():
                print(stdout_text.rstrip())
            if stderr_text.strip():
                print(stderr_text.rstrip(), file=sys.stderr)
            problems.append("结构检查: 命令超过 120 秒未结束")
            return

        stdout_text = read_capture(stdout_file)
        stderr_text = read_capture(stderr_file)
        if stdout_text.strip():
            print(stdout_text.rstrip())
        if stderr_text.strip():
            print(stderr_text.rstrip(), file=sys.stderr)
        if result.returncode != 0:
            problems.append(f"结构检查: 命令失败，退出码 {result.returncode}")
    finally:
        stdout_file.close()
        stderr_file.close()


def main() -> int:
    if not ARCHITECTURE.exists():
        print("[structure] problems:")
        print("  - 缺少 ARCHITECTURE.md")
        return 1

    text = ARCHITECTURE.read_text(encoding="utf-8-sig")
    for title in REQUIRED_SECTIONS:
        if section_of(text, title) is None:
            problems.append(f"缺少栏目: {title}")

    check_environment(text)
    modules = check_modules(text)
    check_dependencies(text, modules)
    check_boundaries(text, modules)
    check_extension_points(text)
    check_invariants(text)
    command = parse_structure_command(text)
    command_ran = False

    if not problems and command:
        command_ran = True
        run_structure_command(command)

    if problems:
        print("[structure] problems:")
        for problem in problems:
            print(f"  - {problem}")
        if command and not command_ran:
            print("[structure] 项目结构命令未运行：先修复契约错误")
        return 1

    print("[structure] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
