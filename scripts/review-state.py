#!/usr/bin/env python
"""交叉审轮次状态机。

为什么要有这个文件：agent 靠记忆判断"跑到第几轮"会漏（实测漏过第 2 轮），
把轮次和代码哈希落到磁盘，"改过但没重审"才变成可检测的事实。
不改：修完就汇报，第 N 轮永远没人跑。

输出一律用 ASCII：PowerShell 5.1 控制台按 GBK 解码 Python 的 UTF-8 输出，中文会乱码。

用法：
    python scripts/review-state.py check
    python scripts/review-state.py record --round 2 --important 4 --axes spec,standards
    python scripts/review-state.py next-round --out .review/next-round
    python scripts/review-state.py status

退出码：
    0 clean   1 never reviewed   2 changed-or-pending   3 hit round limit   4 escalate to human
    5 write-entry refused   6 state unreadable
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

ROOT = _common.ROOT
STATE = ROOT / ".review" / "state.json"

# 为什么不止 src：只盯 src 时，改测试/脚本/门禁都不会让状态失效，
# 会出现「审过了」但实际改动没被审过。这里覆盖审核实际会看的范围。
WATCHED = [
    ROOT / "src",
    ROOT / "tests",
    ROOT / "scripts",
    ROOT / "gate.py",
    ROOT / ".githooks",
    ROOT / ".agents",
    ROOT / ".github",
    ROOT / "sop.json",
    ROOT / "ci.json",
    ROOT / "package.json",
    ROOT / "pyproject.toml",
    ROOT / "requirements.txt",
    ROOT / "index.html",
    ROOT / "README.md",
    ROOT / "REVIEW.md",
    ROOT / "ARCHITECTURE.md",
    ROOT / "GLOSSARY.md",
    ROOT / "spec.md",
    ROOT / "intent.md",
    ROOT / "docs" / "specs",
    # 注意这里**不含** LESSONS.md 与 plan.md：它们是记录，不是规则。
    # 放进来会形成死循环——写一行台账就让状态失效，于是每写一条教训都要重审一轮。
    # 判据：改它会不会改变「什么算做对了」？会 → 放；只是记录「发生过什么」→ 不放。
]

# 「什么算改动」的忽略口径唯一出处是 _common；本文件不再维护第二份清单。
# codebuddy-sop 里两套清单不一致，pytest / mypy 缓存让证据与轮次状态假过期（审计 P1-1）。

# 轮次额度以 README.md 的「轮次与刹车」为准，这里只是它的实现常量（别在这写第二份规则）
MAX_ROUNDS = 3


def src_hash() -> str:
    """对审核覆盖面做内容哈希。只在审核时记录，改动后必然变化。"""
    digest = hashlib.sha256()
    seen: set[str] = set()

    def add(path: Path) -> None:
        # 只把相对路径喂进摘要：绝对路径会随克隆 / 换目录 / 换机器而变，
        # 导致「一行代码没改，check 却报 src changed」并推动轮次虚增（审计发现）。
        relative = path.relative_to(ROOT).as_posix()
        if relative in seen:
            return
        seen.add(relative)
        digest.update(relative.encode())
        digest.update(path.read_bytes())

    for target in WATCHED:
        if target.is_file():
            add(target)
            continue
        if not target.exists():
            continue
        for path in sorted(target.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                add(path)

    # 兜底扫描覆盖任意源码布局与配置；Markdown 记录由 WATCHED 精确选择，避免改台账触发重审。
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(name for name in dirs if name not in _common.IGNORED_DIRS)
        for name in sorted(files):
            path = Path(root) / name
            if (
                name == ".git"
                or name in _common.IGNORED_FILES
                or name.startswith(".coverage.")
                or path.suffix.lower() in _common.IGNORED_SUFFIXES
                or path.suffix.lower() == ".md"
            ):
                continue
            add(path)
    return digest.hexdigest()[:16]


def unreadable_state() -> None:
    # 损坏状态不能伪装成「从未审过」，否则下一次 record 会把轮次和配额清零。
    # 不改：traceback 的退出码 1 会被门禁误报为普通未审状态。
    print("[review] state unreadable; restore .review/state.json or escalate to human")
    raise SystemExit(6)


def load() -> dict:
    if not STATE.exists():
        return {}
    try:
        data = json.loads(_common.read_text(STATE))
    except (json.JSONDecodeError, UnicodeError, OSError):
        unreadable_state()
    if not isinstance(data, dict) or not data:
        unreadable_state()
    round_no = data.get("round")
    important = data.get("important")
    if (
        not isinstance(round_no, int)
        or isinstance(round_no, bool)
        or round_no < 0
        or not isinstance(important, int)
        or isinstance(important, bool)
        or important < 0
    ):
        unreadable_state()
    if round_no > 0 and not isinstance(data.get("srcHash"), str):
        unreadable_state()

    resets = data.get("resets", 0)
    history = data.get("history", [])
    status = data.get("status")
    if (
        not isinstance(resets, int)
        or isinstance(resets, bool)
        or resets < 0
        or not isinstance(history, list)
        or not all(isinstance(item, dict) for item in history)
        or status not in {"clean", "fixes-pending", "reset-pending"}
    ):
        unreadable_state()
    for key in ("categories", "categoryRounds", "categoryAcked"):
        if not isinstance(data.get(key, {}), dict):
            unreadable_state()
    if (round_no == 0 and status != "reset-pending") or (
        round_no > 0 and status == "reset-pending"
    ):
        unreadable_state()
    sequence = data.get("sequence")
    if sequence is not None and (
        not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0
    ):
        unreadable_state()

    # 旧版 reset 留下的 round=0 状态没有归档基线；按损坏处理，避免清零守卫 fail-open。
    if round_no == 0 and "lastArchivedImportant" not in data:
        unreadable_state()
    return data


def save(data: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def cmd_check() -> int:
    state = load()
    if not state:
        print("[review] no review recorded yet -> run round 1 (full scope)")
        return 1

    round_no = state.get("round", 0)
    important = state.get("important", 0)

    if round_no == 0:
        print("[review] new branch after cleanup -> run round 1 (full scope)")
        return 1

    # 这两条必须放在所有 return 之前：放在末尾时，只要有 important 待修就提前 return 2，
    # 提示永远打不出来（实测踩过）。
    # 已 ack 的类别不再提示；但若它出现的最晚轮次比 ack 时更晚，说明又犯了，重新响。
    # 所以 ack 记的是「复盘到第几轮」，不是「这类问题以后不用管了」。
    acked = state.get("categoryAcked") or {}
    repeats = {
        k: v
        for k, v in (state.get("categoryRounds") or {}).items()
        if len(v) >= 2 and max(v) > acked.get(k, 0)
    }
    if repeats:
        detail = "; ".join(f"{k} in reviews {v}" for k, v in repeats.items())
        print(f"[review] WARN: same category across rounds -> retro required ({detail})")
        print(f"[review]       after retro: ack --categories {','.join(repeats)}")

    # 轴不齐的判定放在最后（return 0 之前）：有 important 待修时，那个问题更要紧。

    # 先判上限，再判其他：否则"有 important 待修"会掩盖"已经该停手"这件事
    if important > 0 and round_no >= MAX_ROUNDS:
        if state.get("resets", 0) >= 1:
            print(
                f"[review] round {round_no} still has {important} important after one cleanup branch "
                f"-> escalate to human, no more auto-reset"
            )
            return 4
        print(
            f"[review] round {round_no} still has {important} important "
            f"-> do delete/merge-only cleanup, then: review-state.py reset"
        )
        return 3

    if state.get("status") == "fixes-pending":
        print(
            f"[review] round {round_no} has {important} important pending fix "
            f"-> re-review after fixes"
        )
        return 2

    # 轮次额度已用完后又改代码：必须先 reset 开新分支，不能悄悄跑第 4 轮。
    # 自动 reset 已用过时只能升级给人；否则 3/record/reset 的提示会互相指回，形成死锁。
    if round_no >= MAX_ROUNDS and state.get("srcHash") != src_hash():
        if state.get("resets", 0) >= 1:
            print(
                f"[review] round limit used after one cleanup branch and src changed "
                f"-> escalate to human; only a human may run reset --force after retro"
            )
            return 4
        print(
            f"[review] round limit used and src changed "
            f"-> run review-state.py reset before reviewing a new branch"
        )
        return 3

    if state.get("srcHash") != src_hash():
        print(f"[review] src changed since last review -> run round {round_no + 1}")
        return 2

    # 轴不齐 = 审核不完整。空轴与未跑轴在产物上同形，只能靠这条区分。
    # 放在最后：有 important 待修或代码变过时，那些问题更要紧，会先返回。
    axes_covered = state.get("axes") or []
    if set(axes_covered) != {"spec", "standards"}:
        print(
            "[review] review incomplete: axes covered "
            f"{axes_covered if axes_covered else 'none'} "
            "-> record next round with --axes spec,standards"
        )
        return 2

    print(f"[review] round {round_no} covers current src, nothing pending")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    scope = {1: "full", 2: "narrow"}.get(args.round, "incremental")
    previous = load()

    # 为什么校验：record 是唯一的写入口，只允许 previous_round + 1；
    # 不改：同轮覆盖、跳轮或回退会洗绿状态，让 MAX_ROUNDS 与 resets 上限失效。
    if args.round < 1 or args.important < 0:
        print("[review] refuse: need round >= 1 and important >= 0")
        return 5
    previous_round = previous.get("round", 0)
    # 额度只属于当前分支；到顶后必须 reset，不能把 MAX_ROUNDS 当成建议值。
    if previous_round >= MAX_ROUNDS:
        if previous.get("resets", 0) >= 1:
            print(
                "[review] refuse: auto reset already used; after retro a human may run "
                "reset --force"
            )
        else:
            print(
                f"[review] refuse: round limit {MAX_ROUNDS} already used "
                f"-> run review-state.py reset before a new branch"
            )
        return 5
    if args.round != previous_round + 1:
        if not previous:
            print("[review] refuse: no review recorded yet; start with round 1")
        else:
            print(
                f"[review] refuse: next round must be {previous_round + 1} "
                f"(got {args.round}); use reset instead"
            )
        return 5
    # important 从 >0 降到 0 = 声称「全都修完了」：
    # 跨轮次或 reset 重开时都要求代码真的变过，一行没改却说修完了，只可能是没修。
    # reset 把当前 important/hash 清零了，所以必须从归档字段恢复上一条审核基线。
    baseline_important = previous.get("important", 0)
    baseline_hash = previous.get("srcHash")
    if previous_round == 0:
        baseline_important = previous.get("lastArchivedImportant", 0)
        baseline_hash = previous.get("lastArchivedSrcHash", "")
    if args.important == 0 and baseline_important > 0:
        if src_hash() == baseline_hash:
            print(
                "[review] refuse: important dropped to 0 but no code changed since "
                "the archived review -> nothing was actually fixed"
            )
            return 5

    # 两轴缺一即无效：空轴与未跑轴在产物上完全同形，只能在写入口挡。
    axes = [a.strip() for a in (args.axes or "").split(",") if a.strip()]
    # 用集合比较而不是只看长度：`--axes spec,spec` 长度是 2，却整个漏掉 standards 轴（审计发现）
    if set(axes) != {"spec", "standards"}:
        print("[review] refuse: need exactly both axes -> --axes spec,standards")
        return 5

    # category 值域固定五个：值域一大就没人填。
    # 记它只有一个目的——让「同一类 ≥2 次」这条复盘条件从「永远为假」变成可判定。
    cats: dict[str, int] = {}
    if args.categories:
        for part in args.categories.split(","):
            key, _, val = part.partition(":")
            key, val = key.strip(), val.strip()
            if not key or not val.isdigit():
                print("[review] refuse: bad --categories, use e.g. 4-boundary:5,1-contract:2")
                return 5
            cats[key] = int(val)

    # sequence 跨 reset 单调递增，给类别计数一个不会被轮回轮号覆盖的身份。
    sequence = previous.get("sequence", previous.get("round", 0)) + 1
    cat_rounds = dict(previous.get("categoryRounds") or {})
    for key in cats:
        rounds = cat_rounds.get(key, [])
        if sequence not in rounds:
            rounds.append(sequence)
        cat_rounds[key] = sorted(rounds)

    save(
        {
            "round": args.round,
            "sequence": sequence,
            "scope": scope,
            "srcHash": src_hash(),
            "important": args.important,
            "reviewedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            # 有 important 就不能算 clean，否则 check 会放行
            "status": "fixes-pending" if args.important > 0 else "clean",
            # 必须继承：record 若把 resets 抹掉，清理分支第二次到顶就检测不到，
            # 会退化成无限自动重置
            "resets": previous.get("resets", 0),
            "history": previous.get("history", []),
            "axes": axes,
            "categories": cats,
            "categoryRounds": cat_rounds,
            # 必须继承：record 若把 ack 抹掉，每记一轮就把「已复盘」的标记清空，WARN 永远消不掉。
            # 和当初 resets 被吞掉是同一类错误——写入口只想着写自己关心的字段。
            "categoryAcked": previous.get("categoryAcked", {}),
        }
    )
    print(
        f"[review] recorded round {args.round} / {scope} / important={args.important} "
        f"/ axes={'+'.join(axes)}"
    )
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    """标记「这一类已经复盘过了」。

    为什么不直接删掉计数：删了就看不出「曾经反复过」。
    改成记「已复盘到第几轮」——同类若在之后的轮次再出现，提示会重新响。
    """
    state = load()
    if not state:
        print("[review] nothing to ack (no review recorded yet)")
        return 0

    cat_rounds = state.get("categoryRounds") or {}
    targets = [c.strip() for c in (args.categories or "").split(",") if c.strip()]
    if not targets:
        # 不带 --categories 就 ack 当前所有还在提示的类别
        targets = [k for k, v in cat_rounds.items() if len(v) >= 2]
    if not targets:
        print("[review] nothing to ack")
        return 0

    acked = dict(state.get("categoryAcked") or {})
    for key in targets:
        rounds = cat_rounds.get(key, [])
        if not rounds:
            print(f"[review] refuse: category '{key}' has no recorded rounds")
            return 5
        acked[key] = max(rounds)

    state["categoryAcked"] = acked
    save(state)
    print(f"[review] acked {', '.join(sorted(targets))} (retro covered through review {max(acked.values())})")
    return 0


def last_review_epoch(state: dict) -> float:
    """返回最近一次审核记录的时间；ack 改状态文件不能改变这个判断基准。"""
    reviewed_at = state.get("reviewedAt")
    if not reviewed_at:
        history = state.get("history") or []
        if history:
            reviewed_at = history[-1].get("reviewedAt")
    if reviewed_at:
        try:
            return datetime.fromisoformat(reviewed_at).timestamp()
        except (TypeError, ValueError):
            pass
    return STATE.stat().st_mtime if STATE.exists() else 0


def cmd_reset(force: bool = False) -> int:
    """到顶后进入清理分支：归档旧轮次，轮次归 1。

    为什么校验条件：不加限制它可以被当成"清账工具"，在任意轮次抹掉 important，
    状态机就形同虚设。
    """
    state = load()
    if not state:
        print("[review] nothing to reset")
        return 1

    # 只在到达额度后允许 reset。**不再要求 important > 0**：
    # 「第 3 轮通过后开新分支」也是 reset，那时 important 恰好是 0。
    # 这条曾被一次覆盖式同步冲掉过（模板复制回来时冲掉了项目里的改动）。
    if state.get("round", 0) < MAX_ROUNDS:
        print(
            f"[review] refuse: reset only allowed at round >= {MAX_ROUNDS} "
            f"(current {state.get('round', 0)})"
        )
        return 5

    # 只允许自动重置一次：否则 reset 能反复清账，MAX_ROUNDS 就成了摆设。
    # --force 是人工通道：复盘（LESSONS.md 新增行）之后由人执行，agent 不许自己走。
    if state.get("resets", 0) >= 1 and not force:
        print(
            "[review] refuse: auto-reset already used; do retro then run "
            "'reset --force' (human only)"
        )
        return 5

    # 有 important 的自动 reset 与所有 --force 都必须先留下复盘证据。
    # 基准是「最近一次审核」，不是 state.json 的 mtime——ack 会重写状态，不能用它判断。
    if force or state.get("important", 0) > 0:
        lessons = ROOT / "LESSONS.md"
        if not lessons.exists() or lessons.stat().st_mtime <= last_review_epoch(state):
            print(
                "[review] refuse: reset with important findings/--force requires a new "
                "LESSONS.md row since the last review (retro first, then reset)"
            )
            return 5

    history = state.get("history", [])
    history.append(
        {
            "round": state["round"],
            "important": state["important"],
            "srcHash": state.get("srcHash", ""),
            "reviewedAt": state.get("reviewedAt"),
            # 归档后清空当前分支的类别计数；历史仍可追溯，新分支从第 1 轮重新累计。
            "categories": state.get("categories", {}),
            "categoryRounds": state.get("categoryRounds", {}),
            "categoryAcked": state.get("categoryAcked", {}),
        }
    )

    save(
        {
            "round": 0,
            "scope": "cleanup",
            "srcHash": "",
            "important": 0,
            "status": "reset-pending",
            # 重置次数决定还能不能自动再来一次；第二次到顶就升级给人
            "resets": state.get("resets", 0) + 1,
            "history": history,
            # reset 不能把重要问题洗掉：下一条 record 仍要证明清理期间代码变过。
            "lastArchivedImportant": state.get("important", 0),
            "lastArchivedSrcHash": state.get("srcHash", ""),
            # 类别计数与 sequence 是项目级统计，跨分支保留，轮号才按分支重编。
            "sequence": state.get("sequence", state.get("round", 0)),
            "categories": state.get("categories", {}),
            "categoryRounds": state.get("categoryRounds", {}),
            "categoryAcked": state.get("categoryAcked", {}),
        }
    )
    print(
        f"[review] archived round {state['round']} ({state['important']} important); "
        f"run round 1 (full) on the new branch"
    )
    return 0


def cmd_next_round(args: argparse.Namespace) -> int:
    """把下一轮轮次落盘，供 ci-run.py 的 @file 展开传给 record。

    为什么不写死 round=1：CI 每次是新进程，但 .review/state.json 会跨 run 持久化；
    record 只允许 previous_round + 1，写死会在第 2 轮起被拒绝。
    """
    try:
        previous = load()
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"[review] state unreadable: {exc}")
        return 6
    next_round = previous.get("round", 0) + 1
    if next_round > MAX_ROUNDS:
        print("[review] round limit used -> reset after retro (see WORKFLOW.md)")
        return 5
    if args.out:
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{next_round}\n", encoding="utf-8")
    print(next_round)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["check", "record", "reset", "ack", "status", "next-round"])
    parser.add_argument("--round", type=int, default=0)
    parser.add_argument("--important", type=int, default=0)
    parser.add_argument("--out", default="", help="next-round: write the number to this file")
    parser.add_argument(
        "--axes",
        default="",
        help="comma separated, both required: spec,standards",
    )
    parser.add_argument(
        "--categories",
        default="",
        help="finding categories, e.g. 4-boundary:5,1-contract:2 (fixed vocabulary)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="human-only: bypass the one-auto-reset limit, only after a retro row exists",
    )
    args = parser.parse_args()

    if args.cmd == "check":
        return cmd_check()
    if args.cmd == "next-round":
        return cmd_next_round(args)
    if args.cmd == "record":
        return cmd_record(args)
    if args.cmd == "reset":
        return cmd_reset(args.force)
    if args.cmd == "ack":
        return cmd_ack(args)
    print(json.dumps(load(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
