#!/usr/bin/env python
"""三视角 AI 审核（④ 段）。

为什么存在：ci.json 的 ai 段需要三个"无头调用"，各自产出 spec / compat / impact
三份十字段 findings。本脚本是其中一个调用：读 REVIEW.md 规则 + 本次 diff，
调 OpenAI 兼容的 chat/completions 接口，要求模型输出 JSON，再转成
merge-findings.py 能解析的 YAML。

为什么让模型输出 JSON 而不是 YAML：标准库没有可靠的 YAML 解析器，
模型直接写 YAML 时缩进错一次就整批失败；JSON 解析确定，转 YAML 由脚本做。

密钥：只从环境变量读（CI_AGENT_TOKEN 或 DEEPSEEK_API_KEY），不落盘、不进仓库。

退出码：0 成功；1 调用或校验失败；6 缺少配置/密钥。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

ROOT = _common.ROOT
DIMENSIONS = ("spec", "compat", "impact")
SEVERITIES = ("important", "nit", "pre-existing")
FIELDS = ("id", "dimension", "severity", "file", "line", "claim", "evidence", "trigger", "suggestion", "confidence")
MAX_DIFF_CHARS = 60000

SYSTEM = """你是资深代码审核者，只做只读审核，不修改任何文件。
按 REVIEW.md 的规则审核给定的 diff，只报机器检查（lint/类型/测试）发现不了的问题：
契约不一致、依赖方向错、静默降级、边界缺失、重复实现、过度实现。
不要报格式、命名、风格问题。说不清「会导致什么具体错误」的，不要报。
没有发现就返回空数组。"""

INSTRUCTION = """当前视角：{axis}
{axis_hint}

严格只输出 JSON 数组（不要解释、不要 markdown 代码围栏），每项字段：
id（"{axis}-<序号>"）、dimension（固定 "{axis}"）、severity（{severities} 之一）、
file（相对路径）、line（非负整数）、claim（一句话）、evidence（代码片段或引用，字符串）、
trigger（什么条件下出错）、suggestion（怎么改）、confidence（0 到 1 的小数）。
最多 10 条。"""

AXIS_HINT = {
    "spec": "检查实现是否符合 spec.md / intent.md 承诺的行为；承诺了但没做、或做了但没承诺，都要报。",
    "compat": "检查对外兼容性、输入边界、错误路径、平台差异、安全输入处理。",
    "impact": "检查改动对架构契约（ARCHITECTURE.md）、依赖方向、模块所有权、其他调用方的影响。",
}


def read_optional(name: str) -> str:
    path = ROOT / name
    return _common.read_text(path) if path.is_file() else ""


def git_diff(base: str | None) -> str:
    candidates = []
    if base:
        candidates.append([base])
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        candidates.append([f"origin/{base_ref}...HEAD"])
    candidates.append(["HEAD^", "HEAD"])
    for extra in candidates:
        result = subprocess.run(
            ["git", "diff", "--no-color", "--unified=3", *extra],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout[:MAX_DIFF_CHARS]
    return ""


def build_prompt(axis: str, diff: str) -> str:
    review = read_optional("REVIEW.md")[:8000]
    spec = read_optional("spec.md")[:4000]
    intent = read_optional("intent.md")[:2000]
    arch = read_optional("ARCHITECTURE.md")[:4000]
    return "\n\n".join(
        [
            INSTRUCTION.format(axis=axis, axis_hint=AXIS_HINT[axis], severities="/".join(SEVERITIES)),
            "## REVIEW.md\n" + review,
            "## spec.md\n" + (spec or "（无）"),
            "## intent.md\n" + (intent or "（无）"),
            "## ARCHITECTURE.md\n" + (arch or "（无）"),
            "## DIFF\n```diff\n" + diff + "\n```",
        ]
    )


def call_api(key: str, model: str, prompt: str) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    last_error = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"调用失败：{last_error}")


def extract_json(text: str) -> list:
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fence.group(1) if fence else text
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("响应里没有 JSON 数组")
    return json.loads(candidate[start : end + 1])


def normalize(items: list, axis: str) -> list[dict]:
    out = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 项不是对象")
        row = {field: item.get(field) for field in FIELDS}
        row["id"] = str(row["id"] or f"{axis}-{index}")
        row["dimension"] = axis
        if row["severity"] not in SEVERITIES:
            raise ValueError(f"{row['id']} 的 severity 非法：{row['severity']}")
        row["line"] = int(row["line"] or 0)
        row["confidence"] = float(row["confidence"] if row["confidence"] is not None else 0.5)
        if not 0 <= row["confidence"] <= 1:
            row["confidence"] = 0.5
        for field in ("file", "claim", "trigger", "suggestion"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"{row['id']} 缺字段 {field}")
        row["evidence"] = str(row["evidence"] or "")
        out.append(row)
    return out


def dump_yaml(items: list[dict]) -> str:
    """与 merge-findings.py 的 dump 保持同一格式（规则只写一处）。"""
    lines = ["findings:"] if items else ["findings: []"]
    for item in items:
        lines.append(f"  - id: {item['id']}")
        for field in ("dimension", "severity", "file", "line"):
            lines.append(f"    {field}: {item[field]}")
        lines.append(f'    claim: "{item["claim"]}"')
        lines.append("    evidence: |")
        for part in (item["evidence"].splitlines() or [""]):
            lines.append("      " + part)
        lines.append(f'    trigger: "{item["trigger"]}"')
        lines.append(f'    suggestion: "{item["suggestion"]}"')
        lines.append(f"    confidence: {item['confidence']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--axis", required=True, choices=DIMENSIONS)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--model", default=os.environ.get("SOP_REVIEW_MODEL", "deepseek-chat"))
    args = parser.parse_args(argv)

    key = os.environ.get("CI_AGENT_TOKEN") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("[ai-review] FAIL: 缺少 CI_AGENT_TOKEN / DEEPSEEK_API_KEY")
        return 6

    diff = git_diff(args.base or None)
    if not diff:
        print("[ai-review] FAIL: 取不到 diff（CI 里需要 fetch-depth: 0）")
        return 1

    try:
        content = call_api(key, args.model, build_prompt(args.axis, diff))
        items = normalize(extract_json(content), args.axis)
    except (RuntimeError, ValueError) as exc:
        print(f"[ai-review] FAIL: {exc}")
        return 1

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump_yaml(items), encoding="utf-8")
    important = sum(1 for item in items if item["severity"] == "important")
    print(f"[ai-review] OK: {args.axis} 视角 {len(items)} 条 findings，{important} 条 important -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
