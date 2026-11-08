#!/usr/bin/env python
"""文档一致性自检（DSH 版）。跨语言，不依赖测试框架。

守六件事，全部来自真实踩坑：

1. md 里点到的文件路径必须真实存在（复制文档时最容易漏）
2. 写到「X.md」的「标题」时，标题必须在 X.md 里存在（章节改名会悄悄失效）
3. 不许用「第 N 节」编号引用（章节一挪就指错，且没有任何检查能发现）
4. 轮次类数字只允许出现在 README.md / plan.md / LESSONS.md
5. `.agents/` 下只许引用规则，不许复述
6. 已经有源码，`intent.md` 就必须有「对齐记录」

相比 codebuddy-sop 的三个修正（审计 P1-3 / P1-4）：
- 引用检查扩到 markdown 链接与 `tests/`、`docs/`、`.agents/`、`.github/` 前缀，
  原版只认 `scripts/`、`schemas/`、`prompts/` 开头的反引号路径，漏检过
  `scri pts/ci-run.py` 这种断链与模板里的项目专有文件引用。
- 所有文本按 utf-8-sig 读取，BOM 不再让文档被判坏。
- 「有源码」的目录从 4 个扩到 7 个常见布局。

退出码：0 干净；1 有违规。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

ROOT = _common.ROOT
REPO = ROOT.parent if (ROOT.parent / "tests").is_dir() and (ROOT.parent / "README.md").is_file() else None
NEGATION = re.compile(r"(没有|不提供|不存在|本版无|已删)")

REF = re.compile(
    r"`((?:scripts|tests|docs|\.agents|\.github|schemas|prompts)/[A-Za-z0-9_./*-]+"
    r"|[A-Za-z0-9_.-]+\.md)`"
)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+)\)")
NUM_PREFIX = re.compile(r"^[一二三四五六七八九十]+、\s*")
QUOTED = re.compile(r"`([A-Za-z0-9_.-]+\.md)`\s*的\s*「([^」]+)」")
NUMERIC = re.compile(r"`?[A-Za-z0-9_.-]+\.md`?\s*第[一二三四五六七八九十0-9]+节")
ROUND = re.compile(
    r"(?:共|最多|不超过|上限为?|额度为?|走满|跑满)\s*[0-9一二三四五六七八九十]+\s*[轮次]"
    r"|[0-9一二三四五六七八九十]+\s*[轮次]\s*(?:上限|额度|为限|为止|到顶)"
)
ALLOW_ROUND = {"SOP.md", "plan.md", "LESSONS.md"}
SOURCE_DIRS = ("src", "app", "lib", "pkg", "server", "cmd", "internal")
ALIGN_OK = {"已完成", "事后补录（未达标准）"}
RESTATEMENT_REF = re.compile(r"(README\.md|REVIEW\.md|whenToUse|review-state\.py)")

problems: list[str] = []


def walk_markdown() -> list[Path]:
    files = [path for _, path in _common.iter_files(ROOT, include_markdown=True) if path.suffix == ".md"]
    if REPO is not None:
        extras = sorted(REPO.glob("*.md"))
        for folder in ("tests", ".agents"):
            candidate = REPO / folder
            if candidate.is_dir():
                extras.extend(sorted(candidate.rglob("*.md")))
        files.extend(path for path in extras if path.is_file() and path not in files)
    return sorted(set(files), key=lambda path: path.as_posix())


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def bases(md: Path) -> list[Path]:
    result = [md.parent, ROOT]
    if REPO is not None and REPO not in result:
        try:
            md.relative_to(REPO)
        except ValueError:
            return result
        result.append(REPO)
    return result


def exists(md: Path, ref: str) -> bool:
    return any((base / ref).exists() for base in bases(md))


def headings_of(name: str, md: Path) -> list[str]:
    for base in bases(md):
        path = base / name
        if path.is_file():
            raw = re.findall(r"^#{2,4}\s+(.+)$", _common.read_text(path), re.M)
            return [NUM_PREFIX.sub("", heading).strip() for heading in raw]
    return []


def check_references() -> None:
    for md in walk_markdown():
        for lineno, line in enumerate(_common.read_text(md).splitlines(), 1):
            for match in REF.finditer(line):
                clause = re.split(r"[。；;，,]", line[: match.start()])[-1]
                if NEGATION.search(clause):
                    continue
                ref = match.group(1)
                if "*" in ref or ref.endswith("/"):
                    continue
                if not exists(md, ref):
                    problems.append(f"{rel(md)}:{lineno} 引用了不存在的 {ref}")
            for match in LINK.finditer(line):
                target = match.group(1)
                if re.match(r"^[a-z]+://", target) or target.startswith("#") or target.startswith("mailto:"):
                    continue
                target = target.split("#")[0].strip()
                if not target:
                    continue
                if not exists(md, target):
                    problems.append(f"{rel(md)}:{lineno} 链接指向不存在的 {target}")


def check_headings() -> None:
    for md in walk_markdown():
        for match in QUOTED.finditer(_common.read_text(md)):
            target, title = match.group(1), match.group(2)
            if title not in headings_of(target, md):
                problems.append(f"{rel(md)} 引用了 {target} 里不存在的标题「{title}」")


def check_numbered_sections() -> None:
    for md in walk_markdown():
        if md.name == "REVIEW.md":
            continue
        for lineno, line in enumerate(_common.read_text(md).splitlines(), 1):
            if NUMERIC.search(line):
                problems.append(f"{rel(md)}:{lineno} 用了「第 N 节」编号引用，请改用标题")


def check_round_numbers() -> None:
    for md in walk_markdown():
        if md.name in ALLOW_ROUND:
            continue
        if ".agents" in md.parts:
            continue
        for lineno, line in enumerate(_common.read_text(md).splitlines(), 1):
            normalized = re.sub(r"[*_`]", "", line)
            for match in ROUND.finditer(normalized):
                if normalized[match.start() - 1 : match.start()] == "第":
                    continue
                problems.append(f"{rel(md)}:{lineno} 复述了轮次数字（只允许在 README.md / plan.md / LESSONS.md）")
                break


def check_agent_restatement() -> None:
    folder = ROOT / ".agents"
    if not folder.is_dir():
        return
    for md in sorted(folder.rglob("*.md")):
        for lineno, line in enumerate(_common.read_text(md).splitlines(), 1):
            if RESTATEMENT_REF.search(line):
                continue
            if ROUND.search(re.sub(r"[*_`]", "", line)):
                problems.append(f"{rel(md)}:{lineno} 在 .agents 里复述轮次规则，应改为引用 README")


def check_alignment() -> None:
    if not any((ROOT / name).is_dir() and any(p.is_file() for p in (ROOT / name).rglob("*")) for name in SOURCE_DIRS):
        return
    intent = ROOT / "intent.md"
    if not intent.is_file():
        problems.append("已有源码但没有 intent.md")
        return
    body = re.search(r"^##\s+对齐记录\s*$(.*?)(?=^##\s|\Z)", _common.read_text(intent), re.M | re.S)
    if body is None:
        problems.append("intent.md 缺少「对齐记录」：需求对齐没做就开始了实现")
        return
    text = body.group(1)
    status = re.search(r"\*\*状态[：:]\*\*\s*`([^`\n]+)`", text)
    if not status:
        problems.append("「对齐记录」没有写状态")
    elif status.group(1).strip() not in ALIGN_OK:
        problems.append(f"「对齐记录」状态是「{status.group(1).strip()}」：对齐没完成就开始实现")
    rows = re.findall(r"^\|\s*(Q\d+)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|", text, re.M)
    if not rows:
        problems.append("「对齐记录」里没有 Q1…Qn 条目")
    for qid, question, recommend, confirm in rows:
        if not question.strip().strip("`"):
            problems.append(f"「对齐记录」{qid} 没写问题")
        if not recommend.strip().strip("`"):
            problems.append(f"「对齐记录」{qid} 没有推荐答案")
        if not confirm.strip().strip("`"):
            problems.append(f"「对齐记录」{qid} 没有用户确认")


def main(argv: list[str] | None = None) -> int:
    global ROOT, REPO
    argv = list(sys.argv[1:] if argv is None else argv)
    index = 0
    while index < len(argv):
        if argv[index] == "--root" and index + 1 < len(argv):
            ROOT = Path(argv[index + 1]).resolve()
            REPO = (
                ROOT.parent
                if (ROOT.parent / "tests").is_dir() and (ROOT.parent / "README.md").is_file()
                else None
            )
            index += 2
        else:
            print(f"[docs] FAIL: 未知参数 {argv[index]}")
            return 1
    check_references()
    check_headings()
    check_numbered_sections()
    check_round_numbers()
    check_agent_restatement()
    check_alignment()

    if problems:
        print("\n[docs] problems:")
        for problem in problems:
            print("  - " + problem)
        return 1
    print(f"[docs] OK ({len(walk_markdown())} md files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
