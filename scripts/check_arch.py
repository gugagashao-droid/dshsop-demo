#!/usr/bin/env python
"""最小结构检查：scripts/ 下只允许已登记的门禁脚本。

为什么存在：ARCHITECTURE.md 的「结构检查」必须登记一个真实命令，
不能是空操作（check-structure.py 会拒绝 -c pass 这类内联代码）。
不改会怎样：结构契约只剩文档，没人能证明它和实际目录一致。
"""
from pathlib import Path

EXPECTED = {
    "_common.py",
    "ai_review.py",
    "check-docs.py",
    "check-model-separation.py",
    "check-structure.py",
    "check_arch.py",
    "ci-run.py",
    "gate.py",
    "merge-findings.py",
    "pre-ci-review.py",
    "review-state.py",
}

actual = {path.name for path in Path(__file__).resolve().parent.glob("*.py")}
extra = actual - EXPECTED
missing = EXPECTED - actual
if extra or missing:
    print(f"[arch] FAIL: extra={sorted(extra)} missing={sorted(missing)}")
    raise SystemExit(1)
print("[arch] OK")
