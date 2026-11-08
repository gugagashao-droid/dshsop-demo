#!/usr/bin/env python
"""CI 前独立审核的证据门禁。

为什么存在：agentic Subagent 靠 description 自动判断调用时机，官方明确这是判断而非保证。
只检查「审核者换没换模型」无法证明它真的审过这次改动。这里用落盘证据 + 可失败校验兜住：
审核者跑完必须 record，门禁重算内容指纹比对，删证据或改代码都必须变红。

规则唯一出处：本文件。/pre-ci-review 命令、Stop hook 与 gate.ps1 只调用它，不复述判定。

防伪造：record 不再接受调用方报的 important 数，而是从审核产物
.review/pre-ci-findings.yaml 逐条数出；产物必须能被 merge-findings.py 的十字段
解析器校验通过，record 把产物哈希写进证据，check 重新比对。
边界必须说清：这能证明「有一份合法审核产物、important 没被瞒报、产物没被事后替换」，
不能证明产物一定是另一个模型写的——后者由子代理机制与事后审计承担。

record 写 .review/pre-ci.json；check 校验：
  证据存在、可读、由独立审核者写入、模型是具体 ID、审核产物未被替换、
  覆盖当前内容、important 已清零。

退出码：0 通过；1 证据缺失/过期/模型无效/产物被替换；2 important 未处置；
        3 越权；5 自检失败；6 状态或产物不可读。record 在模型不是具体 ID 时返回 1。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

STATE = Path(".review") / "pre-ci.json"
FINDINGS = Path(".review") / "pre-ci-findings.yaml"
WAIVERS = Path(".review") / "waivers.json"
ACTOR = "dsh-subagent"
REQUIRED_KEYS = ("fingerprint", "model", "actor", "important", "recorded_at", "findings_hash")

# 忽略口径唯一出处是 _common：pytest / mypy / coverage 等缓存不是代码改动。
# codebuddy-sop 里两套口径不一致，导致真实项目「代码没改、证据却过期」（审计 P1-1）。

# 持续更新的记录文件不算「代码改动」，与 review-state.py 的 WATCHED 口径一致；
# 否则每写一行台账就要重审一次，人会被引到 --no-verify 上去。
EXCLUDED_FILES = {"LESSONS.md", "plan.md"}

MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]*")
BAD_MODELS = {"inherit", "reasoning", "lite", "auto", "default", "max", "none", "null", "tbd", "todo"}


class Unreadable(Exception):
    pass


def fingerprint(root: Path) -> str:
    """内容指纹：忽略口径来自 _common，和 review-state.py 共用一份。"""
    return _common.fingerprint(root)


def findings_parser():
    """复用 merge-findings.py 的十字段解析器，规则只写一处。"""
    path = Path(__file__).resolve().parent / "merge-findings.py"
    spec = importlib.util.spec_from_file_location("merge_findings_sop", path)
    if spec is None or spec.loader is None:
        raise OSError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_waivers(root: Path) -> dict[str, dict]:
    """读人裁决的豁免清单。缺文件 = 无豁免；格式错 = 硬失败（不许静默忽略）。"""
    path = root / WAIVERS
    if not path.is_file():
        return {}
    try:
        data = json.loads(_common.read_text(path))
    except (OSError, ValueError) as exc:
        raise Unreadable(f"豁免文件不可读：{exc}") from exc
    entries = data.get("waivers") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise Unreadable("豁免文件顶层必须是 {\"waivers\": [...]}")
    waivers: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise Unreadable("豁免条目必须是对象")
        for field in ("id", "reason", "approved_by"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise Unreadable(f"豁免条目缺少 {field}：{entry}")
        if entry["id"] in waivers:
            raise Unreadable(f"豁免 id 重复：{entry['id']}")
        waivers[entry["id"]] = entry
    return waivers


def findings_hash(root: Path) -> str:
    try:
        return hashlib.sha256((root / FINDINGS).read_bytes()).hexdigest()
    except OSError:
        return ""


def read_findings(root: Path) -> tuple[list[dict], str]:
    """读审核产物并校验十字段格式；产物在 .review/ 下，不进内容指纹，必须单独管。"""
    path = root / FINDINGS
    if not path.is_file():
        raise Unreadable(f"没有审核产物 {FINDINGS}（先让审核 subagent 出 findings）")
    module = findings_parser()
    raw = path.read_bytes()
    try:
        items = module.parse_findings(raw.decode("utf-8-sig"))
    except (module.Invalid, module.Unreadable, UnicodeError) as exc:
        raise Unreadable(f"审核产物格式不合法：{exc}") from exc
    return items, hashlib.sha256(raw).hexdigest()


def valid_model(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.lower() not in BAD_MODELS
        and bool(MODEL_ID.fullmatch(value))
    )


def record(root: Path, model: str) -> int:
    if not valid_model(model):
        print(f"[pre-ci] FAIL: model='{model}' 不是具体模型 ID，拒绝落盘证据")
        return 1
    try:
        items, digest = read_findings(root)
    except (Unreadable, OSError) as exc:
        print(f"[pre-ci] FAIL: {exc}")
        return 6
    important = sum(1 for item in items if item["severity"] == "important")
    state = {
        "fingerprint": fingerprint(root),
        "model": model,
        "actor": ACTOR,
        "important": important,
        "findings_hash": digest,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    path = root / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[pre-ci] recorded: fingerprint={state['fingerprint'][:12]} model={model} "
        f"findings={len(items)} important={important}"
    )
    return 0


def check(root: Path) -> int:
    path = root / STATE
    if not path.is_file():
        print("[pre-ci] FAIL: 没有 CI 前独立审核证据（先跑 /pre-ci-review）")
        return 1
    try:
        state = json.loads(_common.read_text(path))
        if not isinstance(state, dict) or any(k not in state for k in REQUIRED_KEYS):
            raise ValueError("证据字段不完整")
        important = int(state["important"])
        if important < 0:
            raise ValueError("important 不能为负")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"[pre-ci] FAIL: 证据不可读：{exc}")
        return 6

    # 越权先判：主 Agent 自己写一份证据就能绕过整道关，必须最先挡住。
    if state["actor"] != ACTOR:
        print(f"[pre-ci] FAIL: 证据由 {state['actor']} 写入，不是独立审核者")
        return 3
    if not valid_model(state["model"]):
        print(f"[pre-ci] FAIL: 证据里的 model='{state['model']}' 不是具体模型 ID")
        return 1
    if state["findings_hash"] != findings_hash(root):
        print("[pre-ci] FAIL: 审核产物被替换或删除，证据不再对应那份 findings")
        return 1
    if state["fingerprint"] != fingerprint(root):
        print("[pre-ci] FAIL: 审核之后代码又变了，证据不再覆盖当前改动")
        return 1
    try:
        waivers = load_waivers(root)
    except (Unreadable, OSError) as exc:
        print(f"[pre-ci] FAIL: {exc}")
        return 6

    if important > 0:
        try:
            items, _ = read_findings(root)
        except (Unreadable, OSError) as exc:
            print(f"[pre-ci] FAIL: 无法读取审核产物以核对豁免：{exc}")
            return 6
        all_ids = {item["id"] for item in items}
        important_ids = [item["id"] for item in items if item["severity"] == "important"]
        waived_ids = [wid for wid in important_ids if wid in waivers]
        remaining = [wid for wid in important_ids if wid not in waivers]
        if waived_ids:
            print(f"[pre-ci] 已豁免 {len(waived_ids)} 条 important（人已裁决）：{', '.join(waived_ids)}")
        unused = sorted(set(waivers) - all_ids)
        if unused:
            print(f"[pre-ci] 提示：{len(unused)} 条豁免未匹配当前 findings：{', '.join(unused)}")
        if remaining:
            print(f"[pre-ci] FAIL: 还有 {len(remaining)} 条 important 未处置：{', '.join(remaining)}")
            return 2

    print(f"[pre-ci] OK: {state['model']} 于 {state['recorded_at']} 审过当前改动")
    return 0


def self_test() -> int:
    cases: list[tuple[str, int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / ".review").mkdir()
        src = root / "src" / "main.py"
        src.write_text("print(1)\n", encoding="utf-8")
        findings = root / FINDINGS
        module = findings_parser()

        def write_findings(severity: str | None) -> None:
            if severity is None:
                findings.write_text("findings: []\n", encoding="utf-8")
                return
            item = {
                "id": "spec-1",
                "dimension": "spec",
                "severity": severity,
                "file": "src/main.py",
                "line": 1,
                "claim": "示例问题",
                "evidence": "示例证据",
                "trigger": "示例触发",
                "suggestion": "示例建议",
                "confidence": 0.8,
            }
            findings.write_text(module.dump([item]), encoding="utf-8")

        cases.append(("从没审过", check(root), 1))
        write_findings("nit")
        cases.append(("产物合法可 record", record(root, "deepseek-v4.2"), 0))
        cases.append(("审过且未改动", check(root), 0))

        src.write_text("print(2)\n", encoding="utf-8")
        cases.append(("审过后改了代码", check(root), 1))
        src.write_text("print(1)\n", encoding="utf-8")

        write_findings("important")
        record(root, "deepseek-v4.2")
        cases.append(("important 由脚本数出不许瞒报", check(root), 2))

        write_findings("nit")
        record(root, "deepseek-v4.2")
        findings.write_text("findings: []\n", encoding="utf-8")
        cases.append(("审核产物被替换", check(root), 1))

        write_findings("nit")
        record(root, "deepseek-v4.2")
        state_path = root / STATE
        state = json.loads(_common.read_text(state_path))
        state["actor"] = "main-agent"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        cases.append(("证据由主 Agent 伪造", check(root), 3))

        # 无效模型在 record 阶段就拒绝，不写证据；已有证据保持有效。
        cases.append(("record 拒绝无效模型", record(root, "inherit"), 1))
        cases.append(("无效模型未污染证据", check(root), 3))

        # 审计 P1-1 回归：工具缓存 / 覆盖率 / 日志不是代码改动，不能让证据过期。
        write_findings("nit")
        record(root, "deepseek-v4.2")
        (root / ".pytest_cache" / "v" / "cache").mkdir(parents=True)
        (root / ".pytest_cache" / "v" / "cache" / "lastfailed").write_text("{}", encoding="utf-8")
        (root / ".coverage").write_text("cov", encoding="utf-8")
        (root / "build.log").write_text("log", encoding="utf-8")
        cases.append(("工具缓存不算代码改动", check(root), 0))

        # 人裁决豁免：升级给人之后由人写 waivers.json，check 扣除并列出；
        # 缺字段判不可读，未匹配的豁免不影响判定。
        write_findings("important")
        record(root, "deepseek-v4.2")
        waivers_path = root / WAIVERS
        waivers_path.write_text(
            json.dumps({"waivers": [{"id": "spec-1", "reason": "人已接受", "approved_by": "用户"}]}),
            encoding="utf-8",
        )
        cases.append(("waiver 覆盖 important", check(root), 0))
        waivers_path.write_text(
            json.dumps({"waivers": [{"id": "spec-1", "reason": "人已接受"}]}),
            encoding="utf-8",
        )
        cases.append(("waiver 缺字段", check(root), 6))
        waivers_path.write_text(
            json.dumps({"waivers": [{"id": "nope", "reason": "r", "approved_by": "u"}]}),
            encoding="utf-8",
        )
        cases.append(("waiver 未匹配仍按 important 判", check(root), 2))
        waivers_path.unlink()

        # BOM 容忍：findings 带 BOM 也必须能解析。
        state_path.write_text("{not json", encoding="utf-8")
        cases.append(("证据损坏", check(root), 6))
        state_path.unlink()

        findings.unlink()
        cases.append(("缺审核产物不能 record", record(root, "deepseek-v4.2"), 6))
        findings.write_text("findings:\n  - id: x\n", encoding="utf-8")
        cases.append(("产物格式不合法不能 record", record(root, "deepseek-v4.2"), 6))
        cases.append(("不许手填 --important", main(["record", "--model", "x", "--important", "0"]), 6))

    failures = [f"{label}: 期望 {expected}，实际 {actual}" for label, actual, expected in cases if actual != expected]
    if failures:
        print("[pre-ci] self-test FAILED:")
        for failure in failures:
            print("  - " + failure)
        return 5
    print(f"[pre-ci] self-test OK ({len(cases)} cases)")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "self-test":
        return self_test()

    root = Path(__file__).resolve().parent.parent
    command = "check"
    model = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in {"record", "check"}:
            command = arg
            i += 1
        elif arg == "--root" and i + 1 < len(argv):
            root = Path(argv[i + 1])
            i += 2
        elif arg == "--model" and i + 1 < len(argv):
            model = argv[i + 1]
            i += 2
        elif arg == "--important":
            print("[pre-ci] FAIL: important 由脚本从审核产物逐条数出，不接受 --important")
            return 6
        else:
            print(f"[pre-ci] FAIL: 未知参数 {arg}")
            return 6

    if command == "record":
        if not model:
            print("[pre-ci] FAIL: record 需要 --model")
            return 6
        return record(root, model)
    return check(root)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
