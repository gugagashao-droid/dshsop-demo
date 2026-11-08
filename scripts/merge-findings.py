#!/usr/bin/env python
"""CI 后三视角 findings 的合并器。

规则唯一出处：本文件。REVIEW.md 定义单份 findings 的十字段格式（「五、输出格式固定」），
本文件定义多份怎么合并；其他文档只引用，不复述。

合并规则：
1. 每份输入必须是 REVIEW.md 十字段格式的 YAML；字段缺失/多余、枚举越界、
   line 不是非负整数、confidence 不在 0..1 → 整批判失败，不许静默丢弃任何一条。
2. 去重键 = (file, line, normalize(claim))；normalize 去空白与标点、转小写。
   三个视角措辞略有差异时仍能合并，不会把同一问题数成三条。
3. 同键保留 confidence 高的一条；severity 冲突取更严重的，且**绝不降级**——
   REVIEW.md 规定 important 只能修或升级给人，合并器无权关闭。
4. dimension 冲突不吞掉，记进 summary 的 dimension_conflicts 供人复核。
5. important 数 = 合并后 severity == important 的条数，逐条数出来，不许估。

输出（写到 --outdir）：merged.yaml（仍是十字段）、summary.json、important-count（纯数字）。
important-count 供 ci-run.py 的 @file 展开传给 review-state.py record --important。
注意：review-state.py 的 --axes 要的是 spec,standards 两条轴，不是这里的三个 dimension。

退出码：0 成功；1 校验失败；2 输入缺失；5 自检失败；6 不可读。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

FIELDS = ("id", "dimension", "severity", "file", "line", "claim", "evidence", "trigger", "suggestion", "confidence")
DIMENSIONS = {"spec", "compat", "impact"}
SEVERITIES = {"important", "nit", "pre-existing"}
RANK = {"important": 3, "nit": 2, "pre-existing": 1}


class Unreadable(Exception):
    pass


class Invalid(Exception):
    pass


def split_kv(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise Unreadable(f"不是 key: value 形式：{text}")
    key, value = text.split(":", 1)
    return key.strip(), value.strip().strip('"').strip("'")


def parse_findings(text: str) -> list[dict]:
    lines = [line.rstrip() for line in text.splitlines()]
    first = next((line for line in lines if line.strip()), "")
    if first.strip() == "findings: []":
        return []
    if first.strip() != "findings:":
        raise Unreadable("顶层必须是 findings: 或 findings: []")

    items: list[dict] = []
    current: dict | None = None
    block_key: str | None = None
    for line in lines[lines.index(first) + 1 :]:
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current is not None:
                items.append(current)
            current = {}
            key, value = split_kv(line[4:])
            current[key] = "" if value == "|" else value
            block_key = key if value == "|" else None
        elif line.startswith("      ") and block_key and current is not None:
            current[block_key] += ("\n" if current[block_key] else "") + line.strip()
        elif line.startswith("    ") and current is not None:
            key, value = split_kv(line.strip())
            current[key] = "" if value == "|" else value
            block_key = key if value == "|" else None
        else:
            raise Unreadable(f"无法解析的行：{line!r}")

    if current is not None:
        items.append(current)

    for item in items:
        missing = set(FIELDS) - set(item)
        extra = set(item) - set(FIELDS)
        if missing or extra:
            raise Invalid(f"字段不全或多出：缺 {sorted(missing)}，多 {sorted(extra)}")
        if item["dimension"] not in DIMENSIONS:
            raise Invalid(f"dimension 非法：{item['dimension']}")
        if item["severity"] not in SEVERITIES:
            raise Invalid(f"severity 非法：{item['severity']}")
        try:
            item["line"] = int(item["line"])
            item["confidence"] = float(item["confidence"])
        except ValueError as exc:
            raise Invalid(f"line / confidence 不是数字：{exc}") from exc
        if item["line"] < 0 or not 0 <= item["confidence"] <= 1:
            raise Invalid("line 必须 >= 0，confidence 必须在 0..1")
    return items


def normalize_claim(value: str) -> str:
    # \W 在 Unicode 模式下只去掉标点与空白，中文字符属于 \w，会保留。
    return re.sub(r"[\s\W_]+", "", value.lower(), flags=re.UNICODE)


def merge(groups: list[list[dict]]) -> tuple[list[dict], dict]:
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    duplicates = 0
    conflicts: list[str] = []
    seen_ids: set[str] = set()

    for batch in groups:
        for item in batch:
            key = (item["file"], item["line"], normalize_claim(item["claim"]))
            if key not in merged:
                merged[key] = item
                order.append(key)
                if item["id"] in seen_ids:
                    raise Invalid(f"id 重复：{item['id']}")
                seen_ids.add(item["id"])
                continue

            duplicates += 1
            existing = merged[key]
            if existing["dimension"] != item["dimension"]:
                conflicts.append(f"{item['file']}:{item['line']} 同时被 {existing['dimension']} 与 {item['dimension']} 报出")

            chosen = item if (RANK[item["severity"]], item["confidence"]) > (
                RANK[existing["severity"]],
                existing["confidence"],
            ) else existing
            # 绝不降级：只要有一份判 important，合并结果就必须是 important。
            if "important" in (existing["severity"], item["severity"]):
                chosen = dict(chosen)
                chosen["severity"] = "important"
            merged[key] = chosen

    result = [merged[key] for key in order]
    important = sum(1 for item in result if item["severity"] == "important")
    summary = {
        "total": len(result),
        "important": important,
        "duplicates": duplicates,
        "dimension_conflicts": conflicts,
        "by_dimension": {d: sum(1 for i in result if i["dimension"] == d) for d in sorted(DIMENSIONS)},
    }
    return result, summary


def dump(items: list[dict]) -> str:
    lines = ["findings:"] if items else ["findings: []"]
    for item in items:
        lines.append(f"  - id: {item['id']}")
        lines.append(f"    dimension: {item['dimension']}")
        lines.append(f"    severity: {item['severity']}")
        lines.append(f"    file: {item['file']}")
        lines.append(f"    line: {item['line']}")
        lines.append(f'    claim: "{item["claim"]}"')
        lines.append("    evidence: |")
        for part in (item["evidence"].splitlines() or [""]):
            lines.append("      " + part)
        lines.append(f'    trigger: "{item["trigger"]}"')
        lines.append(f'    suggestion: "{item["suggestion"]}"')
        lines.append(f"    confidence: {item['confidence']}")
    return "\n".join(lines) + "\n"


def run(root: Path, inputs: list[str], outdir: str) -> int:
    if len(inputs) != 3:
        print("[merge] FAIL: 必须给三份输入（spec / compat / impact）")
        return 2
    paths = [root / name for name in inputs]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        print(f"[merge] FAIL: 输入缺失：{', '.join(missing)}")
        return 2

    batches = []
    for path in paths:
        try:
            batches.append(parse_findings(path.read_text(encoding="utf-8-sig")))
        except Invalid as exc:
            print(f"[merge] FAIL: {path.name} 校验失败：{exc}")
            return 1
        except (Unreadable, OSError, UnicodeError) as exc:
            print(f"[merge] FAIL: {path.name} 不可读：{exc}")
            return 6

    try:
        result, summary = merge(batches)
    except Invalid as exc:
        print(f"[merge] FAIL: 合并失败：{exc}")
        return 1

    out = root / outdir
    out.mkdir(parents=True, exist_ok=True)
    (out / "merged.yaml").write_text(dump(result), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "important-count").write_text(f"{summary['important']}\n", encoding="utf-8")
    print(f"[merge] OK: {summary['total']} findings, {summary['important']} important, {summary['duplicates']} duplicates")
    return 0


VALID = """findings:
  - id: spec-1
    dimension: spec
    severity: important
    file: src/a.py
    line: 12
    claim: "返回了空结果"
    evidence: |
      return []
    trigger: "查不到数据时"
    suggestion: "抛错"
    confidence: 0.8
