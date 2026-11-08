#!/usr/bin/env python
"""审核者模型分离门禁（DSH 版）。

DSH 没有 CodeBuddy 的 agent 定义文件：审核者模型是 `subagent(provider, model)`
的调用参数，而不是某个 yaml 字段。因此这里检查两处：

1. `sop.json` 的 `review.preCiModel` / `review.crossModel`：
   必须是具体模型 ID、且两者不同（CI 前与 CI 后是两道独立的关）。
2. 若 `.review/pre-ci.json` 已有证据：证据里的 `model` 必须等于 `preCiModel`。
   防止「配置写 A、record 时用 B」这种配置与证据脱节。

边界（必须说清）：本脚本只验证配置与证据的一致性，不能证明子代理运行时真的
用了那个模型——DSH 的模型可用列表会拦截不可用模型并回退。真正的独立性由
`subagent` 的显式 provider/model 参数与事后审计承担。

规则唯一出处：本文件。

退出码：0 通过；1 违规；5 自检失败；6 配置不可读。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

ROOT = _common.ROOT
CONFIG = "sop.json"
EVIDENCE = Path(".review") / "pre-ci.json"

NON_CONCRETE = {
    "inherit",
    "reasoning",
    "lite",
    "auto",
    "default",
    "max",
    "true",
    "false",
    "none",
    "null",
    "same",
    "lead",
    "tbd",
    "todo",
}
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]*")


class Unreadable(Exception):
    pass


def is_concrete(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip().lower() not in NON_CONCRETE
        and bool(MODEL_ID.fullmatch(value.strip()))
    )


def load_config(root: Path) -> dict:
    path = root / CONFIG
    if not path.is_file():
        raise Unreadable(f"缺少 {CONFIG}（项目差异没有登记）")
    try:
        data = json.loads(_common.read_text(path))
    except (OSError, ValueError) as exc:
        raise Unreadable(f"{CONFIG} 不可读：{exc}") from exc
    if not isinstance(data, dict):
        raise Unreadable(f"{CONFIG} 顶层必须是对象")
    return data


def load_evidence_model(root: Path) -> str | None:
    path = root / EVIDENCE
    if not path.is_file():
        return None
    try:
        data = json.loads(_common.read_text(path))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    model = data.get("model")
    return model if isinstance(model, str) else None


def check(root: Path, main_model: str | None = None) -> int:
    try:
        data = load_config(root)
    except Unreadable as exc:
        print(f"[model-separation] FAIL: {exc}")
        return 6

    review = data.get("review")
    if not isinstance(review, dict):
        print("[model-separation] FAIL: sop.json 缺少 review 段（preCiModel / crossModel）")
        return 1

    problems: list[str] = []
    pre = review.get("preCiModel")
    cross = review.get("crossModel")

    for label, value in (("preCiModel", pre), ("crossModel", cross)):
        if value in (None, ""):
            problems.append(f"review.{label} 未填写：审核者会继承主会话模型，盲区同源")
        elif not is_concrete(value):
            problems.append(f"review.{label}='{value}' 不是具体模型 ID")

    if is_concrete(pre) and is_concrete(cross) and pre.strip().lower() == cross.strip().lower():
        problems.append("review.preCiModel 与 review.crossModel 相同：CI 前后两道关不再独立")

    if main_model:
        for label, value in (("preCiModel", pre), ("crossModel", cross)):
            if is_concrete(value) and value.strip().lower() == main_model.strip().lower():
                problems.append(f"review.{label} 与主会话模型相同")

    evidence_model = load_evidence_model(root)
    if evidence_model and is_concrete(pre) and evidence_model.strip().lower() != pre.strip().lower():
        problems.append(
            f"CI 前审核证据里的 model='{evidence_model}' 与 review.preCiModel='{pre}' 不一致"
        )

    if problems:
        print("[model-separation] problems:")
        for problem in problems:
            print("  - " + problem)
        return 1
    print("[model-separation] OK")
    return 0


CASES: list[tuple[str, dict | None, dict | None, str | None, int]] = [
    (
        "合规基线",
        {"review": {"preCiModel": "model-a", "crossModel": "model-b"}},
        None,
        None,
        0,
    ),
    (
        "缺 review 段",
        {},
        None,
        None,
        1,
    ),
    (
        "同模型",
        {"review": {"preCiModel": "model-a", "crossModel": "model-a"}},
        None,
        None,
        1,
    ),
    (
        "占位符",
        {"review": {"preCiModel": "<填写模型ID>", "crossModel": "model-b"}},
        None,
        None,
        1,
    ),
    (
        "inherit",
        {"review": {"preCiModel": "inherit", "crossModel": "model-b"}},
        None,
        None,
        1,
    ),
    (
        "与主会话相同",
        {"review": {"preCiModel": "model-a", "crossModel": "model-b"}},
        None,
        "model-a",
        1,
    ),
    (
        "证据模型与配置不一致",
        {"review": {"preCiModel": "model-a", "crossModel": "model-b"}},
        {"model": "model-z"},
        None,
        1,
    ),
    (
        "证据模型与配置一致",
        {"review": {"preCiModel": "model-a", "crossModel": "model-b"}},
        {"model": "model-a"},
        None,
        0,
    ),
]


def self_test() -> int:
    failures: list[str] = []
    for label, config, evidence, main_model, expected in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if config is not None:
                (root / CONFIG).write_text(
                    json.dumps(config, ensure_ascii=False), encoding="utf-8"
                )
            if evidence is not None:
                (root / ".review").mkdir()
                (root / EVIDENCE).write_text(
                    json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
                )
            actual = check(root, main_model)
            if actual != expected:
                failures.append(f"{label}: 期望 {expected}，实际 {actual}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / CONFIG).write_text("{not json", encoding="utf-8")
        actual = check(root)
        if actual != 6:
            failures.append(f"配置损坏: 期望 6，实际 {actual}")

    if failures:
        print("[model-separation] self-test FAILED:")
        for failure in failures:
            print("  - " + failure)
        return 5
    print(f"[model-separation] self-test OK ({len(CASES) + 1} cases)")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "self-test":
        return self_test()

    root = ROOT
    main_model = None
    index = 0
    while index < len(argv):
        if argv[index] == "check":
            index += 1
        elif argv[index] == "--root" and index + 1 < len(argv):
            root = Path(argv[index + 1])
            index += 2
        elif argv[index] == "--main-model" and index + 1 < len(argv):
            main_model = argv[index + 1]
            index += 2
        else:
            print(f"[model-separation] FAIL: 未知参数 {argv[index]}")
            return 6
    return check(root, main_model)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
