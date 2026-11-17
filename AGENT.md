# code-loop 项目概况

## 项目概述

code-loop 是一个 **GitHub Issue 状态驱动的自动化编码代理技能**，通过扫描指定仓库中分配给当前用户的 open issues，按项目看板状态分类，并自动派发子代理完成 4 阶段工作流：

1. **Task1 — Worktree 初始化**：为 Issue 创建独立的工作区（git worktree），初始化文档目录和 flow_meta.json，推进看板状态 Backlog → Ready
2. **Task2 — 方案 PR 生成**：基于 Issue 内容生成技术解决方案文档，提交 Document PR 等待人工评审
3. **Task3 — 编码 + 测试**：按方案实现业务代码，配套验收测试和服务验证，提交 Deployment PR 等待人工评审
4. **Task4 — 清理回收**：PR 合并后删除 worktree、分支和文档，关闭 Issue，完成流程归档

版本: 3.4.0 | 作者: Limix | 协议: MIT

---

## 目录结构

```
code-loop/
├── SKILL.md                              # 技能主文档（入口定义、输出格式、调度约束）
├── AGENT.md                              # 本文件，项目概况与快速参考
├── references/                           # 工作流参考文档与子代理提示词
│   ├── example.env                       # .env 配置文件模板
│   ├── workflow-task1-worktree-init.md   # Task1 工作流定义
│   ├── workflow-task2-solution-pr.md     # Task2 工作流定义
│   ├── workflow-task3-coding-pr.md       # Task3 工作流定义
│   ├── workflow-task4-cleanup.md         # Task4 工作流定义
│   ├── subagent-solution-prompt.md       # Task2 子代理提示词（方案生成）
│   └── subagent-coding-prompt.md         # Task3 子代理提示词（编码+测试）
└── scripts/                              # Python 执行脚本
    ├── common.py                         # 公共模块（配置加载、GitHub CLI 封装、Lock/Meta 管理）
    ├── issue_dispatch.py                 # 主调度器：查询 Issue 并生成派发计划 JSON
    ├── task1_worktree_init.py            # Task1 脚本：Worktree 初始化
    ├── task2_solution_pr.py              # Task2 脚本：方案 PR 前置检查 / 提交
    ├── task3_code_pr.py                  # Task3 脚本：代码 PR 前置检查 / 提交
    └── task4_cleanup.py                  # Task4 脚本：清理回收
```

---

## 核心工作流

```
Backlog ──Task1──> Ready ──Task2──> In progress ──Task3──> Done ──Task4──> Closed
```

| 阶段 | 触发状态 | 核心动作 | 产物 |
|------|---------|---------|------|
| Task1 | Backlog | 创建 worktree、初始化 flow_meta.json | 独立工作区 + 元数据 |
| Task2 | Ready | LLM 生成解决方案文档、提交 Document PR | solution.md + PR |
| Task3 | In progress | LLM 编码实现、测试验证、提交 Deployment PR | 代码变更 + PR |
| Task4 | Done | 删除 worktree/分支/docs、关闭 Issue | 资源回收 |

---

## 关键文件说明

### 调度层

- **issue_dispatch.py**：一键完成配置加载、GitHub Issue 查询分类、派发计划生成。主代理(LLM)只需机械读取 JSON 输出并按 `dispatch_plan.items` 调用 `Task` 工具，无需再做任何决策。
- **SKILL.md**：定义主调度器的职责边界——仅执行脚本、机械派发、禁止越权（不解析 issue 内容、不生成代码、不修改仓库状态）。

### 执行层（Task1~Task4）

- **task1_worktree_init.py**：单阶段脚本。防重检测 → 创建 run.lock → git fetch → 创建 worktree → 初始化目录结构 → 写入 flow_meta.json → 更新看板状态 → 清理 run.lock。
- **task2_solution_pr.py**：双阶段脚本。`--phase pre` 输出 JSON（含 Issue 信息、驳回意见、action 决策）；`--phase post` 提交 solution.md、创建 Document PR、更新 flow_meta。
- **task3_code_pr.py**：双阶段脚本。`--phase pre` 同 Task2 结构；`--phase post` 提交代码变更、创建 Deployment PR、额外推送 flow_meta.json 更新。
- **task4_cleanup.py**：单阶段脚本。幂等检测 → PR 合并确认 → 删除本地 worktree → 删除远端分支 → 删除 docs 目录 → 关闭 Issue。