"""
EMPTY = "findings: []\n"


def self_test() -> int:
    results: list[tuple[str, int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def write(name, text):
            (root / name).write_text(text, encoding="utf-8")

        def call(a=VALID, b=VALID, c=VALID, names=("a.yaml", "b.yaml", "c.yaml")):
            write(names[0], a)
            write(names[1], b)
            write(names[2], c)
            return run(root, list(names), ".review")

        results.append(("三份相同 → 去重保留一条", call(), 0))
        summary = json.loads((root / ".review" / "summary.json").read_text(encoding="utf-8"))
        results.append(("去重后 total=1", 0 if summary["total"] == 1 else 1, 0))
        results.append(("important 数正确", 0 if summary["important"] == 1 else 1, 0))
        results.append(("important-count 内容", 0 if (root / ".review" / "important-count").read_text(encoding="utf-8").strip() == "1" else 1, 0))

        results.append(("三份全空", call(EMPTY, EMPTY, EMPTY), 0))
        results.append(("空输入的 important=0", 0 if (root / ".review" / "important-count").read_text(encoding="utf-8").strip() == "0" else 1, 0))

        results.append(("缺字段", call(VALID.replace("    trigger: \"查不到数据时\"\n", ""), VALID, VALID), 1))
        results.append(("非法 severity", call(VALID.replace("important", "blocker"), VALID, VALID), 1))
        results.append(("confidence 越界", call(VALID.replace("0.8", "1.8"), VALID, VALID), 1))
        results.append(("YAML 结构损坏", call("oops\n", VALID, VALID), 6))

        # important 绝不降级：一份判 nit、一份判 important，结果仍须是 important。
        nit = VALID.replace("severity: important", "severity: nit")
        results.append(("important 不被降级", call(nit, VALID, VALID), 0))
        summary = json.loads((root / ".review" / "summary.json").read_text(encoding="utf-8"))
        results.append(("降级用例仍计 1 条 important", 0 if summary["important"] == 1 else 1, 0))

        (root / "c.yaml").unlink()
        results.append(("输入缺失", run(root, ["a.yaml", "b.yaml", "c.yaml"], ".review"), 2))
        results.append(("输入数量不对", run(root, ["a.yaml", "b.yaml"], ".review"), 2))

    failures = [f"{label}: 期望 {expected}，实际 {actual}" for label, actual, expected in results if actual != expected]
    if failures:
        print("[merge] self-test FAILED:")
        for failure in failures:
            print("  - " + failure)
        return 5
    print(f"[merge] self-test OK ({len(results)} cases)")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "self-test":
        return self_test()

    root = Path(__file__).resolve().parent.parent
    inputs: list[str] = []
    outdir = ".review"
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "merge":
            i += 1
        elif arg == "--inputs":
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                inputs.append(argv[i])
                i += 1
        elif arg == "--outdir" and i + 1 < len(argv):
            outdir = argv[i + 1]
            i += 2
        elif arg == "--root" and i + 1 < len(argv):
            root = Path(argv[i + 1])
            i += 2
        else:
            print(f"[merge] FAIL: 未知参数 {arg}")
            return 6
    return run(root, inputs, outdir)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
