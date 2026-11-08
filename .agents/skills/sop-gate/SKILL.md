---
name: sop-gate
description: DSHSOP 门禁纪律与 CI 前独立审核流程。改完代码或文档、准备提交、准备汇报完成之前，运行 python scripts/gate.py；红了就停下来修，不许跳过或造假。CI 前用 subagent 换模型做独立审核，把 findings 原样落盘并记录证据。
whenToUse: 改完代码或文档、准备提交、准备向用户汇报完成之前；用户说「收尾 / 交付 / 可以提交了」时。
---

# DSHSOP 门禁 skill

规则唯一出处是仓库根的 `SOP.md`；本文件只写执行顺序，不复述判定。

## 做什么

1. 在项目根运行：

   ```
   python scripts/gate.py
   ```

   没有 `gate.py` 就明说「本项目没有门禁」，**不要假装跑过**。

2. 全绿：一行确认（`GATE PASS: N/N`），继续原任务。
3. 有红：贴出失败项名称与最后 20 行输出；**停下来**，修完重跑，直到全绿或明确把失败交给人。

## CI 前独立审核（② 段）

本地基础检验通过之后：

1. 用 `subagent` 起一个**不同 provider/model** 的审核子代理（模型取 `sop.json` 的 `review.preCiModel`），把 `REVIEW.md` 与改动交给它，要求按十字段格式输出 findings。不要自己审——自己审等于同源复读。
2. 把返回的 YAML **原样**落盘到 `.review/pre-ci-findings.yaml`，不许改写、删条目、降级。
3. 记录证据：`python scripts/pre-ci-review.py record --model <实际使用的模型 ID>`（`important` 由脚本逐条数出，不接受手填）。
4. 立刻校验：`python scripts/pre-ci-review.py check`，返回 0 之前不许汇报完成、不许提交。
5. 再跑 `python scripts/gate.py`。

## 纪律

- **不要跳过**：不要 `git commit --no-verify`、不要临时改 `core.hooksPath`、不要注释掉失败的检查。
- **不要造假**：没跑就是没跑；跑挂了就是挂了。
- 汇报时写「门禁：全绿（N 项）」或「门禁：X 失败」，不要只说「测试通过」。
- 轮次额度、到顶与复盘按 `SOP.md` 的「轮次与刹车」「到顶之后」执行。