### 公共模块

- **common.py**：提供所有脚本共享的能力：
  - `_PARAM_DEFS_BASE`：统一的配置参数定义（含必填/默认值/描述）
  - `load_config_from_env()`：从 `.env` 文件加载配置，优先级 环境变量 > 文件 > 默认值
  - `_run_gh()` / `_run_git()`：GitHub CLI 和 Git 命令的封装
  - Lock 管理：`lock_create/delete/exists`，原子操作防止并发冲突
  - Flow Meta 管理：`flow_meta_read/update`，每个 Issue 的持久化状态跟踪
  - PR 操作：状态查询、评论获取、创建 PR、重跑判断
  - 看板状态更新：通过 `gh project item-edit` 自动推进 Issue 状态

---

## 配置说明

配置文件为 `.env` 格式，默认位于项目根目录的 `.worktree/.env` 下。

### 必填参数

| 参数 | 说明 |
|------|------|
| `REPO` | GitHub 仓库 (owner/name) |
| `BASE_BRANCH` | 基准分支（所有 PR 和 worktree 的基准） |
| `ASSIGNEE` | GitHub 用户名（被分配人） |
| `GITHUB_BOARD_NUMBER` | GitHub Project Board 编号 |
| `GITHUB_BOARD_ID` | GitHub Project Board ID (PVT_...) |
| `GITHUB_BOARD_STATUS_FIELD_ID` | 看板状态字段 ID (PVTSSF_...) |

### 可选参数（带默认值）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `BRANCH_PREFIX` | `issue-` | 分支名前缀 |
| `WORKTREE_PREFIX` | `.worktree` | Worktree 根目录（相对于仓库根） |
| `ISSUE_DOCS_PREFIX` | `docs/issue` | Issue 文档路径前缀 |

### 看板状态映射

| 参数 | 默认值 | 对应 Task |
|------|--------|----------|
| `STATUS_TASK1` | Backlog | Task1 |
| `STATUS_TASK2` | Ready | Task2 |
| `STATUS_TASK3` | In progress | Task3 |
| `STATUS_TASK4` | Done | Task4 |

---

## 关键机制

### 防重与并发控制

- **run.lock**：每个 Task 执行前创建原子锁文件，防止同一 Issue 的并发操作。Task 结束后强制清理。
- **flow_meta.json**：每个 Issue 的持久化元数据，记录分支名、PR 链接、看板 item_id、状态等，是判重和状态推进的核心依据。
- **PR 去重**：通过 `flow_meta.json` 中缓存的 PR 号 + `gh pr view` 判断，禁止直接使用 `gh pr list`。

### 子代理职责边界

- **Task2 / Task3** 的子代理包含 LLM 生成环节：
  - Task2 子代理读取 `subagent-solution-prompt.md`，生成技术方案 Markdown（不含可运行代码）。
  - Task3 子代理读取 `subagent-coding-prompt.md`，实现业务代码并配套测试，启动本地服务验证。
- **Task1 / Task4** 为纯脚本任务，无 LLM 生成环节。

### 主调度器约束

- 仅执行 `issue_dispatch.py` 获取 JSON 输出
- 按 `dispatch_plan.items` 机械调用 `Task` 工具，最多 3 个并行子代理
- 禁止解析 issue 内容、禁止生成代码、禁止修改仓库状态、禁止直接调用 `gh` 或 `git`

---

## 快速使用

```bash
# 1. 进入目标项目目录，确保 .worktree/.env 已配置

# 2. 生成派发计划
python {code-loop技能目录}/scripts/issue_dispatch.py

# 3. 主代理读取 JSON 中的 dispatch_plan，并行派发子代理执行各 Task
```

---

## 设计原则

- **配置驱动**：所有可变参数（仓库、分支、状态映射、看板 ID）通过 `.env` 配置，脚本零硬编码
- **状态机驱动**：Issue 的看板状态决定触发哪个 Task，形成闭环自动化
- **幂等设计**：每个 Task 支持重复执行，通过 worktree 存在性、flow_meta.json、PR 状态进行防重
- **隔离执行**：每个 Issue 拥有独立的 git worktree，避免分支间污染
- **LLM 与脚本解耦**：LLM 仅负责生成内容（方案/代码），所有 git/gh 操作由脚本完成，确保可重复性和安全性
