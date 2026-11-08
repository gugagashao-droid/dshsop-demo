# 配置说明

> 项目差异只写在两个文件里：`sop.json`（命令与审核模型）和 `ci.json`（CI 命令清单）。
> 其余文件是规则或模板，正常不需要改。

## sop.json

```json
{
  "commands": {
    "lint": ["npm", "run", "lint"],
    "typecheck": ["npm", "run", "typecheck"],
    "test": ["npm", "run", "test"],
    "build": ["npm", "run", "build"]
  },
  "review": {
    "preCiModel": "<与主会话不同的具体模型 ID>",
    "crossModel": "<CI 后三视角模型 ID，须与 preCiModel 不同>"
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `commands` | 否 | 用 argv 数组写，避免 shell 引号差异。留空则自动检测（见下）。 |
| `commands.*` | — | 只认 `lint` / `typecheck` / `test` / `build` 四个键；其它键被忽略。 |
| `review.preCiModel` | 是 | CI 前独立审核用的模型；必须是具体 ID（只含字母数字与 `. _ : @ / -`，不能有空格或中文），不能是 `auto`、`inherit` 之类占位词。 |
| `review.crossModel` | 是 | CI 后三视角用的模型；必须与 `preCiModel` 不同。 |

规则：

- `lint` / `typecheck` / `test` 至少配一个；只配 `build` 会被判红（构建不证明代码被检查过）。
- 测试命令必须是**单次运行**，不能是 watch 模式，否则门禁会挂住。
- 命令里不要写 `<...>`（会被当作占位符）。

## gate.py 的自动检测

`sop.json` 的 `commands` 为空时：

| 项目形态 | 检测方式 | 跑的步骤 |
|---|---|---|
| Node | 存在 `package.json`，读 `scripts` 里的 `lint` / `typecheck` / `test` / `build`；包管理器按 `pnpm-lock.yaml` / `yarn.lock` / 默认 npm | 有哪个跑哪个，缺的记 WARN |
| Python | 存在 `pyproject.toml` 或 `requirements.txt` | 跑 `python -m pytest -q`（唯一内置的 Python 检查）；**pytest 没装时这一步判红**，不像 Node 分支那样记 WARN |
| 其它 | 都没有 | 直接判红，要求填 `commands` |

Python 项目如果用 `unittest` / `tox`，请**显式填 `commands`**，否则会因缺 pytest 而红。

## ci.json

```json
{
  "machine": [
    { "name": "门禁一致性", "cmd": ["python", "scripts/gate.py", "--ci"] }
  ],
  "platform": [
    { "name": "多平台构建", "cmd": ["npm", "run", "build"] }
  ],
  "ai": [
    { "name": "spec 视角", "cmd": ["python", "scripts/ai_review.py", "--axis", "spec", "--out", ".review/spec.yaml"] }
  ]
}
```

| 段 | 跑在哪 | 放什么 |
|---|---|---|
| `machine` | 单台 ubuntu | 静态检查、全量测试、依赖扫描、性能基准 |
| `platform` | 三平台矩阵 | 构建、兼容性测试（每条命令会在每个 OS 上各跑一次） |
| `ai` | CI 之后 | 三视角审核 → 合并 → 算轮次 → 记账 |

字段规则：

- `cmd` 必须是数组；任何一项含 `<...>` 就判「未填」，CI 直接红。
- 以 `_` 开头的键会被忽略（可以放 `_note`）。
- `@相对路径` 参数会被替换成文件内容（`record` 的 `--round` / `--important` 用），文件缺失或为空直接判不可读。
- `--stage` 可选 `machine` / `platform` / `ai` / `all`，任一命令非 0 立即停。

## ARCHITECTURE.md 的最小可过形态

七个栏目必须都在，且不能留占位符：

```markdown
## 项目环境
| 项 | 内容 |
|---|---|
| 语言 / 运行时 | Python 3.10 |
| 主要入口 | `src/app.py` |
| 运行 / 部署形态 | CLI |

## 模块
| 模块 | 根路径 / 入口 | 拥有的职责 / 数据 | 对外接口 |
|---|---|---|---|
| app | `src/app.py` | 命令行入口 | `main()` |

## 依赖
| 依赖方 | 被依赖方 | 允许方向与原因 |
|---|---|---|

## 边界
| 边界 | 不可信输入 / 外部副作用 | 负责模块 | 失败时的可观察行为 |
|---|---|---|---|
| CLI 参数 | argv | app | 非零退出码与错误提示 |

## 扩展点
**结论：** `无：当前不需要稳定扩展点。`

## 架构不变量
| 不变量 | 反例 | 可执行检查 |
|---|---|---|
| 入口是唯一参数解析点 | 业务模块直接读 argv | `python -m unittest` |

## 结构检查
```json
["python", "scripts/check_arch.py"]
```
```

要点：

- 模块的根路径必须真实存在，不能用通配符，不能跳出项目根。
- 「架构不变量」的「可执行检查」列必须含反引号命令或测试名，`none` / `n/a` 会被拒。
- 「结构检查」命令不能是 `-c pass` 这类内联空操作，也不能递归调用本门禁脚本。

## 结构检查命令怎么写

一个真实的最小例子（检查 `scripts/` 下没有未登记的文件）：

```python
# scripts/check_arch.py
from pathlib import Path

EXPECTED = {"gate.py", "check_arch.py"}
actual = {p.name for p in Path(__file__).resolve().parent.glob("*.py")}
raise SystemExit(0 if actual == EXPECTED else 1)
```

也可以换成：依赖方向扫描、路由完整性、禁止 import 扫描、生成物与提交物一致性检查。

## 真实项目接入清单

1. 把 `template/` 的全部内容复制到项目根（含隐藏目录），**保留项目自己的 README 不动**；
2. 如果有 `package.json` / `pyproject.toml`，先不填 `commands`，直接跑 `python scripts/gate.py` 看自动检测结果；
3. 按实际项目填 `ARCHITECTURE.md`（尤其模块路径与结构检查命令）；
4. 把模板里的 `spec.md` / `intent.md` / `plan.md` 换成项目真实内容，别留占位符；
5. `ci.json` 的 `machine` 先接真实测试命令，`platform` 接真实构建命令，`ai` 段接 `ai_review.py`；
6. 提交前跑一次 `python scripts/gate.py`，全绿再配 hook。
